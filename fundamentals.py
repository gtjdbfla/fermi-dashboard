"""페르미(FRMI) 펀더멘탈 지표 계산.

이 회사는 매출이 0이다. 그래서 PER·PBR·영업이익률·매출성장률 같은 통상 지표는 계산되지 않거나
계산돼도 의미가 없다. 개발단계 전력인프라 사업에서 '펀더멘탈이 유지되고 있는가'는 결국 다음 7개 축이다.

  1. 자금여력   — 돈이 마르기 전에 전력을 켤 수 있는가 (런웨이)
  2. 자본투입   — 태운 현금이 실제 설비로 남고 있는가
  3. 전력 진척   — 계획 MW가 가동 MW로 바뀌고 있는가
  4. 계약 백로그 — 지은 용량을 사 줄 고객이 실제로 계약돼 있는가
  5. 자본구조   — 그 과정에서 주주 몫이 얼마나 희석되는가
  6. 지배구조   — 경영진·이사회가 안정적으로 그 계획을 집행할 수 있는가
  7. 시장 반영   — 위 상태 대비 주가가 어디에 있는가

각 축은 임계값이 코드에 그대로 드러나 있다(THRESHOLDS). 판정은 정보 정리를 위한 규칙일 뿐이고
투자 판단이나 매매 권유가 아니다.
"""

from pathlib import Path

import pandas as pd
import streamlit as st

import sec_edgar as sec

DATA_DIR = Path(__file__).parent / "data"

# XBRL 태그. 한 개념을 여러 태그로 중복 보고하는 경우가 있어 후보를 순서대로 둔다.
TAG_CASH_TOTAL = "CashCashEquivalentsRestrictedCashAndRestrictedCashEquivalents"
TAG_CASH = "CashAndCashEquivalentsAtCarryingValue"
TAG_OP_CF = "NetCashProvidedByUsedInOperatingActivities"
# 지금은 XBRL에 매출 태그가 아예 없다(pre-revenue). 첫 매출이 잡히면 이 중 하나로 들어온다.
TAG_REVENUE = "Revenues"
TAG_REVENUE_ALT = "RevenueFromContractWithCustomerExcludingAssessedTax"
TAG_FIN_CF = "NetCashProvidedByUsedInFinancingActivities"
TAG_CAPEX = "PaymentsToAcquirePropertyPlantAndEquipment"
TAG_PPE_GROSS = "PropertyPlantAndEquipmentGross"
TAG_PPE_NET = "PropertyPlantAndEquipmentNet"
TAG_ASSETS = "Assets"
TAG_LIABILITIES = "Liabilities"
TAG_EQUITY = "StockholdersEquity"
TAG_NET_LOSS = "NetIncomeLoss"
TAG_GNA = "GeneralAndAdministrativeExpense"
TAG_SBC = "ShareBasedCompensation"
TAG_SHARES = "CommonStockSharesOutstanding"
TAG_SHARES_DEI = "EntityCommonStockSharesOutstanding"
TAG_CAP_INTEREST = "InterestCostsCapitalized"
TAG_INTEREST_EXPENSE = "InterestExpenseDebt"
TAG_PURCHASE_OBLIGATION = "UnrecordedUnconditionalPurchaseObligationBalanceSheetAmount"
# 차입금은 DebtCurrent / LongTermDebt / ShortTermBorrowings에 같은 금액이 중복해 실린다.
# 같은 시점의 최대값을 총차입금으로 본다(2026 Q1 421.3 + Q2 순증 98.8 = 8-K의 520.1과 일치).
TAG_DEBT_CANDIDATES = ["DebtCurrent", "LongTermDebt", "ShortTermBorrowings"]

THRESHOLDS = {
    "runway_good": 18,          # 총소진 기준 런웨이(개월)
    "runway_warning": 12,
    "runway_serious": 6,
    "capitalized_interest": 0.80,  # 이자 중 자본화 비중 — 손익에 안 잡히는 몫
    "contracted_vs_landed": 0.50,  # 계약 MW / 반입 설비 MW
    "share_growth_qoq": 0.10,      # 분기 주식수 증가율
    "proxy_filings_90d": 5,        # 90일간 위임장 관련 공시 건수
}


# ── 데이터 로딩 ────────────────────────────────────────────────────────────────
@st.cache_data(ttl=600, show_spinner=False)
def load_csv(name: str) -> pd.DataFrame:
    path = DATA_DIR / name
    if not path.exists():
        return pd.DataFrame()
    return pd.read_csv(path)


def manual_values() -> dict:
    """10-Q가 아직 안 나온 최신 분기 수치(8-K 발표치)를 key -> value로."""
    frame = load_csv("latest_reported.csv")
    if frame.empty:
        return {}
    return {row["key"]: float(row["value"]) for _, row in frame.iterrows()}


# 수동 CSV별로 "이 행이 근거로 삼은 공시 날짜"가 담긴 열.
MANUAL_SOURCE_DATES = {
    "contracts.csv": "signed",
    "power_stages.csv": "as_of",
    "capital_events.csv": "date",
    "latest_reported.csv": "period_end",
}
# 이 묶음의 공시가 올라오면 계약·용량·조달 수치가 바뀔 수 있어 수동 갱신이 필요하다.
# 정기보고(10-Q/10-K)는 XBRL이 알아서 덮으므로 제외한다.
STALENESS_GROUPS = ("수시공시(중요사건)", "증권신고·자금조달")


def manual_data_asof() -> pd.Timestamp | None:
    """수동 데이터가 어디까지 반영됐는지의 기준일.

    이 날짜 이후에 8-K가 올라와 있으면 화면의 계약·용량 숫자가 낡았을 수 있다는 뜻이다.
    가장 중요한 지표(계약 커버리지)가 하필 전부 수동이라, 화면이 스스로 알려주지 않으면
    사람이 매번 공시를 챙겨야 한다.

    CSV의 근거 날짜만 보면 안 된다. 공시를 읽고 "바꿀 게 없다"고 판단한 경우 아무 CSV도 바뀌지
    않아 배너가 영원히 꺼지지 않는다. 그래서 review_log.csv에 검토 완료 지점을 따로 남기고
    둘 중 더 늦은 날짜를 기준으로 삼는다.
    """
    dates = []
    reviews = load_csv("review_log.csv")
    if not reviews.empty and "reviewed_through" in reviews.columns:
        reviewed = pd.to_datetime(reviews["reviewed_through"], errors="coerce").dropna()
        if not reviewed.empty:
            dates.append(reviewed.max())
    for name, column in MANUAL_SOURCE_DATES.items():
        frame = load_csv(name)
        if frame.empty or column not in frame.columns:
            continue
        parsed = pd.to_datetime(frame[column], errors="coerce").dropna()
        if not parsed.empty:
            dates.append(parsed.max())
    milestones = load_csv("milestones.csv")
    if not milestones.empty and {"date", "status"} <= set(milestones.columns):
        # 예정 마일스톤은 미래 날짜라 기준으로 쓰면 안 된다. 달성된 것만 본다.
        done = pd.to_datetime(milestones[milestones["status"] == "달성"]["date"], errors="coerce").dropna()
        if not done.empty:
            dates.append(done.max())
    return max(dates) if dates else None


def staleness(m: dict) -> dict:
    """수동 데이터가 낡았는지 판정. {asof, count, filings}."""
    asof = manual_data_asof()
    filings = m.get("filings")
    if asof is None or filings is None or filings.empty:
        return {"asof": asof, "count": 0, "filings": pd.DataFrame()}
    fresh = filings[(filings["filed"] > asof) & (filings["group"].isin(STALENESS_GROUPS))]
    return {"asof": asof, "count": int(len(fresh)), "filings": fresh}


def manual_asof() -> pd.Timestamp | None:
    """최신 분기의 기준일. post_q_* 항목은 '분기 후 발생'이라 날짜가 분기말보다 뒤이므로 제외한다."""
    frame = load_csv("latest_reported.csv")
    if frame.empty:
        return None
    quarterly_rows = frame[~frame["key"].str.startswith("post_q")]
    if quarterly_rows.empty:
        return None
    return pd.to_datetime(quarterly_rows["period_end"]).max()


# ── 시계열 조립 ────────────────────────────────────────────────────────────────
def debt_series(facts: dict) -> pd.DataFrame:
    """총차입금 시계열. 중복 보고된 태그들 중 같은 시점 최대값을 채택한다."""
    frames = []
    for tag in TAG_DEBT_CANDIDATES:
        series = sec.instant_series(facts, tag)
        if not series.empty:
            frames.append(series[["end", "val"]].assign(tag=tag))
    if not frames:
        return pd.DataFrame(columns=["end", "val"])
    merged = pd.concat(frames)
    return merged.groupby("end", as_index=False)["val"].max().sort_values("end").reset_index(drop=True)


QUARTER_MAX_DAYS = 100  # 분기(약 90일)는 통과, 반기(약 180일)는 걸러내는 선


def _span_label(row) -> str:
    """구간 라벨. 분기 하나면 '26 Q1', 여러 분기에 걸치면 '25 Q2–Q3'.

    페르미는 2025년에 Q2/Q3를 따로 보고하지 않은 항목이 있어서, 그 구간은 합산으로만 존재한다.
    조용히 버리면 누적 합계가 어긋나므로 합산 구간임을 라벨에 드러낸 채로 남긴다.
    """
    if pd.isna(row.get("days")) or row["days"] <= QUARTER_MAX_DAYS:
        return sec.quarter_label(row["end"])
    start = sec.quarter_label(pd.Timestamp(row["start"]) + pd.Timedelta(days=1))
    end = sec.quarter_label(row["end"])
    if start == end:
        return end
    if start[:2] == end[:2]:  # 같은 연도면 오른쪽 연도는 생략
        return f"{start}–{end[3:]}"
    return f"{start}–{end}"


def quarterly(facts: dict, tag: str) -> pd.DataFrame:
    """기간 항목을 겹치지 않는 구간 시계열로. 분기보다 긴 합산 구간도 라벨을 달아 남긴다."""
    columns = ["start", "end", "val", "label", "days"]
    series = sec.periodic_series(facts, tag)
    if series.empty:
        return pd.DataFrame(columns=columns)
    series = series.copy()
    series["label"] = series.apply(_span_label, axis=1)
    return series[columns].reset_index(drop=True)


def latest_quarter_value(frame: pd.DataFrame):
    """가장 최근의 '분기 하나짜리' 값. 합산 구간은 소진 속도 계산에 쓰면 과대평가된다."""
    if frame.empty:
        return None
    candidates = frame[frame["days"].isna() | (frame["days"] <= QUARTER_MAX_DAYS)]
    if candidates.empty:
        return None
    return float(candidates.iloc[-1]["val"])


def matched_quarter_ratio(numerator: pd.DataFrame, denominator: pd.DataFrame):
    """두 기간 항목의 비율을 같은 분기끼리만 계산해 (비율, 기준분기말, 분자, 분모)로 돌려준다.

    항목마다 최신 분기가 들어오는 시점이 다르다. 실제로 2026 Q2 10-Q 직후 판관비는 Q2가,
    주식보상은 Q1이 최신이어서 그냥 나누면 500%가 나왔다. 양쪽 모두 값이 있는 가장 최근 분기를 쓴다.
    """
    if numerator.empty or denominator.empty:
        return None, None, None, None
    left = numerator[numerator["days"].isna() | (numerator["days"] <= QUARTER_MAX_DAYS)]
    right = denominator[denominator["days"].isna() | (denominator["days"] <= QUARTER_MAX_DAYS)]
    merged = left[["end", "val"]].merge(right[["end", "val"]], on="end", suffixes=("_num", "_den"))
    merged = merged[merged["val_den"] != 0]
    if merged.empty:
        return None, None, None, None
    row = merged.sort_values("end").iloc[-1]
    return float(row["val_num"]) / float(row["val_den"]), row["end"], float(row["val_num"]), float(row["val_den"])


def quarter_ends_only(frame: pd.DataFrame) -> pd.DataFrame:
    """분기말 시점만 남긴다. 설립일이나 공시 표지일 같은 중간 시점은 추이 차트에서 뺀다."""
    if frame.empty:
        return frame
    return frame[pd.to_datetime(frame["end"]).dt.is_quarter_end].reset_index(drop=True)


def with_labels(series: pd.DataFrame) -> pd.DataFrame:
    """시점 시계열에 분기 라벨을 붙인다(빈 프레임도 같은 컬럼 모양을 유지)."""
    if series.empty:
        return pd.DataFrame(columns=["end", "val", "label"])
    frame = series[["end", "val"]].copy()
    frame["label"] = frame["end"].map(sec.quarter_label)
    return frame


def latest_point(frame: pd.DataFrame):
    """병합된 시계열의 마지막 값 → (기준일, 값, 출처).

    수동 CSV 값을 무조건 앞세우면 안 된다. 10-Q가 제출되는 순간 XBRL이 같은 분기를 더 정확하게
    (8-K는 반올림값이다) 덮으므로, append_manual이 이미 정리해 둔 시계열의 끝을 읽어야 자동으로
    XBRL이 우선된다.
    """
    if frame.empty:
        return None, None, None
    row = frame.iloc[-1]
    return row["end"], float(row["val"]), row.get("reported", "SEC XBRL")


def append_manual(frame: pd.DataFrame, end, value) -> pd.DataFrame:
    """XBRL 시계열 뒤에 8-K 발표치를 이어붙인다. reported 컬럼으로 출처를 구분한다."""
    frame = frame.copy()
    if "reported" not in frame.columns:
        frame["reported"] = "SEC XBRL"
    if value is None or end is None:
        return frame
    end = pd.Timestamp(end)
    if not frame.empty and (frame["end"] >= end).any():
        return frame
    row = {"end": end, "val": float(value), "label": sec.quarter_label(end), "reported": "8-K 발표치"}
    return pd.concat([frame, pd.DataFrame([row])], ignore_index=True)


# ── 축별 지표 ──────────────────────────────────────────────────────────────────
def _safe_div(numerator, denominator):
    if numerator is None or denominator in (None, 0):
        return None
    return numerator / denominator


def compute(facts: dict, price_meta: dict) -> dict:
    """모든 축의 원시 지표를 한 번에 계산해 평평한 dict로 돌려준다."""
    manual = manual_values()
    metrics: dict = {"manual": manual, "manual_asof": manual_asof()}

    # --- 1. 자금여력 -----------------------------------------------------------
    cash_xbrl = quarter_ends_only(sec.instant_series(facts, TAG_CASH_TOTAL))
    metrics["cash_series"] = append_manual(
        with_labels(cash_xbrl), metrics["manual_asof"], manual.get("cash_total")
    )
    metrics["cash_asof"], metrics["cash_total"], metrics["cash_source"] = latest_point(metrics["cash_series"])
    metrics["asof"] = metrics["cash_asof"]
    metrics["asof_source"] = metrics["cash_source"]

    # 분기 말 이후 조달분까지 반영한 프로포마 현금. 런웨이는 이 값으로 봐야 현실에 맞는다.
    post_q = manual.get("post_q_financing_net", 0.0) + manual.get("post_q_capped_call_cost", 0.0)
    metrics["post_q_net"] = post_q
    metrics["cash_proforma"] = (metrics["cash_total"] or 0.0) + post_q

    op_cf = quarterly(facts, TAG_OP_CF)
    metrics["op_cf_series"] = op_cf
    op_latest = latest_quarter_value(op_cf)
    # 부호 있는 값과 크기를 나눠 담는다. 런웨이 계산에는 크기가 필요하지만, 흑자 전환 판정에는
    # 부호가 필요하다. 예전에는 abs()만 담아서 영업CF가 흑자로 돌아도 로드맵 5단계가
    # 영원히 '대기'로 남았다.
    metrics["op_cf_q"] = op_latest
    metrics["op_burn_q"] = abs(op_latest) if op_latest is not None else None

    revenue = quarterly(facts, TAG_REVENUE)
    if revenue.empty:
        revenue = quarterly(facts, TAG_REVENUE_ALT)
    metrics["revenue_series"] = revenue
    metrics["revenue_q"] = latest_quarter_value(revenue)

    capex = quarterly(facts, TAG_CAPEX)
    capex_full = append_manual(capex, metrics["manual_asof"], manual.get("capex_quarter"))
    metrics["capex_series"] = capex_full
    metrics["capex_q"] = latest_quarter_value(capex_full)
    # 누적에는 합산 구간까지 모두 더해야 한다. 분기 구간만 세면 2025년 Q2~Q3가 통째로 빠진다.
    metrics["capex_cumulative"] = float(capex_full["val"].sum()) if not capex_full.empty else None

    total_burn = (metrics["op_burn_q"] or 0.0) + (metrics["capex_q"] or 0.0)
    metrics["burn_q_total"] = total_burn or None
    metrics["runway_ops"] = _safe_div(metrics["cash_proforma"], metrics["op_burn_q"])
    metrics["runway_ops"] = metrics["runway_ops"] * 3 if metrics["runway_ops"] else None
    metrics["runway_total"] = _safe_div(metrics["cash_proforma"], metrics["burn_q_total"])
    metrics["runway_total"] = metrics["runway_total"] * 3 if metrics["runway_total"] else None

    # --- 2. 자본투입 -----------------------------------------------------------
    ppe_gross = quarter_ends_only(sec.instant_series(facts, TAG_PPE_GROSS))
    metrics["ppe_series"] = append_manual(
        with_labels(ppe_gross), metrics["manual_asof"], manual.get("ppe_gross")
    )
    _, metrics["ppe_gross"], metrics["ppe_source"] = latest_point(metrics["ppe_series"])
    metrics["noncash_additions"] = (
        metrics["ppe_gross"] - metrics["capex_cumulative"]
        if metrics["ppe_gross"] is not None and metrics["capex_cumulative"] is not None
        else None
    )

    cap_int = sec.periodic_series(facts, TAG_CAP_INTEREST)
    int_exp = sec.periodic_series(facts, TAG_INTEREST_EXPENSE)
    metrics["capitalized_interest_cum"] = float(cap_int["val"].sum()) if not cap_int.empty else None
    metrics["interest_expensed_cum"] = float(int_exp["val"].sum()) if not int_exp.empty else None
    total_interest = (metrics["capitalized_interest_cum"] or 0.0) + (metrics["interest_expensed_cum"] or 0.0)
    metrics["capitalized_interest_ratio"] = _safe_div(metrics["capitalized_interest_cum"], total_interest)

    # --- 3./4. 전력·계약 -------------------------------------------------------
    stages = load_csv("power_stages.csv")
    metrics["power_stages"] = stages
    def _stage_mw(keyword: str):
        if stages.empty:
            return None
        hit = stages[stages["stage"].str.contains(keyword, na=False)]
        return float(hit.iloc[0]["mw"]) if not hit.empty else None

    metrics["mw_operating"] = _stage_mw("상업 가동 중")
    metrics["mw_landed"] = _stage_mw("반입 완료")
    metrics["mw_permitted"] = _stage_mw("허가 확보")
    metrics["mw_target"] = _stage_mw("장기 목표")

    contracts = load_csv("contracts.csv")
    metrics["contracts"] = contracts
    binding = contracts[contracts["binding"] == "Y"] if not contracts.empty else contracts
    metrics["mw_contracted"] = float(binding["phase1_mw"].sum()) if not binding.empty else 0.0
    metrics["mw_contracted_option"] = float(binding["option_mw"].sum()) if not binding.empty else 0.0
    metrics["backlog_musd"] = float(binding["total_revenue_musd"].sum()) if not binding.empty else 0.0
    metrics["customer_count"] = int(len(binding))
    metrics["contracted_vs_landed"] = _safe_div(metrics["mw_contracted"], metrics["mw_landed"])
    metrics["contracted_vs_target"] = _safe_div(metrics["mw_contracted"], metrics["mw_target"])
    metrics["capex_per_landed_mw"] = _safe_div(metrics["ppe_gross"], metrics["mw_landed"])
    # 계약 단가: 총 계약금액 / 계약 MW / 계약기간. 전력인프라 리스의 실질 단가다.
    if not binding.empty:
        years = float(binding.iloc[0]["term_years"])
        metrics["revenue_per_mw_year"] = _safe_div(
            metrics["backlog_musd"] * 1e6, metrics["mw_contracted"] * years
        )
    else:
        metrics["revenue_per_mw_year"] = None

    milestones = load_csv("milestones.csv")
    metrics["milestones"] = milestones
    if not milestones.empty:
        milestones = milestones.copy()
        milestones["date"] = pd.to_datetime(milestones["date"], errors="coerce")
        metrics["milestones"] = milestones.sort_values("date")
        recent = milestones[milestones["date"] >= pd.Timestamp.today() - pd.Timedelta(days=120)]
        metrics["milestones_done_120d"] = int((recent["status"] == "달성").sum())
        metrics["milestones_upcoming"] = int((milestones["status"] == "예정").sum())

    # --- 5. 자본구조 -----------------------------------------------------------
    shares = sec.instant_series(facts, TAG_SHARES, unit="shares")
    shares_dei = sec.instant_series(facts, TAG_SHARES_DEI, unit="shares")
    combined = pd.concat([shares[["end", "val"]], shares_dei[["end", "val"]]]) if not shares.empty else shares_dei
    if not combined.empty:
        combined = combined.groupby("end", as_index=False)["val"].max().sort_values("end")
    metrics["shares_series"] = combined
    metrics["shares_out"] = float(combined.iloc[-1]["val"]) if not combined.empty else None
    metrics["shares_asof"] = combined.iloc[-1]["end"] if not combined.empty else None
    # 발행주식수는 10-Q 표지일 기준으로도 보고돼 며칠 간격 관측치가 섞인다. 바로 앞 관측치와 비교하면
    # 증가율이 0%로 나오므로, 약 한 분기 전(>=80일) 관측치를 기준으로 잡는다.
    metrics["share_growth"] = None
    metrics["share_growth_base"] = None
    if len(combined) >= 2 and metrics["shares_asof"] is not None:
        cutoff = pd.Timestamp(metrics["shares_asof"]) - pd.Timedelta(days=80)
        earlier = combined[combined["end"] <= cutoff]
        if not earlier.empty:
            previous = float(earlier.iloc[-1]["val"])
            metrics["share_growth"] = _safe_div(metrics["shares_out"] - previous, previous)
            metrics["share_growth_base"] = earlier.iloc[-1]["end"]

    sbc = quarterly(facts, TAG_SBC)
    gna = quarterly(facts, TAG_GNA)
    metrics["sbc_series"], metrics["gna_series"] = sbc, gna
    metrics["gna_q"] = latest_quarter_value(gna)
    ratio, ratio_end, sbc_matched, gna_matched = matched_quarter_ratio(sbc, gna)
    metrics["sbc_ratio"] = ratio
    metrics["sbc_ratio_asof"] = ratio_end
    metrics["sbc_q"] = sbc_matched
    metrics["gna_q_matched"] = gna_matched

    debt = quarter_ends_only(debt_series(facts))
    debt_full = append_manual(with_labels(debt), metrics["manual_asof"], manual.get("debt_total"))
    metrics["debt_series"] = debt_full
    _, metrics["debt_total"], metrics["debt_source"] = latest_point(debt_full)

    events = load_csv("capital_events.csv")
    metrics["capital_events"] = events
    # 분기 후 발행한 전환사채는 아직 재무제표에 없다. 프로포마 부채에는 더해야 한다.
    post_q_notes = 0.0
    if not events.empty:
        post = events[events["period"] == "2026 Q3"]
        post_q_notes = float(post["gross_musd"].sum()) * 1e6 if not post.empty else 0.0
    metrics["debt_proforma"] = (metrics["debt_total"] or 0.0) + post_q_notes
    metrics["net_debt_proforma"] = metrics["debt_proforma"] - metrics["cash_proforma"]

    equity = sec.instant_series(facts, TAG_EQUITY)
    metrics["equity_series"] = equity
    metrics["equity"] = float(equity.iloc[-1]["val"]) if not equity.empty else None
    metrics["equity_asof"] = equity.iloc[-1]["end"] if not equity.empty else None
    metrics["debt_to_equity"] = _safe_div(metrics["debt_proforma"], metrics["equity"])
    metrics["bvps"] = _safe_div(metrics["equity"], metrics["shares_out"])

    # 전환사채 잠재 희석. capped call이 상한가까지는 희석을 막지만, 그 위로는 유효하다.
    conv_price, conv_cap = 9.52, 14.64
    metrics["conv_price"], metrics["conv_cap"] = conv_price, conv_cap
    metrics["conv_shares"] = _safe_div(post_q_notes, conv_price)
    metrics["conv_dilution"] = _safe_div(metrics["conv_shares"], metrics["shares_out"])

    metrics["assets"] = (sec.latest_instant(facts, TAG_ASSETS) or (None, None))[1]
    metrics["liabilities"] = (sec.latest_instant(facts, TAG_LIABILITIES) or (None, None))[1]
    metrics["purchase_obligation"] = (sec.latest_instant(facts, TAG_PURCHASE_OBLIGATION) or (None, None))[1]

    # --- 6. 지배구조 -----------------------------------------------------------
    filings = sec.load_filings()
    metrics["filings"] = filings
    if not filings.empty:
        window = filings[filings["filed"] >= pd.Timestamp.today() - pd.Timedelta(days=90)]
        metrics["filings_90d"] = window
        metrics["proxy_filings_90d"] = int((window["group"] == "위임장·주주총회").sum())
        metrics["insider_filings_90d"] = int((window["group"] == "내부자 거래").sum())
    else:
        metrics["filings_90d"] = filings
        metrics["proxy_filings_90d"] = 0
        metrics["insider_filings_90d"] = 0

    # --- 7. 시장 --------------------------------------------------------------
    price = price_meta.get("regularMarketPrice")
    metrics["price"] = price
    metrics["price_currency"] = price_meta.get("currency", "USD")
    metrics["week52_high"] = price_meta.get("fiftyTwoWeekHigh")
    metrics["week52_low"] = price_meta.get("fiftyTwoWeekLow")
    metrics["market_cap"] = price * metrics["shares_out"] if price and metrics["shares_out"] else None
    metrics["pbr"] = _safe_div(metrics["market_cap"], metrics["equity"])
    metrics["ev"] = (
        metrics["market_cap"] + metrics["net_debt_proforma"] if metrics["market_cap"] is not None else None
    )
    metrics["ev_per_contracted_mw"] = _safe_div(metrics["ev"], metrics["mw_contracted"])
    metrics["ev_per_target_mw"] = _safe_div(metrics["ev"], metrics["mw_target"])
    return metrics


# ── 신호등 ─────────────────────────────────────────────────────────────────────
def _band(value, good, warning, serious, higher_is_better=True):
    if value is None:
        return "info"
    if higher_is_better:
        if value >= good:
            return "good"
        if value >= warning:
            return "warning"
        if value >= serious:
            return "serious"
        return "critical"
    if value <= good:
        return "good"
    if value <= warning:
        return "warning"
    if value <= serious:
        return "serious"
    return "critical"


def reference_cards(m: dict) -> list[dict]:
    """참고 지표.

    처음에는 이 항목들도 핵심 판정에 썼지만, 섹터 13개사로 검증해 보니 생존/붕괴를 가르지 못했다
    (근거는 data/axis_validation.csv와 sector.py). 지워버리면 맥락을 잃으므로 판정에서 내리고
    참고로만 남긴다. 각 카드의 demoted에 왜 내렸는지를 담는다.
    """
    t = THRESHOLDS
    cards = []

    runway = m.get("runway_total")
    cards.append({
        "axis": "자금여력 (런웨이)",
        "status": _band(runway, t["runway_good"], t["runway_warning"], t["runway_serious"]),
        "headline": f"{runway:.1f}개월" if runway else "산출 불가",
        "caption": "프로포마 현금 ÷ (분기 영업소진 + 분기 capex) × 3",
        "rule": f"{t['runway_good']}개월↑ 양호 · {t['runway_warning']}↑ 관찰 · {t['runway_serious']}↑ 주의 · 미만 경고",
        "note": "capex는 회사가 속도를 조절할 수 있는 지출이라, 운영만 기준 런웨이는 훨씬 길다.",
        "demoted": "검증 결과 판별력 없음 — 유지 그룹 평균 9.0개월 vs 붕괴 그룹 7.2개월. "
                   "Cheniere는 최대 지출 해에 런웨이 2.8개월이었으나 생존했다.",
    })

    ratio = m.get("capitalized_interest_ratio")
    cards.append({
        "axis": "자본투입 (이자 자본화)",
        "status": "info",
        "headline": f"이자 자본화 {ratio*100:.0f}%" if ratio else "산출 불가",
        "caption": "건설기간 이자를 자산으로 넘긴 비중 — 손익에는 안 잡히는 금융비용",
        "rule": "판정하지 않음 — 검증에서 신호가 아님이 확인됐다",
        "note": "비중이 높을수록 순손실이 실제 자금 부담보다 작아 보인다는 사실 자체는 유효하다.",
        "demoted": "검증 결과 판별력 없음 — 유지 그룹 누적 $1,251M vs 붕괴 그룹 $31M. "
                   "유지 그룹이 40배 많은 것은 프로젝트가 크기 때문이며, 규모의 반영일 뿐이다.",
    })

    # 계약 커버리지와 전력 진척은 검증을 통과해 핵심 판정으로 올라갔다(sector.fermi_position).
    # 여기 남기면 같은 내용이 두 번 나오므로 참고 카드에서는 뺀다.
    growth = m.get("share_growth")
    cards.append({
        "axis": "자본구조 (희석)",
        "status": _band(growth, 0.02, t["share_growth_qoq"], 0.25, higher_is_better=False),
        "headline": f"주식수 {growth*100:+.1f}%" if growth is not None else "산출 불가",
        "caption": "약 한 분기 전 보고 시점 대비 발행주식수 변화",
        "rule": f"2%↓ 양호 · {t['share_growth_qoq']*100:.0f}%↓ 관찰 · 25%↓ 주의 · 초과 경고",
        "note": f"전환사채 전액 전환 시 추가 희석 {m['conv_dilution']*100:.1f}% (전환가 ${m['conv_price']})"
                if m.get("conv_dilution") else "",
        "demoted": "검증 결과 약함 — 방향성은 있으나(유지 차입 69% vs 붕괴 51%) New Fortress가 "
                   "차입 83.6%로 조달하고도 붕괴했다. 희석은 계약 확보 실패의 흔적이지 원인이 아니다.",
    })

    proxy = m.get("proxy_filings_90d", 0)
    cards.append({
        "axis": "지배구조 (공시)",
        "status": _band(proxy, 0, 2, t["proxy_filings_90d"], higher_is_better=False),
        "headline": f"위임장 관련 공시 {proxy}건 / 90일",
        "caption": f"내부자 거래 신고 {m.get('insider_filings_90d', 0)}건",
        "rule": f"0건 양호 · 2건↓ 관찰 · {t['proxy_filings_90d']}건↓ 주의 · 초과 경고",
        "note": "DFAN14A가 몰리면 위임장 대결이 진행 중이라는 뜻이다.",
        "demoted": "섹터 표본으로는 검증하지 못했다 — 공시 건수는 회사마다 편차가 커 비교가 어렵다. "
                   "다만 경영권 분쟁이 자금조달·계약 협상을 지연시킨다는 경로 자체는 유효하다.",
    })

    pbr = m.get("pbr")
    cards.append({
        "axis": "시장 반영 (P/B)",
        "status": "info",
        "headline": f"P/B {pbr:.2f}배" if pbr else "산출 불가",
        "caption": "매출이 없어 이익 기반 배수는 계산되지 않는다. 자산 기반 배수만 의미가 있다.",
        "rule": "판정하지 않음 — 비교 가능한 동종 개발단계 표본이 없다",
        "note": "",
        "demoted": "밸류에이션은 결과이지 펀더멘탈이 아니다. 참고로만 둔다.",
    })
    return cards


# ── 표시 형식 ──────────────────────────────────────────────────────────────────
def usd(value, digits: int = 1) -> str:
    """달러 금액을 백만/십억 단위로. None이면 대시."""
    if value is None or pd.isna(value):
        return "–"
    absolute = abs(value)
    if absolute >= 1e9:
        return f"${value/1e9:,.{digits}f}B"
    if absolute >= 1e6:
        return f"${value/1e6:,.{digits}f}M"
    if absolute >= 1e3:
        return f"${value/1e3:,.0f}K"
    return f"${value:,.0f}"


def pct(value, digits: int = 1) -> str:
    if value is None or pd.isna(value):
        return "–"
    return f"{value*100:,.{digits}f}%"


def num(value, digits: int = 0, suffix: str = "") -> str:
    if value is None or pd.isna(value):
        return "–"
    return f"{value:,.{digits}f}{suffix}"
