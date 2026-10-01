#!/usr/bin/env python3
"""
Monthly usage close: API logs -> SQL -> Excel board pack.

    python jevclose.py setup       # asks where each data source lives, maps columns, writes config.yaml
    python jevclose.py validate    # loads every source and reports what it found, without running the close
    python jevclose.py run         # runs the close for the last complete month (or --as-of 2027-02)
    python jevclose.py template    # rebuilds the blank workbook template from lib/workbook.py
    python jevclose.py example     # generates synthetic example data and runs the close on it
"""

import argparse
import sys
from pathlib import Path

import duckdb
import yaml

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

from lib import contract as C  # noqa: E402
from lib import sources  # noqa: E402

BANNER = "-" * 72


# ------------------------------------------------------------------ prompts
def ask(prompt, default=None, allow_empty=False):
    suffix = f" [{default}]" if default not in (None, "") else ""
    while True:
        v = input(f"{prompt}{suffix}: ").strip()
        if v:
            return v
        if default is not None:
            return default
        if allow_empty:
            return ""


def ask_choice(prompt, options, default=None):
    opts = "/".join(options)
    while True:
        v = ask(f"{prompt} ({opts})", default).lower()
        if v in options:
            return v
        print(f"  Please answer one of: {opts}")


def pick_column(field, cols, suggested, required):
    tag = "required" if required else "optional"
    print(f"    {field} ({tag})" + (f" -> suggested: {suggested}" if suggested else " -> no match found"))
    if suggested:
        v = ask("      Press Enter to accept, type another column name, or '-' for none", suggested)
    else:
        v = ask("      Column name (or '-' for none)", "-" if not required else None)
    if v == "-":
        return None
    while v not in cols:
        print(f"      '{v}' isn't a column. Columns: {', '.join(cols)}")
        v = ask("      Column name (or '-' for none)")
        if v == "-":
            return None
    return v


# ------------------------------------------------------------------ setup
def setup_source(con, name, spec, existing):
    print(f"\n{BANNER}\n{name.upper()}  ({'required' if spec['required'] else 'optional'})\n{spec['what']}")
    choices = ["file", "sql"] + ([] if spec["required"] else ["skip"])
    if existing and not existing.get("skip"):
        default_kind = existing.get("type", "file")
    else:
        default_kind = "file" if spec["required"] else "skip"
    kind = ask_choice("Where is it? file = path or glob to CSV/JSON/Parquet; sql = a database query", choices, default_kind)
    if kind == "skip":
        return {"skip": True}
    s = {"type": kind}
    while True:
        try:
            if kind == "file":
                s["path"] = ask("  Path or glob (e.g. /data/logs/2026-*.jsonl.gz)", (existing or {}).get("path"))
            else:
                print("  Use a SQLAlchemy URL. Keep secrets out of this file: write ${MY_ENV_VAR} and set the variable.")
                s["url"] = ask("  Database URL", (existing or {}).get("url"))
                s["query"] = ask("  SELECT query", (existing or {}).get("query"))
            cols = sources.raw_relation(con, name, s, ROOT)
            n = con.execute(f"SELECT COUNT(*) FROM raw_{name}").fetchone()[0]
            print(f"  Found {n:,} rows and {len(cols)} columns: {', '.join(cols)}")
            break
        except Exception as e:  # noqa: BLE001
            print(f"  Couldn't read that: {e}")
            if ask_choice("  Try again?", ["y", "n"], "y") == "n":
                return {"skip": True} if not spec["required"] else sys.exit("A required source is missing.")
    prev_cols = (existing or {}).get("columns") or {}
    mapping = {}
    if name == "usage":
        has_ts = (prev_cols.get("ts") in cols) or C.auto_match("ts", cols)
        grain = ask_choice("  Is this one row per API call or one row per day?", ["call", "daily"], "call" if has_ts else "daily")
        s["grain"] = grain
    req, opt = C.fields_for(name, s.get("grain"))
    print("  Map your columns to the standard fields:")
    for f in req + opt:
        sug = prev_cols.get(f) if prev_cols.get(f) in cols else C.auto_match(f, cols)
        col = pick_column(f, cols, sug, f in req)
        if col is None and f in req:
            print("      This field is required; the source can't be used without it.")
            col = pick_column(f, cols, None, True)
        if col:
            mapping[f] = col
    s["columns"] = mapping
    sources.standard_view(con, name, s, ROOT)
    nulls = []
    for f in req:
        bad = con.execute(f"SELECT COUNT(*) FILTER (WHERE {f} IS NULL), COUNT(*) FROM src_{name}").fetchone()
        if bad[0]:
            nulls.append(f"{f}: {bad[0]:,} of {bad[1]:,} rows empty or unreadable")
    if nulls:
        print("  Warning: " + "; ".join(nulls))
    return s


def cmd_setup(args):
    cfg_path = Path(args.config)
    cfg = yaml.safe_load(cfg_path.read_text()) if cfg_path.exists() else yaml.safe_load((ROOT / "config.example.yaml").read_text())
    print(f"{BANNER}\nSetup writes {cfg_path}. Press Enter to keep the value in [brackets].\n{BANNER}")
    cfg["company_name"] = ask("Company name", cfg.get("company_name", "TypeSafe AI"))
    cfg["reporting_timezone"] = ask("Reporting time zone (IANA name)", cfg.get("reporting_timezone", "America/Los_Angeles"))
    con = duckdb.connect()
    con.execute("SET TimeZone = 'UTC'")
    cfg.setdefault("sources", {})
    for name, spec in C.SOURCES.items():
        cfg["sources"][name] = setup_source(con, name, spec, cfg["sources"].get(name))
    b = cfg.setdefault("billing", {})
    print(f"\n{BANNER}\nBILLING RULES (see LOGIC_REVIEW.md for what each one changes)")
    price = ask("Current list price, $ per million input tokens", str((b.get("prices") or [{}])[-1].get("price_per_mtok", 0.042)))
    if not b.get("prices"):
        b["prices"] = [{"effective_from": "2000-01-01", "price_per_mtok": float(price)}]
    else:
        b["prices"][-1]["price_per_mtok"] = float(price)
    b["free_credit_usd"] = float(ask("Free credit for new accounts, $", str(b.get("free_credit_usd", 5.0))))
    st = ask("HTTP statuses that are billed (comma-separated)", ",".join(map(str, b.get("billable_statuses", [200]))))
    b["billable_statuses"] = [int(x) for x in st.split(",") if x.strip()]
    cfg["gateway_revenue_treatment"] = ask_choice("Gateway revenue: net (fee reduces revenue) or gross (fee is a cost)",
                                                  ["net", "gross"], cfg.get("gateway_revenue_treatment", "net"))
    print("Gateways and fees are listed under 'gateways:' in the config; edit that block directly.")
    header = ("# Written by `python jevclose.py setup`. Safe to edit by hand.\n"
              "# Never put passwords here: use ${ENV_VAR} in database URLs.\n")
    cfg_path.write_text(header + yaml.safe_dump(cfg, sort_keys=False, default_flow_style=False))
    print(f"\nSaved {cfg_path}. Next: python jevclose.py validate, then python jevclose.py run")


# ------------------------------------------------------------------ validate
def load_cfg(path):
    p = Path(path)
    if not p.exists():
        sys.exit(f"No {p}. Run: python jevclose.py setup  (or copy config.example.yaml)")
    return yaml.safe_load(p.read_text())


def cmd_validate(args):
    cfg = load_cfg(args.config)
    con = duckdb.connect()
    status, grain = sources.load_all(con, cfg, ROOT)
    print(f"Usage grain: {grain}")
    for name, st in status.items():
        n = con.execute(f"SELECT COUNT(*) FROM src_{name}").fetchone()[0]
        line = f"  {name:<20} {st:<7} {n:>12,} rows"
        req, _ = C.fields_for(name, grain if name == "usage" else None)
        if n and name != "label_map":  # blank use_case in label_map is allowed: it marks labels that say nothing
            nulls = [f for f in req if con.execute(f"SELECT COUNT(*) FROM src_{name} WHERE {f} IS NULL").fetchone()[0]]
            if nulls:
                line += f"   WARNING empty/unreadable values in: {', '.join(nulls)}"
        print(line)
    if grain == "call":
        tz = cfg.get("reporting_timezone", "UTC")
        r = con.execute(f"SELECT MIN(CAST(timezone('{tz}', ts) AS DATE)), MAX(CAST(timezone('{tz}', ts) AS DATE)) FROM src_usage").fetchone()
    else:
        r = con.execute("SELECT MIN(date), MAX(date) FROM src_usage").fetchone()
    print(f"Usage covers {r[0]} to {r[1]}")
    sts = con.execute("SELECT status, COUNT(*) FROM src_usage GROUP BY 1 ORDER BY 1").fetchall()
    billable = set((cfg.get("billing") or {}).get("billable_statuses", [200]))
    print("Statuses: " + ", ".join(f"{s} ({'billed' if s in billable else 'not billed'})" for s, _ in sts))
    unk = con.execute("SELECT COUNT(DISTINCT account_id) FROM src_usage WHERE account_id NOT IN (SELECT account_id FROM src_accounts)").fetchone()[0]
    if unk:
        print(f"WARNING: {unk:,} account IDs in usage are missing from accounts (they'll show as 'unmatched').")
    chans = [c for (c,) in con.execute("SELECT DISTINCT lower(channel) FROM src_usage WHERE channel IS NOT NULL UNION "
                                       "SELECT DISTINCT lower(channel) FROM src_accounts WHERE channel IS NOT NULL").fetchall()]
    known = {k.lower() for k in (cfg.get("gateways") or {})} | {"direct"}
    other = [c for c in chans if c not in known]
    if other:
        print(f"Note: channels not in config 'gateways' are treated as direct sales: {', '.join(other)}")
    print("Validation finished.")


# ------------------------------------------------------------------ run / template / example
def cmd_run(args):
    from lib.run import close
    cfg = load_cfg(args.config)
    close(cfg, ROOT, as_of=args.as_of, out_dir=args.out)


def cmd_template(args):
    from lib.workbook import build
    out = ROOT / "template" / "Jev_Close_Template.xlsx"
    build(out)
    print(f"Wrote {out}")


def cmd_example(args):
    import subprocess
    subprocess.run([sys.executable, str(ROOT / "examples" / "generate_example_data.py")], check=True)
    from lib.run import close
    cfg = load_cfg(ROOT / "examples" / "config.example-data.yaml")
    close(cfg, ROOT, as_of=args.as_of, out_dir=args.out or "examples/outputs")


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("command", choices=["setup", "validate", "run", "template", "example"])
    ap.add_argument("--config", default=str(ROOT / "config.yaml"))
    ap.add_argument("--as-of", help="month to close, YYYY-MM (default: last complete month in the data)")
    ap.add_argument("--out", help="output folder (default: outputs/)")
    args = ap.parse_args()
    {"setup": cmd_setup, "validate": cmd_validate, "run": cmd_run, "template": cmd_template, "example": cmd_example}[args.command](args)


if __name__ == "__main__":
    main()
