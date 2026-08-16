"""페르미 관련 뉴스와 커뮤니티 반응.

**뉴스는 펀더멘탈이 아니다.** 이 화면의 목적은 하나다 — 핵심 판정 ①(계약 커버리지 15%)을 바꿀
소식이 떴는지 먼저 알아채는 것. 그래서 제목에서 계약·테넌트 키워드를 뽑아 맨 위로 올린다.

무엇을 봤든 **확정은 SEC 공시로만 한다.** 뉴스와 커뮤니티 글은 틀릴 수 있고, 특히 커뮤니티는
검증되지 않은 추측이 섞인다. 대시보드의 계약 MW 숫자는 8-K를 근거로만 갱신한다.
"""

import html
import re
from xml.etree import ElementTree

import pandas as pd
import requests
import streamlit as st

UA = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                    "AppleWebKit/537.36 Chrome/120.0 Safari/537.36"}
JSON_UA = {**UA, "Accept": "application/json"}

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
                                    headers=UA, timeout=20)
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
                                headers=UA, timeout=20)
        items = response.json().get("news") or []
    except Exception:
        return _frame([])
    return _frame([{
        "published": pd.to_datetime(item.get("providerPublishTime"), unit="s", errors="coerce", utc=True),
        "title": item.get("title", ""), "source": item.get("publisher", "Yahoo"),
        "url": item.get("link", ""),
    } for item in items])


@st.cache_data(ttl=900, show_spinner=False)
def nasdaq_news() -> pd.DataFrame:
    try:
        response = requests.get("https://api.nasdaq.com/api/news/topic/articlebysymbol",
                                params={"q": "FRMI|stocks", "offset": 0, "limit": 20},
                                headers=JSON_UA, timeout=20)
        rows = ((response.json().get("data") or {}).get("rows")) or []
    except Exception:
        return _frame([])
    return _frame([{
        "published": pd.to_datetime(row.get("created"), errors="coerce", utc=True),
        "title": row.get("title", ""), "source": row.get("publisher") or "Nasdaq",
        "url": ("https://www.nasdaq.com" + row["url"]) if row.get("url", "").startswith("/") else row.get("url", ""),
    } for row in rows])


@st.cache_data(ttl=900, show_spinner=False)
def collect() -> pd.DataFrame:
    """세 소스를 합치고 같은 기사를 하나로 줄인다."""
    frame = pd.concat([google_news(), yahoo_news(), nasdaq_news()], ignore_index=True)
    if frame.empty:
        return frame
    frame = frame.dropna(subset=["title"])
    frame = frame[frame["title"].str.strip() != ""]
    # 매체마다 제목 표기가 조금씩 달라 기호·공백을 지운 형태로 중복을 잡는다.
    frame["key"] = frame["title"].str.lower().str.replace(r"[^a-z0-9가-힣]", "", regex=True).str[:70]
    frame = frame.drop_duplicates("key").drop(columns="key")
    frame["group"] = frame["title"].map(classify)
    frame["priority"] = frame["group"].map(lambda g: PRIORITY.get(g, 99))
    return frame.sort_values(["priority", "published"], ascending=[True, False]).reset_index(drop=True)


@st.cache_data(ttl=600, show_spinner=False)
def community() -> pd.DataFrame:
    """Stocktwits 종목 게시글. 검증되지 않은 추측이 섞이므로 화면에서도 그렇게 표시한다."""
    try:
        response = requests.get("https://api.stocktwits.com/api/2/streams/symbol/FRMI.json",
                                headers=UA, timeout=20)
        messages = response.json().get("messages") or []
    except Exception:
        return pd.DataFrame(columns=["published", "body", "user", "url", "group"])
    records = []
    for message in messages:
        body = html.unescape(re.sub(r"\s+", " ", message.get("body", ""))).strip()
        records.append({
            "published": pd.to_datetime(message.get("created_at"), errors="coerce", utc=True),
            "body": body,
            "user": (message.get("user") or {}).get("username", ""),
            "url": f"https://stocktwits.com/message/{message.get('id')}" if message.get("id") else "",
            "group": classify(body),
        })
    frame = pd.DataFrame(records)
    return frame.sort_values("published", ascending=False).reset_index(drop=True) if not frame.empty else frame


def contract_hits(frame: pd.DataFrame) -> pd.DataFrame:
    """계약·테넌트로 분류된 것만. 핵심 판정 ①을 바꿀 수 있는 소식이다."""
    return frame[frame["group"] == "계약·테넌트"] if not frame.empty else frame
