"""법적·규제 사건을 공시 원문에서 찾는다.

**왜 필요했나.** 2026-07-30 EDNY 소환장과 2026-08-03 SEC 문서요구가 8/14 10-Q에
적혀 있었는데 알림이 한 통도 안 갔다. 기존 감지기가 전부 계약 커버리지만 보기
때문이다 — filing_events는 8-K Item 1.01/1.02/2.01만 보는데 이건 10-Q에 실렸고,
news_events는 제목에 '서명 동사 + 용량 명사'를 요구하는데 소환장 기사에는 없다.

**가장 큰 함정은 위험요인이다.** 10-K에는 "우리는 조사를 받을 수도 있다"가 반드시
적혀 있다. 그걸 실제 사건으로 읽으면 매 분기 헛알림이 나간다. 실측하면 10-K 한 건에
그런 문장이 6개 있다. 그래서 세 겹으로 거른다.

    ① TRIGGERS  — 실제 사건을 가리키는 말이 있는가 (subpoena, wells notice …)
    ② ACTUAL    — 벌어졌다고 말하는가 (received, was served …)
    ③ HYPOTHETICAL — 가정법이면 버린다 (may, could, if we, risk that …)

실측 결과(2025-11 10-Q / 2026-03 10-K / 2026-05 10-Q / 2026-08 10-Q):
트리거 문장 12건 중 **실제 사건 1건만 채택, 보일러플레이트 11건 전부 기각.**

**같은 사실이 다음 분기 10-Q에 또 적힌다.** 그래서 문장 지문으로 기억해 두고,
처음 본 지문만 사건으로 만든다.
"""

import hashlib
import re

import pandas as pd
import requests

import diskcache as dc
import sec_edgar as sec

CACHE = "legal_findings"
CACHE_MAX_AGE = 86400 * 3650     # 접수된 공시는 변하지 않는다. 만료 개념이 없다.
SCAN_DAYS = 150                  # 처음 켤 때 과거를 무한정 훑지 않는다.
TIMEOUT = 90
SNIPPET = 700

SCAN_FORMS = ("10-Q", "10-K", "8-K", "8-K/A", "10-Q/A", "10-K/A")

# ① 실제로 벌어진 법적·규제 사건을 가리키는 말.
TRIGGERS = re.compile(
    r"subpoena|grand jury|indictment|wells notice|civil investigative demand|"
    r"formal order of investigation|cease[- ]and[- ]desist|consent decree|"
    r"enforcement action|deferred prosecution|search warrant|"
    r"(?:sec|doj|department of justice|attorney general|ftc|ferc)\s+"
    r"(?:investigation|inquiry|subpoena|probe)", re.I)

# ② 벌어졌다고 말하는가. 과거형·수령 표현.
ACTUAL = re.compile(
    r"\b(received|receipt of|was served|were served|has been served|served with|"
    r"issued (?:to|a|an)|we received|the company received|notified (?:us|the company)|"
    r"filed against|commenced|initiated|entered into a consent|"
    r"is cooperating|are cooperating|fully cooperating)\b", re.I)

# ③ 가정법·일반론이면 실제 사건이 아니다.
HYPOTHETICAL = re.compile(
    r"\b(may|might|could|would|should|if we|in the event|risk that|no assurance|"
    r"from time to time|are not currently|is not currently|we do not expect|"
    r"any such|potential|potentially|possible|expects? to|intends? to)\b", re.I)


def _clean(html: str) -> str:
    text = re.sub(r"(?is)<(script|style)[^>]*>.*?</\1>", " ", html)
    text = re.sub(r"<[^>]+>", " ", text)
    text = re.sub(r"&#x00a0;|&nbsp;?|&#160;", " ", text)
    text = re.sub(r"&#8217;|&rsquo;|&#8216;|&lsquo;", "'", text)
    text = re.sub(r"&#8220;|&#8221;|&ldquo;|&rdquo;", '"', text)
    text = re.sub(r"&amp;", "&", text)
    return re.sub(r"\s+", " ", text)


def full_text(url: str) -> str:
    """공시 전문. filing_review._text는 12,000자에서 자르는데, 소환장 문단은
    10-Q 30만 자의 뒤쪽에 있어 그 방식으로는 영원히 안 잡힌다."""
    try:
        return _clean(requests.get(url, headers=sec._HEADERS, timeout=TIMEOUT).text)
    except Exception:
        return ""


def _sentences(text: str) -> list[str]:
    return re.split(r"(?<=[.;])\s+(?=[A-Z(])", text)


def _fingerprint(sentence: str) -> str:
    """숫자·기호를 지운 낱말만으로 지문을 만든다. 같은 사실이 다음 분기에 표현만
    조금 바뀌어 다시 실려도 같은 지문이 되게 한다."""
    words = re.findall(r"[a-z]+", sentence.lower())
    return hashlib.sha1(" ".join(words[:40]).encode()).hexdigest()[:16]


def scan(text: str) -> list[str]:
    """원문에서 실제 법적·규제 사건 문장만 뽑는다."""
    out = []
    for sentence in _sentences(text):
        if not TRIGGERS.search(sentence):
            continue
        if not ACTUAL.search(sentence):
            continue
        if HYPOTHETICAL.search(sentence):
            continue
        out.append(sentence.strip())
    return out


def findings(filings: pd.DataFrame | None = None, refresh: bool = True) -> list[dict]:
    """새 공시만 훑어 법적 사건을 돌려준다. 훑은 공시는 접수번호로 기억한다."""
    store = dc.load_json(CACHE, CACHE_MAX_AGE) or {}
    scanned = set(store.get("scanned") or [])
    hits = list(store.get("hits") or [])

    if refresh:
        if filings is None:
            loader = getattr(sec.load_filings, "__wrapped__", sec.load_filings)
            filings = loader()
        if filings is not None and not filings.empty:
            cutoff = pd.Timestamp.today().normalize() - pd.Timedelta(days=SCAN_DAYS)
            todo = filings[filings["form"].astype(str).str.upper().isin(SCAN_FORMS)
                           & (pd.to_datetime(filings["filed"]) >= cutoff)]
            changed = False
            for row in todo.itertuples():
                if row.accn in scanned or not row.url:
                    continue
                text = full_text(row.url)
                if not text:
                    continue        # 실패한 건은 기억하지 않는다. 다음에 다시 시도한다.
                for sentence in scan(text):
                    hits.append({
                        "accn": row.accn,
                        "form": row.form,
                        "filed": str(pd.Timestamp(row.filed).date()),
                        "url": row.url,
                        "fingerprint": _fingerprint(sentence),
                        "text": sentence[:SNIPPET],
                    })
                scanned.add(row.accn)
                changed = True
            if changed:
                dc.save_json(CACHE, {"scanned": sorted(scanned)[-300:], "hits": hits[-100:]})
    dc.record_health("법적·규제 스캔", len(scanned))

    # 같은 사실이 여러 분기에 걸쳐 실린다. 지문당 가장 이른 공시만 남긴다.
    first: dict[str, dict] = {}
    for hit in sorted(hits, key=lambda h: h["filed"]):
        first.setdefault(hit["fingerprint"], hit)
    return sorted(first.values(), key=lambda h: h["filed"], reverse=True)
