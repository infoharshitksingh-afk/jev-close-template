"""
Generate a SYNTHETIC example dataset for the close template (shaped like TypeSafe's Jev API).

    python examples/generate_example_data.py     # writes examples/data/raw/

Nothing here is real TypeSafe data. The shapes come from TypeSafe's public docs
(docs.typesafe.ai): the request/response schema (state + typed questions -> answers +
usage.input_tokens / usage.output_tokens), the x-typesafe-request-id header, model
aliases (jev-latest -> jev-1.13.0), list price ($0.042 per M input tokens, output free),
per-account rate limits (429), overload errors (529), and the industry use-case map.
Volumes, customers, contracts and errors are invented for a finance-pipeline demo.

Outputs (data/raw/):
  usage_events.jsonl.gz      daily usage buckets per account x project label x status (the metering export)
  api_calls_sample_*.jsonl   every call for one small account on one day (raw call-log format)
  accounts.csv               console / account data
  contracts.csv              CRM: signed enterprise contracts
  pipeline.csv               CRM: open enterprise deals
  billing_ledger.csv         billing system: monthly invoices for direct + enterprise accounts
  gateway_statements.csv     monthly statements from Vercel, OpenRouter, DigitalOcean
  platform_hourly_last7d.csv platform-wide hourly tokens for the last 7 days (peak vs base load)
  label_map.csv              finance-maintained map from customer project labels to use cases

Planted issues (so the reconciliation checks have something to find):
  1. Dec 10-20 overload incident: the billing meter counted tokens from failed 529 calls.
  2. Jan invoice for one enterprise customer used list price instead of its contract price.
  3. OpenRouter's January statement reports ~2.6% fewer tokens than TypeSafe's logs.
  4. One enterprise customer uses less than its monthly commitment (a true-up, not an error).
"""

import csv
import gzip
import json
import math
import uuid
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import numpy as np

SEED = 7
rng = np.random.default_rng(SEED)
OUT = Path(__file__).parent / "data" / "raw"  # examples/data/raw
OUT.mkdir(parents=True, exist_ok=True)

LIST_PRICE = 0.042  # $ per M input tokens (docs.typesafe.ai/models)
FREE_CREDIT_USD = 5.0
FREE_CREDIT_TOKENS = FREE_CREDIT_USD / LIST_PRICE * 1e6  # ~119.05M tokens
START, END = date(2026, 9, 15), date(2027, 2, 28)
DAYS = [START + timedelta(d) for d in range((END - START).days + 1)]
N_DAYS = len(DAYS)
INCIDENT = (date(2026, 12, 10), date(2026, 12, 20))
SAMPLE_DAY = date(2026, 12, 15)

# Use cases from docs.typesafe.ai/concepts/use-case-map, with a typical call profile.
# tok = input tokens per call, q = questions per call, card = largest Choice cardinality,
# off = share of calls 00:00-06:00 PT, biz = weekday-heavy, w = self-serve popularity.
USE_CASES = {
    "ticket_routing":     dict(label="Support ticket routing",           tok=900,  q=3,  card=10, off=0.08, biz=True,  w=0.12),
    "llm_guardrails":     dict(label="LLM guardrails",                   tok=1500, q=6,  card=5,  off=0.22, biz=False, w=0.17),
    "model_routing":      dict(label="Model routing",                    tok=650,  q=2,  card=6,  off=0.22, biz=False, w=0.14),
    "rag_rerank":         dict(label="RAG re-ranking and search",        tok=2600, q=1,  card=2,  off=0.20, biz=False, w=0.14),
    "fraud_triage":       dict(label="Fraud and AML alert triage",       tok=1800, q=4,  card=8,  off=0.30, biz=False, w=0.05),
    "moderation":         dict(label="Content moderation",               tok=600,  q=4,  card=7,  off=0.25, biz=False, w=0.10),
    "data_enrichment":    dict(label="Bulk data enrichment",             tok=1200, q=8,  card=20, off=0.75, biz=False, w=0.08),
    "doc_classification": dict(label="Document and contract classification", tok=9000, q=10, card=75, off=0.35, biz=True, w=0.05),
    "lead_scoring":       dict(label="Lead scoring",                     tok=500,  q=3,  card=4,  off=0.10, biz=True,  w=0.07),
    "ci_lint":            dict(label="Code and writing lint in CI",      tok=3200, q=6,  card=4,  off=0.05, biz=True,  w=0.08),
}
UC_KEYS = list(USE_CASES)
UC_W = np.array([USE_CASES[k]["w"] for k in UC_KEYS]); UC_W /= UC_W.sum()

# Free-text project labels customers attach (hypothetical optional field; Jev's public API
# has no tag field today). Finance maintains label_map.csv to map them.
LABELS = {
    "ticket_routing": ["support-router", "ticket-classifier", "helpdesk-triage"],
    "llm_guardrails": ["guardrails-prod", "safety-filter", "llm-guard"],
    "model_routing": ["llm-router", "model-router", "router-v2"],
    "rag_rerank": ["rerank-v2", "search-rerank", "rag-context"],
    "fraud_triage": ["fraud-scoring", "aml-triage", "risk-alerts"],
    "moderation": ["moderation", "content-filter", "chat-mod"],
    "data_enrichment": ["enrichment-nightly", "feature-extract", "catalog-normalize"],
    "doc_classification": ["doc-classifier", "contract-review", "filings-classify"],
    "lead_scoring": ["lead-score", "inbound-qual", "icp-match"],
    "ci_lint": ["ci-lint", "pr-checks", "docs-lint"],
}
UNMAPPED_LABELS = ["prod", "test-env", "default"]  # labels that tell finance nothing

CHANNELS = ["direct", "vercel", "openrouter", "digitalocean"]
CH_P = [0.45, 0.32, 0.18, 0.05]
CONVERT_P = {"direct": 0.16, "vercel": 0.12, "openrouter": 0.14, "digitalocean": 0.10}
SIGNUPS = {(2026, 9): 260, (2026, 10): 380, (2026, 11): 420, (2026, 12): 400, (2027, 1): 470, (2027, 2): 520}
GATEWAY_FEE = {"vercel": 0.05, "openrouter": 0.05, "digitalocean": 0.08}

ENTERPRISE = [
    # name, industry, start, monthly commit $, discount, usage vs commit, use-case mix
    ("Quanta Assist", "AI app builder", date(2026, 10, 1), 30000, 0.15, 1.5, {"llm_guardrails": 0.6, "model_routing": 0.4}),
    ("Ledgerline Payments", "Fintech", date(2026, 10, 15), 40000, 0.20, 1.2, {"fraud_triage": 0.8, "doc_classification": 0.2}),
    ("Northwind Insurance", "Insurance", date(2026, 11, 1), 20000, 0.15, 0.6, {"doc_classification": 0.6, "ticket_routing": 0.4}),
    ("Halcyon Games", "Gaming", date(2026, 11, 15), 15000, 0.10, 1.1, {"moderation": 1.0}),
    ("Cobalt Ads", "Ad tech", date(2026, 12, 1), 25000, 0.15, 1.3, {"data_enrichment": 0.7, "moderation": 0.3}),
    ("Meridian Legal", "Legal", date(2026, 12, 15), 12500, 0.20, 0.9, {"doc_classification": 0.7, "rag_rerank": 0.3}),
    ("Sable Research", "Research data", date(2027, 1, 1), 17500, 0.15, 1.0, {"data_enrichment": 1.0}),
    ("Brightpath Logistics", "Logistics", date(2027, 2, 1), 10000, 0.10, 1.0, {"ticket_routing": 0.5, "lead_scoring": 0.5}),
]
PIPELINE = [
    ("D-101", "Vantage Health Claims", "Contract sent", 0.70, 22500, "2027-03-15", "doc_classification"),
    ("D-102", "Orbit Marketplace", "Negotiation", 0.50, 15000, "2027-04-01", "moderation; data_enrichment"),
    ("D-103", "Helix Bank", "Technical evaluation", 0.25, 45000, "2027-05-01", "fraud_triage"),
    ("D-104", "Atlas Support Cloud", "Technical evaluation", 0.30, 12500, "2027-04-15", "ticket_routing"),
    ("D-105", "Kite Robotics", "Discovery", 0.10, 7500, "2027-05-15", "model_routing"),
]

day_index = {d: i for i, d in enumerate(DAYS)}
weekend = np.array([d.weekday() >= 5 for d in DAYS])
in_incident = np.array([INCIDENT[0] <= d <= INCIDENT[1] for d in DAYS])


def month_key(d):
    return f"{d.year}-{d.month:02d}"


def profile(uc):
    """Per-account call profile: the use case's typical shape plus account-level noise."""
    p = USE_CASES[uc]
    return dict(
        tok=p["tok"] * float(rng.lognormal(0, 0.15)),
        q=max(1.0, p["q"] + float(rng.normal(0, 0.4))),
        card=max(2, int(round(p["card"] * float(rng.lognormal(0, 0.1))))),
        off=float(np.clip(p["off"] + rng.normal(0, 0.06), 0.0, 0.95)),
        biz=p["biz"],
    )


def day_shape(biz):
    shape = np.ones(N_DAYS)
    if biz:
        shape[weekend] = 0.35
    return shape * rng.lognormal(0, 0.08, N_DAYS)


# ------------------------------------------------------------------ accounts
accounts, streams = [], []  # stream = (account_id, channel, use_case, label, profile, daily tokens array)
acct_n = 0


def new_id():
    global acct_n
    acct_n += 1
    return f"acct_{acct_n:05d}"


for (y, m), n in SIGNUPS.items():
    first = date(y, m, 15) if (y, m) == (2026, 9) else date(y, m, 1)
    last = (date(y + (m == 12), m % 12 + 1, 1) - timedelta(1))
    span = (last - first).days + 1
    for _ in range(n):
        signup = first + timedelta(int(rng.integers(0, span)))
        ch = str(rng.choice(CHANNELS, p=CH_P))
        aid = "gw_openrouter" if ch == "openrouter" else new_id()
        converts = rng.random() < CONVERT_P[ch]
        n_uc = 1 if rng.random() < 0.75 else 2
        ucs = list(rng.choice(UC_KEYS, size=n_uc, replace=False, p=UC_W))
        tagged = rng.random() < 0.35
        s0 = day_index[signup]
        daily_by_uc = {}
        if not converts:
            total = min(float(rng.lognormal(math.log(20e6), 1.0)), FREE_CREDIT_TOKENS * 0.98)
            active = int(rng.integers(1, 11))
            dd = np.zeros(N_DAYS)
            idx = np.arange(s0, min(s0 + active, N_DAYS))
            w = rng.random(len(idx)); dd[idx] = total * w / w.sum()
            daily_by_uc[ucs[0]] = dd
        else:
            base = float(rng.lognormal(math.log(80e6), 1.4))
            start_paid = s0 + int(rng.integers(3, 21))
            ramp_days = int(rng.integers(30, 61))
            churn_after = int(rng.geometric(0.03 / 30))
            t = np.arange(N_DAYS) - start_paid
            ramp = 0.15 + 0.85 / (1 + np.exp(-(t - ramp_days / 2) / (ramp_days / 8)))
            drift = (1.03) ** (np.clip(t, 0, None) / 30)
            dd = base * ramp * drift
            dd[np.arange(N_DAYS) < s0] = 0
            trial = (np.arange(N_DAYS) >= s0) & (np.arange(N_DAYS) < start_paid)
            dd[trial] = base * 0.05
            dd[np.arange(N_DAYS) > start_paid + churn_after] = 0
            split = rng.dirichlet(np.ones(n_uc)) if n_uc > 1 else np.array([1.0])
            for uc, s in zip(ucs, split):
                daily_by_uc[uc] = dd * s
        if aid != "gw_openrouter":
            accounts.append(dict(account_id=aid, display_name="Self-serve account", segment="self_serve",
                                 channel=ch, signup_date=signup.isoformat(),
                                 payment_method_added=bool(converts),
                                 free_credit_usd=FREE_CREDIT_USD if ch == "direct" else 0.0, industry=""))
        for uc, dd in daily_by_uc.items():
            dd = dd * day_shape(USE_CASES[uc]["biz"])
            label = (str(rng.choice(LABELS[uc])) if rng.random() > 0.12 else str(rng.choice(UNMAPPED_LABELS))) if tagged else None
            streams.append((aid, ch, uc, label, profile(uc), dd))

accounts.append(dict(account_id="gw_openrouter", display_name="OpenRouter (aggregate, end customers not passed)",
                     segment="gateway_aggregate", channel="openrouter", signup_date=START.isoformat(),
                     payment_method_added=True, free_credit_usd=0.0, industry=""))

contracts = []
for i, (name, ind, start, commit, disc, factor, mix) in enumerate(ENTERPRISE, 1):
    aid = new_id()
    price = LIST_PRICE * (1 - disc)
    target = commit * factor / price * 1e6 / 30.4  # tokens per day at full ramp
    t = np.arange(N_DAYS) - day_index[start]
    ramp = np.clip(0.1 + 0.9 * t / 60, 0, 1.0); ramp[t < 0] = 0
    accounts.append(dict(account_id=aid, display_name=name, segment="enterprise", channel="direct",
                         signup_date=(start - timedelta(21)).isoformat(), payment_method_added=True,
                         free_credit_usd=0.0, industry=ind))
    contracts.append(dict(contract_id=f"C-{200 + i}", account_id=aid, customer=name, start_date=start.isoformat(),
                          term_months=12, monthly_commit_usd=commit, discount_pct=disc,
                          unit_price_per_mtok=round(price, 5), billing="monthly in arrears",
                          sla_p95_latency_ms=500))
    for uc, share in mix.items():
        dd = target * share * ramp * day_shape(USE_CASES[uc]["biz"])
        label = str(rng.choice(LABELS[uc])) if rng.random() < 0.8 else None
        streams.append((aid, "direct", uc, label, profile(uc), dd))

acct_meta = {a["account_id"]: a for a in accounts}

# ------------------------------------------------------------------ usage events
events = []
truth = []  # hidden true use case per stream, for the demo's own sanity checks only
sample_candidates = []
for sid, (aid, ch, uc, label, prof, dd) in enumerate(streams):
    for i in np.nonzero(dd > 0)[0]:
        tok = float(dd[i])
        calls_ok = int(max(1, round(tok / prof["tok"])))
        tokens_ok = int(round(calls_ok * prof["tok"] * float(rng.lognormal(0, 0.03))))
        r529 = 0.06 if in_incident[i] else 0.002
        n429 = int(rng.poisson(calls_ok * (0.004 if calls_ok > 20000 else 0.0005)))
        n529 = int(rng.poisson(calls_ok * r529))
        d = DAYS[i]
        common = dict(date=d.isoformat(), account_id=aid, channel=ch, model_requested="jev-latest",
                      model_served="jev-1.13.0", project_label=label)
        meta = dict(avg_questions_per_call=round(prof["q"] * float(rng.lognormal(0, 0.02)), 2),
                    max_choice_cardinality=prof["card"],
                    offhours_call_share=round(float(np.clip(prof["off"] + rng.normal(0, 0.02), 0, 1)), 3))
        lat50 = int(150 + prof["tok"] / 60 + rng.normal(0, 10))
        events.append({**common, "status": 200, "calls": calls_ok, "input_tokens": tokens_ok, "output_tokens": 0,
                       **meta, "p50_latency_ms": lat50, "p95_latency_ms": int(lat50 * 2.1 + (120 if in_incident[i] else 0))})
        truth.append((aid, d.isoformat(), label, uc))
        for status, n in ((429, n429), (529, n529)):
            if n:
                events.append({**common, "status": status, "calls": n,
                               "input_tokens": int(round(n * prof["tok"])), "output_tokens": 0, **meta,
                               "p50_latency_ms": 5 if status == 429 else 30000, "p95_latency_ms": 9 if status == 429 else 30000})
        if (d == SAMPLE_DAY and ch == "direct" and acct_meta[aid]["segment"] == "self_serve"
                and 8000 <= calls_ok <= 25000):
            sample_candidates.append((aid, uc, label, prof, calls_ok))

# ------------------------------------------------------------------ raw call-log sample
# Every call for one small account on the incident day, then overwrite that account-day's
# usage events with sums of the raw calls so the two tie exactly.
aid, uc, label, prof, n_ok = sample_candidates[0]
hour_w = np.array([0.6 if h < 6 else (1.6 if 14 <= h <= 23 else 1.0) for h in range(24)])  # UTC hours
calls = []
n_429 = int(rng.poisson(n_ok * 0.0005)); n_529 = int(rng.poisson(n_ok * 0.06))
statuses = [200] * n_ok + [429] * n_429 + [529] * n_529
rng.shuffle(statuses)
qtypes_pool = ["choice", "noul", "score"]
for status in statuses:
    h = int(rng.choice(24, p=hour_w / hour_w.sum()))
    ts = datetime(SAMPLE_DAY.year, SAMPLE_DAY.month, SAMPLE_DAY.day, h, int(rng.integers(60)), int(rng.integers(60)),
                  int(rng.integers(1_000_000)), tzinfo=timezone.utc)
    nq = max(1, int(round(prof["q"] + rng.normal(0, 0.5))))
    tok = max(80, int(rng.lognormal(math.log(prof["tok"]), 0.25)))
    calls.append({
        "ts": ts.isoformat(timespec="milliseconds").replace("+00:00", "Z"),
        "request_id": f"req_{uuid.UUID(int=int(rng.integers(2**63)) << 64 | int(rng.integers(2**63))).hex[:20]}",
        "account_id": aid, "api_key_id": f"key_{aid[-5:]}a", "channel": "direct",
        "model_requested": "jev-latest", "model_served": "jev-1.13.0", "project_label": label,
        "region": "us-west", "status": status,
        "input_tokens": tok if status == 200 else 0,
        "attempted_input_tokens": tok,
        "output_tokens": 0, "questions": nq,
        "question_types": sorted(set(rng.choice(qtypes_pool, size=nq))),
        "max_choice_cardinality": prof["card"],
        "latency_ms": int(rng.lognormal(math.log(150 + prof["tok"] / 60), 0.35)) if status == 200 else (4 if status == 429 else 30000),
    })
calls.sort(key=lambda c: c["ts"])
sample_path = OUT / f"api_calls_sample_{aid}_{SAMPLE_DAY.isoformat()}.jsonl"
with open(sample_path, "w") as f:
    for c in calls:
        f.write(json.dumps(c) + "\n")
events = [e for e in events if not (e["account_id"] == aid and e["date"] == SAMPLE_DAY.isoformat())]
ok = [c for c in calls if c["status"] == 200]
off = sum(1 for c in ok if int(c["ts"][11:13]) in range(7, 13)) / len(ok)  # 00:00-06:00 PT = 07:00-13:00 UTC (PST)
base_row = dict(date=SAMPLE_DAY.isoformat(), account_id=aid, channel="direct", model_requested="jev-latest",
                model_served="jev-1.13.0", project_label=label,
                avg_questions_per_call=round(sum(c["questions"] for c in ok) / len(ok), 2),
                max_choice_cardinality=prof["card"], offhours_call_share=round(off, 3))
for status in (200, 429, 529):
    sub = [c for c in calls if c["status"] == status]
    if sub:
        lats = sorted(c["latency_ms"] for c in sub)
        events.append({**base_row, "status": status, "calls": len(sub),
                       "input_tokens": sum(c["attempted_input_tokens"] for c in sub), "output_tokens": 0,
                       "p50_latency_ms": lats[len(lats) // 2], "p95_latency_ms": lats[int(len(lats) * 0.95)]})
# keep column order identical across rows
cols = ["date", "account_id", "channel", "model_requested", "model_served", "project_label", "status", "calls",
        "input_tokens", "output_tokens", "avg_questions_per_call", "max_choice_cardinality", "offhours_call_share",
        "p50_latency_ms", "p95_latency_ms"]
events.sort(key=lambda e: (e["date"], e["account_id"], e["project_label"] or "", e["status"]))
with gzip.open(OUT / "usage_events.jsonl.gz", "wt") as f:
    for e in events:
        f.write(json.dumps({k: e[k] for k in cols}) + "\n")


def write_csv(name, rows, fields=None):
    fields = fields or list(rows[0].keys())
    with open(OUT / name, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=fields)
        w.writeheader(); w.writerows(rows)


write_csv("accounts.csv", accounts)
write_csv("contracts.csv", contracts)
write_csv("pipeline.csv", [dict(deal_id=d, customer=c, stage=s, probability=p, expected_monthly_commit_usd=v,
                                expected_start=st, expected_discount_pct=0.15, use_cases=u)
                           for d, c, s, p, v, st, u in PIPELINE])
lm = [dict(project_label=l, use_case=uc) for uc, ls in LABELS.items() for l in ls]
lm += [dict(project_label=l, use_case="") for l in UNMAPPED_LABELS]
write_csv("label_map.csv", lm)

# ------------------------------------------------------------------ billing ledger (direct + enterprise)
contract_by_acct = {c["account_id"]: c for c in contracts}
months = sorted({month_key(d) for d in DAYS})
ok_tok, bad529 = {}, {}
for e in events:
    if e["channel"] != "direct":
        continue
    k = (e["account_id"], e["date"][:7])
    if e["status"] == 200:
        ok_tok[k] = ok_tok.get(k, 0) + e["input_tokens"]
    elif e["status"] == 529 and INCIDENT[0].isoformat() <= e["date"] <= INCIDENT[1].isoformat():
        bad529[k] = bad529.get(k, 0) + e["input_tokens"]

ledger, free_left = [], {}
ent_mispriced = contracts[5]["account_id"]  # Meridian Legal
for mk in months:
    for a in accounts:
        if a["channel"] != "direct":
            continue
        aid = a["account_id"]; k = (aid, mk)
        if k not in ok_tok and aid not in contract_by_acct:
            continue
        metered = ok_tok.get(k, 0) + bad529.get(k, 0)  # planted issue 1: billing meter counted 529s
        c = contract_by_acct.get(aid)
        if c and mk < c["start_date"][:7]:
            continue
        free_left.setdefault(aid, FREE_CREDIT_TOKENS if a["free_credit_usd"] else 0.0)
        free_used = min(free_left[aid], metered); free_left[aid] -= free_used
        paid = metered - free_used
        price = c["unit_price_per_mtok"] if c else LIST_PRICE
        if aid == ent_mispriced and mk == "2027-01":
            price = LIST_PRICE  # planted issue 2: contract discount not applied
        usage = round(paid / 1e6 * price, 2)
        commit = 0.0
        if c:
            commit = float(c["monthly_commit_usd"])
            if mk == c["start_date"][:7]:
                sd = date.fromisoformat(c["start_date"])
                dim = (date(sd.year + (sd.month == 12), sd.month % 12 + 1, 1) - timedelta(1)).day
                commit = round(commit * (dim - sd.day + 1) / dim, 2)  # prorated first month
        true_up = round(max(0.0, commit - usage), 2)
        ledger.append(dict(month=mk, account_id=aid, invoice_id=f"INV-{mk.replace('-', '')}-{aid[-5:]}",
                           tokens_billed=int(metered), free_credit_tokens=int(free_used), paid_tokens=int(paid),
                           unit_price_per_mtok=price, usage_charge_usd=usage, commitment_usd=commit,
                           true_up_usd=true_up, invoice_total_usd=round(usage + true_up, 2),
                           status="paid" if mk < "2027-02" else "open"))
write_csv("billing_ledger.csv", ledger)

# ------------------------------------------------------------------ gateway statements
gw = {}
for e in events:
    if e["channel"] in GATEWAY_FEE and e["status"] == 200:
        k = (e["channel"], e["date"][:7]); gw[k] = gw.get(k, 0) + e["input_tokens"]
stmts = []
for (g, mk), tok in sorted(gw.items()):
    reported = tok * (0.974 if (g == "openrouter" and mk == "2027-01") else 1.0)  # planted issue 3
    reported = int(round(reported, -6))  # statements report to the nearest million tokens
    gross = round(reported / 1e6 * LIST_PRICE, 2); fee = round(gross * GATEWAY_FEE[g], 2)
    stmts.append(dict(gateway=g, month=mk, tokens_reported=reported, list_price_per_mtok=LIST_PRICE,
                      gross_usd=gross, fee_pct=GATEWAY_FEE[g], fee_usd=fee, net_remitted_usd=round(gross - fee, 2)))
write_csv("gateway_statements.csv", stmts)

# ------------------------------------------------------------------ hourly platform profile, last 7 days
# Spread each stream's daily tokens over hours using its pattern (Pacific time).
last7 = DAYS[-7:]
hourly = np.zeros((7, 24))
for aid, ch, uc, label, prof, dd in streams:
    p = USE_CASES[uc]
    for j, d in enumerate(last7):
        tok = dd[day_index[d]]
        if tok <= 0:
            continue
        hp = np.array([1.0] * 24)
        if p["biz"]:
            hp = np.array([0.25 if (h < 7 or h > 19) else 1.6 for h in range(24)])
        else:
            hp = np.array([0.7 if h < 6 else (1.25 if 8 <= h <= 17 else 1.0) for h in range(24)])
        night = np.array([1.0 if h < 6 else 0.0 for h in range(24)])
        mix = (1 - prof["off"]) * hp / hp.sum() + prof["off"] * night / night.sum()
        hourly[j] += tok * mix / mix.sum()
rows = []
for j, d in enumerate(last7):
    for h in range(24):
        rows.append(dict(hour_start=f"{d.isoformat()}T{h:02d}:00:00-08:00", tokens=int(hourly[j, h])))
write_csv("platform_hourly_last7d.csv", rows)

print(f"accounts: {len(accounts):,}  streams: {len(streams):,}  usage events: {len(events):,}")
print(f"raw sample: {sample_path.name}  calls: {len(calls):,}")
print(f"ledger rows: {len(ledger):,}  gateway statements: {len(stmts)}")
