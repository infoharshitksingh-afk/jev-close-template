"""
Slack app for the close agent (Socket Mode: no public URL, no server to expose).

    export SLACK_BOT_TOKEN=xoxb-...      # from the Slack app's OAuth page
    export SLACK_APP_TOKEN=xapp-...      # app-level token with connections:write
    export ANTHROPIC_API_KEY=...
    python -m agent slack

What it does:
  - @mention the bot (or DM it) with a question about the close -> it answers in the thread.
  - If it needs an owner, it tags them in the thread. When they reply there, it records the answer.
  - After each answer it asks the asker "did this solve it?" A ✅ or ❌ reaction is logged.
  - Questions waiting longer than owner_reminder_hours get one reminder to the backup / close lead.

The Slack wiring is thin; the logic lives in SlackBridge so it can be tested with a fake client.
"""

import os
import re
import threading
import time
from datetime import datetime, timezone

from . import brain
from .qlog import now
from .tools import Agent, mention

YES = {"white_check_mark", "heavy_check_mark", "+1", "thumbsup", "ballot_box_with_check"}
NO = {"x", "-1", "thumbsdown", "negative_squared_cross_mark"}
FEEDBACK = "Did this solve it? React ✅ if yes, ❌ if not. [{qid}]"


class SlackBridge:
    def __init__(self, ag, slack, llm, bot_user_id):
        self.ag, self.slack, self.llm, self.bot = ag, slack, llm, bot_user_id
        self._names = {}
        self._chan = {}

    # ------------------------------------------------------------ helpers
    def name(self, uid):
        if uid not in self._names:
            try:
                u = self.slack.users_info(user=uid)["user"]
                self._names[uid] = (u.get("profile") or {}).get("real_name") or u.get("name") or uid
            except Exception:  # noqa: BLE001
                self._names[uid] = uid
        return self._names[uid]

    def channel_name(self, cid):
        if cid not in self._chan:
            try:
                c = self.slack.conversations_info(channel=cid)["channel"]
                self._chan[cid] = "DM" if c.get("is_im") else "#" + c.get("name", cid)
            except Exception:  # noqa: BLE001
                self._chan[cid] = cid
        return self._chan[cid]

    def allowed(self, cid):
        allow = ((self.ag.cfg.get("slack") or {}).get("channels")) or []
        name = self.channel_name(cid)
        return not allow or name == "DM" or name in allow

    def thread_link(self, cid, ts):
        try:
            return self.slack.chat_getPermalink(channel=cid, message_ts=ts)["permalink"]
        except Exception:  # noqa: BLE001
            return f"slack://{cid}/{ts}"

    def thread(self, cid, ts):
        msgs = self.slack.conversations_replies(channel=cid, ts=ts, limit=50)["messages"]
        out = []
        for m in msgs:
            uid = m.get("user") or m.get("bot_id") or "?"
            who = "close-agent (you)" if uid == self.bot or m.get("bot_id") else f"{self.name(uid)} <@{uid}>"
            text = re.sub(rf"<@{self.bot}>\s*", "", m.get("text", "")).strip()
            out.append({"user": who, "text": text})
        return out

    def post(self, cid, ts, text):
        return self.slack.chat_postMessage(channel=cid, thread_ts=ts, text=text)

    # ------------------------------------------------------------ events
    def on_question(self, cid, user, ts, thread_ts=None):
        """An @mention, a DM, or an owner replying in a thread with an open question."""
        root = thread_ts or ts
        if not self.allowed(cid):
            allow = ", ".join((self.ag.cfg.get("slack") or {}).get("channels") or [])
            self.post(cid, root, f"I answer close questions in {allow}, since they involve customer revenue. Ask me there?")
            return
        ctx = {"asker": f"{self.name(user)} <@{user}>", "asker_id": user, "channel": self.channel_name(cid),
               "thread_link": self.thread_link(cid, root)}
        reply, ctx = brain.respond(self.ag, self.llm, self.thread(cid, root), ctx)
        if reply:
            self.post(cid, root, reply)
        for qid in dict.fromkeys(ctx.get("ask_feedback", [])):
            q = self.ag.log.get(qid) or {}
            asker_id = re.search(r"<@(\w+)>", q.get("asked_by", "")) or None
            asker_id = asker_id.group(1) if asker_id else user
            r = self.post(cid, root, f"<@{asker_id}> " + FEEDBACK.format(qid=qid))
            self.ag.log.add_followup(r["ts"], qid, asker_id)

    def on_thread_message(self, cid, user, ts, thread_ts, text):
        """A plain reply in a thread. Only acted on if the thread has a question waiting on this person."""
        if not thread_ts or user == self.bot or f"<@{self.bot}>" in (text or ""):
            return  # the mention handler covers @mentions
        link = self.thread_link(cid, thread_ts)
        waiting = [q for q in self.ag.log.open_in_thread(link) if q["status"] == "waiting_on_owner"]
        if any(f"<@{user}>" in (q["escalated_to"] or "") for q in waiting):
            self.on_question(cid, user, ts, thread_ts)

    def on_reaction(self, user, reaction, item_ts, cid):
        f = self.ag.log.followup(item_ts)
        if not f or user != f["asked_user"]:
            return
        if reaction in YES:
            worked = "yes"
        elif reaction in NO:
            worked = "no"
        else:
            return
        fields = {"worked": worked, "worked_note": f"{self.name(user)} reacted :{reaction}:"}
        if worked == "yes":
            fields.update(status="closed", closed_at=now())
        self.ag.log.update(f["question_id"], f"{self.name(user)} <@{user}>", f"outcome: {worked}", **fields)
        msg = self.slack.conversations_replies(channel=cid, ts=item_ts, limit=1)["messages"][0]
        root = msg.get("thread_ts", item_ts)
        if worked == "no":
            self.post(cid, root, f"Noted, that didn't solve it [{f['question_id']}]. What's still off? "
                                 f"Reply here and tag me, and I'll dig further or ask the owner.")

    def remind(self):
        """One reminder per waiting question, to the backup or close lead, after owner_reminder_hours."""
        hours = float((self.ag.cfg.get("slack") or {}).get("owner_reminder_hours", 24))
        reminded = {e["question_id"] for e in self.ag.log.events() if e["event"] == "reminder sent"}
        for q in self.ag.log.waiting():
            last = max((e["at"] for e in self.ag.log.events() if e["question_id"] == q["id"]), default=q["opened_at"])
            age = (datetime.now(timezone.utc) - datetime.strptime(last, "%Y-%m-%d %H:%M:%S").replace(tzinfo=timezone.utc)).total_seconds() / 3600
            if q["id"] in reminded or age < hours:
                continue
            m = re.search(r"/archives/(\w+)/p(\d{10})(\d{6})", q["thread_link"] or "")
            if not m:
                continue
            area = (self.ag.areas().get(q["area"]) or {})
            backup = area.get("backup") or {}
            who = mention(backup) if backup.get("slack_id") else mention(self.ag.cfg.get("close_lead"))
            self.post(m.group(1), f"{m.group(2)}.{m.group(3)}",
                      f"{who} this has been waiting {age:.0f}h on {q['escalated_to'].split('; ')[-1]}: "
                      f"{q['escalation_question']} Can you answer or point me to who can? [{q['id']}]")
            self.ag.log.update(q["id"], "agent", "reminder sent", escalated_to=q["escalated_to"] + f"; reminder {who}")


def main(cfg_path=None):
    from anthropic import Anthropic
    from slack_bolt import App
    from slack_bolt.adapter.socket_mode import SocketModeHandler

    ag = Agent(cfg_path)
    app = App(token=os.environ["SLACK_BOT_TOKEN"])
    bot_id = app.client.auth_test()["user_id"]
    bridge = SlackBridge(ag, app.client, Anthropic(), bot_id)

    @app.event("app_mention")
    def _mention(event):
        bridge.on_question(event["channel"], event["user"], event["ts"], event.get("thread_ts"))

    @app.event("message")
    def _message(event):
        if event.get("subtype") or event.get("bot_id"):
            return
        if event.get("channel_type") == "im":
            bridge.on_question(event["channel"], event["user"], event["ts"], event.get("thread_ts"))
        else:
            bridge.on_thread_message(event["channel"], event["user"], event["ts"], event.get("thread_ts"), event.get("text"))

    @app.event("reaction_added")
    def _reaction(event):
        item = event.get("item") or {}
        if item.get("type") == "message":
            bridge.on_reaction(event["user"], event["reaction"], item["ts"], item["channel"])

    def loop():
        while True:
            time.sleep(1800)
            try:
                bridge.remind()
            except Exception as e:  # noqa: BLE001
                print("reminder check failed:", e)

    threading.Thread(target=loop, daemon=True).start()
    print(f"Close agent connected as <@{bot_id}>. Close month: {ag.close_month()}. Ctrl+C to stop.")
    SocketModeHandler(app, os.environ["SLACK_APP_TOKEN"]).start()
