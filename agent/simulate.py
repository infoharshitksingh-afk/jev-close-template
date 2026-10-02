"""
Try the agent without Slack.

    python -m agent replay              # scripted demo on the example close; no API key, no Slack
    python -m agent chat                # live: you type as anyone, Claude answers (needs ANTHROPIC_API_KEY)

Both run the real Slack logic (agent/slack_app.py) against a fake Slack that prints to the
terminal, and write a real question log you can export to Excel.
"""

import itertools
import re
import sys
from pathlib import Path
from types import SimpleNamespace

import yaml

from .slack_app import SlackBridge
from .tools import Agent

BOT = "U0CLOSEBOT"


# ---------------------------------------------------------------------- fake Slack
class FakeSlack:
    """Enough of slack_sdk.WebClient for SlackBridge. Prints every post like a Slack thread."""

    def __init__(self, people, channel="#finance-close", echo=True):
        self.people = people  # {slack_id: name}
        self.channel = channel
        self.msgs = {}  # ts -> {ts, thread_ts, user, text}
        self.clock = itertools.count(1)
        self.echo = echo

    def _ts(self):
        return f"1767225600.{next(self.clock):06d}"

    def users_info(self, user):
        return {"user": {"name": user, "profile": {"real_name": self.people.get(user, user)}}}

    def conversations_info(self, channel):
        return {"channel": {"name": self.channel.lstrip("#"), "is_im": False}}

    def chat_getPermalink(self, channel, message_ts):
        return {"permalink": f"https://example.slack.com/archives/C0FINANCE/p{message_ts.replace('.', '')}"}

    def conversations_replies(self, channel, ts, limit=50):
        root = self.msgs[ts].get("thread_ts") or ts
        return {"messages": [m for m in self.msgs.values() if m["ts"] == root or m.get("thread_ts") == root][:limit]
                if ts == root else [self.msgs[ts]]}

    def chat_postMessage(self, channel, thread_ts, text, user=BOT):
        ts = self._ts()
        m = {"ts": ts, "thread_ts": thread_ts, "user": user, "text": text}
        if user == BOT:
            m["bot_id"] = "B0CLOSE"
        self.msgs[ts] = m
        if self.echo:
            who = "close-agent" if user == BOT else self.people.get(user, user)
            show = re.sub(r"<@(\w+)>", lambda x: "@" + self.people.get(x.group(1), x.group(1)), text)
            show = re.sub(r"<!subteam\^(\w+)>", lambda x: "@" + self.people.get(x.group(1), x.group(1)), show)
            pad = "    " if thread_ts and thread_ts != ts else ""
            print(f"{pad}\033[1m{who}\033[0m: " + show.replace("\n", "\n" + pad + "  "))
        return {"ts": ts}

    def user_post(self, user, text, thread_ts=None):
        ts = self._ts()
        self.msgs[ts] = {"ts": ts, "thread_ts": thread_ts or ts, "user": user, "text": text}
        if self.echo:
            pad = "    " if thread_ts else ""
            show = text.replace(f"<@{BOT}>", "@close-agent")
            show = re.sub(r"<@(\w+)>", lambda x: "@" + self.people.get(x.group(1), x.group(1)), show)
            print(f"\n{pad}\033[1m{self.people.get(user, user)}\033[0m: {show}")
        return ts


# ---------------------------------------------------------------------- scripted model
class ScriptedClient:
    """Stands in for anthropic.Anthropic(): returns the next scripted tool call or reply.
    The tools still run for real against the example close and write the real log."""

    def __init__(self):
        self.queue = []
        self.messages = SimpleNamespace(create=self.create)
        self.ids = itertools.count(1)

    def load(self, steps):
        self.queue = list(steps)

    def create(self, **kw):
        step = self.queue.pop(0)
        if "tool" in step:
            block = SimpleNamespace(type="tool_use", id=f"toolu_{next(self.ids)}", name=step["tool"],
                                    input=step.get("input") or {})
        else:
            block = SimpleNamespace(type="text", text=step["reply"].strip())
        return SimpleNamespace(content=[block], stop_reason="tool_use" if "tool" in step else "end_turn")


# ---------------------------------------------------------------------- runners
def people_from(cfg, extra):
    p = {BOT: "close-agent"}
    for a in (cfg.get("areas") or {}).values():
        for role in ("owner", "backup"):
            x = a.get(role) or {}
            if x.get("slack_id"):
                p[x["slack_id"]] = x["name"]
    lead = cfg.get("close_lead") or {}
    if lead.get("slack_id"):
        p[lead["slack_id"]] = lead["name"]
    p.update(extra or {})
    return p


def replay(script_path, cfg_path, export_to=None, echo=True, show_tools=False):
    from .export import export
    script = yaml.safe_load(Path(script_path).read_text())
    cfg = yaml.safe_load(Path(cfg_path).read_text())
    ag = Agent(cfg=cfg)
    if ag.log.path.exists():
        ag.log.path.unlink()
    ag = Agent(cfg=cfg)
    people = people_from(cfg, script.get("people"))
    by_name = {v: k for k, v in people.items()}
    slack = FakeSlack(people, echo=echo)
    llm = ScriptedClient()
    bridge = SlackBridge(ag, slack, llm, BOT)
    threads = {}
    if show_tools:
        import agent.brain as B
        orig = B.respond
        B.respond = lambda *a, **k: orig(*a, **k, on_tool=lambda n, i, o: print(f"      · {n}({', '.join(f'{k}={str(v)[:50]!r}' for k, v in i.items())})"
                                                                         + (f"  -> ERROR {o['error']}" if isinstance(o, dict) and o.get("error") else "")))
    for ev in script["events"]:
        if "note" in ev and echo:
            print(f"\n\033[2m--- {ev['note']} ---\033[0m")
        if "react" in ev:
            r = ev["react"]
            qid = r["question"]
            ts = next(t for t, m in slack.msgs.items() if m["user"] == BOT and f"[{qid}]" in m["text"]
                      and "Did this solve it" in m["text"])
            uid = by_name[r["as"]]
            if echo:
                print(f"    \033[2m{r['as']} reacted :{r['emoji']}: to the follow-up on {qid}\033[0m")
            bridge.on_reaction(uid, r["emoji"], ts, "C0FINANCE")
            continue
        if "say" not in ev:
            continue
        uid = by_name[ev["as"]]
        llm.load(ev["agent"])
        th = threads.get(ev.get("thread"))
        text = ev["say"].replace("@close-agent", f"<@{BOT}>")
        text = re.sub(r"@\{([^}]+)\}", lambda x: f"<@{by_name[x.group(1)]}>", text)
        ts = slack.user_post(uid, text, th)
        if ev.get("thread") and th is None:
            threads[ev["thread"]] = ts
        if f"<@{BOT}>" in text or th is None:
            bridge.on_question("C0FINANCE", uid, ts, th)
        else:
            bridge.on_thread_message("C0FINANCE", uid, ts, th, text)
        if llm.queue:
            raise SystemExit(f"Script step left unused in event: {ev.get('say')[:60]} -> {llm.queue[0]}")
    out = None
    if export_to:
        out = export(ag.log, Path(export_to))
        if echo:
            print(f"\nQuestion log: {out}")
    return ag, slack, out


def chat(cfg_path):
    from anthropic import Anthropic
    from .export import export
    cfg = yaml.safe_load(Path(cfg_path).read_text())
    ag = Agent(cfg=cfg)
    people = people_from(cfg, {"U0YOU": "You"})
    by_name = {v.lower(): k for k, v in people.items()}
    slack = FakeSlack(people)
    bridge = SlackBridge(ag, slack, Anthropic(), BOT)
    me, thread = "U0YOU", None
    print("Live chat with the close agent (fake Slack, real Claude). Commands:\n"
          "  /as <name>       speak as someone else (an owner from agent.yaml, or any name)\n"
          "  /new             start a new thread\n"
          "  /react yes|no    answer the last 'did this solve it?'\n"
          "  /export          write the question log to Excel\n"
          "  /quit\n"
          f"Owners: {', '.join(n for k, n in people.items() if k not in (BOT, 'U0YOU'))}\n")
    while True:
        try:
            line = input(f"[{people.get(me, me)}] > ").strip()
        except (EOFError, KeyboardInterrupt):
            break
        if not line:
            continue
        if line == "/quit":
            break
        if line == "/new":
            thread = None
            continue
        if line.startswith("/as "):
            n = line[4:].strip()
            me = by_name.get(n.lower()) or f"U0{re.sub(r'[^A-Z0-9]', '', n.upper())[:8]}"
            people.setdefault(me, n)
            by_name[n.lower()] = me
            continue
        if line.startswith("/react"):
            emoji = "white_check_mark" if "yes" in line else "x"
            ts = next((t for t, m in reversed(list(slack.msgs.items())) if "Did this solve it" in m["text"]), None)
            if ts:
                f = ag.log.followup(ts)
                bridge.on_reaction(f["asked_user"], emoji, ts, "C0FINANCE")
                print("  (logged)")
            continue
        if line == "/export":
            print("  wrote", export(ag.log, ag.out_dir / "agent" / "Close_Questions_Log.xlsx"))
            continue
        text = line if thread else f"<@{BOT}> {line}"
        text = text.replace("@close-agent", f"<@{BOT}>")
        ts = slack.user_post(me, text, thread)
        if thread is None:
            thread = ts
        bridge.on_question("C0FINANCE", me, ts, None if ts == thread else thread)


if __name__ == "__main__":
    replay(sys.argv[1], sys.argv[2])
