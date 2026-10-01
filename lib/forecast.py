"""
The demand forecast in Python, written to match the Forecast tabs' formulas line for line.

Why it exists alongside the Excel formulas:
  1. Forecast history. Each close stores this run's forecast in forecast_history.csv, so next
     month's close can compare actuals with what was predicted (the Forecast tab variance).
  2. A test. tests/test_parity.py checks the workbook formulas reproduce these numbers.

Inputs are the Data_ tables (pandas DataFrames) the runner also writes to the workbook, plus
the scenario inputs from config (the same values seeded into the Forecast tab's blue cells).
"""

import calendar
from datetime import date

SCEN = ("low", "base", "high")
W = 12
T = W - 1


def add_months(d, n):
    t = d.year * 12 + d.month - 1 + n
    return date(t // 12, t % 12 + 1, 1)


def month_end(d):
    return date(d.year, d.month, calendar.monthrange(d.year, d.month)[1])


def _d(x):
    if x is None or x != x:
        return None
    return x if isinstance(x, date) and not hasattr(x, "hour") else (x.date() if hasattr(x, "date") else x)


def run(data, inputs, as_of):
    """data: dict of DataFrames keyed by Data_ sheet name. inputs: forecast inputs (config).
    Returns {(scenario, target_month): {component: B tokens/day, ..., 'revenue_k': $k}}."""
    months = [add_months(as_of, j - T) for j in range(W)]
    md = data["Data_Months"]
    days = {_d(r.month): float(r.days) for r in md.itertuples()}
    dd = [days.get(m, calendar.monthrange(m.year, m.month)[1]) for m in months]

    co = data["Data_Cohort"]
    cot = {(_d(r.cohort), int(r.k)): float(r.billable_tokens) for r in co.itertuples()}
    A = [[cot.get((months[i], k), 0.0) / dd[i + k] / 1e9 for k in range(W - i)] for i in range(W)]
    sg = {_d(r.cohort): float(r.signups) for r in data["Data_Signups"].itertuples()}
    S = [sg.get(m, 0.0) for m in months]

    mo = data["Data_Monthly"]

    def seg_tpd(seg, j, field="billable_tokens"):
        x = mo[(mo["segment"] == seg) & (mo["month"].map(_d) == months[j])][field].sum()
        return float(x)

    er = data["Data_EarlierCohorts"]
    e_last = float(er[er["month"].map(_d) == months[T]]["billable_tokens"].sum()) / dd[T] / 1e9

    G = [seg_tpd("gateway_aggregate", j) / dd[j] / 1e9 for j in range(W)]
    g_last = (G[T] / G[T - 1] - 1) if G[T - 1] > 0 else 0.0

    ss_tok = seg_tpd("self_serve", T)
    ss_price = seg_tpd("self_serve", T, "net_revenue") / ss_tok * 1e6 if ss_tok > 0 else 0.0
    ga_tok = seg_tpd("gateway_aggregate", T)
    ga_price = seg_tpd("gateway_aggregate", T, "net_revenue") / ga_tok * 1e6 if ga_tok > 0 else 0.0

    params = {r.key: r.value for r in data["Data_Params"].itertuples()}
    list_price = float(params["list_price_per_mtok"])
    as_of_end = month_end(as_of)
    R = float(inputs["ent_ramp_days"])

    out = {}
    for s in SCEN:
        tail = float(inputs["tail_factor"][s])
        # development factors
        f = []
        for k in range(W - 1):
            rows = range(0, W - 1 - k)
            if s == "base":
                num = sum(A[i][k + 1] for i in rows if A[i][k] > 0)
                den = sum(A[i][k] for i in rows)
                f.append(num / den if den > 0 else tail)
            else:
                rs = [A[i][k + 1] / A[i][k] for i in rows if A[i][k] > 0]
                f.append((min(rs) if s == "low" else max(rs)) if rs else tail)
        fac = lambda k: f[k] if k < W - 1 else tail  # noqa: E731
        gb = ((S[T] / S[T - 3]) ** (1 / 3) - 1) if (S[T - 3] > 0 and S[T] > 0) else 0.0
        g = {"low": 0.0, "base": gb, "high": 2 * gb}[s]
        m0v = [A[i][0] / S[i] for i in range(T - 2, T + 1) if S[i] > 0]
        m0 = ({"low": min, "high": max}[s](m0v) if s != "base" else sum(m0v) / len(m0v)) if m0v else 0.0
        Sx = S + [0.0, 0.0, 0.0]
        for j in range(W, W + 3):
            Sx[j] = Sx[j - 1] * (1 + g)
        P = {}
        for i in range(W + 3):
            for k in range(W + 3 - i):
                if i + k <= T:
                    P[(i, k)] = A[i][k]
                elif k == 0:
                    P[(i, k)] = Sx[i] * m0
                else:
                    P[(i, k)] = P[(i, k - 1)] * fac(k - 1)
        g_or = {"low": min(0.0, g_last), "base": g_last * float(inputs["gateway_growth_decay"]), "high": max(0.0, g_last)}[s]
        ratio = float(inputs["ent_usage_ratio"][s]); eg = float(inputs["ent_growth"][s])
        for n in (1, 2, 3):
            tm = add_months(as_of, n)
            ndays = calendar.monthrange(tm.year, tm.month)[1]
            ss = sum(P[(i, T + n - i)] for i in range(T + n + 1)) + e_last * tail ** n
            ga = G[T] * (1 + g_or) ** n
            # signed contracts
            ent_tok = ent_rev = 0.0
            for c in data["Data_Contracts"].itertuples():
                start, end = _d(c.start_date), _d(c.end_date)
                if start is None:
                    continue
                if end is not None and end < tm:
                    continue
                age = (as_of_end - start).days
                steady = float(c.monthly_commit_usd) / float(c.unit_price_per_mtok) * ratio / 30.4 / 1000
                L = float(c.last7_btok_per_day)
                rate = (L + (steady - L) * min(1.0, n * 30.4 / (R - age))) if age < R else L
                rate *= (1 + eg) ** n
                ent_tok += rate
                ent_rev += max(rate * ndays * 1000 * float(c.unit_price_per_mtok), float(c.monthly_commit_usd))
            # pipeline
            p_tok = p_rev = 0.0
            m_end = month_end(tm)
            for d in data["Data_Pipeline"].itertuples():
                start = _d(d.expected_start)
                if start is None:
                    continue
                pr = float(d.probability)
                w = {"low": 0.0, "base": pr, "high": 1.0 if pr >= 0.5 else pr}[s]
                price = list_price * (1 - float(d.expected_discount_pct or 0))
                steady = float(d.expected_monthly_commit_usd) / price / 30.4 / 1000
                first = max(start, tm)
                active = max(0, (m_end - first).days + 1)
                if active == 0:
                    continue
                mid = (first.toordinal() + m_end.toordinal()) / 2 - start.toordinal()
                ramp = min(1.0, 0.1 + 0.9 * mid / R)
                t = w * steady * active * ramp
                p_tok += t / ndays
                p_rev += t * 1000 * price
            rev = (ss * ndays * 1000 * ss_price + ga * ndays * 1000 * ga_price + ent_rev + p_rev) / 1000
            out[(s, tm)] = dict(self_serve=ss, gateway_aggregate=ga, enterprise_signed=ent_tok,
                                enterprise_pipeline=p_tok, enterprise=ent_tok + p_tok,
                                total=ss + ga + ent_tok + p_tok, revenue_k=rev)
    return out
