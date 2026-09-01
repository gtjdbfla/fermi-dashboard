"""SEC EDGAR XBRL / 공시 피드 클라이언트.

페르미(FRMI)는 매출이 없는 개발단계 회사라 손익계산서가 사실상 비어 있다.
펀더멘탈의 본체는 현금흐름표와 재무상태표이므로 이 모듈은 그 두 곳을 정확히 읽는 데 집중한다.

EDGAR companyfacts API의 함정 두 가지를 여기서 흡수한다.
1) 현금흐름/손익 항목을 '회계연도 기초부터 누적(YTD)'으로 담는다. 그대로 쓰면 4분기 막대가
   연간값이 되어 소진 속도가 4배로 부풀려진다. periodic_series()가 누적을 분기로 되돌린다.
2) 같은 시점을 10-K와 이후 10-Q가 다른 값으로 보고하는 재분류가 실제로 있다
   (2025-12-31 LongTermDebt: 10-K 148,986 -> 10-Q 109,799). 항상 최근 제출본을 채택한다.
"""

import os

import pandas as pd
import requests
import streamlit as st

CIK = "0002071778"
TICKER = "FRMI"
COMPANY_KO = "페르미 (Fermi Inc. / Fermi America)"
EXCHANGE = "NASDAQ / LSE"

# SEC는 자동 요청에 연락처가 담긴 User-Agent를 요구한다. 없으면 403으로 막힌다.
# 저장소가 공개라 실제 이메일을 코드에 두지 않는다. 배포처의 .env에서 주입한다.
PLACEHOLDER_UA = "fermi-dashboard/1.0 (set-me@example.com)"
SEC_USER_AGENT = os.environ.get("SEC_USER_AGENT", PLACEHOLDER_UA)
USER_AGENT_IS_PLACEHOLDER = SEC_USER_AGENT == PLACEHOLDER_UA
_HEADERS = {"User-Agent": SEC_USER_AGENT, "Accept-Encoding": "gzip, deflate"}

COMPANY_FACTS_URL = f"https://data.sec.gov/api/xbrl/companyfacts/CIK{CIK}.json"
SUBMISSIONS_URL = f"https://data.sec.gov/submissions/CIK{CIK}.json"
FILING_BASE = "https://www.sec.gov/Archives/edgar/data/{cik}/{accn}/{doc}"

# 공시 종류를 펀더멘탈 관점의 묶음으로 정리한다. 개발단계 회사는 정기보고보다
# 8-K(자금조달·계약)와 위임장(경영권 분쟁) 쪽에서 상태 변화가 먼저 드러난다.
FORM_GROUPS = [
    ("정기보고", ("10-K", "10-Q", "20-F", "10-K/A", "10-Q/A")),
    ("수시공시(중요사건)", ("8-K", "8-K/A", "6-K")),
    # 144는 내부자가 팔기 전에 내는 매도 예정 신고다. '기타'로 빠져 있어서 내부자 지표에
    # 잡히지 않았다. Form 4(체결)보다 먼저 나오므로 오히려 선행 신호에 가깝다.
    ("내부자 거래", ()),   # INSIDER_FORMS에서 정확히 일치시킨다
    ("대량보유", ("SC 13D", "SC 13G", "SCHEDULE 13D", "SCHEDULE 13G")),
    # 경쟁 위임장 서식(PRRN/PREN/PRER/DEFN)이 빠져 있어 24건이 '기타'로 흘렀다.
    ("위임장·주주총회", ("DEF 14A", "DEFA14A", "DFAN14A", "PRE 14A", "PREC14A", "DEFC14A",
                   "PRRN14A", "PREN14A", "PRER14A", "DEFN14A", "DEFR14A")),
    ("증권신고·자금조달", ("S-1", "S-3", "S-8", "S-11", "424B", "FWP", "POS AM")),
    # 상장·심사 과정에서 오가는 서식. 펀더멘탈과 무관하지만 '기타'에 섞이면
    # 정말 분류 안 된 것이 무엇인지 알 수 없다.
    ("상장·등록 절차", ("CORRESP", "UPLOAD", "DRS", "CERT", "EFFECT", "8-A12B", "RW", "AW")),
]


# 내부자 서식은 숫자 하나짜리라 접두어로 맞추면 안 된다. "4"로 시작한다는 이유로
# 424B4(투자설명서)가 내부자 거래로 분류돼 내부자 지표를 부풀리고 있었다. 정확히 일치시킨다.
INSIDER_FORMS = {"3", "4", "5", "3/A", "4/A", "5/A", "144", "144/A"}


def form_group(form: str) -> str:
    """공시 코드를 한글 묶음 이름으로 바꾼다. 접두어 일치까지 허용해야 SC 13D/A 같은 변형이 잡힌다."""
    upper = (form or "").upper().strip()
    if upper in INSIDER_FORMS:
        return "내부자 거래"
    for label, prefixes in FORM_GROUPS:
        for prefix in prefixes:
            if upper.startswith(prefix):
                return label
    return "기타"


def _get_json(url: str) -> dict:
    response = requests.get(url, headers=_HEADERS, timeout=60)
    response.raise_for_status()
    return response.json()


@st.cache_data(ttl=3600, show_spinner=False)
def load_company_facts() -> dict:
    return _get_json(COMPANY_FACTS_URL)


@st.cache_data(ttl=1800, show_spinner=False)
def load_submissions() -> dict:
    return _get_json(SUBMISSIONS_URL)


def _tag_rows(facts: dict, tag: str, unit: str) -> pd.DataFrame:
    """companyfacts에서 한 태그의 원시 행을 꺼낸다. us-gaap에 없으면 dei도 본다."""
    root = facts.get("facts", {})
    node = root.get("us-gaap", {}).get(tag) or root.get("dei", {}).get(tag) or root.get("srt", {}).get(tag)
    if not node:
        return pd.DataFrame()
    rows = node.get("units", {}).get(unit)
    if not rows:
        return pd.DataFrame()
    frame = pd.DataFrame(rows)
    for column in ("start", "end", "filed"):
        if column in frame.columns:
            frame[column] = pd.to_datetime(frame[column], errors="coerce")
    if "filed" not in frame.columns:
        frame["filed"] = pd.NaT
    return frame



def instant_series(facts: dict, tag: str, unit: str = "USD") -> pd.DataFrame:
    """재무상태표 항목(특정 시점의 잔액) 시계열. 컬럼: end, val, form, filed."""
    frame = _tag_rows(facts, tag, unit)
    if frame.empty:
        return pd.DataFrame(columns=["end", "val", "form", "filed"])
    if "start" in frame.columns:
        frame = frame[frame["start"].isna()]
    if frame.empty:
        return pd.DataFrame(columns=["end", "val", "form", "filed"])
    frame = frame.sort_values(["end", "filed"]).drop_duplicates("end", keep="last")
    return frame[["end", "val", "form", "filed"]].sort_values("end").reset_index(drop=True)


def periodic_series(facts: dict, tag: str, unit: str = "USD") -> pd.DataFrame:
    """기간 항목(현금흐름·손익)을 겹치지 않는 가장 잘게 쪼갠 구간 시계열로 되돌린다.

    EDGAR는 같은 항목을 '분기', '반기 누적', '연 누적'으로 중복해서 담는다.
    같은 start를 공유하는 행들은 누적 계열이므로 뒤 값에서 앞 값을 빼 구간값을 만들고,
    그렇게 얻은 후보들 중 짧은 구간부터 채워 넣어 서로 겹치지 않는 최소 단위 시계열을 만든다.
    원본으로 직접 보고된 구간이 있으면 같은 길이의 차분 구간보다 우선한다.

    컬럼: start, end, days, val, derived(차분으로 만들어낸 구간인지)
    """
    empty = pd.DataFrame(columns=["start", "end", "days", "val", "derived"])
    frame = _tag_rows(facts, tag, unit)
    if frame.empty or "start" not in frame.columns:
        return empty
    frame = frame.dropna(subset=["start", "end"])
    if frame.empty:
        return empty
    # 같은 (start, end)를 여러 공시가 보고하면 최근 제출본을 채택한다.
    frame = frame.sort_values("filed").drop_duplicates(["start", "end"], keep="last")

    candidates = [
        {"start": row["start"], "end": row["end"], "val": row["val"], "derived": False}
        for _, row in frame.iterrows()
    ]
    for _, group in frame.groupby("start"):
        group = group.sort_values("end")
        previous_end, previous_val = None, None
        for _, row in group.iterrows():
            if previous_end is not None:
                candidates.append({
                    "start": previous_end,
                    "end": row["end"],
                    "val": row["val"] - previous_val,
                    "derived": True,
                })
            previous_end, previous_val = row["end"], row["val"]

    pool = pd.DataFrame(candidates)
    pool["days"] = (pool["end"] - pool["start"]).dt.days
    pool = pool[pool["days"] > 0]
    if pool.empty:
        return empty

    # 짧은 구간 우선, 같은 길이면 직접 보고된 구간 우선.
    pool = pool.sort_values(["days", "derived", "end"])
    chosen: list[dict] = []
    for _, row in pool.iterrows():
        overlaps = any(row["start"] < taken["end"] and taken["start"] < row["end"] for taken in chosen)
        if not overlaps:
            chosen.append(row.to_dict())
    result = pd.DataFrame(chosen).sort_values("end").reset_index(drop=True)
    return result[["start", "end", "days", "val", "derived"]]


def latest_instant(facts: dict, tag: str, unit: str = "USD"):
    """(기준일, 값) 또는 None."""
    series = instant_series(facts, tag, unit)
    if series.empty:
        return None
    row = series.iloc[-1]
    return row["end"], float(row["val"])



def quarter_label(timestamp) -> str:
    """2026-03-31 -> '26 Q1'. 축 라벨이 길어지지 않게 두 자리 연도를 쓴다."""
    stamp = pd.Timestamp(timestamp)
    return f"{stamp.year % 100:02d} Q{stamp.quarter}"


@st.cache_data(ttl=1800, show_spinner=False)
def load_filings(limit: int = 300) -> pd.DataFrame:
    """최근 공시 목록. 컬럼: filed, report, form, group, title, items, url.

    items는 8-K의 항목 코드다("1.01,9.01" 꼴). 계약 체결(1.01)·해지(1.02)를 제목이 아니라
    코드로 잡을 수 있어서, 알림이 회사가 뭐라고 적었는지에 의존하지 않는다.
    """
    submissions = load_submissions()
    recent = submissions.get("filings", {}).get("recent", {})
    if not recent:
        return pd.DataFrame(columns=["filed", "report", "form", "group", "title", "items", "url"])

    frame = pd.DataFrame({
        "filed": pd.to_datetime(recent.get("filingDate"), errors="coerce"),
        # **접수 시각을 따로 들고 있어야 한다.** filingDate는 날짜뿐이라 항상 자정이 되고,
        # 정밀 시각(워터마크)과 비교하면 같은 날 오후에 접수된 공시가 통째로 밀려난다.
        # 실제로 2026-08-31 16:15 ET 접수 8-K가 일일 리포트에서 빠졌다 —
        # filed(08-31 00:00) < 직전 리포트 시각(08-31 02:10 UTC) 이었기 때문이다.
        # SEC 공시는 대부분 20~21시 UTC에 오므로 사실상 전부가 그렇게 빠지고 있었다.
        "accepted": pd.to_datetime(recent.get("acceptanceDateTime"), errors="coerce", utc=True),
        "report": pd.to_datetime(recent.get("reportDate"), errors="coerce"),
        "form": recent.get("form"),
        "title": recent.get("primaryDocDescription"),
        "accn": recent.get("accessionNumber"),
        "doc": recent.get("primaryDocument"),
        "items": recent.get("items") or "",
    })
    frame["group"] = frame["form"].map(form_group)
    frame["url"] = [
        FILING_BASE.format(cik=int(CIK), accn=str(accn).replace("-", ""), doc=doc)
        if isinstance(accn, str) and isinstance(doc, str) and doc
        else ""
        for accn, doc in zip(frame["accn"], frame["doc"])
    ]
    frame["title"] = frame["title"].fillna("").replace("", pd.NA).fillna(frame["form"])
    return frame.sort_values("filed", ascending=False).head(limit).reset_index(drop=True)


@st.cache_data(ttl=3600, show_spinner=False)
def company_profile() -> dict:
    submissions = load_submissions()
    return {
        "name": submissions.get("name", ""),
        "sic": submissions.get("sicDescription", ""),
        "exchanges": ", ".join(submissions.get("exchanges", []) or []),
        "tickers": ", ".join(submissions.get("tickers", []) or []),
        "fiscal_year_end": submissions.get("fiscalYearEnd", ""),
    }
