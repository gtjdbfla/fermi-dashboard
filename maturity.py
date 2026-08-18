"""핵심 판정 ② — 계약 기간이 부채 만기를 덮는가.

**New Fortress를 무너뜨린 것이 이 불일치였다.** 계약이 없어서가 아니라, 계약에서 현금이
들어오기 전에 부채 만기가 먼저 와서 재융자에 실패했다. 검증 표본에서 붕괴 4곳 중 유일하게
계약을 갖고도 무너진 사례다(data/sector_profiles.csv).

그래서 보는 것은 두 시점의 순서다.
  · 리스에서 현금이 들어오기 시작하는 때 → 그리고 끝나는 때
  · 갚아야 할 돈의 만기

값을 문장에 박아두면 새 사채가 발행돼도 화면이 그대로다. `data/contracts.csv`와
`data/capital_events.csv`에서 읽어 계산한다.
"""

import re
from pathlib import Path

import pandas as pd
import streamlit as st

DATA_DIR = Path(__file__).parent / "data"

# "2027-2H", "2027 2H", "2027-H2" 같은 반기 표기를 날짜로 바꾼다.
_HALF = re.compile(r"(20\d{2})\s*[-\s]?\s*(?:H\s*([12])|([12])\s*H)", re.I)
_YEAR = re.compile(r"(20[2-9]\d)\s*년?\s*만기|만기\s*(20[2-9]\d)|due\s+(20[2-9]\d)", re.I)


def _to_date(text: str):
    """'2027-2H' → 2027-07-01. 반기 표기가 없으면 연·월 파싱에 맡긴다."""
    if not text or (isinstance(text, float) and pd.isna(text)):
        return pd.NaT
    text = str(text).strip()
    match = _HALF.search(text)
    if match:
        year = int(match.group(1))
        half = int(match.group(2) or match.group(3))
        return pd.Timestamp(year=year, month=1 if half == 1 else 7, day=1)
    return pd.to_datetime(text, errors="coerce")


def _maturity_year(*texts) -> int | None:
    """계약서 문구에서 만기 연도를 뽑는다. '전환사채 5.00% 2031년 만기' → 2031."""
    for text in texts:
        if not text or (isinstance(text, float) and pd.isna(text)):
            continue
        match = _YEAR.search(str(text))
        if match:
            return int(next(g for g in match.groups() if g))
    return None


@st.cache_data(ttl=600, show_spinner=False)
def schedule() -> pd.DataFrame:
    """리스 유입과 부채 만기를 한 표에. 컬럼: 구분·항목·시작·종료·금액(백만$)·비고."""
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
                "비고": f"{int(years)}년" if pd.notna(years) else "",
            })

    events = DATA_DIR / "capital_events.csv"
    if events.exists():
        frame = pd.read_csv(events)
        for row in frame.to_dict("records"):
            if str(row.get("dilutive", "")).upper().startswith("Y") and "사채" not in str(row.get("instrument", "")):
                continue          # 주식 발행은 갚을 의무가 없다
            instrument = str(row.get("instrument", ""))
            if not re.search(r"사채|차입|debt|note|loan", instrument, re.I):
                continue
            year = _maturity_year(instrument, row.get("terms"))
            rows.append({
                "구분": "부채 만기", "항목": instrument,
                "시작": pd.to_datetime(row.get("date"), errors="coerce"),
                "종료": pd.Timestamp(year=year, month=12, day=31) if year else pd.NaT,
                "금액(백만$)": pd.to_numeric(row.get("gross_musd"), errors="coerce"),
                "비고": "만기 미상" if not year else f"{year}년 만기",
            })

    return pd.DataFrame(rows)


def verdict(m: dict) -> dict:
    """핵심 판정 ② 카드. {status, value, detail, verdict, gap_years}."""
    frame = schedule()
    leases = frame[frame["구분"] == "리스 유입"].dropna(subset=["종료"])
    debts = frame[frame["구분"] == "부채 만기"].dropna(subset=["종료"])

    if leases.empty or debts.empty:
        return {"status": "info", "value": "산출 불가",
                "detail": "리스 종료일 또는 부채 만기를 읽지 못했다.",
                "verdict": "판정 불가", "gap_years": None}

    lease_end = leases["종료"].max()
    lease_start = leases["시작"].min()
    debt_due = debts["종료"].min()          # 가장 먼저 오는 만기가 관문이다
    unknown = int(frame[frame["비고"] == "만기 미상"].shape[0])
    gap = (lease_end - debt_due).days / 365.25

    # 유입이 시작되기도 전에 만기가 오면 계약 길이와 무관하게 재융자를 해야 한다.
    if pd.notna(lease_start) and debt_due < lease_start:
        status = "warning"
        verdict_text = "주의 — 첫 리스 수입보다 만기가 먼저 온다"
    elif gap > 0:
        status, verdict_text = "good", "충족 — 계약이 부채보다 길다"
    else:
        status, verdict_text = "critical", "미달 — 만기가 계약보다 먼저 끝난다"

    detail = (
        f"가장 이른 부채 만기는 {debt_due.year}년, 리스 종료는 {lease_end.year}년으로 "
        f"{gap:+.0f}년 여유다. 다만 첫 리스 수입은 {lease_start.date()}부터 들어오므로, "
        f"그 전까지는 계약 길이와 무관하게 재융자나 현금으로 버텨야 한다.\n\n"
        "**왜 이 축을 보는가.** New Fortress는 계약이 있었는데도 무너졌다 — 계약 기간과 "
        "부채 만기가 어긋나 재융자에 실패했고, 영업현금흐름이 +$602M에서 -$583M으로 "
        "뒤집혔다. 붕괴 4곳 중 계약을 갖고도 무너진 유일한 사례다."
    )
    if unknown:
        detail += f"\n\n⚠️ 만기를 확인하지 못한 차입이 {unknown}건 있다. 그중 하나가 " \
                  f"{debt_due.year}년보다 앞서면 판정이 바뀐다."

    return {
        "status": status,
        "value": f"리스 {lease_end.year}년 vs 만기 {debt_due.year}년",
        "detail": detail,
        "verdict": verdict_text,
        "gap_years": gap,
    }


def view(frame: pd.DataFrame | None = None) -> pd.DataFrame:
    frame = schedule() if frame is None else frame
    if frame.empty:
        return frame
    out = frame.copy()
    for column in ("시작", "종료"):
        out[column] = out[column].dt.date.astype(str).replace("NaT", "–")
    return out[["구분", "항목", "시작", "종료", "금액(백만$)", "비고"]]
