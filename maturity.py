"""핵심 판정 ② — 계약 기간이 부채 만기를 덮는가.

**New Fortress를 무너뜨린 것이 이 불일치였다.** 계약이 없어서가 아니라, 계약에서 현금이
들어오기 전에 부채 만기가 먼저 와서 재융자에 실패했다. 검증 표본에서 붕괴 4곳 중 유일하게
계약을 갖고도 무너진 사례다.

그래서 보는 것은 **금액과 시점을 함께**다. 만기가 리스보다 늦어도, 그때까지 들어올 리스
수입이 갚아야 할 금액에 못 미치면 재융자를 해야 한다. 만기 연도만 비교하면 그 사실이 가려진다.

수치는 `data/contracts.csv`(리스)와 `data/capital_events.csv`(차입, 10-Q Note 5 기준)에서
읽는다. 약정 조건은 `data/covenants.csv`에 따로 둔다 — 만기보다 먼저 오는 기한이라 그렇다.
"""

from pathlib import Path

import pandas as pd
import streamlit as st

DATA_DIR = Path(__file__).parent / "data"


def _to_date(text):
    """'2027-2H' → 2027-07-01, '2031' → 2031-12-31, '2027-08-10' → 그대로."""
    if text is None or (isinstance(text, float) and pd.isna(text)):
        return pd.NaT
    text = str(text).strip()
    if not text:
        return pd.NaT
    import re
    half = re.match(r"^(20\d{2})\s*[-\s]?\s*(?:H\s*([12])|([12])\s*H)$", text, re.I)
    if half:
        year = int(half.group(1))
        return pd.Timestamp(year=year, month=1 if (half.group(2) or half.group(3)) == "1" else 7, day=1)
    if re.fullmatch(r"20\d{2}", text):
        return pd.Timestamp(year=int(text), month=12, day=31)      # 연도만 있으면 연말로 본다
    if re.fullmatch(r"20\d{2}-\d{2}", text):
        return pd.Timestamp(text + "-01") + pd.offsets.MonthEnd(0)
    return pd.to_datetime(text, errors="coerce")


@st.cache_data(ttl=600, show_spinner=False)
def schedule() -> pd.DataFrame:
    """리스 유입과 차입 만기를 한 표에. 컬럼: 구분·항목·시작·종료·금액(백만$)·금리·비고."""
    rows = []

    contracts = DATA_DIR / "contracts.csv"
    if contracts.exists():
        frame = pd.read_csv(contracts)
        if "binding" in frame.columns:
            frame = frame[frame["binding"].astype(str).str.upper().str.startswith("Y")]
        for row in frame.to_dict("records"):
            start = _to_date(row.get("delivery_start"))
            years = pd.to_numeric(row.get("term_years"), errors="coerce")
            end = (start + pd.DateOffset(years=int(years))
                   if pd.notna(start) and pd.notna(years) else pd.NaT)
            rows.append({
                "구분": "리스 유입", "항목": f"{row.get('customer')} 리스",
                "시작": start, "종료": end,
                "금액(백만$)": pd.to_numeric(row.get("total_revenue_musd"), errors="coerce"),
                "금리": "–",
                "비고": f"{int(years)}년 · 연 약 ${pd.to_numeric(row.get('total_revenue_musd'))/years:,.0f}M"
                        if pd.notna(years) else "",
            })

    events = DATA_DIR / "capital_events.csv"
    if events.exists():
        frame = pd.read_csv(events)
        for row in frame.to_dict("records"):
            outstanding = pd.to_numeric(row.get("outstanding_musd"), errors="coerce")
            if pd.isna(outstanding):
                continue                     # 지분 조달은 갚을 의무가 없다
            end = _to_date(row.get("maturity"))
            rows.append({
                "구분": "부채 만기", "항목": row.get("instrument"),
                "시작": pd.to_datetime(row.get("date"), errors="coerce"),
                "종료": end, "금액(백만$)": outstanding,
                "금리": row.get("rate") or "–",
                "비고": "상환 완료" if outstanding == 0 else (
                    "만기 미상" if pd.isna(end) else ""),
            })

    return pd.DataFrame(rows)


@st.cache_data(ttl=600, show_spinner=False)
def covenants() -> pd.DataFrame:
    """만기보다 먼저 오는 약정 기한. 전부 '테넌트를 언제까지 잡느냐'에 걸려 있다."""
    path = DATA_DIR / "covenants.csv"
    if not path.exists():
        return pd.DataFrame()
    frame = pd.read_csv(path)
    frame["deadline"] = pd.to_datetime(frame["deadline"], errors="coerce")
    frame["남은 일수"] = (frame["deadline"] - pd.Timestamp.today().normalize()).dt.days
    return frame.sort_values("deadline")


def verdict(m: dict) -> dict:
    """핵심 판정 ②. 만기 연도가 아니라 **그때까지 들어올 리스 수입 대비 상환액**으로 본다."""
    frame = schedule()
    leases = frame[frame["구분"] == "리스 유입"].dropna(subset=["종료"])
    debts = frame[(frame["구분"] == "부채 만기") & (frame["금액(백만$)"] > 0)]
    known = debts.dropna(subset=["종료"])

    if leases.empty or known.empty:
        return {"status": "info", "value": "산출 불가", "verdict": "판정 불가",
                "detail": "리스 종료일 또는 차입 만기를 읽지 못했다.", "cover": None}

    lease_start = leases["시작"].min()
    lease_end = leases["종료"].max()
    annual = leases["금액(백만$)"].sum() / max(
        (lease_end - lease_start).days / 365.25, 1)

    first = known.sort_values("종료").iloc[0]
    due_date, due_amount = first["종료"], first["금액(백만$)"]
    # 그 만기까지 리스에서 들어올 누적 수입
    months = max((due_date - lease_start).days / 30.44, 0)
    accrued = annual * months / 12
    cover = accrued / due_amount if due_amount else None

    if due_date < lease_start:
        status, text = "critical", "미달 — 리스 수입이 시작되기 전에 만기가 온다"
    elif cover is not None and cover < 1:
        status, text = "warning", "주의 — 만기까지 들어올 리스 수입이 상환액에 못 미친다"
    else:
        status, text = "good", "충족 — 리스 수입이 만기 전에 상환액을 덮는다"

    unknown = int(debts["종료"].isna().sum())
    detail = (
        f"가장 먼저 오는 만기는 **{first['항목']} ${due_amount:,.0f}M, "
        f"{due_date.date()}**다. 리스 수입은 {lease_start.date()}부터 연 약 ${annual:,.0f}M "
        f"들어오므로 그 시점까지 누적 ${accrued:,.0f}M — 상환액의 "
        f"**{cover*100:.0f}%**에 해당한다.\n\n"
        "**왜 이 축을 보는가.** New Fortress는 계약이 있었는데도 무너졌다 — 계약 기간과 "
        "부채 만기가 어긋나 재융자에 실패했고, 영업현금흐름이 +$602M에서 -$583M으로 "
        "뒤집혔다. 붕괴 4곳 중 계약을 갖고도 무너진 유일한 사례다.\n\n"
        "만기 연도만 비교하면 '리스 2042년 vs 만기 2031년'처럼 보여 여유가 있는 것 같지만, "
        "실제로 먼저 오는 것은 그보다 4년 이른 만기다."
    )
    if unknown:
        detail += f"\n\n⚠️ 만기를 확인하지 못한 차입이 {unknown}건 남아 있다."

    return {"status": status, "value": f"{due_date.year}년 ${due_amount:,.0f}M 대비 수입 "
                                       f"{cover*100:.0f}%" if cover is not None else "산출 불가",
            "detail": detail, "verdict": text, "cover": cover,
            "due_date": due_date, "due_amount": due_amount, "lease_start": lease_start,
            "annual": annual}


def view(frame: pd.DataFrame | None = None) -> pd.DataFrame:
    frame = schedule() if frame is None else frame
    if frame.empty:
        return frame
    out = frame.copy()
    for column in ("시작", "종료"):
        out[column] = out[column].dt.date.astype(str).replace("NaT", "–")
    return out[["구분", "항목", "시작", "종료", "금액(백만$)", "금리", "비고"]]
