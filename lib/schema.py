"""
Layout of the Data_ sheets: the ONLY place numbers enter the workbook. The runner writes SQL
results here; every report tab is a formula that reads these sheets. A team without Python can
paste query results into the same columns by hand and the workbook still works.
"""

WINDOW = 12          # months shown and modelled, ending at the as-of month
MAX_CONTRACTS = 40   # enterprise contract rows the forecast can hold
MAX_DEALS = 40       # pipeline rows
MAX_CHANNELS = 8
MAX_USE_CASES = 15
MAX_CHECK_ROWS = 150
MAX_CONC = 25

DATA_SHEETS = {
    "Data_Params": ["key", "value", "description"],
    "Data_Months": ["month", "days", "calendar_days"],
    "Data_Monthly": ["month", "segment", "channel", "active_accounts", "paying_accounts", "billable_tokens",
                     "free_credit_tokens", "free_credit_usd", "list_value", "usage_revenue", "true_up",
                     "net_revenue", "gateway_fees"],
    "Data_Lists": ["n", "channel", "use_case"],
    "Data_UseCase": ["month", "use_case", "billable_tokens", "tokens_from_label"],
    "Data_InferenceAcc": ["use_case", "labelled_tokens", "inferred_correctly"],
    "Data_Cohort": ["cohort", "k", "billable_tokens", "active_accounts"],
    "Data_EarlierCohorts": ["month", "billable_tokens"],
    "Data_Signups": ["cohort", "signups", "added_payment_method", "payment_flag_known"],
    "Data_Contracts": ["customer", "account_id", "start_date", "end_date", "monthly_commit_usd",
                       "unit_price_per_mtok", "discount_pct", "last7_btok_per_day"],
    "Data_EntMonth": ["customer", "month", "billable_tokens", "usage_revenue", "commitment", "true_up"],
    "Data_Pipeline": ["deal_id", "customer", "stage", "probability", "expected_monthly_commit_usd",
                      "expected_start", "expected_discount_pct", "use_cases"],
    "Data_Concentration": ["rank", "account_id", "display_name", "segment", "channel", "billable_tokens",
                           "net_revenue", "share_of_revenue"],
    "Data_Checks": ["check_no", "check_name", "scope", "expected", "actual", "usd_impact", "status", "detail"],
    "Data_Reliability": ["month", "calls_ok", "calls_failed"],
    "Data_Hourly": ["hour_start", "tokens"],
    "Data_FcHistory": ["close_month", "target_month", "scenario", "component", "btok_per_day"],
}

# Data_Params is a fixed list: formulas read value cells by position (row = index + 2).
PARAMS = [
    ("as_of_month", "Last complete month in this close (first day of month)"),
    ("window_start", "First month of the 12-month reporting window"),
    ("list_price_per_mtok", "Current list price, $ per million input tokens"),
    ("free_credit_usd", "Default free credit for new accounts, $"),
    ("aggregate_gateway_fee", "Average fee on gateways that don't pass end-customer IDs"),
    ("gateway_revenue_treatment", "net = fee reduces revenue; gross = fee is a cost"),
    ("total_net_revenue_asof", "Net revenue in the as-of month, all accounts, $"),
    ("usage_grain", "call = raw request logs; daily = rollup"),
    ("data_first_date", "First date with usage"),
    ("data_last_date", "Last date with usage (on or before as-of month end)"),
    ("sources_loaded", "Sources connected for this close"),
    ("generated_at", "When the runner produced this workbook"),
    ("company_name", "Shown in titles"),
    ("reporting_timezone", "Time zone used for days and hours"),
]
PARAM_ROW = {k: i + 2 for i, (k, _) in enumerate(PARAMS)}


def P(key):
    """Absolute reference to a parameter's value cell."""
    return f"Data_Params!$B${PARAM_ROW[key]}"
