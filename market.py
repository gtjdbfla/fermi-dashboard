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
    return frame.reset_index(drop=True), result.get("meta", {})
