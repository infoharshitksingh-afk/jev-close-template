"""
The agent's loop: Slack thread in, reply out. Claude decides which tools to call; the tools do the
work and write the log. Any client with Anthropic's `messages.create` interface works, which is
how the simulator replays a scripted demo without an API key.
"""

import json
from pathlib import Path

from . import tools as T

RULES = (Path(__file__).parent / "RULES.md").read_text()
MAX_STEPS = 14


def system_prompt(ag):
    lines = []
    for key, a in ag.areas().items():
        person, role = ag.owner_of(key)
        lines.append(f"- `{key}` ({a.get('name')}): checks {a.get('checks') or '-'}, "
                     f"assumptions {a.get('assumptions') or '-'}. Asks: {person.get('name')} [{role}]")
    try:
        month = ag.close_month()
    except FileNotFoundError:
        month = "none yet (tell people to run the close first)"
    return (f"{RULES}\n\n## This deployment\n\nCompany: {ag.cfg.get('company_name', '')}\n"
            f"Close month loaded: {month}\nAreas and owners:\n" + "\n".join(lines))


def transcript(thread):
    """thread: list of {user, text} oldest first. The last item is the message to respond to."""
    lines = [f"[{m['user']}]: {m['text']}" for m in thread[:-1]]
    last = thread[-1]
    head = ("Slack thread so far:\n" + "\n".join(lines) + "\n\n") if lines else ""
    return f"{head}New message from {last['user']}:\n{last['text']}"


def respond(ag, client, thread, ctx, model=None, on_tool=None):
    """Returns (reply_text, ctx). ctx carries asker / channel / thread_link in, and the IDs of
    questions that should get a 'did this work?' follow-up out (ctx['ask_feedback'])."""
    msgs = [{"role": "user", "content": transcript(thread)}]
    model = model or ag.cfg.get("model", "claude-sonnet-5-5")
    for _ in range(MAX_STEPS):
        r = client.messages.create(model=model, max_tokens=2000, system=system_prompt(ag),
                                   tools=T.anthropic_tools(), messages=msgs)
        msgs.append({"role": "assistant", "content": r.content})
        uses = [b for b in r.content if getattr(b, "type", None) == "tool_use"]
        if not uses:
            text = "\n".join(b.text for b in r.content if getattr(b, "type", None) == "text").strip()
            return text, ctx
        results = []
        for u in uses:
            out = T.call(ag, ctx, u.name, u.input)
            if on_tool:
                on_tool(u.name, u.input, out)
            results.append({"type": "tool_result", "tool_use_id": u.id,
                            "content": json.dumps(out, default=str)[:20000]})
        msgs.append({"role": "user", "content": results})
    return "I couldn't finish that within my step limit. I've logged what I found; a person should take a look.", ctx
