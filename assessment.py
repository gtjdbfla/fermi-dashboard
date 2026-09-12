"""Conservative, offline rules shared by dashboard calculations and tests."""

import pandas as pd


def active_contracts(frame):
    """A signature is not closing; terminated contracts are no longer coverage."""
    out = frame.copy()
    if out.empty:
        return out.assign(closing_status=pd.Series(dtype=str))
    status = out.get("closing_status", pd.Series("", index=out.index)).fillna("").astype(str).str.strip()
    out["closing_status"] = status.where(status.isin(["종결", "미종결", "연장", "해지"]), "미확인")
    signed = out.get("binding", pd.Series("", index=out.index)).fillna("").astype(str).str.upper().str.strip().eq("Y")
    return out[signed & out["closing_status"].ne("해지")].copy()


def after_balance(date, asof, today=None):
    date, asof = pd.to_datetime(date, errors="coerce"), pd.to_datetime(asof, errors="coerce")
    today = pd.Timestamp(today) if today is not None else pd.Timestamp.today().normalize()
    return pd.notna(date) and pd.notna(asof) and asof < date <= today


def cash_adjustment(points, asof, today=None):
    return sum(value for key, (value, date) in points.items()
               if key.startswith("post_q_") and pd.notna(value) and after_balance(date, asof, today))


def debt_adjustment(events, asof, today=None):
    if events.empty:
        return 0.0
    total = 0.0
    for row in events.to_dict("records"):
        amount = pd.to_numeric(row.get("outstanding_musd"), errors="coerce")
        if pd.notna(amount) and after_balance(row.get("date"), asof, today):
            total += amount * 1e6
    return total


def value_before(series, cutoff):
    """시계열에서 cutoff 이전(포함) 마지막 값. '이 시점까지 투입된 금액'처럼, 이후 데이터가
    쌓여도 과거 시점 값이 따라 바뀌면 안 되는 곳에 쓴다 — PP&E 총액을 매 분기 그대로 쓰면
    '투입 후 첫 계약'이라는 과거 문장이 미래 투자까지 계속 흡수해 버린다."""
    if series.empty or pd.isna(cutoff):
        return None
    prior = series[pd.to_datetime(series["end"]) <= pd.Timestamp(cutoff)]
    return float(prior.iloc[-1]["val"]) if not prior.empty else None


def cash_split(points, asof, total):
    def aligned(key):
        value, date = points.get(key, (None, None))
        return value if pd.notna(value) and pd.notna(date) and pd.notna(asof) and pd.Timestamp(date) == pd.Timestamp(asof) else None
    free, restricted = aligned("cash_free"), aligned("cash_restricted")
    if total is not None:
        if free is None and restricted is not None:
            free = total - restricted
        if restricted is None and free is not None:
            restricted = total - free
        if free is not None and restricted is not None and (min(free, restricted) < 0 or abs(free + restricted - total) > 1):
            return None, None
    return free, restricted
