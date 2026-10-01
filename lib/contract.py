"""
The data contract: every source the close can use, the standard field names the SQL expects,
and the column names we try automatically when mapping a team's own data.

A source is REQUIRED or OPTIONAL. Optional sources switch features on: no contracts table means
no enterprise tab, no billing ledger means the billing checks report SKIP, and so on.
"""

SOURCES = {
    "usage": {
        "required": True,
        "what": "API usage. Either one row per API call (raw request logs) or one row per day per account "
                "(a daily rollup from the metering pipeline). The grain is detected from the columns you map.",
        "grains": {
            "call": {
                "required": ["ts", "account_id", "status", "input_tokens"],
                "optional": ["request_id", "channel", "model", "project_label", "output_tokens",
                             "questions", "max_choice_cardinality", "latency_ms"],
            },
            "daily": {
                "required": ["date", "account_id", "status", "calls", "input_tokens"],
                "optional": ["channel", "model", "project_label", "output_tokens", "avg_questions_per_call",
                             "max_choice_cardinality", "offhours_call_share", "p95_latency_ms"],
            },
        },
    },
    "accounts": {
        "required": True,
        "what": "One row per customer account: when it signed up and how it is sold.",
        "fields": {"required": ["account_id", "signup_date"],
                   "optional": ["display_name", "segment", "channel", "free_credit_usd", "payment_method_added"]},
    },
    "contracts": {
        "required": False,
        "what": "Signed enterprise contracts (CRM). Enables the Enterprise tab, true-ups and the enterprise forecast.",
        "fields": {"required": ["account_id", "customer", "start_date", "monthly_commit_usd", "unit_price_per_mtok"],
                   "optional": ["contract_id", "end_date", "discount_pct"]},
    },
    "pipeline": {
        "required": False,
        "what": "Open enterprise deals (CRM). Enables the pipeline part of the forecast.",
        "fields": {"required": ["deal_id", "customer", "probability", "expected_monthly_commit_usd", "expected_start"],
                   "optional": ["stage", "expected_discount_pct", "use_cases"]},
    },
    "billing_ledger": {
        "required": False,
        "what": "Invoices from the billing system, one row per account per month. Enables billing checks.",
        "fields": {"required": ["month", "account_id", "tokens_billed", "unit_price_per_mtok", "usage_charge_usd"],
                   "optional": ["invoice_id", "free_credit_tokens", "paid_tokens", "true_up_usd", "invoice_total_usd"]},
    },
    "gateway_statements": {
        "required": False,
        "what": "Monthly statements from resellers (Vercel, OpenRouter, ...). Enables partner reconciliation.",
        "fields": {"required": ["gateway", "month", "tokens_reported"],
                   "optional": ["gross_usd", "fee_pct", "net_remitted_usd"]},
    },
    "hourly": {
        "required": False,
        "what": "Platform tokens by hour, only needed if usage is a daily rollup (call-level logs give hours already). "
                "Enables peak vs base-load sizing on the Capacity tab.",
        "fields": {"required": ["hour_start", "tokens"], "optional": []},
    },
    "label_map": {
        "required": False,
        "what": "Finance-maintained map from customer project labels to use cases. A starter file ships in reference/.",
        "fields": {"required": ["project_label", "use_case"], "optional": []},
    },
}

# Names we try automatically, in order, when mapping a team's columns to the standard field.
SYNONYMS = {
    "ts": ["ts", "timestamp", "created_at", "request_time", "time", "event_time", "logged_at"],
    "date": ["date", "day", "usage_date", "event_date", "ds"],
    "account_id": ["account_id", "account", "org_id", "organization_id", "customer_id", "workspace_id", "team_id"],
    "status": ["status", "status_code", "http_status", "response_status"],
    "input_tokens": ["input_tokens", "usage_input_tokens", "prompt_tokens", "tokens_in", "billed_input_tokens", "tokens"],
    "output_tokens": ["output_tokens", "usage_output_tokens", "completion_tokens", "tokens_out"],
    "calls": ["calls", "requests", "request_count", "n_calls", "count"],
    "request_id": ["request_id", "x_typesafe_request_id", "req_id", "id"],
    "channel": ["channel", "source", "gateway", "sales_channel", "route"],
    "model": ["model", "model_served", "model_id", "model_version"],
    "project_label": ["project_label", "project", "label", "tag", "project_name", "api_key_name"],
    "questions": ["questions", "question_count", "n_questions"],
    "max_choice_cardinality": ["max_choice_cardinality", "max_cardinality", "choice_cardinality"],
    "latency_ms": ["latency_ms", "duration_ms", "elapsed_ms", "latency"],
    "avg_questions_per_call": ["avg_questions_per_call", "questions_per_call"],
    "offhours_call_share": ["offhours_call_share", "night_share", "offhours_share"],
    "p95_latency_ms": ["p95_latency_ms", "latency_p95_ms", "p95_ms"],
    "signup_date": ["signup_date", "created_at", "created", "signup_at", "joined_at", "first_seen"],
    "display_name": ["display_name", "name", "account_name", "company", "customer_name"],
    "segment": ["segment", "tier", "plan", "account_type"],
    "free_credit_usd": ["free_credit_usd", "free_credit", "credit_granted_usd", "promo_credit_usd"],
    "payment_method_added": ["payment_method_added", "has_payment_method", "card_on_file"],
    "customer": ["customer", "customer_name", "account_name", "name", "company"],
    "start_date": ["start_date", "contract_start", "effective_date", "start"],
    "end_date": ["end_date", "contract_end", "expiry_date", "end"],
    "monthly_commit_usd": ["monthly_commit_usd", "monthly_commitment", "commit_usd", "minimum_monthly_spend"],
    "unit_price_per_mtok": ["unit_price_per_mtok", "price_per_mtok", "price_per_million_tokens", "unit_price"],
    "contract_id": ["contract_id", "contract", "agreement_id"],
    "discount_pct": ["discount_pct", "discount", "discount_rate"],
    "deal_id": ["deal_id", "opportunity_id", "opp_id", "id"],
    "probability": ["probability", "win_probability", "prob"],
    "expected_monthly_commit_usd": ["expected_monthly_commit_usd", "expected_commit", "amount_monthly", "monthly_amount"],
    "expected_start": ["expected_start", "close_date", "expected_close", "start_date"],
    "stage": ["stage", "deal_stage", "opportunity_stage"],
    "expected_discount_pct": ["expected_discount_pct", "discount_pct", "discount"],
    "use_cases": ["use_cases", "use_case", "products"],
    "month": ["month", "billing_month", "period", "invoice_month"],
    "tokens_billed": ["tokens_billed", "billed_tokens", "quantity", "usage_quantity"],
    "usage_charge_usd": ["usage_charge_usd", "usage_charge", "amount_usd", "subtotal"],
    "invoice_id": ["invoice_id", "invoice", "invoice_number"],
    "free_credit_tokens": ["free_credit_tokens", "credit_tokens"],
    "paid_tokens": ["paid_tokens"],
    "true_up_usd": ["true_up_usd", "true_up", "shortfall_usd"],
    "invoice_total_usd": ["invoice_total_usd", "total_usd", "invoice_total", "total"],
    "gateway": ["gateway", "partner", "reseller", "channel"],
    "tokens_reported": ["tokens_reported", "tokens", "reported_tokens", "quantity"],
    "gross_usd": ["gross_usd", "gross", "gross_amount"],
    "fee_pct": ["fee_pct", "fee_rate", "commission_pct"],
    "net_remitted_usd": ["net_remitted_usd", "net_usd", "remitted", "payout_usd"],
    "hour_start": ["hour_start", "hour", "ts_hour", "hour_pt"],
    "tokens": ["tokens", "input_tokens", "billable_tokens"],
    "use_case": ["use_case", "category", "workload"],
}

DATE_FIELDS = {"date", "signup_date", "start_date", "end_date", "expected_start"}
TIMESTAMP_FIELDS = {"ts", "hour_start"}
MONTH_FIELDS = {"month"}
NUMERIC_FIELDS = {"status", "input_tokens", "output_tokens", "calls", "questions", "max_choice_cardinality",
                  "latency_ms", "avg_questions_per_call", "offhours_call_share", "p95_latency_ms", "free_credit_usd",
                  "monthly_commit_usd", "unit_price_per_mtok", "discount_pct", "probability",
                  "expected_monthly_commit_usd", "expected_discount_pct", "tokens_billed", "usage_charge_usd",
                  "free_credit_tokens", "paid_tokens", "true_up_usd", "invoice_total_usd", "tokens_reported",
                  "gross_usd", "fee_pct", "net_remitted_usd", "tokens"}


def fields_for(source, grain=None):
    spec = SOURCES[source]
    if source == "usage":
        g = spec["grains"][grain]
        return g["required"], g["optional"]
    return spec["fields"]["required"], spec["fields"]["optional"]


def detect_grain(columns_mapped):
    """columns_mapped: set of standard field names the user mapped for usage."""
    if "ts" in columns_mapped:
        return "call"
    if "date" in columns_mapped:
        return "daily"
    return None


def auto_match(standard, available):
    """Return the first available column whose normalized name matches a synonym."""
    norm = {c.lower().replace(" ", "_").replace("-", "_").replace(".", "_"): c for c in available}
    for cand in SYNONYMS.get(standard, [standard]):
        if cand in norm:
            return norm[cand]
    return None
