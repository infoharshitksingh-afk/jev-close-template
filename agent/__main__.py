"""
Close agent: answers questions about the monthly close in Slack, tags owners when it can't,
and logs every question, answer and outcome.

    python -m agent replay      scripted demo on the example close (no Slack, no API key)
    python -m agent chat        talk to it live in the terminal (needs ANTHROPIC_API_KEY)
    python -m agent setup       fill in owners and Slack IDs -> agent/agent.yaml
    python -m agent doctor      check the close, the owners file, Slack and the API key
    python -m agent slack       run the Slack app (needs SLACK_BOT_TOKEN, SLACK_APP_TOKEN, ANTHROPIC_API_KEY)
    python -m agent mcp         serve the tools over MCP for an existing Slack agent (--http for HTTP)
    python -m agent export      write the question log to Excel
"""

import argparse
import os
import re
import sys
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent
HERE = Path(__file__).resolve().parent
ID = re.compile(r"^[UWS][A-Z0-9]{6,}$")


def ask(prompt, default=""):
    v = input(f"{prompt}{f' [{default}]' if default else ''}: ").strip()
    return v or default


def person(label, cur):
    cur = cur or {}
    name = ask(f"    {label} name", cur.get("name", ""))
    while True:
        sid = ask(f"    {label} Slack member or group ID (U.../S..., Enter to leave empty)", cur.get("slack_id", ""))
        if not sid or ID.match(sid):
            return {"name": name, "slack_id": sid}
        print("      That doesn't look like a Slack ID. Profile > ... > Copy member ID gives one starting with U.")


def cmd_setup(a):
    path = Path(a.config)
    cfg = yaml.safe_load((path if path.exists() else HERE / "agent.example.yaml").read_text())
    print(f"Writes {path}. Enter keeps the value in [brackets].\n")
    cfg["company_name"] = ask("Company name", cfg.get("company_name", ""))
    ch = ask("Channels the agent answers in (comma-separated)", ",".join((cfg.get("slack") or {}).get("channels") or []))
    cfg.setdefault("slack", {})["channels"] = [c.strip() if c.strip().startswith("#") else "#" + c.strip()
                                              for c in ch.split(",") if c.strip()]
    print("\nClose lead: signs off the close, gets anything with no owner, approves changes.")
    cfg["close_lead"] = person("Close lead", cfg.get("close_lead"))
    for key, area in cfg["areas"].items():
        print(f"\n{area['name']}  (checks {area.get('checks') or '-'}, assumptions {area.get('assumptions') or '-'})")
        area["owner"] = person("Owner", area.get("owner"))
        if ask("    Add a backup? (y/n)", "y" if (area.get("backup") or {}).get("name") else "n").lower() == "y":
            area["backup"] = person("Backup", area.get("backup"))
    path.write_text("# Written by `python -m agent setup`. Safe to edit by hand. Holds Slack IDs, not secrets.\n"
                    + yaml.safe_dump(cfg, sort_keys=False, allow_unicode=True, width=120))
    print(f"\nSaved {path}. Next: python -m agent doctor")


def cmd_doctor(a):
    from .tools import Agent
    ok = True

    def line(good, msg):
        nonlocal ok
        ok &= bool(good)
        print(("  OK    " if good else "  TODO  ") + msg)

    cfg_path = Path(a.config)
    line(cfg_path.exists(), f"settings file {cfg_path.name}" + ("" if cfg_path.exists() else " missing: run python -m agent setup"))
    ag = Agent(cfg_path if cfg_path.exists() else HERE / "agent.example.yaml")
    try:
        line(True, f"close found: {ag.close_month()} in {ag.out_dir}")
    except FileNotFoundError as e:
        line(False, str(e))
    lead = ag.cfg.get("close_lead") or {}
    line(lead.get("slack_id"), f"close lead: {lead.get('name')} {lead.get('slack_id') or '(no Slack ID)'}")
    ids = {}
    for k, ar in ag.areas().items():
        o = ar.get("owner") or {}
        line(o.get("slack_id"), f"{k:<22} owner {o.get('name')} {o.get('slack_id') or '(no Slack ID: questions go to the close lead)'}")
        for p in (o, ar.get("backup") or {}, lead):
            if p.get("slack_id"):
                ids[p["slack_id"]] = p.get("name")
    if os.environ.get("SLACK_BOT_TOKEN"):
        from slack_sdk import WebClient
        c = WebClient(token=os.environ["SLACK_BOT_TOKEN"])
        try:
            me = c.auth_test()
            line(True, f"Slack token works: bot <@{me['user_id']}> in {me['team']}")
            for sid, name in ids.items():
                if sid.startswith("S"):
                    continue
                try:
                    u = c.users_info(user=sid)["user"]
                    line(not u.get("deleted"), f"{sid} is {u.get('real_name')} (configured as {name})")
                except Exception as e:  # noqa: BLE001
                    line(False, f"{sid} ({name}) not found: {e}")
            chans = {"#" + ch["name"]: ch for ch in c.conversations_list(types="public_channel,private_channel", limit=1000)["channels"]}
            for ch in (ag.cfg.get("slack") or {}).get("channels") or []:
                info = chans.get(ch)
                line(info and info.get("is_member"), f"{ch}: " + ("bot is a member" if info and info.get("is_member")
                                                                 else "invite the bot: /invite @close-agent"))
        except Exception as e:  # noqa: BLE001
            line(False, f"Slack token rejected: {e}")
    else:
        line(False, "SLACK_BOT_TOKEN not set (skip if you use the MCP route)")
    line(os.environ.get("ANTHROPIC_API_KEY"), "ANTHROPIC_API_KEY " + ("set" if os.environ.get("ANTHROPIC_API_KEY") else "not set (needed for chat and slack)"))
    print("\nAll set." if ok else "\nFix the TODO lines, then run doctor again.")


def cmd_replay(a):
    from .simulate import replay
    replay(a.script, a.config if a.config_given else ROOT / "examples" / "agent.example-data.yaml",
           export_to=ROOT / "examples" / "outputs" / "agent" / "Close_Questions_Log.xlsx", show_tools=a.show_tools)


def cmd_chat(a):
    from .simulate import chat
    if not os.environ.get("ANTHROPIC_API_KEY"):
        sys.exit("Set ANTHROPIC_API_KEY first (export ANTHROPIC_API_KEY=...). Never paste it into a file.")
    chat(a.config if Path(a.config).exists() else ROOT / "examples" / "agent.example-data.yaml")


def cmd_slack(a):
    for v in ("SLACK_BOT_TOKEN", "SLACK_APP_TOKEN", "ANTHROPIC_API_KEY"):
        if not os.environ.get(v):
            sys.exit(f"Set {v} first. See agent/ONBOARDING.md.")
    from .slack_app import main
    main(a.config)


def cmd_mcp(a):
    from .mcp_server import main
    main(a.config, http=a.http)


def cmd_export(a):
    from .export import export
    from .tools import Agent
    ag = Agent(a.config)
    print("Wrote", export(ag.log, ag.out_dir / "agent" / "Close_Questions_Log.xlsx"))


def main():
    ap = argparse.ArgumentParser(prog="python -m agent", description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("command", choices=["replay", "chat", "setup", "doctor", "slack", "mcp", "export"])
    ap.add_argument("--config", default=None, help="settings file (default agent/agent.yaml)")
    ap.add_argument("--script", default=str(ROOT / "examples" / "agent_demo.yaml"), help="replay: scripted conversation")
    ap.add_argument("--show-tools", action="store_true", help="replay: print each tool call")
    ap.add_argument("--http", action="store_true", help="mcp: serve over HTTP instead of stdio")
    a = ap.parse_args()
    a.config_given = a.config is not None
    a.config = a.config or str(HERE / "agent.yaml")
    {"replay": cmd_replay, "chat": cmd_chat, "setup": cmd_setup, "doctor": cmd_doctor, "slack": cmd_slack,
     "mcp": cmd_mcp, "export": cmd_export}[a.command](a)


if __name__ == "__main__":
    main()
