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
# 빠른층(30분)과 느린층(하루 2회)이 다르다.
STALE_AFTER = {
    "뉴스·커뮤니티": 5400,
    "AI 정리": 5400,
    "공시 판독": 5400,
    "AI 인프라 바스켓": 90000,
    "공매도·기관·내부자": 90000,
    "섹터 시가총액": 90000,
}

# 탭마다 어떤 데이터를 쓰는지. 화면 상단에 그 탭 것만 짧게 보여준다.
TAB_SOURCES = {
    "contract": ["수동 데이터(계약·용량)", "공시 피드", "계약 알림(텔레그램)"],
    "cashflow": ["페르미 재무제표(XBRL)", "수동 데이터(계약·용량)"],
    "roadmap": ["페르미 재무제표(XBRL)", "수동 데이터(계약·용량)", "공시 피드"],
    "sector": ["섹터 표본 13개사", "섹터 시가총액"],
    "flow": ["AI 인프라 바스켓", "공매도·기관·내부자", "주가"],
    "news": ["뉴스·커뮤니티", "AI 정리", "계약 알림(텔레그램)"],
    "reference": ["페르미 재무제표(XBRL)", "공시 피드", "주가"],
    "raw": ["페르미 재무제표(XBRL)"],
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
    add("AI 인프라 바스켓", None, dc.age_seconds("basket", "frame.json"), "크론 하루 2회(원본 일봉)")
    add("공매도·기관·내부자", None, dc.age_seconds("supply", "json"), "크론 하루 2회(공시 격주·분기)")
    add("섹터 시가총액", None, dc.age_seconds("market_caps", "json"), "크론 하루 2회")

    # 알림은 '언제 받아왔나'가 아니라 '감시가 살아 있나'를 봐야 한다. 조용히 죽으면
    # 아무 일도 안 일어난 것과 구분이 안 된다.
    import alerts
    state = alerts.status()
    if not state["configured"]:
        records.append({"데이터": "계약 알림(텔레그램)", "최신 시점": "–", "경과": "꺼짐",
                        "갱신 주기": "크론 30분", "상태": "· 미설정"})
    else:
        last = state.get("last_sent")
        records.append({
            "데이터": "계약 알림(텔레그램)",
            "최신 시점": str(pd.Timestamp(last).date()) if last else "발송 없음",
            "경과": _ago(state.get("age")),
            "갱신 주기": f"크론 30분 · 감시 {state['watching']}건",
            "상태": "⚠️ 지연" if (state.get("age") or 0) > 5400 else "✅",
        })

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


def tab_line(tab: str, m: dict, price_frame: pd.DataFrame) -> str:
    """그 탭이 쓰는 데이터만 골라 한 줄로. 어느 숫자가 언제 것인지 탭 안에서 바로 보이게 한다."""
    wanted = TAB_SOURCES.get(tab, [])
    if not wanted:
        return ""
    frame = rows(m, price_frame)
    parts, late = [], False
    for _, row in frame[frame["데이터"].isin(wanted)].iterrows():
        when = row["최신 시점"] if row["최신 시점"] != "–" else row["경과"]
        parts.append(f"{row['데이터']} {when}")
        late = late or row["상태"].startswith("⚠️")
    return ("⚠️ " if late else "🕒 ") + " · ".join(parts)


def summary_line() -> str:
    worst = worst_age()
    if worst is None:
        return "자동 수집 캐시 없음 — `refresh_news.py`를 한 번 실행하면 채워진다"
    name, age = worst
    late = age > STALE_AFTER.get(name, 5400)
    mark = "⚠️" if late else "🕒"
    return f"{mark} 자동 수집 최신 {_ago(age)} (가장 오래된 항목: {name})"
