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
    "뉴스 정리(AI)": 5400,
    "공시 판독(AI)": 5400,
    "AI 인프라 바스켓": 90000,
    "공매도·기관·내부자": 90000,
    "섹터 시가총액": 90000,
    "섹터 표본 13개사": 259200,
    "애널리스트 액션": 5400,
    "애널리스트 정리(AI)": 5400,
    "애널리스트 컨센서스": 90000,
    "증권사 등급표": 90000,
}

# 탭마다 어떤 데이터를 쓰는지. 화면 상단에 그 탭 것만 짧게 보여준다.
TAB_SOURCES = {
    "contract": ["계약·용량 수치", "공시 피드", "알림(텔레그램)"],
    "cashflow": ["페르미 재무제표(XBRL)", "계약·용량 수치"],
    "roadmap": ["페르미 재무제표(XBRL)", "계약·용량 수치", "공시 피드"],
    "sector": ["섹터 표본 13개사", "섹터 시가총액"],
    "flow": ["AI 인프라 바스켓", "공매도·기관·내부자", "주가"],
    "news": ["뉴스·커뮤니티", "뉴스 정리(AI)", "공시 피드"],
    "analyst": ["애널리스트 컨센서스", "증권사 등급표", "애널리스트 액션", "애널리스트 정리(AI)", "주가"],
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


def _sector_age() -> float | None:
    """크론이 갱신한 캐시본을 먼저 본다. 없으면 저장소 커밋본의 파일 시각."""
    cached = DATA_DIR / ".cache" / "sector_annuals.csv"
    return _file_age(cached if cached.exists() else DATA_DIR / "sector_annuals.csv")


def rows(m: dict, price_frame: pd.DataFrame) -> pd.DataFrame:
    """계층별 (구분, 데이터, 최신 시점, 경과, 갱신 주기, 상태)."""
    records = []

    def add(tier, name, asof, age, cadence):
        limit = STALE_AFTER.get(name)
        late = age is not None and limit is not None and age > limit
        records.append({
            "구분": tier,
            "데이터": name,
            "최신 시점": asof or "–",
            "경과": _ago(age),
            "갱신 주기": cadence,
            "상태": "⚠️ 지연" if late else ("· 없음" if age is None and asof == "–" else "✅"),
        })

    # ── 접속할 때마다 ─────────────────────────────────────────────────────────
    # 조회 시각이 아니라 데이터 자체의 최신 시점을 쓴다. 5분 전에 받았어도 값은 어제 종가다.
    last_bar = None
    if price_frame is not None and not price_frame.empty:
        last_bar = str(pd.Timestamp(price_frame.iloc[-1]["date"]).date())
    add("실시간", "주가", last_bar, None, "5분 캐시 · 원본 일봉")
    filings = m.get("filings")
    add("실시간", "공시 피드",
        str(filings.iloc[0]["filed"].date()) if filings is not None and not filings.empty else None,
        None, "30분 캐시")
    add("실시간", "페르미 재무제표(XBRL)",
        str(pd.Timestamp(m["asof"]).date()) if m.get("asof") is not None else None,
        None, "10-Q/10-K 제출 시 자동")

    # ── 크론 빠른층(30분) ─────────────────────────────────────────────────────
    add("30분", "뉴스·커뮤니티", None, dc.age_seconds("articles", "json"), "크론 30분")
    add("30분", "공시 판독(AI)", None, dc.age_seconds("filing_review", "json"), "새 공시 있을 때")
    add("30분", "뉴스 정리(AI)", None, dc.age_seconds("ai_review", "json"),
        "새 기사 있을 때 · 최소 2시간 간격")
    add("30분", "애널리스트 액션", None, dc.age_seconds("analyst_headlines", "frame.json"),
        "크론 30분 · 뉴스 제목에서 추출")
    add("30분", "애널리스트 정리(AI)", None, dc.age_seconds("analyst_review", "json"),
        "자료 바뀔 때 · 최소 2시간 간격")

    import alerts
    state = alerts.status()
    if not state["configured"]:
        records.append({"구분": "30분", "데이터": "알림(텔레그램)", "최신 시점": "–",
                        "경과": "꺼짐", "갱신 주기": "크론 30분", "상태": "· 미설정"})
    else:
        last = state.get("last_sent")
        records.append({
            "구분": "30분", "데이터": "알림(텔레그램)",
            "최신 시점": str(pd.Timestamp(last).date()) if last else "발송 없음",
            "경과": _ago(state.get("age")),
            "갱신 주기": f"크론 30분 · 감시 {state['watching']}건",
            "상태": "⚠️ 지연" if (state.get("age") or 0) > 5400 else "✅",
        })

    # ── 크론 느린층 — 원본이 자주 안 바뀌는 것들 ──────────────────────────────
    add("하루 2회", "AI 인프라 바스켓", None, dc.age_seconds("basket", "frame.json"),
        "09·21시 · 원본 일봉")
    add("하루 2회", "공매도·기관·내부자", None, dc.age_seconds("supply", "json"),
        "09·21시 · 원본 격주·분기 공시")
    add("하루 2회", "섹터 시가총액", None, dc.age_seconds("market_caps", "json"), "09·21시")
    add("하루 2회", "애널리스트 컨센서스", None, dc.age_seconds("analyst_consensus", "json"),
        "09·21시 · 원본 월별 갱신")
    add("하루 2회", "증권사 등급표", None, dc.age_seconds("analyst_ratings", "frame.json"),
        "09·21시 · Finviz")
    add("하루 1회", "섹터 표본 13개사", None, _sector_age(), "07시 · 원본 연간 공시·일봉")

    # ── 사람이 확정하는 계층 ──────────────────────────────────────────────────
    # 계약 MW는 8-K 본문을 읽어야 나온다. AI 판독은 자동으로 돌지만 숫자 확정은 사람이 한다 —
    # 옵션을 계약으로 잘못 읽으면 핵심 판정 ①이 통째로 틀어진다.
    add("사람 확정", "계약·용량 수치",
        str(pd.Timestamp(m["staleness_asof"]).date()) if m.get("staleness_asof") is not None else None,
        None, "새 8-K 감지는 자동 · 반영은 커밋")
    # AI 호출량 — 무료 한도는 모델별로 잡히고, stock_dashboard와 키를 공유하면 같이 깎인다.
    # 얼마나 쓰고 있는지 보이지 않으면 429가 날 때까지 모른다.
    import ai_review
    used = ai_review.usage()
    if used:
        records.append({
            "구분": "참고", "데이터": "AI 호출(오늘)",
            "최신 시점": " · ".join(f"{k.split('/')[-1]} {v}회" for k, v in used.items()),
            "경과": "–", "갱신 주기": f"주 {ai_review.MODEL} / 보조 {ai_review.FALLBACK_MODEL}",
            "상태": "✅",
        })

    return pd.DataFrame(records)


@st.cache_data(ttl=60, show_spinner=False)
def worst_age() -> tuple[str, float] | None:
    """가장 오래된 크론 캐시. 헤더에 한 줄로 요약할 때 쓴다."""
    ages = {
        "뉴스·커뮤니티": dc.age_seconds("articles", "json"),
        "AI 인프라 바스켓": dc.age_seconds("basket", "frame.json"),
        "공매도·기관·내부자": dc.age_seconds("supply", "json"),
        "섹터 시가총액": dc.age_seconds("market_caps", "json"),
        "섹터 표본 13개사": _sector_age(),
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
