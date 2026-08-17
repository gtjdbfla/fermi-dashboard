"""데이터가 각각 언제 갱신됐는지.

이 대시보드는 갱신 주기가 층마다 다르다 — 주가는 5분, 공시 피드는 30분, 뉴스·수급은 크론이
채우는 디스크 캐시, 수동 CSV는 사람이 고칠 때. 화면에 숫자만 있으면 그게 방금 값인지 사흘 전
값인지 알 수 없고, 크론이 조용히 멈춰도 눈치채지 못한다. 여기서 한자리에 모아 보여준다.
"""

import time
from pathlib import Path

import pandas as pd
import streamlit as st

import diskcache as dc

DATA_DIR = Path(__file__).parent / "data"

# 이 시간을 넘으면 화면에서 '지연'으로 표시한다. 갱신 주기의 3배쯤으로 잡았다.
STALE_AFTER = {
    "뉴스·커뮤니티": 5400,
    "AI 정리": 5400,
    "공시 판독": 5400,
    "AI 인프라 바스켓": 7200,
    "공매도·기관·내부자": 43200,
    "섹터 시가총액": 7200,
}


def _ago(seconds: float | None) -> str:
    if seconds is None:
        return "없음"
    minutes = seconds / 60
    if minutes < 1:
        return "방금"
    if minutes < 60:
        return f"{int(minutes)}분 전"
    hours = minutes / 60
    if hours < 48:
        return f"{hours:.1f}시간 전"
    return f"{hours/24:.1f}일 전"


def _file_age(path: Path) -> float | None:
    return (time.time() - path.stat().st_mtime) if path.exists() else None


def rows(m: dict, price_frame: pd.DataFrame) -> pd.DataFrame:
    """계층별 (항목, 최신 시점, 경과, 갱신 주기, 상태)."""
    records = []

    def add(name, asof, age, cadence):
        limit = STALE_AFTER.get(name)
        late = age is not None and limit is not None and age > limit
        records.append({
            "데이터": name,
            "최신 시점": asof or "–",
            "경과": _ago(age),
            "갱신 주기": cadence,
            "상태": "⚠️ 지연" if late else ("· 없음" if age is None and asof == "–" else "✅"),
        })

    # 실시간 계층 — 조회 시각이 아니라 데이터 자체의 최신 시점을 쓴다.
    last_bar = None
    if price_frame is not None and not price_frame.empty:
        last_bar = str(pd.Timestamp(price_frame.iloc[-1]["date"]).date())
    add("주가", last_bar, None, "5분 캐시")
    add("페르미 재무제표(XBRL)",
        str(pd.Timestamp(m["asof"]).date()) if m.get("asof") is not None else None,
        None, "10-Q/10-K 제출 시")
    filings = m.get("filings")
    add("공시 피드",
        str(filings.iloc[0]["filed"].date()) if filings is not None and not filings.empty else None,
        None, "30분 캐시")

    # 크론이 채우는 디스크 캐시 — 파일 수정 시각이 곧 갱신 시각이다.
    add("뉴스·커뮤니티", None, dc.age_seconds("articles", "json"), "크론 30분")
    add("AI 정리", None, dc.age_seconds("ai_review", "json"), "새 기사 있을 때")
    add("공시 판독", None, dc.age_seconds("filing_review", "json"), "새 공시 있을 때")
    add("AI 인프라 바스켓", None, dc.age_seconds("basket", "frame.json"), "크론 30분")
    add("공매도·기관·내부자", None, dc.age_seconds("supply", "json"), "크론 30분(공시는 격주·분기)")
    add("섹터 시가총액", None, dc.age_seconds("market_caps", "json"), "크론 30분")

    # 손으로 고치는 계층 — 파일 시각이 아니라 '무엇까지 반영했는가'가 기준이다.
    add("수동 데이터(계약·용량)",
        str(pd.Timestamp(m["staleness_asof"]).date()) if m.get("staleness_asof") is not None else None,
        None, "공시 나올 때 수동")
    add("섹터 표본 13개사", None, _file_age(DATA_DIR / "sector_annuals.csv"),
        "refresh_sector.py 수동")
    return pd.DataFrame(records)


@st.cache_data(ttl=60, show_spinner=False)
def worst_age() -> tuple[str, float] | None:
    """가장 오래된 크론 캐시. 헤더에 한 줄로 요약할 때 쓴다."""
    ages = {
        "뉴스·커뮤니티": dc.age_seconds("articles", "json"),
        "AI 인프라 바스켓": dc.age_seconds("basket", "frame.json"),
        "공매도·기관·내부자": dc.age_seconds("supply", "json"),
        "섹터 시가총액": dc.age_seconds("market_caps", "json"),
    }
    known = {k: v for k, v in ages.items() if v is not None}
    if not known:
        return None
    name = max(known, key=known.get)
    return name, known[name]


def summary_line() -> str:
    worst = worst_age()
    if worst is None:
        return "자동 수집 캐시 없음 — `refresh_news.py`를 한 번 실행하면 채워진다"
    name, age = worst
    late = age > STALE_AFTER.get(name, 5400)
    mark = "⚠️" if late else "🕒"
    return f"{mark} 자동 수집 최신 {_ago(age)} (가장 오래된 항목: {name})"
