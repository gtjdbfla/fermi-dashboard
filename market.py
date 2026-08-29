"""주가 데이터. 기존 stock_dashboard와 같은 Yahoo 차트 엔드포인트를 쓴다(키 불필요)."""

import pandas as pd
import requests
import streamlit as st

CHART_URL = "https://query1.finance.yahoo.com/v8/finance/chart/{symbol}"
_HEADERS = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/120.0 Safari/537.36"}


@st.cache_data(ttl=300, show_spinner=False)
def load_price(symbol: str = "FRMI", period: str = "1y", interval: str = "1d") -> tuple[pd.DataFrame, dict]:
    """(일별 시세 DataFrame[date, close, volume], meta). 실패하면 빈 DataFrame."""
    try:
        response = requests.get(
            CHART_URL.format(symbol=symbol),
            params={"range": period, "interval": interval},
            headers=_HEADERS,
            timeout=20,
        )
        response.raise_for_status()
        result = response.json()["chart"]["result"][0]
    except Exception:
        return pd.DataFrame(columns=["date", "close", "volume"]), {}

    quote = result["indicators"]["quote"][0]
    frame = pd.DataFrame({
        "date": pd.to_datetime(result["timestamp"], unit="s"),
        "close": quote.get("close"),
        "volume": quote.get("volume"),
    }).dropna(subset=["close"])
    meta = result.get("meta", {})
    return _patch_last(frame.reset_index(drop=True), meta), meta


def _patch_last(frame: pd.DataFrame, meta: dict) -> pd.DataFrame:
    """마지막 봉이 비어 있으면 meta의 최종가로 메운다.

    **야후는 최근 봉의 close를 이따금 null로 돌려준다.** 실제로 2026-08-29 오전에는
    8/28 종가 $5.00이 들어 있었는데 같은 날 저녁에 None이 됐다. dropna가 그 행을
    버리니 마지막 값이 8/27 $5.45가 되고, 리포트가 "주가 $5.45 · 전일 -2.9%"라고
    보냈다 — 실제로는 $5.00에 -8.3%였다.

    **그럴듯해 보이는 틀린 값이라 눈으로는 못 잡는다.** 봉이 비어도 meta의
    regularMarketPrice/Time에는 값이 남아 있으므로 그걸로 메운다.
    """
    price = meta.get("regularMarketPrice")
    stamp = meta.get("regularMarketTime")
    if price is None or stamp is None:
        return frame
    try:
        when = pd.to_datetime(stamp, unit="s").normalize()
    except (TypeError, ValueError):
        return frame
    if frame.empty:
        return pd.DataFrame([{"date": when, "close": float(price), "volume": None}])
    last = pd.Timestamp(frame.iloc[-1]["date"]).normalize()
    if when <= last:
        return frame          # 봉이 최신이면 손대지 않는다
    patched = pd.concat(
        [frame, pd.DataFrame([{"date": when, "close": float(price), "volume": None}])],
        ignore_index=True)
    try:
        import diskcache as dc
        dc.record_health("주가 봉 결측 보정", 1)
    except Exception:
        pass
    return patched
