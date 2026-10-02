"""
End-to-end tests.

    python tests/run_tests.py

1. Example close: the three planted errors are flagged, and nothing else.
2. Call-level raw logs + accounts from a SQL database (SQLite stands in for Postgres/Snowflake).
3. Only the two required sources: everything optional reports SKIP, no errors.
4. If LibreOffice is installed: the Excel formulas reproduce the Python forecast.
"""

import csv
import json
import os
import shutil
import sqlite3
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
TESTS = ROOT / "tests"
sys.path.insert(0, str(ROOT))
os.chdir(ROOT)


def run(*args):
    r = subprocess.run([sys.executable, "jevclose.py", *args], capture_output=True, text=True)
    if r.returncode != 0:
        print(r.stdout, r.stderr)
        raise SystemExit(f"FAILED: jevclose.py {' '.join(args)}")
    return r.stdout


def checks(path):
    return json.loads(Path(path).read_text())["checks"]


def ok(cond, msg):
    print(("  PASS " if cond else "  FAIL ") + msg)
    if not cond:
        raise SystemExit(1)


print("1. Example close")
for d in ("examples/outputs",):
    shutil.rmtree(d, ignore_errors=True)
run("example")
ex = checks("examples/outputs/close_2027-02.json")
flags = sorted((c["check_no"], c["scope"]) for c in ex if c["status"] == "FLAG")
ok(flags == [(4, "2026-12"), (6, "2027-01"), (8, "openrouter 2027-01")], f"planted errors flagged: {flags}")

print("2. Call-level logs + SQL accounts source")
db = TESTS / "accounts_test.sqlite"
con = sqlite3.connect(db)
con.execute("DROP TABLE IF EXISTS orgs")
con.execute("CREATE TABLE orgs (id TEXT, name TEXT, created_at TEXT, plan TEXT, source TEXT)")
rows = [(r["account_id"], r["display_name"], r["signup_date"], r["segment"], r["channel"])
        for r in csv.DictReader(open("examples/data/raw/accounts.csv"))]
con.executemany("INSERT INTO orgs VALUES (?,?,?,?,?)", rows)
con.commit(); con.close()
os.environ["TEST_DIR"] = str(TESTS)
shutil.rmtree(TESTS / "outputs", ignore_errors=True)
run("validate", "--config", "tests/config.call_sqlite.yaml")
run("run", "--config", "tests/config.call_sqlite.yaml", "--as-of", "2026-12")
cc = {c["check_no"]: c["status"] for c in checks(TESTS / "outputs/close_2026-12.json")}
ok(cc[1] == "PASS", "duplicate request-ID check runs on call-level logs")
ok(cc[2] == "FLAG", "missing-days check flags a two-day sample")
ok(cc[4] == "SKIP", "billing checks skip without a ledger")

print("3. Required sources only")
shutil.rmtree(TESTS / "outputs_min", ignore_errors=True)
run("run", "--config", "tests/config.minimal.yaml")
mc = checks(TESTS / "outputs_min/close_2027-02.json")
ok(sum(c["status"] == "SKIP" for c in mc) == 8, "eight checks skip")
ok(not any(c["status"] == "FLAG" for c in mc), "no false flags")

print("4. Excel formulas match the Python forecast")
office = shutil.which("soffice") or shutil.which("libreoffice")
if not office:
    print("  SKIP LibreOffice not installed (Excel recalculates on open)")
else:
    tmp = TESTS / "outputs" / "parity"
    tmp.mkdir(parents=True, exist_ok=True)
    src = ROOT / "examples/outputs/Close_2027-02.xlsx"
    subprocess.run([office, "--headless", "--convert-to", "xlsx", "--outdir", str(tmp), str(src)], capture_output=True)
    from openpyxl import load_workbook
    wb = load_workbook(tmp / src.name, data_only=True)
    fs = wb["Forecast"]
    tot = next(r for r in range(1, fs.max_row + 1) if fs.cell(r, 1).value == "Total tokens per day (B)")
    py = json.loads(Path("examples/outputs/close_2027-02.json").read_text())["forecast_btok_per_day"]
    months = {1: "2027-03-01", 2: "2027-04-01", 3: "2027-05-01"}
    worst = 0.0
    for j, (s, n) in enumerate([(s, n) for s in ("low", "base", "high") for n in (1, 2, 3)]):
        x = fs.cell(tot, 2 + j).value
        worst = max(worst, abs(x / py[f"{s}|{months[n]}"] - 1))
    ok(worst < 1e-4, f"largest gap between Excel and Python: {worst:.2e}")

print("5. Close agent (scripted Slack conversation, no API key)")
from agent.simulate import replay  # noqa: E402
from agent.tools import Agent, run_sql  # noqa: E402
ag, slack, xlsx = replay("examples/agent_demo.yaml", "examples/agent.example-data.yaml",
                         export_to=TESTS / "outputs" / "agent" / "log.xlsx", echo=False)
log = {r["id"]: r for r in ag.log.all()}
ok(len(log) == 5, "five questions logged")
ok(log["Q-0001"]["escalated_to"] == "" and log["Q-0001"]["worked"] == "yes", "data question answered without tagging anyone")
ok("<@U0DEV>" in log["Q-0002"]["escalated_to"] and "<@U0ANA>" in log["Q-0002"]["escalated_to"]
   and log["Q-0002"]["area"] == "billing", "disagreeing sources tag both owners, area kept")
ok(log["Q-0002"]["change_type"] == "billing_correction" and log["Q-0002"]["status"] == "closed", "owner answer recorded and closed")
ok(log["Q-0003"]["worked"] == "no" and log["Q-0003"]["status"] == "waiting_on_owner"
   and log["Q-0003"]["escalation_reason"] == "contradicts_prior_decision", "answer that didn't work is re-asked")
ok(log["Q-0005"]["related_to"] == "Q-0002" and log["Q-0005"]["escalated_to"] == "", "repeat question answered from the log")
posted = [m["text"] for m in slack.msgs.values() if m["user"] == "U0CLOSEBOT"]
ok(any("<@U0JORDAN>" in t for t in posted), "billing correction routed to the close lead for approval")
ok(xlsx.exists(), "question log exported to Excel")
ag2 = Agent("examples/agent.example-data.yaml")
for bad in ("DROP TABLE checks", "SELECT 1; DELETE FROM checks", "SELECT state FROM src_usage",
            "SELECT * FROM read_csv_auto('/etc/passwd')", "SELECT * FROM raw_usage"):
    ok("error" in run_sql(ag2, {}, bad), f"refused: {bad}")
ok(run_sql(ag2, {}, "SELECT COUNT(*) FROM src_billing_ledger")["rows"][0][0] > 0, "source views still readable")

print("All tests passed.")
