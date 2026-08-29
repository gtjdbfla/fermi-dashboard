"""정기보고서(10-Q·10-K)의 '회사 상태' 공시를 범주별로 추적한다.

**왜 legal.py로 부족했나.** legal.py는 *사건*을 잡는다 — 소환장이 왔다, 기소됐다.
그래서 3중 게이트(사건어 + 과거형 + 가정법 아님)를 건다. 그런데 이 파일이 잡는 것들은
사건이 아니라 **상태**다. 계속기업 불확실성, 내부통제 취약점, 유동성 부족은 한 번
생기면 분기마다 반복해서 실린다. 그리고 그 문장들은 본질적으로 "may / could"를
품고 있어서 legal.py의 가정법 게이트에 걸려 통째로 버려진다.

실제로 그렇게 놓쳤다. 2026-05-15 10-Q와 2026-08-14 10-Q **두 분기 연속** going
concern 문구가 있었는데 알림도 리포트도 한 줄이 없었다. 사용자가 "파산 찌라시 있냐"고
물어서야 발견했다.

**상태는 사건과 다르게 다뤄야 한다.**
  없음 → 있음   🔴 새로 생겼다      (알린다)
  있음 → 있음   ⚪ 지속 중          (알리지 않는다. 리포트 상태에만 적는다)
  있음 → 없음   🟢 해소됐다         (알린다 — 좋은 소식도 판정을 바꾼다)

**계속기업 불확실성은 2단계로 읽어야 한다.** ASC 205-40은 ① 경영진 계획 고려 전
중대한 의문이 있는지 ② 계획으로 해소되는지를 따로 결론낸다. 둘을 뭉뚱그리면
"의문 제기됨"과 "해소 실패"가 같아 보인다. 실제 무게는 하늘과 땅 차이다.
"""

import re

import pandas as pd
import requests

import diskcache as dc
import sec_edgar as sec

CACHE = "redflags"
CACHE_MAX_AGE = 86400 * 3650
TIMEOUT = 90
FORMS = ("10-Q", "10-K", "10-Q/A", "10-K/A")
SCAN_DAYS = 400        # 분기보고서 4~5개를 덮는다

# 범주 정의. legal.py가 이미 맡은 '사건'(소환장·기소)은 여기서 제외해 중복 알림을 막는다.
CATEGORIES = {
    "계속기업 불확실성": {
        "pattern": r"substantial doubt about (?:the Company's|our) ability to continue as a going concern",
        "why": "회사가 1년 내 재무 의무를 감당할 수 있는지에 대한 공식 의문 표기다. "
               "정기보고서에서 가장 무거운 단일 문구다.",
    },
    "내부통제 중대한 취약점": {
        "pattern": r"material weakness(?:es)? in (?:our|the Company's) internal control",
        "why": "재무제표 숫자 자체의 신뢰도에 영향을 준다. 대시보드가 XBRL을 근거로 "
               "판정하므로 그 근거의 품질 문제다.",
    },
    "공시통제 미흡": {
        "pattern": r"disclosure controls and procedures were not effective",
        "why": "회사가 스스로 '공시 절차가 효과적이지 않다'고 인정한 것이다.",
    },
    "유동성 부족 서술": {
        "pattern": r"not sufficient to (?:satisfy|meet|fund) (?:the Company's|our)?\s*"
                   r"(?:financial )?obligations",
        "why": "보유 자원이 향후 1년 의무에 못 미친다는 회사 자체 서술이다.",
    },
    "증권 집단소송": {
        "pattern": r"putative (?:securities )?class action|securities class action",
        "why": "IPO 공시 내용에 대한 책임을 다투는 소송이다. 합의금과 경영진 시간을 잡아먹는다.",
    },
    # ── 아래 넷은 현재 페르미에 없다. 생기면 매우 무거우므로 미리 깔아둔다. ──
    #
    # **전부 보일러플레이트와 싸워야 한다.** 정기보고서에는 "손상 여부를 검토한다",
    # "재작성이 필요하게 되면", "최저 주가 요건을 못 맞추면 상장폐지될 수 있다" 같은
    # 회계정책·위험요인 문구가 늘 실려 있다. 그래서 **실제로 벌어졌음을 말하는
    # 표현만** 고른다 — 검토가 아니라 인식, 가능성이 아니라 통지 수령.
    "손상차손 인식": {
        "pattern": r"(?:recorded|recognized|incurred)\s+(?:an?\s+)?(?:non-?cash\s+)?"
                   r"impairment\s+(?:charge|loss|expense)|"
                   r"impairment\s+(?:charge|loss)\s+of\s+\$",
        "why": "자산 가치를 장부에서 깎았다는 뜻이다. 무매출 개발사에서는 "
               "'짓던 것이 계획대로 안 된다'의 회계적 표현이다.",
    },
    "재무제표 재작성": {
        "pattern": r"(?:have|has|we)\s+restated\s+(?:our|the)\s+(?:previously issued\s+)?"
                   r"(?:consolidated\s+)?financial statements|"
                   r"restatement of (?:our|the) previously issued|"
                   r"should no longer be relied upon",
        "why": "이미 발표한 숫자가 틀렸다는 뜻이다. 대시보드 판정의 근거가 통째로 흔들린다.",
    },
    "감사인 변경": {
        "pattern": r"dismissed\s+.{0,70}?as (?:our|its|the Company's) independent registered "
                   r"public accounting firm|"
                   r"(?:engaged|appointed)\s+.{0,70}?as (?:our|its|the Company's)\s+(?:new\s+)?"
                   r"independent registered public accounting firm|"
                   r"resign(?:ed|ation) (?:as|of) .{0,50}?independent registered public accounting",
        "why": "회계법인이 바뀌는 것은 회계 쟁점이 있었다는 신호일 수 있다. "
               "특히 내부통제 취약점이 이미 있는 회사에서는 무겁다.",
    },
    "상장규정 위반 통지": {
        "pattern": r"received\s+(?:a\s+)?(?:written\s+)?(?:deficiency\s+)?notice\s+from\s+"
                   r"(?:the\s+)?Nasdaq|"
                   r"Nasdaq\s+.{0,40}?deficiency (?:letter|notice)|"
                   r"not in compliance with\s+(?:Nasdaq\s+)?Listing Rule",
        "why": "상장 유지 요건을 못 맞췄다는 통지다. 유예기간과 이의절차가 뒤따른다.",
    },
}

# '아직 아니다'를 '벌어졌다'로 읽지 않기 위한 부정 표현. 문장에 이게 있으면 버린다.
NEGATED = re.compile(
    r"\bno impairment\b|\bnot record(?:ed)? any impairment\b|"
    r"\bno such (?:notice|letter)\b|\bhave not (?:received|restated)\b|"
    r"\bwe are not (?:aware|in receipt)\b", re.I)

# 계속기업 불확실성 2단계 판정.
ALLEVIATED = re.compile(
    r"plans (?:alleviate|have alleviated) the substantial doubt|"
    r"substantial doubt (?:has been|is) alleviated|"
    r"concluded that (?:its|the Company's) plans alleviate", re.I)


def _clean(html: str) -> str:
    text = re.sub(r"(?is)<(script|style)[^>]*>.*?</\1>", " ", html)
    text = re.sub(r"<[^>]+>", " ", text)
    text = re.sub(r"&#x00a0;|&nbsp;?|&#160;", " ", text)
    text = re.sub(r"&#8217;|&rsquo;|&#8216;", "'", text)
    text = re.sub(r"&#8220;|&#8221;", '"', text)
    return re.sub(r"\s+", " ", re.sub(r"&amp;", "&", text))


def _full_text(url: str) -> str:
    try:
        return _clean(requests.get(url, headers=sec._HEADERS, timeout=TIMEOUT).text)
    except Exception:
        return ""


# 수정본(10-K/A 등)은 고친 부분만 담는 경우가 많다. 그걸 정식 보고서로 세면 모든
# 범주가 갑자기 0이 되어 '해소 → 신규'라는 가짜 전이가 만들어진다.
# 실측: 2026-04-30 10-K/A는 96,910자에 본문 지표가 하나도 없었다(정상 보고서는
# 195,000~599,000자에 4~5개). 본문이 실려 있는지를 보고 아니면 아예 건너뛴다.
BODY_ANCHORS = ("consolidated balance", "item 1a", "risk factors", "liquidity",
                "cash flows from operating")
MIN_ANCHORS = 3


def is_full_report(body: str) -> bool:
    lowered = body.lower()
    return sum(1 for a in BODY_ANCHORS if a in lowered) >= MIN_ANCHORS


def _snippet(body: str, match) -> str:
    start = max(0, match.start() - 160)
    return body[start:match.start() + 260].strip()


def assess(body: str) -> dict:
    """한 보고서의 범주별 상태. {범주: {"present", "count", "snippet", "detail"}}"""
    out = {}
    for name, spec in CATEGORIES.items():
        # 부정 문장은 뺀다. "no impairment charge was recorded"가 손상 인식으로
        # 잡히면 안 된다. 매치 주변 한 문장만 보고 판단한다.
        hits = [m for m in re.finditer(spec["pattern"], body, re.I)
                if not NEGATED.search(body[max(0, m.start() - 120):m.end() + 120])]
        entry = {"present": bool(hits), "count": len(hits),
                 "snippet": _snippet(body, hits[0]) if hits else "", "detail": ""}
        if name == "계속기업 불확실성" and hits:
            # ① 의문 제기는 확인됐다. ② 해소 결론이 있는지가 무게를 가른다.
            entry["detail"] = ("경영진 계획으로 해소 결론" if ALLEVIATED.search(body)
                               else "해소 결론 없음")
        out[name] = entry
    return out


def scan(filings: pd.DataFrame | None = None, refresh: bool = True) -> list[dict]:
    """정기보고서별 범주 상태를 접수일 순으로. 새 보고서만 내려받는다."""
    store = dc.load_json(CACHE, CACHE_MAX_AGE) or {}
    reports = dict(store.get("reports") or {})

    if refresh:
        if filings is None:
            loader = getattr(sec.load_filings, "__wrapped__", sec.load_filings)
            filings = loader()
        if filings is not None and not filings.empty:
            cutoff = pd.Timestamp.today().normalize() - pd.Timedelta(days=SCAN_DAYS)
            todo = filings[filings["form"].astype(str).str.upper().isin(FORMS)
                           & (pd.to_datetime(filings["filed"]) >= cutoff)]
            changed = False
            for row in todo.itertuples():
                if row.accn in reports or not row.url:
                    continue
                body = _full_text(row.url)
                if not body:
                    continue        # 실패는 기억하지 않는다. 다음에 다시 받는다.
                if not is_full_report(body):
                    # 부분 수정본. 기억은 해서 매번 다시 받지 않되, 상태 이력에선 뺀다.
                    reports[row.accn] = {"filed": str(pd.Timestamp(row.filed).date()),
                                         "form": row.form, "url": row.url,
                                         "partial": True, "status": {}}
                    changed = True
                    continue
                reports[row.accn] = {
                    "filed": str(pd.Timestamp(row.filed).date()),
                    "form": row.form, "url": row.url, "status": assess(body),
                }
                changed = True
            if changed:
                dc.save_json(CACHE, {"reports": reports})
    dc.record_health("정기보고서 상태 스캔", len(reports))
    full = [r for r in reports.values() if not r.get("partial")]
    return sorted(full, key=lambda r: r["filed"])


def transitions(reports: list[dict] | None = None) -> list[dict]:
    """직전 보고서 대비 상태가 바뀐 범주만. 지속 중인 것은 사건이 아니다."""
    reports = scan() if reports is None else reports
    if len(reports) < 1:
        return []
    events = []
    for index, report in enumerate(reports):
        before = reports[index - 1]["status"] if index else {}
        for name, now in report["status"].items():
            was = (before.get(name) or {}) if before else {}
            # 첫 보고서에는 비교 대상이 없다. 존재 자체를 '신규'로 본다.
            if now["present"] and not was.get("present"):
                kind = "신규"
            elif not now["present"] and was.get("present"):
                kind = "해소"
            elif now["present"] and now.get("detail") != was.get("detail"):
                kind = "변경"      # 예: 해소 결론 있음 → 없음
            else:
                continue
            events.append({
                "category": name, "kind": kind,
                "filed": report["filed"], "form": report["form"], "url": report["url"],
                "detail": now.get("detail", ""), "before": was.get("detail", ""),
                "snippet": now.get("snippet", ""),
                "why": CATEGORIES[name]["why"],
            })
    return events


def current(reports: list[dict] | None = None) -> dict:
    """가장 최근 보고서의 상태. 리포트가 '지금 어떤 깃발이 서 있나'를 보여줄 때 쓴다."""
    reports = scan() if reports is None else reports
    if not reports:
        return {}
    latest = reports[-1]
    flags = {n: v for n, v in latest["status"].items() if v["present"]}
    return {"filed": latest["filed"], "form": latest["form"], "url": latest["url"],
            "flags": flags}
