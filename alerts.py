"""테넌트 2호가 뜨면 30분 안에 텔레그램으로 알린다.

이 대시보드에서 실제로 감시할 값은 하나다 — 계약 커버리지 15%. 그리고 그 숫자를 바꾸는 사건은
새 리스 계약뿐이다. 화면을 열어야 알 수 있으면 늦으므로 사건 쪽에서 먼저 찾아오게 한다.

**두 갈래로 받는다.**
  🔴 공시 — 8-K의 Item 코드로 잡는다. 회사가 SEC에 신고한 것이므로 확정이다.
  🟡 뉴스 — 공시보다 몇 시간 빠를 수 있지만 틀릴 수 있다. 그래서 등급을 나눠 표시한다.

**Item 1.01은 리스도 사채도 똑같이 붙는다.** 실제로 2026-07-15 전환사채 8-K가 1.01로 들어왔다.
그래서 코드로 후보를 좁힌 뒤 원문 단어로 계약/자금조달을 가른다. 다만 애매하면 **보낸다** —
테넌트 2호를 놓치는 비용이 헛알림 한 번보다 훨씬 크다.

**첫 실행은 침묵한다.** 과거 공시·기사를 전부 보내면 알림이 무의미해지므로, 처음에는 현재
상태를 '이미 본 것'으로 기록만 하고 켜졌다는 사실만 알린다.
"""

import html
import os
import re
from pathlib import Path
from xml.etree import ElementTree

import pandas as pd
import requests

import diskcache as dc

SEEN_CACHE = "alerts_seen"
SEEN_MAX_AGE = 86400 * 3650      # 사실상 만료 없음. 중복 발송을 막는 것이 목적이다.
SEEN_KEEP = 400                  # 무한정 쌓이지 않게 최근 것만 남긴다.
# 오래된 사건은 기록만 하고 보내지 않는다.
#
# 감지기를 새로 붙이면 그 감지기가 보는 과거 공시가 전부 '처음 보는 것'이 되어 한꺼번에
# 터진다. 경영권 분쟁 감지를 붙였을 때 실제로 9건이 대기했다.
#
# **뉴스는 훨씬 짧게 잡는다.** 구글 뉴스는 같은 사건을 여러 매체가 며칠에 걸쳐 쓰고, RSS가
# 그걸 뒤늦게 노출한다. 실제로 8/10 계약 기사가 8/19에 새로 떠올라 알림이 나갔다 — 9일 전
# 일이라 이미 알고 있는 소식이었다. 공시는 접수 자체가 사건이라 14일을 둬도 되지만
# 뉴스는 이틀만 본다.
MAX_EVENT_AGE_DAYS = 14
NEWS_MAX_AGE_DAYS = 2

# 같은 사건을 여러 매체가 쓴다. 기사 단위로 중복을 빼면 매체 수만큼 알림이 간다.
# 제목에서 흔한 말을 걷어낸 낱말 묶음을 사건 지문으로 삼아, 한 번 알린 사건은 이 기간 동안
# 다시 알리지 않는다.
TOPIC_WINDOW_DAYS = 21
_STOP = {"fermi", "frmi", "nasdaq", "inc", "stock", "shares", "the", "a", "an", "of", "in",
         "on", "for", "to", "and", "with", "at", "as", "after", "its", "is", "are", "по",
         "says", "amid", "from", "by", "new", "us", "it", "that", "this"}


def topic_key(title: str) -> str:
    """제목에서 흔한 말을 걷어낸 낱말 묶음. 매체가 달라도 같은 사건이면 크게 겹친다.

    앞뒤를 잘라 고정 길이로 만들면 안 된다. 단어 하나만 늘어도 잘리는 지점이 달라져
    다른 지문이 되고, 같은 소식이 두 번 나간다(실측으로 확인).
    """
    words = re.findall(r"[a-z0-9]+", (title or "").lower())
    return "|".join(sorted({w for w in words if w not in _STOP and len(w) > 2}))


def same_topic(one: str, other: str, threshold: float = 0.55) -> bool:
    """두 지문이 같은 사건인지. 낱말 겹침 비율로 본다."""
    left, right = set(one.split("|")), set(other.split("|"))
    if not left or not right:
        return False
    return len(left & right) / len(left | right) >= threshold


DASHBOARD_URL = os.environ.get("DASHBOARD_URL", "")
TIMEOUT = 15

# 8-K 항목 코드 중 계약 커버리지를 바꿀 수 있는 것만. 5.02(임원 변경)나 2.02(실적)는
# 이 회사에서 두 달에 네 번씩 나와서 넣으면 알림이 소음이 된다.
WATCHED_ITEMS = {
    "1.01": "중요 계약 체결",
    "1.02": "중요 계약 해지",       # 기존 테넌트 이탈. NuScale이 UAMPS를 잃은 것이 이 항목이다.
    "2.01": "인수·처분 완료",
    # **8.01을 빠뜨렸던 것이 가장 비싼 실수였다.** 연중 최대 낙폭(-32.76%)을 만든
    # 2025-12-12 첫 테넌트 이탈 공시가 1.02가 아니라 8.01로 들어왔다. 회사가 어느
    # 항목에 넣을지는 회사가 정하고, 계약이 '해지'가 아니라 '독점기간 만료'로 끝나면
    # 8.01이 된다. 10개월간 3건뿐이라 소음도 아니다.
    "8.01": "기타 중요사건",
}

# 원문에 이 단어가 있으면 계약 쪽으로 본다.
LEASE_WORDS = ["lease", "tenant", "colocation", "co-location", "offtake", "off-take",
               "take-or-pay", "power purchase", "ppa", "hyperscaler", "data center customer",
               "capacity agreement", "anchor customer"]
# 이 단어만 있고 위가 없으면 자금조달로 본다.
FINANCE_WORDS = ["note purchase", "convertible note", "indenture", "underwrit", "placement agent",
                 "credit agreement", "term loan", "registration rights", "securities purchase"]

# 페르미를 가리키는 표지. 회사명만으로는 부족하다.
#
# 수집된 기사 중 34건이 이름 없이 회사를 가리켰다 — "Three Natural Gas Turbines Reach
# Houston, Bound for Texas AI Campus", "This Texas billionaire's nuclear-powered data
# center company faces collapse", "Trump-branded data center project CEO departs".
# 애널리스트 추출처럼 이름을 엄격히 요구하면 **이런 부정적 기사를 통째로 버린다.**
# 그래서 알림 판정에는 고유명사를 넓게 잡는다.
FERMI_MARKS = re.compile(
    r"\bfermi\b|\bfrmi\b|페르미|matador|amarillo|tensorwave|neugebauer|"
    r"trump[- ]?(branded|linked)|rick\s+perry", re.I)


# 뉴스는 훨씬 엄격하게 건다. '계약·테넌트' 분류만으로는 지금도 38건이 걸려 알림이 못 된다.
# 서명 동사와 용량/테넌트 명사가 **둘 다** 있어야 한다.
# **낱말 경계를 반드시 건다.** 이 세션에서 같은 종류 버그가 네 번 나왔다 —
#   Citi ⊂ Citigroup   sues ⊂ Issues   sued ⊂ issued   lease ⊂ Release
# 마지막 것 때문에 "Fermi Announces Second Quarter Earnings **Release** and Call
# Date"가 계약 뉴스로 잡혔다. 부분 문자열은 반드시 다른 낱말 안에 숨어 있다.
SIGN_VERBS = r"(\bsigns?\b|\bsigned\b|\bsigning\b|\bexecutes?\b|\bexecuted\b|\bexecuting\b|" \
             r"\benters?\s+into\b|\bentered\s+into\b|\binks?\b|\binked\b|" \
             r"\bsecures?\b|\bsecured\b|\bsecuring\b|\bawards?\b|\bawarded\b|" \
             r"\bfinaliz\w*|\bcloses?\s+on\b|\bclosed\s+on\b|\bdefinitive\b|" \
             r"(?<!non-)(?<!non)\bbinding\b|체결|계약을\s*맺)"
TENANT_NOUNS = r"(\btenants?\b|\bleases?\b|\bleasing\b|\bofftake\b|\boff-take\b|\btake-or-pay\b|" \
               r"\bcolocation\b|\bco-location\b|\bhyperscalers?\b|" \
               r"\banchor\s+customer\b|\d\s*(mw|gw)\b|\bmegawatts?\b|\bgigawatts?\b|" \
               r"테넌트|임차|임대차)"
# 자금조달 기사가 서명 동사에 걸려 들어오는 것을 막는다.
NEWS_EXCLUDE = r"(convertible|offering|notes\s+due|underwrit|placement|dilut|증자|사채|" \
               r"price\s+target|analyst|목표주가)"
# 구속력 없는 건 커버리지를 못 바꾼다. 거르지는 않되 제목에 그렇다고 적는다 —
# 2025-09 Siemens LOI가 이 부류였고, 계약처럼 읽히면 판단을 흐린다.
NONBINDING = r"(letter\s+of\s+intent|\bloi\b|\bmou\b|memorandum|non-?binding|framework|" \
             r"preliminary|의향서|양해각서)"

# **약한 동사도 잡되, 반드시 비구속으로 표시한다.**
#
# 2026-08-11 Hillcore BOOT 제휴(2.6GW)가 알림 문턱을 못 넘었다. 제목이 "Hillcore
# Targets First Power Within 24 Months of Notice to Proceed"였는데 'targets'는
# 서명 동사가 아니어서다. 수집은 됐지만 알림이 안 갔다.
#
# 확인해보니 실제로 구속력 있는 계약이 아니었다(원문이 framework agreement이고
# 전용 8-K도 없다). 즉 **문턱은 옳게 작동했지만, 그래도 알았어야 할 소식**이었다.
# 2.6GW짜리 발전 제휴는 커버리지를 안 바꿔도 판단 재료다.
#
# 그래서 약한 동사는 통과시키되 등급을 낮춰 보낸다. 이 동사로만 걸린 건은
# NONBINDING 표기가 없어도 무조건 '비구속 가능성'으로 찍는다 — 강한 동사가 없다는
# 것 자체가 아직 서명 전이라는 신호다.
WEAK_VERBS = r"(\btargets?\b|\btargeting\b|\bplans?\b|\bplanning\b|\bplanned\b|" \
             r"\bannounces?\b|\bannounced\b|\bunveils?\b|\bunveiled\b|" \
             r"\bpartners?\s+with\b|\balliance\b|\bteams?\s+up\b|" \
             r"\bto\s+build\b|\bto\s+develop\b|\bto\s+supply\b|추진|계획|발표)"

# 약한 동사는 문턱을 낮추므로 회사가 정기적으로 내는 안내문이 함께 들어온다.
# "Announces Second Quarter Earnings Release and Call Date"가 그랬다.
ROUTINE_NOTICE = r"(earnings\s+(release|call|date)|conference\s+call|webcast|" \
                 r"investor\s+day|annual\s+meeting|results\s+date|실적\s*발표\s*일정)"


# ── 텔레그램 ──────────────────────────────────────────────────────────────────
def configured() -> bool:
    return bool(os.environ.get("TELEGRAM_BOT_TOKEN") and os.environ.get("TELEGRAM_CHAT_ID"))


def send(text: str) -> tuple[bool, str]:
    """HTML 서식으로 보낸다. 실패해도 예외를 올리지 않고 사유를 돌려준다."""
    token = os.environ.get("TELEGRAM_BOT_TOKEN")
    chat_id = os.environ.get("TELEGRAM_CHAT_ID")
    if not (token and chat_id):
        return False, "TELEGRAM_BOT_TOKEN/CHAT_ID 미설정"
    try:
        response = requests.post(
            f"https://api.telegram.org/bot{token}/sendMessage",
            json={"chat_id": chat_id, "text": text, "parse_mode": "HTML",
                  "disable_web_page_preview": False},
            timeout=TIMEOUT)
        payload = response.json()
    except Exception as error:
        return False, f"{type(error).__name__}: {error}"
    if not payload.get("ok"):
        return False, str(payload.get("description") or payload)[:200]
    return True, ""


# ── 중복 방지 ─────────────────────────────────────────────────────────────────
def _seen() -> dict:
    return dc.load_json(SEEN_CACHE, SEEN_MAX_AGE) or {}


def _remember(store: dict, ids, first_run_done: bool = True, topics: dict | None = None) -> None:
    stamp = pd.Timestamp.now(tz="UTC").isoformat()
    sent = dict(store.get("ids") or {})
    for key in ids:
        sent[key] = stamp
    # 오래된 것부터 버린다. 이미 사라진 기사를 다시 보낼 일은 없다.
    if len(sent) > SEEN_KEEP:
        sent = dict(sorted(sent.items(), key=lambda kv: kv[1])[-SEEN_KEEP:])
    dc.save_json(SEEN_CACHE, {"ids": sent, "initialized": first_run_done,
                              "last_check": stamp,
                              "last_sent": store.get("last_sent"),
                              # 스냅샷을 빠뜨리면 매 실행이 '이전 값 없음'이 되어
                              # 분기 실적 변화를 영원히 못 잡는다.
                              "snapshot": store.get("snapshot"),
                              # 사건 지문을 빠뜨리면 같은 소식을 매체 수만큼 다시 보낸다.
                              "topics": dict(list((topics or store.get("topics") or {}).items())[-SEEN_KEEP:])})


# ── 감지 ──────────────────────────────────────────────────────────────────────
def _kind(text: str) -> str:
    """8-K 원문이 계약 쪽인지 자금조달 쪽인지. 애매하면 계약으로 본다(놓치지 않는 쪽)."""
    lowered = (text or "").lower()
    lease = sum(word in lowered for word in LEASE_WORDS)
    finance = sum(word in lowered for word in FINANCE_WORDS)
    if lease and lease >= finance:
        return "계약"
    if finance:
        return "자금조달"
    return "확인 필요"


def filing_events(filings: pd.DataFrame, read_text=None, known: set | None = None) -> list[dict]:
    """감시 대상 Item이 붙은 8-K. read_text(url)를 주면 원문으로 종류까지 가른다.

    known을 주면 이미 보낸 공시는 원문을 받지 않는다. 안 그러면 같은 8-K를 30분마다
    다시 내려받게 된다.
    """
    if filings is None or filings.empty or "items" not in filings.columns:
        return []
    known = known or set()
    events = []
    for row in filings.itertuples():
        codes = [code.strip() for code in str(getattr(row, "items", "") or "").split(",")]
        hits = [WATCHED_ITEMS[code] for code in codes if code in WATCHED_ITEMS]
        if not hits:
            continue
        event_id = f"filing:{row.accn}"
        body = read_text(row.url) if (read_text and row.url and event_id not in known) else ""
        events.append({
            "id": event_id,
            "tier": "확정",
            "kind": _kind(body) if body else "확인 필요",
            "when": str(pd.Timestamp(row.filed).date()),
            "form": row.form,
            "items": ", ".join(hits),
            "title": row.title or row.form,
            "url": row.url,
            "excerpt": (body[:500] + "…") if len(body) > 500 else body,
        })
    return events


def news_events(articles: pd.DataFrame) -> list[dict]:
    """제목에 '동사 + 용량/테넌트 명사'가 함께 있는 기사.

    동사는 두 등급이다. 강한 동사(서명·체결)는 계약으로, 약한 동사(targets·plans·
    alliance)는 **무조건 비구속으로** 찍는다. 약한 쪽을 아예 버리면 Hillcore 2.6GW
    제휴 같은 판단 재료를 통째로 놓친다.
    """
    if articles is None or articles.empty:
        return []
    events = []
    for row in articles.itertuples():
        title = str(getattr(row, "title", "") or "")
        # 어느 회사 얘기인지 먼저 본다. 증권사 오탐(Health Catalyst·Weibo 기사가 페르미
        # 애널리스트 액션으로 나갔다)과 같은 구멍이 계약 알림에도 있었다.
        if not FERMI_MARKS.search(title):
            continue
        lowered = title.lower()
        if not re.search(TENANT_NOUNS, lowered):
            continue
        strong = bool(re.search(SIGN_VERBS, lowered))
        weak = bool(re.search(WEAK_VERBS, lowered))
        if not (strong or weak):
            continue
        if re.search(NEWS_EXCLUDE, lowered):
            continue
        # 정기 안내문은 약한 동사에만 걸린다. 계약과 무관하다.
        if weak and not strong and re.search(ROUTINE_NOTICE, lowered):
            continue
        # 강한 동사가 없으면 아직 서명 전이다. NONBINDING 표기가 제목에 없더라도
        # 비구속으로 찍는다 — 'targets'만 있는 소식을 계약처럼 읽으면 판단이 흐려진다.
        if not strong:
            kind = "비구속(계획·제휴)"
        elif re.search(NONBINDING, lowered):
            kind = "비구속(LOI/MOU)"
        else:
            kind = "계약"
        key = re.sub(r"[^a-z0-9가-힣]", "", lowered)[:70]
        events.append({
            "id": f"news:{key}",
            "tier": "미확인",
            "kind": kind,
            "when": str(pd.Timestamp(row.published).date()) if pd.notna(row.published) else "",
            "form": "",
            "items": "",
            "title": title,
            "url": getattr(row, "url", ""),
            "excerpt": "",
            "source": getattr(row, "source", ""),
            "topic": topic_key(title),
        })
    return events


# ── 테넌트 신용 ───────────────────────────────────────────────────────────────
# 고객이 1곳뿐이라 그 1곳이 무너지면 커버리지가 15%에서 0%가 된다. NuScale이 UAMPS 하나를
# 잃고 -82%가 된 것과 같은 구조다. 테넌트는 비상장이라 SEC 공시가 없어 뉴스로만 잡힌다.
# 놓치는 비용이 헛알림보다 훨씬 크므로 넓게 잡는다. 테넌트 파산을 놓치면 커버리지가
# 0이 된 걸 뒤늦게 아는 것이고, 헛알림은 메시지 한 통이다.
TENANT_TROUBLE = r"(bankrupt|chapter\s*11|insolven|receivership|default|" \
                 r"miss(es|ed)?\s+(a\s+)?payment|delinquen|" \
                 r"restructur|layoff|job\s+cuts|cuts?\s+staff|furlough|" \
                 r"lawsuit|sue[sd]?\b|fraud|investigat|subpoena|probe|downgrade|" \
                 r"going\s+concern|shut\s*(down|ting)|wind[\s-]?down|halt|" \
                 r"funding\s+(trouble|crunch|gap|shortfall)|cash\s+(crunch|burn)|" \
                 r"fail(s|ed)?\s+to\s+raise|scrap(s|ped)?|pull(s|ed)?\s+out|terminat|" \
                 r"파산|회생|디폴트|구조조정|감원|소송|자금난)"


def tenants() -> list[str]:
    """contracts.csv에 서명된 고객 이름. 감시 대상은 대시보드가 계약으로 인정한 곳뿐이다."""
    path = Path(__file__).parent / "data" / "contracts.csv"
    if not path.exists():
        return []
    try:
        frame = pd.read_csv(path)
    except Exception:
        return []
    if "customer" not in frame.columns:
        return []
    if "binding" in frame.columns:
        frame = frame[frame["binding"].astype(str).str.upper().str.startswith("Y")]
    return [str(name).strip() for name in frame["customer"].dropna().unique() if str(name).strip()]


def tenant_events(names: list[str] | None = None) -> list[dict]:
    """테넌트 이름 + 악재 단어가 함께 걸린 기사만. 평범한 테넌트 소식은 보내지 않는다."""
    names = names if names is not None else tenants()
    events = []
    for name in names:
        try:
            response = requests.get(
                "https://news.google.com/rss/search",
                params={"q": f'"{name}"', "hl": "en-US", "gl": "US", "ceid": "US:en"},
                headers={"User-Agent": "Mozilla/5.0"}, timeout=TIMEOUT)
            root = ElementTree.fromstring(response.content)
        except Exception:
            continue
        for item in root.findall(".//item"):
            title = html.unescape(item.findtext("title") or "")
            source = item.findtext("{http://search.yahoo.com/mrss/}source") or ""
            if not source and " - " in title:
                title, source = title.rsplit(" - ", 1)
            lowered = title.lower()
            if name.lower() not in lowered or not re.search(TENANT_TROUBLE, lowered):
                continue
            key = re.sub(r"[^a-z0-9가-힣]", "", lowered)[:70]
            published = pd.to_datetime(item.findtext("pubDate"), errors="coerce", utc=True)
            events.append({
                "id": f"tenant:{key}",
                "tier": "테넌트",
                "kind": name,
                "when": str(published.date()) if pd.notna(published) else "날짜 불명",
                "form": "", "items": "", "excerpt": "",
                "title": title.strip(),
                "url": item.findtext("link") or "",
                "source": source.strip() or "Google News",
                "topic": topic_key(title),
            })
    return events


# ── 애널리스트 액션 ───────────────────────────────────────────────────────────
# 처음엔 "애널리스트 목표주가는 소음"이라 보고 알림에서 뺐다. 그 판단은 컨센서스가
# $32에서 $5로 무너진 걸 보기 전이었고, 인하 사유가 전부 tenant/contract delay라는 것도
# 나중에 알았다. 지금 보면 소음이 아니라 판정 ①과 같은 축을 가리키는 선행 신호다.
#
# 다만 **'유지'는 뺀다.** 8월 액션 4건 중 3건이 유지였다. 등급을 안 바꾼 재확인까지
# 알리면 월 4회가 되어 소음이 된다. 방향이 바뀌는 순간만 보낸다.
ANALYST_ACTIONS = {"신규 커버리지", "커버리지 재개", "상향", "하향",
                   "목표가 상향", "목표가 인하"}
ANALYST_MAX_AGE_DAYS = 7


def analyst_events(articles: pd.DataFrame) -> list[dict]:
    """등급표+기사에서 방향이 바뀐 액션만 사건으로 만든다."""
    try:
        import analyst as an
        table = an.combined(an.merged_actions(articles))
    except Exception:
        return []
    if table is None or table.empty:
        return []
    events = []
    for row in table.to_dict("records"):
        if row.get("행동") not in ANALYST_ACTIONS:
            continue
        when = pd.to_datetime(row.get("시점"), errors="coerce")
        if pd.isna(when):
            continue
        events.append({
            "id": f"analyst:{row['증권사']}:{when.date()}:{row['행동']}",
            "tier": "애널리스트", "kind": row["증권사"],
            "when": str(when.date()),
            "form": "", "items": "", "excerpt": "",
            "title": row["행동"],
            "url": row.get("링크") or DASHBOARD_URL,
            "등급": row.get("등급", "–"), "목표가": row.get("목표가", "–"),
            "이전": row.get("이전", "–"), "이유": row.get("언급된 이유", "–"),
            "출처": row.get("출처", ""),
        })
    return events


# ── 정기보고서 상태 (레드플래그) ──────────────────────────────────────────────
# legal.py는 *사건*을 잡고 이건 *상태*를 잡는다. 계속기업 불확실성·내부통제 취약점은
# 한 번 생기면 분기마다 반복 게재되고 문장에 may/could가 섞여 있어, legal.py의
# 가정법 게이트에 걸려 통째로 버려졌다. 실제로 두 분기 연속 놓쳤다.
#
# 상태는 **바뀔 때만** 사건이다. 지속 중인 것은 일일 리포트의 상태 칸에만 적는다.
REDFLAG_MAX_AGE_DAYS = 21      # 정기보고서는 분기당 1회라 창을 넉넉히 준다


def redflag_events() -> list[dict]:
    """정기보고서 범주 상태가 바뀐 것만. 신규·해소·변경."""
    try:
        import redflags as rf
        changes = rf.transitions()
    except Exception:
        return []
    events = []
    for item in changes:
        events.append({
            "id": f"redflag:{item['category']}:{item['filed']}:{item['kind']}",
            "tier": "레드플래그",
            "kind": item["kind"],
            "when": item["filed"],
            "form": item["form"], "items": "", "excerpt": item.get("snippet", ""),
            "title": item["category"],
            "url": item["url"],
            "detail": item.get("detail", ""),
            "before": item.get("before", ""),
            "why": item.get("why", ""),
        })
    return events


# ── 건설 속도 ─────────────────────────────────────────────────────────────────
# 가동 중 MW가 0인 회사에서 **건설 속도는 유일하게 관측 가능한 실적**이다. 그런데
# 건설이 늦춰지는 것은 공시로 따로 나오지 않고 분기 현금흐름표에 숫자로만 남는다.
# 실제로 2026Q2에 전분기 대비 -58%가 이미 벌어졌고(그 분기에 CEO가 해임됐다),
# 알림은 한 통도 없었다.
#
# 사건 날짜는 분기말이 아니라 **10-Q가 접수된 날**이다. 6/30을 쓰면 나이 제한에
# 걸려 영원히 안 나간다.
CAPEX_MAX_AGE_DAYS = 14


def capex_events(m: dict | None, filings: pd.DataFrame | None = None) -> list[dict]:
    """분기 capex가 직전 분기와 직전 4분기 평균 대비 동시에 꺾였을 때만."""
    try:
        import capex as cx
        verdict = cx.assess(m)
    except Exception:
        return []
    if not verdict or not verdict["triggered"]:
        return []
    filed, source_url = None, ""
    try:
        filed, source_url = cx.reported_on(filings, verdict["end"])
    except Exception:
        pass
    when = filed if filed is not None else verdict["end"]
    return [{
        "id": f"capex:{verdict['quarter']}",
        "tier": "건설속도",
        "kind": verdict["quarter"],
        "when": str(pd.Timestamp(when).date()),
        "form": "10-Q", "items": "", "excerpt": "",
        "title": f"{verdict['quarter']} 분기 capex 급감",
        "url": source_url or DASHBOARD_URL,
        "capex": verdict,
    }]


# ── 법적·규제 ─────────────────────────────────────────────────────────────────
# 2026-07-30 EDNY 소환장과 2026-08-03 SEC 문서요구가 8/14 10-Q에 적혀 있었는데
# 알림이 한 통도 안 갔다. 공시 감지기는 8-K Item 코드만 보고, 뉴스 감지기는 제목에
# '서명 동사 + 용량 명사'를 요구했기 때문이다. 둘 다 계약 커버리지만 보도록 만든
# 결과이고, 그 사이로 규제 리스크가 통째로 빠져나갔다.
#
# 공시 원문 쪽은 legal.py가 맡는다(가정법 걸러내는 규칙이 거기 있다). 여기서는
# 뉴스 쪽을 함께 본다 — 공시보다 며칠 빠를 수 있다. 실제로 소환장 기사는 8/17에
# 났고 10-Q는 8/14였다(기사가 늦은 경우지만 순서는 사건마다 다르다).
# 낱말 경계를 반드시 양쪽에 건다. 경계 없이 `sues?`로 두면 "Governance Is-sues"가,
# `sued`로 두면 "is-sued"가 걸린다 — 실측으로 둘 다 오탐을 냈다.
# 'Citi'가 'Citigroup' 앞부분에 걸려 Weibo 기사를 페르미 애널리스트 액션으로 만든
# 것과 같은 종류의 버그다.
LEGAL_NEWS = re.compile(
    r"\bsubpoena|\bindict|\bgrand jury\b|\bwells notice\b|\bprobe[sd]?\b|"
    # 맨 'inquiry'는 안 된다. "In Response to Inquiries from London Press"가 걸렸다 —
    # 언론 문의지 규제 조회가 아니다. 조회는 주체가 붙어 있을 때만 사건으로 본다.
    r"\binvestigation|\blawsuits?\b|\bsues?\b|\bsued\b|"
    r"(?:regulatory|government|federal|criminal|sec|doj|ftc|ferc)\s+inquir|"
    r"\blitigation\b|\bcourt\s+order\b|\binjunction\b|\bindictment\b|"
    r"소환장|기소|수사|조사|고발|소송", re.I)
# 애널리스트·실적 기사가 'investigation' 같은 낱말에 걸리는 것을 막는다.
LEGAL_NEWS_EXCLUDE = r"(price\s+target|analyst|rating|upgrade|downgrade|earnings\s+call|" \
                     r"options?\s+chain|목표주가)"
LEGAL_MAX_AGE_DAYS = 14


def legal_events(articles: pd.DataFrame | None = None) -> list[dict]:
    """공시 원문에서 확정된 법적 사건 + 같은 주제의 뉴스."""
    events = []
    # 공시가 이미 확정한 사건은 그 핵심 낱말을 기억해 둔다. 같은 낱말을 쓴 기사는
    # 같은 사건이므로 다시 보내지 않는다 — 소환장 하나에 기사 3건이 붙어 있었다.
    covered: list[tuple[pd.Timestamp, set]] = []
    try:
        import legal as lg
        for hit in lg.findings():
            when = pd.to_datetime(hit["filed"], errors="coerce")
            covered.append((when, {w.lower() for w in
                                   re.findall(r"subpoena|indictment|grand jury|wells notice|"
                                              r"investigation|inquiry|lawsuit|litigation",
                                              hit["text"], re.I)}))
            events.append({
                "id": f"legal:{hit['fingerprint']}",
                "tier": "법적",
                "kind": hit["form"],
                "when": hit["filed"],
                "form": hit["form"], "items": "", "excerpt": hit["text"],
                "title": "공시 원문에서 확인된 법적·규제 사건",
                "url": hit["url"],
                "확정": True,
            })
    except Exception:
        pass

    if articles is not None and not articles.empty:
        for row in articles.itertuples():
            title = str(getattr(row, "title", "") or "")
            if not FERMI_MARKS.search(title):
                continue
            lowered = title.lower()
            if not LEGAL_NEWS.search(lowered):
                continue
            if re.search(LEGAL_NEWS_EXCLUDE, lowered):
                continue
            published = pd.to_datetime(getattr(row, "published", None), errors="coerce", utc=True)
            words = set(re.findall(r"[a-z]+", lowered))
            if any(marks & words and pd.notna(when) and pd.notna(published)
                   and abs((published.tz_localize(None) - when).days) <= 21
                   for when, marks in covered):
                continue
            key = re.sub(r"[^a-z0-9가-힣]", "", lowered)[:70]
            events.append({
                "id": f"legalnews:{key}",
                "tier": "법적",
                "kind": "뉴스",
                "when": str(pd.Timestamp(row.published).date()) if pd.notna(row.published) else "",
                "form": "", "items": "", "excerpt": "",
                "title": title,
                "url": getattr(row, "url", ""),
                "source": getattr(row, "source", ""),
                "topic": topic_key(title),
                "확정": False,
            })
    return events


# ── 내부자 거래 ───────────────────────────────────────────────────────────────
# 경영진이 자기 돈을 어디에 거는지는 그들이 말하는 것보다 정확하다. 특히 이 회사는
# 상장 10개월 만에 CEO가 두 번 바뀌었고, 그 사이 내부자들이 무엇을 했는지가 판단의
# 한 축이다.
#
# **부여(A)는 알리지 않는다.** 회사가 준 주식은 본인의 판단이 아니다. 그런데 Form 4를
# 건수로만 보면 부여와 매수가 똑같이 '내부자 거래 1건'으로 보인다. 실제로 2026-08-14
# 신임 CEO의 468,750주 부여가 "CEO가 매수했다"로 퍼졌다. 코드 P와 A를 갈라서 P만
# 매수라고 부르는 것이 이 감지기의 존재 이유다.
#
# 매수(P)와 매도(S)는 무게가 다르다. 매도는 집을 사거나 세금을 내려고 할 수도 있지만,
# 매수는 그럴 이유가 없다. 그래서 매수는 규모와 무관하게 전부 보내고, 메시지 등급도
# 다르게 준다.
INSIDER_MAX_AGE_DAYS = 14


def insider_events(frame=None) -> list[dict]:
    """Form 4의 공개시장 매수·매도. 제출본 하나 = 사건 하나."""
    try:
        import insider as ins
        if frame is None:
            frame = ins.transactions()
        totals = ins.tally(frame)
        actions = ins.by_filing(frame)
    except Exception:
        return []
    events = []
    for item in actions:
        events.append({
            "id": f"insider:{item['accn']}:{item['code']}",
            "tier": "내부자",
            "kind": item["action"],
            "when": item["filed"],
            "form": "4", "items": "", "excerpt": "",
            "title": f"{item['person']} {item['action']}",
            "url": item["url"] or DASHBOARD_URL,
            "insider": item,
            "tally": totals,
        })
    return events


# ── 약정 기한 ─────────────────────────────────────────────────────────────────
# 차입 계약에 '테넌트를 언제까지 잡아야 하는지'가 조건으로 붙어 있다. 만기보다 이 날짜가
# 먼저 오고, 못 지키면 상환 부담이 즉시 커진다. 계약 커버리지(판정 ①)와 직결된 기한이라
# 지나가기 전에 알려야 한다. 매일 보내면 소음이므로 정해진 잔여일에만 한 번씩 보낸다.
# 카운트다운은 며칠 남았는지만 말한다. **채웠는지는 따로 봐야 한다.**
# 400MW 문턱을 넘는 순간과, 기한 당일의 충족·미충족 판정을 별도 사건으로 만든다.
COVENANT_MARKS = (90, 60, 30, 14, 7, 3, 1, 0)


def _covenant_rules():
    try:
        import maturity as mt
        return mt.covenants.__wrapped__() if hasattr(mt.covenants, "__wrapped__") else mt.covenants()
    except Exception:
        return None


def covenant_events(m: dict | None = None) -> list[dict]:
    """약정 알림 세 갈래.

    1. 카운트다운 — 정해진 잔여일에 한 번씩
    2. **문턱 통과** — 계약 MW가 기준을 넘은 순간 (좋은 소식이라 즉시 알린다)
    3. **기한 당일 판정** — 그날 충족인지 미충족인지 명시
    """
    rules = _covenant_rules()
    if rules is None or rules.empty:
        return []
    contracted = (m or {}).get("mw_contracted") or 0
    events = []
    for row in rules.to_dict("records"):
        left = row.get("남은 일수")
        if left is None or pd.isna(left):
            continue
        left = int(left)
        deadline = pd.Timestamp(row["deadline"]).date()
        threshold = row.get("threshold_mw")
        threshold = float(threshold) if pd.notna(threshold) else None
        met = threshold is not None and contracted >= threshold
        base = {
            "tier": "약정", "kind": row.get("facility", ""),
            "when": str(pd.Timestamp.today().normalize().date()),
            "form": "", "items": "", "excerpt": "",
            "title": row.get("condition", ""), "url": DASHBOARD_URL,
            "deadline": str(deadline), "consequence": row.get("consequence", ""),
            "threshold": threshold, "contracted": contracted, "met": met,
        }

        # 문턱을 넘었다 — 기한과 무관하게 알린다.
        if met:
            events.append({**base, "id": f"covenant:{deadline}:met",
                           "left": left, "phase": "충족"})
            continue

        # 기한 당일 또는 지난 뒤 — 미충족을 명시한다. 하루만 보낸다.
        if left <= 0:
            if left >= -1:
                events.append({**base, "id": f"covenant:{deadline}:verdict",
                               "left": left, "phase": "미충족"})
            continue

        mark = next((x for x in COVENANT_MARKS if left <= x), None)
        if mark is None:
            continue
        events.append({**base, "id": f"covenant:{deadline}:{mark}",
                       "left": left, "phase": "카운트다운"})
    return events


# ── 상태 변화(분기 실적) ──────────────────────────────────────────────────────
def snapshot(m: dict, steps_done: int | None = None) -> dict:
    """판정을 바꾸는 값만 추린다. 이 값들이 움직였을 때가 곧 판정이 움직인 때다."""
    return {
        "asof": str(pd.Timestamp(m["asof"]).date()) if m.get("asof") is not None else None,
        "revenue_q": m.get("revenue_q"),
        "op_cf_q": m.get("op_cf_q"),
        "mw_contracted": m.get("mw_contracted"),
        "customer_count": m.get("customer_count"),
        "steps_done": steps_done,
    }


def _crossed_up(before, after) -> bool:
    """0 이하에서 0 초과로 올라섰는가. None은 '아직 없음'으로 본다."""
    return (after or 0) > 0 and (before or 0) <= 0


def state_events(current: dict, previous: dict) -> list[dict]:
    """분기 실적이 반영됐거나 로드맵 단계가 움직였을 때."""
    if not previous:
        return []
    events = []

    if _crossed_up(previous.get("revenue_q"), current.get("revenue_q")):
        events.append({"id": f"state:revenue:{current['asof']}", "kind": "첫 매출 인식",
                       "detail": f"분기 매출 ${(current['revenue_q'] or 0)/1e6:,.1f}M — "
                                 f"로드맵 4단계. 매출 0이 페르미를 NextDecade와 같은 자리에 "
                                 f"두고 있었다."})

    if _crossed_up(previous.get("op_cf_q"), current.get("op_cf_q")):
        events.append({"id": f"state:opcf:{current['asof']}", "kind": "영업현금흐름 흑자 전환",
                       "detail": f"분기 영업CF ${(current['op_cf_q'] or 0)/1e6:+,.1f}M — "
                                 f"로드맵 5단계. 검증 표본에서 유지 6곳은 전부 도달했고 "
                                 f"붕괴 4곳은 전부 실패한 지점이다."})

    before_steps, after_steps = previous.get("steps_done"), current.get("steps_done")
    if before_steps is not None and after_steps is not None and after_steps != before_steps:
        direction = "달성" if after_steps > before_steps else "후퇴"
        events.append({"id": f"state:steps:{after_steps}:{current['asof']}",
                       "kind": f"로드맵 {after_steps}/5단계 {direction}",
                       "detail": f"이전 {before_steps}/5에서 바뀌었다."})

    if previous.get("asof") and current.get("asof") != previous.get("asof"):
        events.append({"id": f"state:quarter:{current['asof']}", "kind": "분기 실적 반영",
                       "detail": f"재무 기준일 {previous['asof']} → {current['asof']}. "
                                 f"핵심 판정이 새 수치로 다시 계산됐다."})
    return events


def status_icon(status: str) -> str:
    """화면과 같은 아이콘을 쓴다. 여기서 따로 매핑을 만들었다가 대시보드가 🔴인 항목을
    알림이 ⚪로 보내는 어긋남이 났다."""
    import theme as th
    return th.STATUS_ICON.get(status, "⚪")


def compose_state(events: list[dict], m: dict, verdicts: list[dict]) -> str:
    """한 분기에 여러 전환이 동시에 일어나므로 **한 통으로 묶는다.**

    첫 매출·흑자 전환·로드맵 단계·분기 반영이 같은 10-Q에서 한꺼번에 걸리는데, 따로 보내면
    같은 사건으로 네 번 울린다.
    """
    if not events:
        return ""
    single = len(events) == 1
    lines = [f"📊 <b>{_escape(events[0]['kind'] if single else '분기 실적 — 판정 변경')}</b>", ""]
    for event in events:
        if not single:
            lines.append(f"<b>• {_escape(event['kind'])}</b>")
        lines += [_escape(event["detail"]), ""]

    lines.append("<b>핵심 판정</b>")
    for item in verdicts:
        lines.append(f"{status_icon(item.get('status'))} {_escape(item['label'])} — "
                     f"{_escape(item['value'])}")
        lines.append(f"    <i>{_escape(item['verdict'])}</i>")
    if DASHBOARD_URL:
        lines.append(f'\n<a href="{_escape(DASHBOARD_URL)}">대시보드</a>')
    return "\n".join(lines)


# ── 주가 움직임의 사유 ────────────────────────────────────────────────────────
# 알림에 주가만 적으면 그게 이 소식 때문인지 시장 전체가 빠진 건지 알 수 없다.
# AI 인프라 바스켓(CORZ CRWV SMR OKLO APLD NBIS)과 견줘 개별 요인인지 동행인지 가른다.
BASKET_GAP = 3.0        # 이 %p 넘게 벌어지면 개별 요인으로 본다


def price_context() -> str:
    """주가 한 줄 + 왜 움직였는지. 실패하면 빈 문자열(알림은 그대로 나간다)."""
    raw = lambda f: getattr(f, "__wrapped__", f)          # noqa: E731
    try:
        import market
        frame, _ = raw(market.load_price)("FRMI")
        data = frame.dropna(subset=["close"])
        if len(data) < 2:
            return ""
        last, prev = data.iloc[-1], data.iloc[-2]
        move = (last["close"] / prev["close"] - 1) * 100
    except Exception:
        return ""

    line = f"주가 <b>${last['close']:,.2f}</b> · 전일 {move:+.1f}%"

    # 바스켓과 비교. basket_frame은 date + 티커별 컬럼인 **wide 포맷**이다.
    # long 포맷으로 읽다가 조용히 건너뛰었다.
    try:
        import market_flow as mf
        basket = raw(mf.basket_frame)()
        peers = [c for c in basket.columns if c not in ("date", "FRMI")]
        recent = basket.dropna(subset=peers, how="all").tail(2)
        if len(recent) == 2 and peers:
            moves = []
            for ticker in peers:
                before, after = recent.iloc[0][ticker], recent.iloc[1][ticker]
                if pd.notna(before) and pd.notna(after) and before:
                    moves.append((after / before - 1) * 100)
            if moves:
                peer = sum(moves) / len(moves)
                gap = move - peer
                verdict = (f"<b>개별 요인</b> ({gap:+.1f}%p 차이)"
                           if abs(gap) >= BASKET_GAP else "섹터 동행")
                line += f"\n· AI 인프라 바스켓 {peer:+.1f}% → {verdict}"
    except Exception:
        pass

    # 같은 날 페르미 기사. 무엇 때문에 움직였는지 짚을 실마리다.
    try:
        import news as nw
        articles = raw(nw.cached_articles)()
        articles["published"] = pd.to_datetime(articles["published"], errors="coerce", utc=True)
        day = pd.Timestamp(last["date"]).tz_localize("UTC")
        same_day = articles[articles["published"] >= day]
        same_day = same_day[same_day["title"].astype(str).apply(
            lambda t: bool(FERMI_MARKS.search(t)))]
        if not same_day.empty:
            named = same_day[same_day.get("group", "기타") != "기타"]["group"].value_counts()
            tag = f" · 최다 {named.index[0]}" if not named.empty else ""
            headline = same_day.sort_values("published", ascending=False).iloc[0]["title"]
            line += (f"\n· 당일 기사 {len(same_day)}건{tag}"
                     f"\n  <i>{_escape(str(headline)[:64])}</i>")
    except Exception:
        pass
    return line


# ── 메시지 ────────────────────────────────────────────────────────────────────
def _escape(text: str) -> str:
    return html.escape(str(text or ""))


def compose(event: dict, m: dict) -> str:
    icon = "🔴" if event["tier"] == "확정" else "🟡"
    contracted = m.get("mw_contracted") or 0
    customers = m.get("customer_count") or 0
    coverage = (m.get("contracted_vs_landed") or 0) * 100

    if event["tier"] == "확정":
        head = f"{icon} <b>공시 — {_escape(event['items'])}</b>"
        lines = [head, "",
                 f"{_escape(event['when'])} · {_escape(event['form'])} · 판독: {_escape(event['kind'])}",
                 f"<i>{_escape(event['title'])}</i>"]
        if event.get("excerpt"):
            lines += ["", f"<blockquote>{_escape(event['excerpt'])}</blockquote>"]
    elif event["tier"] == "애널리스트":
        icon = {"상향": "🟢", "목표가 상향": "🟢", "하향": "🔴", "목표가 인하": "🔴"}.get(
            event["title"], "🔵")
        lines = [f"{icon} <b>애널리스트 — {_escape(event['kind'])} {_escape(event['title'])}</b>", "",
                 f"{_escape(event['when'])} · {_escape(event.get('출처', ''))}"]
        if event.get("등급", "–") != "–":
            lines.append(f"등급: {_escape(event['등급'])}")
        if event.get("목표가", "–") != "–":
            target = f"목표가: {_escape(event['목표가'])}"
            if event.get("이전", "–") != "–":
                target += f" (이전 {_escape(event['이전'])})"
            lines.append(target)
        if event.get("이유", "–") != "–":
            lines.append(f"사유: {_escape(event['이유'])}")
        lines += ["", "목표주가는 예측이지 사실이 아니다. 다만 <b>인하 사유가 계약 지연이면</b> "
                      "핵심 판정 ①과 같은 축을 가리킨다."]
    elif event["tier"] == "약정":
        left = event.get("left", 0)
        phase = event.get("phase", "카운트다운")
        threshold = event.get("threshold")
        contracted = event.get("contracted") or 0
        if phase == "충족":
            lines = [f"🟢 <b>약정 충족 — {_escape(event['deadline'])} 기한</b>", "",
                     f"<b>{_escape(event['kind'])}</b>",
                     f"조건: {_escape(event['title'])}", "",
                     f"계약 {contracted:,.0f} MW ≥ 기준 {threshold:,.0f} MW — <b>충족했다.</b>"]
        elif phase == "미충족":
            lines = [f"🔴 <b>약정 미충족 — 기한 {_escape(event['deadline'])} 경과</b>", "",
                     f"<b>{_escape(event['kind'])}</b>",
                     f"조건: {_escape(event['title'])}", ""]
            if threshold:
                lines.append(f"계약 {contracted:,.0f} MW < 기준 {threshold:,.0f} MW — "
                             f"<b>{threshold - contracted:,.0f} MW 부족.</b>")
            lines += ["", f"결과: {_escape(event['consequence'])}"]
        else:
            mark = "🔴" if left <= 14 else ("🟡" if left <= 60 else "⏳")
            lines = [f"{mark} <b>약정 기한 D-{left}</b> · {_escape(event['deadline'])}", "",
                     f"<b>{_escape(event['kind'])}</b>",
                     f"조건: {_escape(event['title'])}"]
            if threshold:
                lines.append(f"현재 {contracted:,.0f} MW / 기준 {threshold:,.0f} MW "
                             f"— <b>{threshold - contracted:,.0f} MW 부족</b>")
            lines += ["", f"미충족 시: {_escape(event['consequence'])}", "",
                      "이 기한은 만기보다 먼저 온다. 테넌트를 못 잡으면 커버리지가 안 오르는 데서 "
                      "끝나지 않고 <b>상환 부담이 즉시 커진다.</b>"]
    elif event["tier"] == "레드플래그":
        icon = {"신규": "🚩", "해소": "🟢", "변경": "🔶"}.get(event["kind"], "🚩")
        lines = [f"{icon} <b>정기보고서 상태 {_escape(event['kind'])} — "
                 f"{_escape(event['title'])}</b>", "",
                 f"{_escape(event['when'])} · {_escape(event['form'])} 원문"]
        if event.get("detail"):
            line = f"판정: {_escape(event['detail'])}"
            if event.get("before"):
                line += f"  (이전: {_escape(event['before'])})"
            lines.append(line)
        if event.get("excerpt"):
            lines += ["", f"<blockquote>{_escape(event['excerpt'][:600])}</blockquote>"]
        if event.get("why"):
            lines += ["", f"<i>{_escape(event['why'])}</i>"]
        if event["kind"] == "해소":
            lines += ["", "이 항목이 <b>사라졌다.</b> 나쁜 소식만 판정을 바꾸는 게 아니다."]
        else:
            lines += ["", "이 문구는 회사가 <b>스스로 공시에 적은 것</b>이다. 추측이나 "
                          "언론 해석이 아니다."]
    elif event["tier"] == "건설속도":
        v = event["capex"]
        trail = " → ".join(f"{label} ${value/1e6:,.0f}M" for label, value in v["trail"])
        lines = [f"🏗️ <b>건설 속도 급감 — {_escape(v['quarter'])}</b>", "",
                 f"분기 capex <b>${v['current']/1e6:,.0f}M</b>",
                 f"직전 분기({_escape(v['prev_quarter'])}) ${v['previous']/1e6:,.0f}M 대비 "
                 f"<b>-{v['drop_prev']*100:.0f}%</b>",
                 f"직전 {v['baseline_n']}분기 평균 ${v['baseline']/1e6:,.0f}M 대비 "
                 f"<b>-{v['drop_avg']*100:.0f}%</b>", "",
                 f"<b>궤적</b>  {_escape(trail)}", "",
                 "가동 중 MW가 0인 동안 <b>건설 속도가 유일하게 관측 가능한 실적</b>이다. "
                 "건설 지연은 공시로 따로 나오지 않고 이 숫자로만 남는다.", "",
                 "다만 capex는 원래 울퉁불퉁하다 — 터빈 대금 같은 큰 건이 한 분기에 몰리면 "
                 "다음 분기는 자연히 준다. <b>두 분기 연속 꺾이는지</b>를 봐야 확정이다."]
    elif event["tier"] == "법적":
        confirmed = event.get("확정")
        if confirmed:
            lines = [f"⚖️ <b>법적·규제 — 공시 확정</b>", "",
                     f"{_escape(event['when'])} · {_escape(event['form'])} 원문", "",
                     f"<blockquote>{_escape(event['excerpt'])}</blockquote>", "",
                     "공시 원문에서 확인된 <b>실제 사건</b>이다. 위험요인의 가정법 문장은 "
                     "걸러냈다."]
        else:
            lines = [f"⚖️ <b>법적·규제 — 뉴스</b>", "",
                     f"{_escape(event['when'])} · {_escape(event.get('source', ''))}",
                     f"<i>{_escape(event['title'])}</i>", "",
                     "공시로 확정되기 전까지는 소문으로 취급한다."]
        lines += ["", "법적 리스크는 계약 커버리지를 직접 바꾸지는 않는다. 다만 "
                      "<b>재융자와 테넌트 실사에 걸린다</b> — 2027-08-10 만기 $444.9M을 "
                      "다시 빌려야 하고, 15년 리스에 서명할 고객은 이 항목을 반드시 본다."]
    elif event["tier"] == "내부자":
        item = event["insider"]
        totals = event.get("tally") or {}
        buying = item["code"] == "P"
        who = _escape(item["person"])
        role = " · ".join(x for x in (item["roles"], item["officer_title"]) if x)
        span = (_escape(item["date"]) if item["date"] == item["date_end"]
                else f"{_escape(item['date'])}~{_escape(item['date_end'])}")
        price = ""
        if item["price_low"]:
            price = (f" @ ${item['price_low']:,.2f}" if item["price_low"] == item["price_high"]
                     else f" @ ${item['price_low']:,.2f}~${item['price_high']:,.2f}")
        lines = [f"{'🟢' if buying else '🔴'} <b>내부자 {_escape(item['action'])} — {who}</b>", "",
                 f"{span} 거래 · {_escape(item['filed'])} 신고"]
        if role:
            lines.append(_escape(role))
        lines.append(f"<b>{item['shares']:,.0f}주</b>{price}"
                     + (f" ≈ ${item['value'] / 1e6:,.1f}M" if item["value"] else ""))
        if item["share_pct"] is not None:
            lines.append((f"기존 보유의 <b>{item['share_pct']:.1f}%</b>만큼 늘렸다"
                          if buying else f"보유 지분의 <b>{item['share_pct']:.1f}%</b>를 팔았다")
                         + (f" · 현재 {item['owned_after']:,.0f}주"
                            if item["owned_after"] is not None else ""))
        if item["planned"]:
            lines.append("<i>10b5-1 사전계획 거래 — 매매 시점을 미리 정해둔 건이라 "
                         "지금 시점의 판단으로 읽으면 안 된다.</i>")
        # 개별 건보다 누적 대비가 말이 된다. 매도 한 건은 집을 사는 것일 수도 있지만,
        # 상장 이래 매수가 0인 것은 다른 얘기다.
        if totals:
            lines += ["", f"<b>누적</b> — 매수 {totals['buy_shares']:,.0f}주"
                          + (f" (${totals['buy_value'] / 1e6:,.1f}M, {totals['buyers']}명)"
                             if totals["buy_shares"] else " (없음)")
                          + f" / 매도 {totals['sell_shares']:,.0f}주"
                          + (f" (${totals['sell_value'] / 1e6:,.1f}M, {totals['sellers']}명)"
                             if totals["sell_shares"] else " (없음)")]
        if buying:
            lines += ["", "<b>본인 돈으로 시장에서 산 것이다.</b> 회사가 준 무상 부여(코드 A)와 "
                          "다르다. 이 회사에서 지금까지 없던 종류의 신호다."]
        else:
            lines += ["", "매도 자체는 개인 사정일 수 있다. 보는 것은 <b>시점과 누적</b>이다 — "
                          "약정 기한이나 실적 발표 직전인지, 그리고 반대편에 매수가 있는지."]
    elif event["tier"] == "테넌트":
        lines = [f"🚨 <b>테넌트 악재 — {_escape(event['kind'])}</b>", "",
                 f"{_escape(event['when'])} · {_escape(event.get('source', ''))}",
                 f"<i>{_escape(event['title'])}</i>", "",
                 f"{_escape(event['kind'])}는 현재 <b>유일한 고객</b>이다. "
                 f"이 회사가 계약을 이행하지 못하면 커버리지가 0이 된다."]
    else:
        nonbinding = event["kind"].startswith("비구속")
        head = f"{'⚪' if nonbinding else icon} <b>뉴스 — {_escape(event['kind'])}</b>"
        if event["kind"] == "비구속(계획·제휴)":
            tail = ("제목에 <b>서명·체결 동사가 없다</b> — 아직 계획·제휴 단계로 보인다. "
                    "커버리지는 바뀌지 않지만 규모가 크면 판단 재료다. "
                    "8-K Item 1.01로 확정되는지 확인해야 한다.")
        elif nonbinding:
            tail = "LOI·MOU는 구속력이 없어 커버리지를 바꾸지 않는다."
        else:
            tail = "공시로 확정되기 전까지는 소문으로 취급한다."
        lines = [head, "",
                 f"{_escape(event['when'])} · {_escape(event.get('source', ''))}",
                 f"<i>{_escape(event['title'])}</i>", "", tail]

    lines += ["", f"현재 기록: <b>{contracted:,.0f} MW · 고객 {customers}곳 · 커버리지 "
                  f"{coverage:.1f}%</b>"]
    context = price_context()
    if context:
        lines += ["", context]
    if event.get("url"):
        lines.append(f'\n<a href="{_escape(event["url"])}">원문 보기</a>')
    if DASHBOARD_URL:
        lines.append(f'<a href="{_escape(DASHBOARD_URL)}">대시보드</a>')
    return "\n".join(lines)


# ── 실행 ──────────────────────────────────────────────────────────────────────
def check(m: dict, articles: pd.DataFrame, filings: pd.DataFrame,
          read_text=None, verdicts=None, steps_done=None) -> dict:
    """새 사건을 찾아 보낸다. {sent, skipped, error, seeded}."""
    store = _seen()
    known = set((store.get("ids") or {}).keys())
    first_run = not store.get("initialized")

    # 첫 실행에는 어차피 보내지 않으므로 원문을 받지 않는다.
    events = (filing_events(filings, read_text=None if first_run else read_text, known=known)
              + covenant_events(m)
              + analyst_events(articles)
              + insider_events()
              + capex_events(m, filings)
              + redflag_events()
              + legal_events(articles)
              + news_events(articles)
              + tenant_events())

    # 상태 변화는 지문이 아니라 이전 값과의 비교로 잡는다.
    #
    # **스냅샷은 발송에 성공했을 때만 앞으로 민다.** 무조건 갱신하면, 분기 실적이 들어온
    # 순간 텔레그램이 죽어 있었을 때 다음 실행에서는 '이전 값 = 새 값'이 되어 그 알림이
    # 영원히 사라진다. 1년에 네 번뿐인 알림을 그렇게 잃으면 안 된다.
    current = snapshot(m, steps_done)
    changes = state_events(current, store.get("snapshot") or {})

    # 오래된 건 기록만 하고 넘어간다. 뉴스는 훨씬 짧게 본다(위 설명 참고).
    today = pd.Timestamp.today().normalize()
    topics = dict(store.get("topics") or {})
    fresh, aged_ids, new_topics = [], [], {}
    for event in events:
        if event["id"] in known:
            continue
        limit = {"미확인": NEWS_MAX_AGE_DAYS, "테넌트": NEWS_MAX_AGE_DAYS,
                 "애널리스트": ANALYST_MAX_AGE_DAYS,
                 "내부자": INSIDER_MAX_AGE_DAYS,
                 "건설속도": CAPEX_MAX_AGE_DAYS,
                 "레드플래그": REDFLAG_MAX_AGE_DAYS,
                 "법적": LEGAL_MAX_AGE_DAYS}.get(event["tier"], MAX_EVENT_AGE_DAYS)
        when = pd.to_datetime(event.get("when"), errors="coerce")
        if pd.notna(when) and (today - when).days > limit:
            aged_ids.append(event["id"])
            continue
        # 같은 사건을 다른 매체가 다시 쓴 경우. 이미 알린 사건이면 조용히 기록만 한다.
        topic = event.get("topic")
        if topic:
            now = pd.Timestamp.now(tz="UTC")
            recent = any(same_topic(topic, seen_topic)
                         and pd.notna(pd.to_datetime(when_sent, errors="coerce"))
                         and (now - pd.to_datetime(when_sent, errors="coerce")).days <= TOPIC_WINDOW_DAYS
                         for seen_topic, when_sent in {**topics, **new_topics}.items())
            if recent:
                aged_ids.append(event["id"])
                continue
            new_topics[topic] = now.isoformat()
        fresh.append(event)
    fresh_changes = [event for event in changes if event["id"] not in known]

    if first_run:
        # 과거 것을 몰아 보내지 않는다. 지금 상태를 기준선으로 삼는다.
        store["snapshot"] = current
        seeded_topics = {e["topic"]: pd.Timestamp.now(tz="UTC").isoformat()
                         for e in events if e.get("topic")}
        _remember(store, [event["id"] for event in events], first_run_done=True,
                  topics={**(store.get("topics") or {}), **seeded_topics})
        ok, error = send(
            "✅ <b>페르미 알림 켜짐</b>\n\n"
            f"감시 중: 8-K Item {' / '.join(WATCHED_ITEMS)} · 계약 키워드 기사 · "
            f"테넌트({', '.join(tenants()) or '없음'}) 악재 · 분기 실적 판정\n"
            f"현재 기록: <b>{(m.get('mw_contracted') or 0):,.0f} MW · "
            f"고객 {m.get('customer_count') or 0}곳</b>\n\n"
            f"기존 {len(events)}건은 기준선으로 처리했다. 다음 신규 건부터 알린다.")
        return {"sent": 0, "skipped": len(events), "seeded": True,
                "error": "" if ok else error}

    if not (fresh or fresh_changes):
        store["snapshot"] = current          # 바뀐 게 없으니 그대로 밀어도 잃을 알림이 없다
        _remember(store, aged_ids, first_run_done=True)
        return {"sent": 0, "skipped": len(events), "seeded": False, "error": ""}

    sent, failures, changes_failed = [], [], False
    if fresh_changes:
        ok, error = send(compose_state(fresh_changes, m, verdicts or []))
        if ok:
            sent += [event["id"] for event in fresh_changes]
        else:
            failures.append(error)
            changes_failed = True
    for event in fresh:
        ok, error = send(compose(event, m))
        if ok:
            sent.append(event["id"])
        else:
            failures.append(error)

    # 상태 알림을 하나라도 못 보냈으면 이전 스냅샷을 남겨 다음 실행에서 다시 잡게 한다.
    if not changes_failed:
        store["snapshot"] = current
    store["last_sent"] = pd.Timestamp.now(tz="UTC").isoformat() if sent else store.get("last_sent")
    # 실패한 건은 기억하지 않는다. 다음 크론에서 다시 시도한다.
    sent_topics = {e["topic"]: new_topics[e["topic"]] for e in fresh
                   if e.get("topic") in new_topics and e["id"] in sent}
    _remember(store, sent + aged_ids, first_run_done=True,
              topics={**(store.get("topics") or {}), **sent_topics})
    return {"sent": len(sent), "skipped": len(events) - len(fresh), "seeded": False,
            "error": "; ".join(failures[:3])}


def self_test(m: dict, filings: pd.DataFrame, articles: pd.DataFrame) -> str:
    """실제로 도착하는지 확인용. 감지기를 과거 데이터에 돌려 무엇이 잡히는지 함께 보낸다.

    알림은 조용히 죽는 게 최악이라, 켜자마자 한 번은 눈으로 확인해야 한다.
    """
    caught = filing_events(filings, read_text=None) + news_events(articles)
    lines = ["🧪 <b>알림 테스트</b>", "",
             f"감시 대상 Item: {' / '.join(f'{k} {v}' for k, v in WATCHED_ITEMS.items())}",
             f"과거 데이터에서 잡히는 건: <b>{len(caught)}건</b>", ""]
    for event in caught[:5]:
        mark = "🔴" if event["tier"] == "확정" else "🟡"
        lines.append(f"{mark} {_escape(event['when'])} {_escape(event['items'] or '뉴스')} — "
                     f"{_escape(event['title'][:70])}")
    lines += ["", f"현재 기록: <b>{(m.get('mw_contracted') or 0):,.0f} MW · "
                  f"고객 {m.get('customer_count') or 0}곳</b>",
              "", "이 메시지가 보이면 테넌트 2호 공시도 같은 경로로 온다."]
    text = "\n".join(lines)
    ok, error = send(text)
    return "" if ok else error


def status() -> dict:
    """대시보드가 읽는 쪽 — 알림이 살아 있는지 화면에서 확인할 수 있어야 한다."""
    store = _seen()
    return {
        "configured": configured(),
        "initialized": bool(store.get("initialized")),
        "watching": len(store.get("ids") or {}),
        "last_check": store.get("last_check"),
        "last_sent": store.get("last_sent"),
        "age": dc.age_seconds(SEEN_CACHE),
    }


def chat_id() -> str:
    """봇에게 아무 메시지나 보낸 뒤 이걸 실행하면 chat_id가 나온다.

    직접 getUpdates를 읽어 눈으로 찾는 과정을 줄인다. 토큰만 .env에 있으면 된다.
    """
    token = os.environ.get("TELEGRAM_BOT_TOKEN")
    if not token:
        return "TELEGRAM_BOT_TOKEN이 없다"
    try:
        payload = requests.get(f"https://api.telegram.org/bot{token}/getUpdates",
                               timeout=TIMEOUT).json()
    except Exception as error:
        return f"{type(error).__name__}: {error}"
    if not payload.get("ok"):
        return f"토큰이 거부됐다: {payload.get('description')}"
    for update in reversed(payload.get("result") or []):
        chat = ((update.get("message") or update.get("channel_post") or {}).get("chat") or {})
        if chat.get("id"):
            return str(chat["id"])
    return "메시지가 없다 — 텔레그램에서 봇에게 아무 말이나 보낸 뒤 다시 실행해라"


if __name__ == "__main__":
    print(chat_id())
