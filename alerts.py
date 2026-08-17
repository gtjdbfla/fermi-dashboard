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

import pandas as pd
import requests

import diskcache as dc

SEEN_CACHE = "alerts_seen"
SEEN_MAX_AGE = 86400 * 3650      # 사실상 만료 없음. 중복 발송을 막는 것이 목적이다.
SEEN_KEEP = 400                  # 무한정 쌓이지 않게 최근 것만 남긴다.
DASHBOARD_URL = os.environ.get("DASHBOARD_URL", "")
TIMEOUT = 15

# 8-K 항목 코드 중 계약 커버리지를 바꿀 수 있는 것만. 5.02(임원 변경)나 2.02(실적)는
# 이 회사에서 두 달에 네 번씩 나와서 넣으면 알림이 소음이 된다.
WATCHED_ITEMS = {
    "1.01": "중요 계약 체결",
    "1.02": "중요 계약 해지",       # 기존 테넌트 이탈. NuScale이 UAMPS를 잃은 것이 이 항목이다.
    "2.01": "인수·처분 완료",
}

# 원문에 이 단어가 있으면 계약 쪽으로 본다.
LEASE_WORDS = ["lease", "tenant", "colocation", "co-location", "offtake", "off-take",
               "take-or-pay", "power purchase", "ppa", "hyperscaler", "data center customer",
               "capacity agreement", "anchor customer"]
# 이 단어만 있고 위가 없으면 자금조달로 본다.
FINANCE_WORDS = ["note purchase", "convertible note", "indenture", "underwrit", "placement agent",
                 "credit agreement", "term loan", "registration rights", "securities purchase"]

# 뉴스는 훨씬 엄격하게 건다. '계약·테넌트' 분류만으로는 지금도 38건이 걸려 알림이 못 된다.
# 서명 동사와 용량/테넌트 명사가 **둘 다** 있어야 한다.
SIGN_VERBS = r"(sign|signs|signed|signing|execut|enters?\s+into|entered\s+into|ink|inks|inked|" \
             r"secur|award|finaliz|close[sd]?\s+on|definitive|binding|체결|계약을\s*맺)"
TENANT_NOUNS = r"(tenant|lease|offtake|off-take|take-or-pay|colocation|co-location|hyperscaler|" \
               r"anchor\s+customer|\d\s*(mw|gw)\b|megawatt|gigawatt|테넌트|임차|임대차)"
# 자금조달 기사가 서명 동사에 걸려 들어오는 것을 막는다.
NEWS_EXCLUDE = r"(convertible|offering|notes\s+due|underwrit|placement|dilut|증자|사채|" \
               r"price\s+target|analyst|목표주가)"
# 구속력 없는 건 커버리지를 못 바꾼다. 거르지는 않되 제목에 그렇다고 적는다 —
# 2025-09 Siemens LOI가 이 부류였고, 계약처럼 읽히면 판단을 흐린다.
NONBINDING = r"(letter\s+of\s+intent|\bloi\b|\bmou\b|memorandum|non-?binding|framework|" \
             r"preliminary|의향서|양해각서)"


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


def _remember(store: dict, ids, first_run_done: bool = True) -> None:
    stamp = pd.Timestamp.now(tz="UTC").isoformat()
    sent = dict(store.get("ids") or {})
    for key in ids:
        sent[key] = stamp
    # 오래된 것부터 버린다. 이미 사라진 기사를 다시 보낼 일은 없다.
    if len(sent) > SEEN_KEEP:
        sent = dict(sorted(sent.items(), key=lambda kv: kv[1])[-SEEN_KEEP:])
    dc.save_json(SEEN_CACHE, {"ids": sent, "initialized": first_run_done,
                              "last_check": stamp,
                              "last_sent": store.get("last_sent")})


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
    """제목에 '서명 동사 + 용량/테넌트 명사'가 함께 있는 기사만."""
    if articles is None or articles.empty:
        return []
    events = []
    for row in articles.itertuples():
        title = str(getattr(row, "title", "") or "")
        lowered = title.lower()
        if not (re.search(SIGN_VERBS, lowered) and re.search(TENANT_NOUNS, lowered)):
            continue
        if re.search(NEWS_EXCLUDE, lowered):
            continue
        key = re.sub(r"[^a-z0-9가-힣]", "", lowered)[:70]
        events.append({
            "id": f"news:{key}",
            "tier": "미확인",
            "kind": "비구속(LOI/MOU)" if re.search(NONBINDING, lowered) else "계약",
            "when": str(pd.Timestamp(row.published).date()) if pd.notna(row.published) else "",
            "form": "",
            "items": "",
            "title": title,
            "url": getattr(row, "url", ""),
            "excerpt": "",
            "source": getattr(row, "source", ""),
        })
    return events


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
    else:
        nonbinding = event["kind"].startswith("비구속")
        head = f"{'⚪' if nonbinding else icon} <b>뉴스 — {_escape(event['kind'])}</b>"
        lines = [head, "",
                 f"{_escape(event['when'])} · {_escape(event.get('source', ''))}",
                 f"<i>{_escape(event['title'])}</i>", "",
                 "LOI·MOU는 구속력이 없어 커버리지를 바꾸지 않는다." if nonbinding
                 else "공시로 확정되기 전까지는 소문으로 취급한다."]

    lines += ["", f"현재 기록: <b>{contracted:,.0f} MW · 고객 {customers}곳 · 커버리지 "
                  f"{coverage:.1f}%</b>"]
    if event.get("url"):
        lines.append(f'\n<a href="{_escape(event["url"])}">원문 보기</a>')
    if DASHBOARD_URL:
        lines.append(f'<a href="{_escape(DASHBOARD_URL)}">대시보드</a>')
    return "\n".join(lines)


# ── 실행 ──────────────────────────────────────────────────────────────────────
def check(m: dict, articles: pd.DataFrame, filings: pd.DataFrame,
          read_text=None) -> dict:
    """새 사건을 찾아 보낸다. {sent, skipped, error, seeded}."""
    store = _seen()
    known = set((store.get("ids") or {}).keys())
    first_run = not store.get("initialized")

    # 첫 실행에는 어차피 보내지 않으므로 원문을 받지 않는다.
    events = (filing_events(filings, read_text=None if first_run else read_text, known=known)
              + news_events(articles))
    fresh = [event for event in events if event["id"] not in known]

    if first_run:
        # 과거 것을 몰아 보내지 않는다. 지금 상태를 기준선으로 삼는다.
        _remember(store, [event["id"] for event in events], first_run_done=True)
        ok, error = send(
            "✅ <b>페르미 알림 켜짐</b>\n\n"
            f"감시 중: 8-K Item {' / '.join(WATCHED_ITEMS)} · 계약 키워드 기사\n"
            f"현재 기록: <b>{(m.get('mw_contracted') or 0):,.0f} MW · "
            f"고객 {m.get('customer_count') or 0}곳</b>\n\n"
            f"기존 {len(events)}건은 기준선으로 처리했다. 다음 신규 건부터 알린다.")
        return {"sent": 0, "skipped": len(events), "seeded": True,
                "error": "" if ok else error}

    if not fresh:
        _remember(store, [], first_run_done=True)
        return {"sent": 0, "skipped": len(events), "seeded": False, "error": ""}

    sent, failures = [], []
    for event in fresh:
        ok, error = send(compose(event, m))
        if ok:
            sent.append(event["id"])
        else:
            failures.append(error)

    store["last_sent"] = pd.Timestamp.now(tz="UTC").isoformat() if sent else store.get("last_sent")
    # 실패한 건은 기억하지 않는다. 다음 크론에서 다시 시도한다.
    _remember(store, sent, first_run_done=True)
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
