"""시장·수급 지표.

**이건 펀더멘탈이 아니다.** 회사의 건강이 아니라 주가가 왜 움직였는지를 설명하는 층이다.
핵심 판정은 계약 커버리지와 현금흐름 전환에서 나오고, 이 모듈은 그 신호를 읽을 때 끼는
잡음을 걷어내는 데 쓴다.

상장 후 219거래일로 실측한 결과가 이 모듈의 설계 근거다.
  - AI 인프라 동종주 상관 0.39~0.45  ← 가장 강하다. 페르미는 사실상 테마 바스켓으로 거래된다
  - 광의 시장(S&P500·러셀2000) 0.28~0.33
  - 금리(10년 실질) -0.13, 천연가스 -0.05  ← 예상보다 훨씬 약하다

그래서 A축은 '바스켓을 빼고 남는 페르미 고유 움직임'을 본다. 계약 발표에 주가가 오른 것인지
그날 AI주가 다 오른 것인지 구분하지 못하면 공시와 주가를 잘못 엮게 된다.
"""

from concurrent.futures import ThreadPoolExecutor

import pandas as pd
import requests
import streamlit as st

import diskcache as dc

YAHOO_HEADERS = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                               "AppleWebKit/537.36 Chrome/120.0 Safari/537.36"}
NASDAQ_HEADERS = {**YAHOO_HEADERS, "Accept": "application/json"}

# AI 인프라 바스켓. 페르미와 같은 테마로 묶여 거래되는 종목들이다.
# 상관이 높은 순서로 실측해 골랐고, 사업 모델이 아니라 '같이 움직이는가'가 선정 기준이다.
BASKET = {
    "CORZ": "Core Scientific",
    "CRWV": "CoreWeave",
    "SMR": "NuScale",
    "OKLO": "Oklo",
    "APLD": "Applied Digital",
    "NBIS": "Nebius",
}
ROLLING_WINDOW = 60


def _daily(ticker: str, period: str = "2y") -> pd.DataFrame:
    try:
        response = requests.get(
            f"https://query1.finance.yahoo.com/v8/finance/chart/{ticker}",
            params={"range": period, "interval": "1d"}, headers=YAHOO_HEADERS, timeout=10,
        )
        result = response.json()["chart"]["result"][0]
    except Exception:
        return pd.DataFrame(columns=["date", ticker])
    frame = pd.DataFrame({
        "date": pd.to_datetime(result["timestamp"], unit="s").normalize(),
        ticker: result["indicators"]["quote"][0]["close"],
    }).dropna()
    return frame.drop_duplicates("date").reset_index(drop=True)


# 크론이 하루 2회만 채우므로 그 간격을 견딜 만큼 잡는다. 짧게 두면 캐시가 먼저 만료돼
# 화면이 직접 받아오게 되고, 느린층으로 뺀 의미가 없어진다.
# 원본이 바뀌는 주기를 보면 이래도 충분하다 — 바스켓은 일봉, 공매도는 격주, 기관 보유는 분기다.
BASKET_MAX_AGE = 46800
SUPPLY_MAX_AGE = 46800


@st.cache_data(ttl=1800, show_spinner=False)
def basket_frame(force: bool = False) -> pd.DataFrame:
    """페르미와 바스켓 구성종목의 일별 종가를 하나로 합친다. 페르미 상장일 이후만 남긴다.

    7종목을 순차로 받으면 4.3초가 걸렸다. 한 번에 받고, 결과는 디스크에도 남겨
    재기동 직후 첫 접속자가 다시 기다리지 않게 한다.
    """
    if not force:
        cached = dc.load_frame("basket", BASKET_MAX_AGE)
        if cached is not None and not cached.empty:
            cached["date"] = pd.to_datetime(cached["date"])
            return cached
    with ThreadPoolExecutor(max_workers=7) as pool:
        parts = list(pool.map(_daily, ["FRMI"] + list(BASKET)))
    frame = parts[0]
    if frame.empty:
        return frame
    for member in parts[1:]:
        if not member.empty:
            frame = frame.merge(member, on="date", how="left")
    frame = frame.sort_values("date").reset_index(drop=True)
    dc.save_frame("basket", frame)
    return frame


def theme_view(frame: pd.DataFrame) -> pd.DataFrame:
    """페르미와 바스켓을 상장일 100 기준으로 정규화한다.

    바스켓은 동일가중이다. 시총가중으로 하면 CoreWeave 하나가 지수를 지배해 '테마'가 아니라
    'CoreWeave 대비'가 되어버린다.
    """
    if frame.empty:
        return frame
    members = [t for t in BASKET if t in frame.columns and frame[t].notna().any()]
    if not members:
        return pd.DataFrame()
    view = frame[["date", "FRMI"] + members].dropna(subset=["FRMI"]).copy()
    for column in ["FRMI"] + members:
        first = view[column].dropna()
        if first.empty:
            continue
        view[column] = view[column] / first.iloc[0] * 100
    view["바스켓"] = view[members].mean(axis=1)
    return view[["date", "FRMI", "바스켓"]].rename(columns={"FRMI": "페르미"})


def theme_stats(frame: pd.DataFrame) -> dict:
    """동조도와 상대강도. 상대강도는 페르미 수익률에서 바스켓 수익률을 뺀 값이다."""
    view = theme_view(frame)
    if view.empty or len(view) < 5:
        return {}
    returns = frame[["date", "FRMI"]].copy()
    members = [t for t in BASKET if t in frame.columns and frame[t].notna().any()]
    basket_level = frame[members].mean(axis=1)
    returns["basket"] = basket_level
    changes = returns[["FRMI", "basket"]].pct_change().dropna()

    stats = {"members": members}
    if len(changes) >= 20:
        stats["corr_full"] = float(changes["FRMI"].corr(changes["basket"]))
    if len(changes) >= ROLLING_WINDOW:
        rolling = changes["FRMI"].rolling(ROLLING_WINDOW).corr(changes["basket"])
        stats["corr_rolling"] = float(rolling.iloc[-1])
        stats["corr_series"] = pd.DataFrame(
            {"date": returns["date"].iloc[1:].values, "corr": rolling.values}).dropna()

    for label, days in [("1개월", 21), ("3개월", 63)]:
        if len(view) > days:
            fermi = view["페르미"].iloc[-1] / view["페르미"].iloc[-1 - days] - 1
            basket = view["바스켓"].iloc[-1] / view["바스켓"].iloc[-1 - days] - 1
            stats[f"rs_{label}"] = (fermi - basket) * 100
            stats[f"fermi_{label}"] = fermi * 100
            stats[f"basket_{label}"] = basket * 100
    return stats


def peer_correlations(frame: pd.DataFrame) -> pd.DataFrame:
    """구성종목별 상관. 어느 종목과 가장 붙어 다니는지 본다."""
    if frame.empty:
        return pd.DataFrame()
    changes = frame.drop(columns=["date"]).pct_change().dropna(how="all")
    rows = []
    for ticker, name in BASKET.items():
        if ticker not in changes.columns:
            continue
        pair = changes[["FRMI", ticker]].dropna()
        if len(pair) < 20:
            continue
        rows.append({"종목": f"{name} ({ticker})", "상관": round(pair["FRMI"].corr(pair[ticker]), 3),
                     "관측일": len(pair)})
    return pd.DataFrame(rows).sort_values("상관", ascending=False).reset_index(drop=True)


# ── 종목 수급 ─────────────────────────────────────────────────────────────────
def _nasdaq(path: str, params: dict | None = None) -> dict:
    try:
        response = requests.get(f"https://api.nasdaq.com/api/{path}",
                                params=params or {}, headers=NASDAQ_HEADERS, timeout=10)
        return response.json().get("data") or {}
    except Exception:
        return {}


@st.cache_data(ttl=21600, show_spinner=False)
def _supply_raw(force: bool = False) -> tuple[dict, dict, dict]:
    """Nasdaq 세 곳을 한 번에 받는다. 순차로 부르면 7.7초가 그대로 쌓인다.

    공매도는 격주, 기관 보유는 분기, 내부자는 수시 공시라 6시간 캐시로 충분하다.
    """
    if not force:
        cached = dc.load_json("supply", SUPPLY_MAX_AGE)
        if cached:
            return tuple(cached)
    with ThreadPoolExecutor(max_workers=3) as pool:
        futures = [
            pool.submit(_nasdaq, "quote/FRMI/short-interest", {"assetClass": "stocks"}),
            pool.submit(_nasdaq, "company/FRMI/institutional-holdings", {"assetclass": "stocks"}),
            pool.submit(_nasdaq, "company/FRMI/insider-trades", {"assetclass": "stocks"}),
        ]
        parts = [future.result() for future in futures]
    if any(parts):
        dc.save_json("supply", parts)
    return tuple(parts)


def short_interest() -> pd.DataFrame:
    """공매도 잔고."""
    data = _supply_raw()[0]
    rows = (data.get("shortInterestTable") or {}).get("rows") or []
    if not rows:
        return pd.DataFrame(columns=["date", "shares", "avg_volume", "days_to_cover"])
    frame = pd.DataFrame([{
        "date": pd.to_datetime(row["settlementDate"], format="%m/%d/%Y", errors="coerce"),
        "shares": pd.to_numeric(str(row["interest"]).replace(",", ""), errors="coerce"),
        "avg_volume": pd.to_numeric(str(row["avgDailyShareVolume"]).replace(",", ""), errors="coerce"),
        "days_to_cover": pd.to_numeric(row.get("daysToCover"), errors="coerce"),
    } for row in rows]).dropna(subset=["date"])
    return frame.sort_values("date").reset_index(drop=True)


def ownership() -> dict:
    summary = (_supply_raw()[1].get("ownershipSummary") or {})
    return {key: (summary.get(key) or {}).get("value") for key in summary}


def insider_activity() -> pd.DataFrame:
    rows = (_supply_raw()[2].get("numberOfTrades") or {}).get("rows") or []
    if not rows:
        return pd.DataFrame()
    return pd.DataFrame([{"구분": r.get("insiderTrade"), "3개월": r.get("months3"),
                          "12개월": r.get("months12")} for r in rows])


def convertible_hedge(short_frame: pd.DataFrame, conv_shares: float | None,
                      issue_date: str = "2026-07-09") -> dict:
    """전환사채 발행 이후 늘어난 공매도가 헤지로 설명되는 몫을 추정한다.

    전환사채를 산 쪽은 보통 주식을 빌려 팔아 델타를 중립으로 맞춘다. 그래서 발행 직후 공매도가
    구조적으로 늘어난다. 이걸 하락 베팅과 섞어 읽으면 수급을 정반대로 해석하게 된다.
    초기 헤지는 통상 전환주식수의 30~60% 선이다.
    """
    if short_frame.empty or not conv_shares:
        return {}
    issued = pd.Timestamp(issue_date)
    before = short_frame[short_frame["date"] <= issued]
    after = short_frame[short_frame["date"] > issued]
    if before.empty or after.empty:
        return {}
    base, latest = float(before.iloc[-1]["shares"]), float(after.iloc[-1]["shares"])
    increase = latest - base
    return {
        "issue_date": issued,
        "before_date": before.iloc[-1]["date"], "before": base,
        "after_date": after.iloc[-1]["date"], "after": latest,
        "increase": increase,
        "conv_shares": conv_shares,
        "coverage": increase / conv_shares if conv_shares else None,
    }
