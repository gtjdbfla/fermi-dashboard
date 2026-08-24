"""페르미 관련 뉴스와 커뮤니티 반응.

**뉴스는 펀더멘탈이 아니다.** 이 화면의 목적은 하나다 — 핵심 판정 ①(계약 커버리지 15%)을 바꿀
소식이 떴는지 먼저 알아채는 것. 그래서 제목에서 계약·테넌트 키워드를 뽑아 맨 위로 올린다.

무엇을 봤든 **확정은 SEC 공시로만 한다.** 뉴스와 커뮤니티 글은 틀릴 수 있고, 특히 커뮤니티는
검증되지 않은 추측이 섞인다. 대시보드의 계약 MW 숫자는 8-K를 근거로만 갱신한다.
"""

import html
import re
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from xml.etree import ElementTree

import pandas as pd
import requests
import streamlit as st

# 화면은 이 파일만 읽는다. 채우는 일은 크론이 refresh_news.py로 미리 해 둔다.
# Streamlit은 어느 탭을 보든 모든 탭 코드를 실행하므로, 여기서 직접 HTTP를 치면
# 뉴스 탭을 안 보는 사람도 그 시간을 문다. 실측 6초였다.
CACHE_DIR = Path(__file__).parent / "data" / ".cache"
ARTICLES_CACHE = CACHE_DIR / "articles.json"
COMMUNITY_CACHE = CACHE_DIR / "community.json"

UA = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                    "AppleWebKit/537.36 Chrome/120.0 Safari/537.36"}
JSON_UA = {**UA, "Accept": "application/json"}
# 소스별 실측: Google 1.9초, Yahoo 0.8초, Nasdaq 4.7초, Stocktwits 0.7초.
# 순차로 부르면 8초가 그대로 쌓이고, Streamlit은 어느 탭을 보든 모든 탭 코드를 실행하므로
# 그 8초를 매번 문다. 병렬로 부르면 가장 느린 하나(약 4.7초)로 줄어든다.
TIMEOUT = 8
SLOW_TIMEOUT = 12

# 제목에 이 단어가 있으면 해당 묶음으로 분류한다. 위에 있는 묶음이 우선한다.
KEYWORD_GROUPS = [
    ("계약·테넌트", ["tenant", "lease", "contract", "agreement", "customer", "offtake", "ppa",
                  "anchor", "binding", "megawatt", "gigawatt", " mw", " gw", "signs", "signed",
                  "expansion option", "테넌트", "계약", "임차"]),
    ("자금조달", ["offering", "notes", "convertible", "raise", "financing", "debt", "equity",
                "dilution", "capital", "증자", "사채", "조달"]),
    ("일정·리스크", ["delay", "terminate", "cancel", "lawsuit", "investigation", "probe",
                  "default", "short seller", "downgrade", "지연", "취소", "소송"]),
    ("실적·전망", ["earnings", "results", "guidance", "quarter", "revenue", "price target",
                "upgrade", "analyst", "실적", "목표주가"]),
]
PRIORITY = {name: index for index, (name, _) in enumerate(KEYWORD_GROUPS)}
GROUP_ICON = {"계약·테넌트": "🎯", "자금조달": "💰", "일정·리스크": "⚠️",
              "실적·전망": "📊", "기타": "·"}

# 야후 검색 엔드포인트(q=FRMI)는 커버리지가 얇은 종목에서 **아무 시황 기사나 돌려준다.**
# 실측: 응답 10건 중 페르미 기사 0건 — 세리에A 축구 결과, 바르셀로나 경기, 마이크론 CEO,
# 다우 선물, 하와이 헬기 추락 소송이 '페르미 뉴스'로 들어왔다. 8/24 아침 리포트가
# "기사 22건"이라고 적었는데 그중 페르미 기사는 2건이었다.
#
# **제목 필터를 전체에 걸면 안 된다.** 구글 뉴스 쪽에는 회사명 없이 회사를 가리키는
# 기사가 많고 하필 그게 중요한 것들이다 — "Mystery solved: Amazon is the prospective
# tenant...", "CEO's exit casts doubt on massive Texas data center plan",
# "This Texas billionaire's nuclear-powered data center company faces collapse".
# 확인해보니 그런 기사는 **전부 news.google.com**에서 온다. 야후와 나스닥에서는
# 하나도 안 온다. 그래서 그 두 수집기에만 걸고 구글은 건드리지 않는다.
#
# 나스닥도 종목 페이지에 '관련 뉴스'를 끼워 넣는다. 실측하면 표지 없는 10건이 전부
# 무관했다 — Energy Transfer 4건, 배당 ETF, Anthropic IPO, 고배당주 추천.
RELEVANT = re.compile(
    r"\bfermi\b|\bfrmi\b|페르미|matador|amarillo|tensorwave|neugebauer|mcintire|"
    r"trump[- ]?(branded|linked)|rick\s+perry", re.I)

# 기계가 찍어내는 시세 페이지. 표지는 있지만 읽을 내용이 없다.
# 알림의 '당일 기사' 맥락에 "FRMI 260821 7.00P Options Chain"이 대표 헤드라인으로
# 올라온 적이 있다. 어느 소스에서 오든 버린다.
BOILERPLATE = re.compile(
    r"options?\s+chain|stock community|quotes\s*&\s*news|"
    r"short\s+interest|price\s+target\s+history", re.I)


def classify(title: str) -> str:
    lowered = (title or "").lower()
    for name, words in KEYWORD_GROUPS:
        if any(word in lowered for word in words):
            return name
    return "기타"


def _frame(records: list[dict]) -> pd.DataFrame:
    columns = ["published", "title", "source", "url"]
    if not records:
        return pd.DataFrame(columns=columns)
    return pd.DataFrame(records)[columns]


@st.cache_data(ttl=900, show_spinner=False)
def google_news() -> pd.DataFrame:
    """Google 뉴스 RSS. 티커와 회사명 두 갈래로 찾아야 지역지·업계지까지 걸린다."""
    queries = ['Fermi America FRMI', '"Fermi Inc" OR "Fermi America" data center power']
    records = []
    for query in queries:
        try:
            response = requests.get("https://news.google.com/rss/search",
                                    params={"q": query, "hl": "en-US", "gl": "US", "ceid": "US:en"},
                                    headers=UA, timeout=TIMEOUT)
            root = ElementTree.fromstring(response.content)
        except Exception:
            continue
        for item in root.findall(".//item"):
            title = html.unescape(item.findtext("title") or "")
            # Google은 제목 끝에 " - 매체명"을 붙인다. 매체명을 출처로 떼어낸다.
            source = item.findtext("{http://search.yahoo.com/mrss/}source") or ""
            if not source and " - " in title:
                title, source = title.rsplit(" - ", 1)
            records.append({"published": pd.to_datetime(item.findtext("pubDate"), errors="coerce", utc=True),
                            "title": title.strip(), "source": source.strip() or "Google News",
                            "url": item.findtext("link") or ""})
    return _frame(records)


@st.cache_data(ttl=900, show_spinner=False)
def yahoo_news() -> pd.DataFrame:
    try:
        response = requests.get("https://query1.finance.yahoo.com/v1/finance/search",
                                params={"q": "FRMI", "newsCount": 20, "quotesCount": 0},
                                headers=UA, timeout=TIMEOUT)
        items = response.json().get("news") or []
    except Exception:
        return _frame([])
    return _frame([{
        "published": pd.to_datetime(item.get("providerPublishTime"), unit="s", errors="coerce", utc=True),
        "title": item.get("title", ""), "source": item.get("publisher", "Yahoo"),
        "url": item.get("link", ""),
    } for item in items if RELEVANT.search(str(item.get("title", "")))])


@st.cache_data(ttl=900, show_spinner=False)
def nasdaq_news() -> pd.DataFrame:
    try:
        response = requests.get("https://api.nasdaq.com/api/news/topic/articlebysymbol",
                                # limit=20이 기본이었는데 50으로 올리면 34건이 온다(실측).
                                # 50 위로는 더 오지 않는다.
                                params={"q": "FRMI|stocks", "offset": 0, "limit": 50},
                                headers=JSON_UA, timeout=SLOW_TIMEOUT)
        rows = ((response.json().get("data") or {}).get("rows")) or []
    except Exception:
        return _frame([])
    return _frame([{
        "published": pd.to_datetime(row.get("created"), errors="coerce", utc=True),
        "title": row.get("title", ""), "source": row.get("publisher") or "Nasdaq",
        "url": ("https://www.nasdaq.com" + row["url"]) if row.get("url", "").startswith("/") else row.get("url", ""),
    } for row in rows if RELEVANT.search(str(row.get("title", "")))])


@st.cache_data(ttl=1800, show_spinner=False)
def collect() -> pd.DataFrame:
    """세 소스를 병렬로 받아 합치고 같은 기사를 하나로 줄인다."""
    with ThreadPoolExecutor(max_workers=3) as pool:
        jobs = {"Google": pool.submit(google_news), "Yahoo": pool.submit(yahoo_news),
                "Nasdaq": pool.submit(nasdaq_news)}
        parts = []
        for name, future in jobs.items():
            got = future.result()
            # 소스 하나가 막혀도 나머지가 캐시를 갱신해 정상으로 보인다. 건수를 남긴다.
            import diskcache as _dc
            _dc.record_health(f"뉴스/{name}", len(got))
            parts.append(got)
    frame = pd.concat(parts, ignore_index=True)
    if frame.empty:
        return frame
    frame = frame.dropna(subset=["title"])
    frame = frame[frame["title"].str.strip() != ""]
    frame = frame[~frame["title"].str.contains(BOILERPLATE, na=False)]
    # 매체마다 제목 표기가 조금씩 달라 기호·공백을 지운 형태로 중복을 잡는다.
    frame["key"] = frame["title"].str.lower().str.replace(r"[^a-z0-9가-힣]", "", regex=True).str[:70]
    frame = frame.drop_duplicates("key").drop(columns="key")
    frame["group"] = frame["title"].map(classify)
    frame["priority"] = frame["group"].map(lambda g: PRIORITY.get(g, 99))
    return frame.sort_values(["priority", "published"], ascending=[True, False]).reset_index(drop=True)


@st.cache_data(ttl=900, show_spinner=False)
def community() -> pd.DataFrame:
    """Stocktwits 종목 게시글. 검증되지 않은 추측이 섞이므로 화면에서도 그렇게 표시한다."""
    try:
        response = requests.get("https://api.stocktwits.com/api/2/streams/symbol/FRMI.json",
                                headers=UA, timeout=TIMEOUT)
        messages = response.json().get("messages") or []
    except Exception:
        return pd.DataFrame(columns=["published", "body", "user", "url", "group"])
    records = []
    for message in messages:
        body = html.unescape(re.sub(r"\s+", " ", message.get("body", ""))).strip()
        username = (message.get("user") or {}).get("username", "")
        # 개별 글 주소에는 작성자 아이디가 들어간다. /message/{id}만 쓰면 404가 뜬다.
        link = (f"https://stocktwits.com/{username}/message/{message['id']}"
                if message.get("id") and username else "https://stocktwits.com/symbol/FRMI")
        records.append({
            "published": pd.to_datetime(message.get("created_at"), errors="coerce", utc=True),
            "body": body,
            "user": username,
            "url": link,
            "group": classify(body),
        })
    frame = pd.DataFrame(records)
    return frame.sort_values("published", ascending=False).reset_index(drop=True) if not frame.empty else frame


def contract_hits(frame: pd.DataFrame) -> pd.DataFrame:
    """계약·테넌트로 분류된 것만. 핵심 판정 ①을 바꿀 수 있는 소식이다."""
    return frame[frame["group"] == "계약·테넌트"] if not frame.empty else frame


# ── 디스크 캐시 ───────────────────────────────────────────────────────────────
KEEP_ARTICLES = 800        # 누적 상한. 넘으면 오래된 것부터 버린다.


def save_cache(articles: pd.DataFrame, chatter: pd.DataFrame) -> None:
    """**기사는 덮지 않고 누적한다.**

    구글 뉴스 RSS는 같은 질의라도 호출마다 관련도 순위가 달라져 들어오는 묶음이 바뀐다
    (실측: 215 → 219 → 216건으로 오르내렸다). 덮어쓰면 어제 보이던 기사가 오늘 사라지고,
    '전체 기사' 목록이 마지막 수집분에 불과해진다. 합집합으로 쌓으면 이력이 남는다.

    커뮤니티 글은 Stocktwits 최신 스트림이라 덮어써도 무방하다.
    """
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    merged = articles
    previous = _load(ARTICLES_CACHE, list(articles.columns))
    if not previous.empty and not articles.empty:
        merged = pd.concat([previous, articles], ignore_index=True)
        merged["_key"] = (merged["title"].astype(str).str.lower()
                          .str.replace(r"[^a-z0-9가-힣]", "", regex=True).str[:70])
        merged = (merged.drop_duplicates("_key").drop(columns="_key")
                        .sort_values("published", ascending=False, na_position="last")
                        .head(KEEP_ARTICLES).reset_index(drop=True))
    merged.to_json(ARTICLES_CACHE, orient="records", date_format="iso", force_ascii=False)
    chatter.to_json(COMMUNITY_CACHE, orient="records", date_format="iso", force_ascii=False)


def _load(path: Path, columns: list[str]) -> pd.DataFrame:
    if not path.exists():
        return pd.DataFrame(columns=columns)
    try:
        frame = pd.read_json(path, orient="records", convert_dates=["published"])
    except Exception:
        return pd.DataFrame(columns=columns)
    if "published" in frame.columns and not frame.empty:
        frame["published"] = pd.to_datetime(frame["published"], errors="coerce", utc=True)
    return frame


@st.cache_data(ttl=300, show_spinner=False)
def cached_articles() -> pd.DataFrame:
    return _load(ARTICLES_CACHE, ["published", "title", "source", "url", "group", "priority"])


@st.cache_data(ttl=300, show_spinner=False)
def cached_community() -> pd.DataFrame:
    return _load(COMMUNITY_CACHE, ["published", "body", "user", "url", "group"])


def cache_age() -> pd.Timedelta | None:
    """캐시가 얼마나 묵었는지. 크론이 멈추면 화면에서 바로 드러나야 한다."""
    if not ARTICLES_CACHE.exists():
        return None
    stamp = pd.Timestamp(ARTICLES_CACHE.stat().st_mtime, unit="s", tz="UTC")
    return pd.Timestamp.now(tz="UTC") - stamp
