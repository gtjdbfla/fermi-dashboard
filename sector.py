"""전력·에너지 인프라 섹터 검증.

이 모듈의 존재 이유: 펀더멘탈 항목을 그냥 고르지 않고, **실제로 결과를 갈랐는지 검증한 뒤** 남긴다.

매출보다 먼저 대규모 인프라를 지은 상장사 13곳을 놓고, 결과를 주가가 아니라 객관적 재무 사건으로
유지/진행중/붕괴로 나눈 다음(주가로 나누면 순환논증이 된다), 항목별 값을 두 그룹에 대조했다.
그 결과 처음 세웠던 7개 축 중 3개는 판별력이 없었다(data/axis_validation.csv).

살아남은 것은 두 개다.
  ① 계약 커버리지 — 계약된 수요가 자본 투입에 선행하고, 계약 기간이 부채 만기를 덮는가
  ② 영업현금흐름 전환 — 가동 후 현금이 실제로 들어오기 시작하는가 (그리고 되돌아가지 않는가)
"""

from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pandas as pd
import requests
import streamlit as st

import diskcache as dc

DATA_DIR = Path(__file__).parent / "data"

GROUP_ORDER = ["유지", "진행중", "붕괴"]
# T0 판정: 연간 capex가 이 금액을 넘긴 첫 해. 그 수준에 도달하지 못한 회사는
# 영업 소진이 이 금액을 넘긴 첫 해로 대신한다(자본을 못 넣고 관리비만 태운 경우).
T0_CAPEX_THRESHOLD = 100e6
T0_BURN_FALLBACK = 50e6


CACHE_DIR = DATA_DIR / ".cache"


def _table(name: str) -> pd.DataFrame:
    """크론이 갱신한 `.cache/` 사본을 먼저 보고, 없으면 저장소에 커밋된 기준선을 쓴다.

    서버가 `data/*.csv`를 직접 고치면 배포가 쓰는 `git pull --ff-only`가 깨진다. 그래서
    자동 갱신은 `.cache/`에만 쓰고(추적 대상이 아니다), 저장소 파일은 사람이 커밋한
    기준선으로 남긴다. 크론이 죽어도 화면은 기준선으로 계속 돈다.
    """
    for path in (CACHE_DIR / name, DATA_DIR / name):
        if path.exists():
            return pd.read_csv(path)
    return pd.DataFrame()


@st.cache_data(ttl=3600, show_spinner=False)
def load_annuals() -> pd.DataFrame:
    return _table("sector_annuals.csv")


@st.cache_data(ttl=3600, show_spinner=False)
def load_prices() -> pd.DataFrame:
    return _table("sector_prices.csv")


@st.cache_data(ttl=86400, show_spinner=False)
def load_profiles() -> pd.DataFrame:
    path = DATA_DIR / "sector_profiles.csv"
    return pd.read_csv(path) if path.exists() else pd.DataFrame()


@st.cache_data(ttl=86400, show_spinner=False)
def load_axis_validation() -> pd.DataFrame:
    path = DATA_DIR / "axis_validation.csv"
    return pd.read_csv(path) if path.exists() else pd.DataFrame()


def detect_t0(frame: pd.DataFrame) -> int | None:
    """대규모 자본 투입을 확정한 해. LNG 기업에서는 최종투자결정(FID) 연도와 일치한다."""
    heavy = frame[frame["capex"].fillna(0) >= T0_CAPEX_THRESHOLD]
    if not heavy.empty:
        return int(heavy["fy"].min())
    burning = frame[frame["op_cf"].fillna(0) <= -T0_BURN_FALLBACK]
    return int(burning["fy"].min()) if not burning.empty else None


def opcf_turn(frame: pd.DataFrame) -> int | None:
    """영업현금흐름이 흑자로 돌아선 뒤 되돌아가지 않은 첫 해.

    한 해 반짝 흑자는 전환이 아니다. New Fortress는 2021~2024년 흑자였다가 2025년에 -$583M으로
    되돌아갔고, 그것이 붕괴의 신호였다. 그래서 '이후 연도가 모두 흑자'인 해만 전환으로 본다.
    """
    known = frame.dropna(subset=["op_cf"]).sort_values("fy")
    for index in range(len(known)):
        rest = known.iloc[index:]
        if (rest["op_cf"] > 0).all():
            return int(rest.iloc[0]["fy"])
    return None


@st.cache_data(ttl=86400, show_spinner=False)
def summary() -> pd.DataFrame:
    """기업별 한 줄 요약: 그룹 · T0 · 흑자 전환 연도 · 계약 커버리지 · 주가 결과."""
    annuals, profiles, prices = load_annuals(), load_profiles(), load_prices()
    if annuals.empty or profiles.empty:
        return pd.DataFrame()

    rows = []
    for ticker, group in annuals.groupby("ticker"):
        group = group.sort_values("fy")
        profile = profiles[profiles["ticker"] == ticker]
        if profile.empty:
            continue
        profile = profile.iloc[0]
        t0 = detect_t0(group)
        turn = opcf_turn(group)
        price = prices[prices["ticker"] == ticker]
        rows.append({
            "ticker": ticker,
            "company": profile["company"],
            "sub": profile["sub"],
            "group": profile["group"],
            "coverage": profile.get("contract_coverage_pct"),
            "t0": t0,
            "opcf_turn": turn,
            "years_to_turn": (turn - t0) if (t0 and turn and turn >= t0) else None,
            "from_peak_pct": float(price.iloc[0]["from_peak_pct"]) if not price.empty else None,
            "max_drawdown_pct": float(price.iloc[0]["max_drawdown_pct"]) if not price.empty else None,
            "outcome_basis": profile["outcome_basis"],
            "what_broke": profile.get("what_broke"),
        })
    frame = pd.DataFrame(rows)
    frame["group"] = pd.Categorical(frame["group"], categories=GROUP_ORDER, ordered=True)
    return frame.sort_values(["group", "from_peak_pct"], ascending=[True, False]).reset_index(drop=True)


@st.cache_data(ttl=3600, show_spinner=False)
def load_price_history() -> pd.DataFrame:
    return _table("sector_price_history.csv")


# 페르미는 2025년에 대규모 자본 투입을 시작했고 지금은 그 다음 해다. 각 기업의 같은 지점은 T0+1년차.
FERMI_T0 = 2025
FERMI_STAGE_OFFSET = 1


@st.cache_data(ttl=86400, show_spinner=False)
def stage_matched(offset: int = FERMI_STAGE_OFFSET) -> pd.DataFrame:
    """각 기업이 '지금의 페르미'와 같은 단계에 있던 해를 찾고, 그때부터 지금까지의 주가를 잰다.

    연도를 그대로 비교하면 의미가 없으므로 T0(대규모 자본 투입을 확정한 해)로 정렬한 뒤
    같은 상대 연차를 고른다. 보유 기간이 제각각이라 총수익률만 보면 오래 보유한 쪽이 유리해
    보이므로 연환산 수익률을 함께 낸다.

    시세가 없는 해는 억지로 채우지 않는다. 상장폐지·회생·비상장이 이유인데, 그 사실 자체가
    결과를 말해준다(profiles의 price_gap_reason에 남긴다).
    """
    base, profiles, history = summary(), load_profiles(), load_price_history()
    prices = load_prices()
    if base.empty or history.empty:
        return pd.DataFrame()

    annuals = load_annuals()
    latest_year = int(history["year"].max())
    rows = []
    for _, item in base.iterrows():
        if pd.isna(item["t0"]):
            continue
        ticker, matched_year = item["ticker"], int(item["t0"]) + offset
        profile = profiles[profiles["ticker"] == ticker]
        profile = profile.iloc[0] if not profile.empty else {}

        series = history[history["ticker"] == ticker]
        then_row = series[series["year"] == matched_year]
        now_row = series[series["year"] == latest_year]
        then = float(then_row.iloc[0]["close"]) if not then_row.empty else None
        # 경과 기간은 실제 관측 월로 잰다. 연도 차이로 근사하면 12월 종가와 8월 종가를 비교할 때
        # 최대 1년 가까이 부풀려져 연환산 수익률이 낮게 나온다.
        then_asof = pd.Period(then_row.iloc[0]["asof"], freq="M") if not then_row.empty else None
        now_asof = pd.Period(now_row.iloc[0]["asof"], freq="M") if not now_row.empty else None

        # T0+1년차에 아직 상장 전이었거나 회생 중이던 기업은 그 해 주가가 없다. 그런 곳은
        # 상장(또는 재상장) 첫 관측치부터 잰다. 다만 출발점이 다르므로 반드시 표시해야 한다 —
        # Talen과 Core Scientific은 파산 직후 재상장이라 바닥에서 시작해 수익률이 크게 잡힌다.
        basis, basis_asof = "T0+1년차", (str(then_asof) if then_asof is not None else None)
        if then is None:
            fallback = prices[prices["ticker"] == ticker] if not prices.empty else pd.DataFrame()
            if not fallback.empty and pd.notna(fallback.iloc[0].get("first_close")):
                then = float(fallback.iloc[0]["first_close"])
                then_asof = pd.Period(str(fallback.iloc[0]["first_asof"]), freq="M")
                basis, basis_asof = "상장/재상장 후", str(then_asof)
        # 상장폐지된 곳은 마지막 거래가 대신 인수가를 쓴다(프로필에 근거를 적어 둔다).
        final_price = profile.get("final_price") if hasattr(profile, "get") else None
        now = float(final_price) if pd.notna(final_price) else (
            float(now_row.iloc[0]["close"]) if not now_row.empty else None)

        revenue = annuals[(annuals["ticker"] == ticker) & (annuals["fy"] == matched_year)]
        revenue = float(revenue.iloc[0]["revenue"]) if not revenue.empty and pd.notna(
            revenue.iloc[0]["revenue"]) else None

        years = ((now_asof - then_asof).n / 12) if (then_asof is not None and now_asof is not None) else None
        # **기간이 0이면 수익률은 '0%'가 아니라 '아직 없다'이다.** T0가 올해로 잡히는
        # 기업은 시작점과 현재가 같은 달이라 multiple이 1.0으로 떨어지고, 그대로 두면
        # 화면에 "총수익률 0.0%"가 측정 결과처럼 뜬다. 실제로 OKLO가 그랬다 — T0가
        # 2026년이라 관측 창의 길이가 0인데 수익률이 0%로 보였다.
        measurable = years is not None and years > 0
        multiple = (now / then) if (then and now and measurable) else None
        cagr = ((multiple ** (1 / years) - 1) * 100) if (
            multiple and multiple > 0 and years and years > 0) else None
        # profile은 pandas Series라 빈 칸이 None이 아니라 NaN으로 온다. **NaN은 truthy라서**
        # `not profile.get(...)`로 검사하면 항상 '사유가 있다'로 판정된다.
        reason = profile.get("price_gap_reason") if hasattr(profile, "get") else None
        if isinstance(reason, float) and pd.isna(reason):
            reason = None
        if reason is None and not measurable and then and now:
            reason = (f"T0가 {matched_year}년이라 비교 구간의 길이가 아직 0이다"
                      f"({then_asof} 시작). 결과가 나오기 전이지 수익률이 0인 것이 아니다.")

        rows.append({
            "ticker": ticker, "company": item["company"], "group": str(item["group"]),
            "t0": int(item["t0"]), "matched_year": matched_year,
            "revenue_then": revenue,
            "price_then": then, "price_now": now,
            "basis": basis, "basis_asof": basis_asof,
            "multiple": multiple,
            "years_held": round(years, 1) if years else None,
            "total_return_pct": (multiple - 1) * 100 if multiple else None,
            "cagr_pct": cagr,
            "gap_reason": reason,
        })
    frame = pd.DataFrame(rows)
    frame["group"] = pd.Categorical(frame["group"], categories=GROUP_ORDER, ordered=True)
    return frame.sort_values(["group", "cagr_pct"], ascending=[True, False]).reset_index(drop=True)


CAPS_MAX_AGE = 46800  # 크론 느린층(하루 2회) 간격을 견디게. 시총은 일 단위면 충분하다.


@st.cache_data(ttl=3600, show_spinner=False)
def market_caps(force: bool = False) -> dict:
    """티커별 현재 시가총액. Nasdaq 공개 API에서 받는다.

    주가 × 발행주식수로 계산하지 않는 이유: XBRL 발행주식수가 비어 있는 기업이 있고
    (Venture Global, NuScale), 액면병합을 겪은 곳은 시점이 어긋난다. 시가총액은 결과값이라
    출처에서 그대로 받는 편이 안전하다.
    """
    if not force:
        cached = dc.load_json("market_caps", CAPS_MAX_AGE)
        if cached:
            return cached
    headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                             "AppleWebKit/537.36 Chrome/120.0 Safari/537.36",
               "Accept": "application/json"}

    def one(ticker: str):
        try:
            response = requests.get(f"https://api.nasdaq.com/api/quote/{ticker}/summary",
                                    params={"assetclass": "stocks"}, headers=headers, timeout=10)
            raw = ((response.json().get("data") or {}).get("summaryData") or {}) \
                .get("MarketCap", {}).get("value")
            value = float(str(raw).replace(",", "")) if raw else None
            return ticker, (value or None)
        except Exception:
            return ticker, None

    # Nasdaq은 한 번에 2~3초씩 걸린다. 13종목을 순차로 부르면 33초였고, 그 시간을 어느 탭을
    # 보든 매번 물었다(Streamlit은 모든 탭 코드를 실행한다). 동시에 부르면 가장 느린 하나로 줄어든다.
    tickers = list(load_profiles().get("ticker", pd.Series(dtype=str)))
    with ThreadPoolExecutor(max_workers=8) as pool:
        caps = dict(pool.map(one, tickers))
    if any(caps.values()):
        dc.save_json("market_caps", caps)
    return caps


@st.cache_data(ttl=3600, show_spinner=False)
def company_view() -> pd.DataFrame:
    """기업 하나당 한 행. 지표별로 흩어진 표를 합쳐 기업 단위로 읽게 한다.

    정렬은 총수익률 내림차순이다. 주가가 많이 오른 곳부터 훑으면 '무엇을 한 곳이 올랐나'가
    바로 보인다. 시세를 구하지 못한 기업은 맨 뒤로 보낸다(상장폐지·회생·비상장이 이유이고,
    그 사실 자체는 gap_reason에 남아 있다).
    """
    base, stage, profiles = summary(), stage_matched(), load_profiles()
    if base.empty:
        return pd.DataFrame()

    frame = base.merge(
        stage[["ticker", "matched_year", "revenue_then", "price_then", "price_now", "basis",
               "basis_asof", "multiple", "years_held", "total_return_pct", "cagr_pct", "gap_reason"]],
        on="ticker", how="left")
    if not profiles.empty:
        frame = frame.merge(profiles[["ticker", "contract_detail"]], on="ticker", how="left")
    frame["market_cap"] = frame["ticker"].map(market_caps())
    frame["has_price"] = frame["total_return_pct"].notna()
    return frame.sort_values(["has_price", "total_return_pct"],
                             ascending=[False, False]).reset_index(drop=True)


def coverage_benchmark() -> dict:
    """계약 커버리지의 그룹별 관측 범위. 페르미를 놓을 눈금이 된다."""
    frame = summary()
    out = {}
    for name in GROUP_ORDER:
        values = frame[(frame["group"] == name)]["coverage"].dropna()
        if not values.empty:
            out[name] = (float(values.min()), float(values.max()))
    return out


def _maturity_card() -> dict:
    """판정 ②는 계산해서 채운다. 예전에는 "리스 15년 vs 사채 2031년"이 문자열로 박혀 있어,
    새 사채가 발행되거나 리스 조건이 바뀌어도 화면이 그대로였다."""
    import maturity as mt
    return mt.verdict({})


def fermi_position(m: dict) -> list[dict]:
    """검증에서 살아남은 세 지표에 페르미를 놓는다."""
    contracted = m.get("mw_contracted") or 0
    landed = m.get("mw_landed") or 0
    coverage = (contracted / landed * 100) if landed else None
    benchmark = coverage_benchmark().get("유지")

    items = [{
        "label": "① 계약이 자본 투입에 선행했는가",
        "status": "critical",
        "value": f"반입 설비의 {coverage:.0f}%" if coverage is not None else "산출 불가",
        "detail": f"$1.2B 투입 후 첫 계약 체결. 유지 그룹 관측 범위는 "
                  f"{benchmark[0]:.0f}~{benchmark[1]:.0f}%" if benchmark else "",
        "verdict": "미달 — 투입과 계약의 순서가 반대다",
    }, {
        "label": "② 계약 기간이 부채 만기를 덮는가",
        **{k: v for k, v in _maturity_card().items() if k != "gap_years"},
    }]

    # 전환까지 걸린 연수는 표본에서 직접 뽑는다. 숫자를 문장에 박아두면 표본을 갱신했을 때
    # 화면과 근거가 어긋난다.
    frame = summary()
    turns = frame.dropna(subset=["years_to_turn"])
    if not turns.empty:
        longest = turns.loc[turns["years_to_turn"].idxmax()]
        detail = (f"유지 그룹에서 가장 오래 걸린 곳은 {longest['company']}로 "
                  f"{int(longest['years_to_turn'])}년이었다. 붕괴 그룹은 전환에 도달한 곳이 없다. "
                  "페르미는 T0+1년차라 어느 쪽으로도 판정할 수 없다.")
    else:
        detail = "페르미는 T0+1년차라 어느 쪽으로도 판정할 수 없다."
    # 매출과 영업현금흐름은 실제 값에서 읽는다. 문구에 박아두면 첫 매출이 잡힌 뒤에도
    # "매출 0"이라고 계속 표시된다.
    revenue_q, op_cf_q = m.get("revenue_q"), m.get("op_cf_q")
    if op_cf_q is not None and op_cf_q > 0:
        status, verdict = "good", "충족 — 영업현금흐름 흑자"
    else:
        status, verdict = "info", "판정 불가 구간"
    value = f"T0+1년차 · 매출 {'$%.0fM' % (revenue_q / 1e6) if revenue_q else '0'}"
    items.append({
        "label": "③ 영업현금흐름 전환",
        "status": status,
        "value": value,
        "detail": detail,
        "verdict": verdict,
    })
    return items
