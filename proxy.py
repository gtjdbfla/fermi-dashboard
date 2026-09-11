"""위임장 대결을 **상태**로 추적한다.

`alerts.proxy_events`는 서식이 접수됐다는 사실만 알린다. 그런데 이 회사에서 위임장은
곁가지가 아니다 — 공시 154건 중 60건이 위임장·주주총회다. 창업자 Toby Neugebauer가
2026-04-30 Cause로 해임된 뒤 지분 22.7~24.1%를 들고 이사회 교체와 **회사 매각**을
요구했고, 회사는 법원 판결 직후 이사 선임 정족수를 70%로 올리는 정관 개정으로 맞섰다.
2026-07-03 권유가 잠정 철회되며 소강 상태지만, 첫 정기주총(2026-10-30)이 400MW 기한
11일 전이고 주주제안 마감이 2026-09-10이라 다시 붙을 자리가 남아 있다.

**서식 건수로는 이걸 읽을 수 없다.** DFAN14A가 47건 쌓인 것과 그중 철회 선언이 있는
것은 완전히 다른 뜻인데, 접수 알림만으로는 구분되지 않는다. 원문을 읽어 신호를
가려내고, 대결이 살아 있는지 죽어 있는지를 상태로 남긴다.

**영어 원문으로 판정한다.** 건별 AI 요약을 재료로 쓰면 요약 → 판정으로 두 단계가 되어
왜곡이 끼어든다. 원문 어휘가 정형적이라 정규식이 더 정확하다.
"""

import re

import pandas as pd

import diskcache as dc

CACHE = "proxy_findings"
HEALTH = "위임장 대결 스캔"
# 크론 주기(30분)보다 짧게 잡는다 — 크론은 매 회차 기록되고, 화면 리런은 묶인다.
HEALTH_EVERY = 1200
MAX_AGE = 86400 * 3650        # 제출된 공시는 변하지 않는다
MAX_SCAN = 40                 # 한 번에 새로 읽을 공시 수
MAX_CHARS = 200_000

# 분쟁 갈래 서식. 회사 측 자료(DEF 14A·DEFA14A)는 상태 판정의 근거로 쓰지 않는다 —
# 대결이 없어도 정기주총 때문에 나온다.
CONTEST_FORMS = ("PREC14A", "DEFC14A", "PREN14A", "DEFN14A", "PRRN14A", "DFAN14A", "PRER14A")
ALL_PROXY_FORMS = CONTEST_FORMS + ("DEF 14A", "DEFA14A", "PRE 14A")

# 신호별 (이름, 방향, 정규식). 방향은 리포트에서 색을 고르는 데 쓴다.
#   🔴 후퇴 = 회사 지배구조가 흔들리는 쪽 / 🟢 진정 = 가라앉는 쪽 / 🟠 발생 = 중립적 사건
SIGNALS = (
    ("권유 철회", "진정", re.compile(
        r"\b(?:withdraw(?:n|s|ing|al of)?|terminat(?:e[ds]?|ing)|suspend(?:ed|ing)?)\b"
        r"[^.]{0,60}\bsolicitation\b"
        r"|\bno longer (?:seeking|soliciting|intend(?:s|ing)? to solicit)\b", re.I)),
    # **완료형만 받는다.** "I will resume my solicitation only if a court postpones the
    # meeting"이 재개로 잡혔다. 조건부 선언은 재개가 아닌데, 이 신호는 상태를
    # 소강에서 활성으로 뒤집으므로 오탐 하나가 판정을 통째로 바꾼다.
    # `is soliciting`은 넣으면 안 된다 — 대결이 한창일 때 회사 자료가 상대의 권유를
    # 서술하는 현재진행형("Neugebauer is soliciting support to…")이라 재개가 아니다.
    ("권유 재개", "후퇴", re.compile(
        r"\b(?:has|have|had)\s+(?:\w+\s+){0,2}(?:resumed|re-?commenced|re-?initiated)\b"
        r"|\b(?:resumed|re-?commenced|re-?initiated)\b[^.]{0,60}\bsolicitation\b", re.I)),
    ("임시주총 소집 요구", "후퇴", re.compile(
        r"\bdemand(?:ed|ing|s)?\b[^.]{0,50}\bspecial meeting\b"
        r"|\bagent designation\b"
        r"|\brequest(?:ed|ing|s)?\b[^.]{0,60}\bcall (?:a|the) special meeting\b", re.I)),
    ("이사 후보 지명", "후퇴", re.compile(
        r"\bnominat(?:e[ds]?|ing|ion of)\b[^.]{0,60}\b(?:director|nominee)s?\b"
        r"|\bslate of (?:director )?nominees\b", re.I)),
    ("합의·화해", "진정", re.compile(
        r"\bcooperation agreement\b|\bsettlement agreement\b|\bstandstill\b"
        r"|\bagreed to (?:appoint|add)\b[^.]{0,50}\b(?:to the board|as (?:a )?director)\b", re.I)),
    # ↑ 이 신호만은 당사자 확인이 더 필요하다. NEEDS_PARTY 참조.
    ("경영권 방어 조치", "발생", re.compile(
        r"\brights plan\b|\bpoison pill\b|\badvance notice\b[^.]{0,30}\bby-?laws?\b"
        r"|\bsupermajority\b|\b(?:70|66|75|80)%\b[^.]{0,60}\b(?:vote|approval|affirmative)\b", re.I)),
    ("회사 매각 요구", "발생", re.compile(
        r"\b(?:strategic alternatives|sale of the company|sale process)\b"
        r"|\bexplore\b[^.]{0,40}\b(?:sale|merger|strategic partnership)\b", re.I)),
    ("소송", "발생", re.compile(
        r"\bbusiness court\b|\bfiled (?:a )?(?:lawsuit|complaint|petition)\b"
        r"|\btemporary restraining order\b|\binjunction\b", re.I)),
)

# 가정법은 버린다. "we may seek to nominate"는 지명이 아니다.
HYPOTHETICAL = re.compile(
    r"\b(?:may|might|could|would|intend(?:s|ed)? to|plan(?:s|ned)? to|expect(?:s|ed)? to"
    r"|if we|should we|in the event|reserve(?:s)? the right|no assurance)\b", re.I)

SENTENCE = re.compile(r"[^.!?]{0,400}[.!?]")

# **이 신호들은 당사자가 문장 안에 있어야 한다.** 위임장 자료에는 이사 후보의 약력이
# 통째로 실리는데, 거기 남의 회사 얘기가 섞여 있다. 실제로 "AGL Private Credit Income
# Fund, a closed-end management investment company launched through an exclusive
# cooperation agreement with Barclays"라는 **후보 약력 한 줄**이 7건 전부에서
# '합의·화해'로 잡혔다. 대결이 타결됐다는 뜻이 되므로 가장 위험한 오탐이다.
#
# 대문자 `the Company`를 요구하는 것이 핵심이다 — 위 문장의 'management investment
# company'는 소문자라 걸러진다.
NEEDS_PARTY = {"합의·화해"}
PARTY = re.compile(r"\bFermi\b|\bthe Company\b|\bthe Board\b|\bNeugebauer\b"
                   r"|\bParticipants\b|\bthe Issuer\b")


def _sentences(text: str) -> list[str]:
    return [s.strip() for s in SENTENCE.findall(text or "")]


def scan_text(text: str) -> list[dict]:
    """원문 하나에서 신호를 뽑는다. 가정법 문장은 버린다."""
    found = {}
    for sentence in _sentences(text):
        if HYPOTHETICAL.search(sentence):
            continue
        for name, way, pattern in SIGNALS:
            if name in found:
                continue
            if not pattern.search(sentence):
                continue
            if name in NEEDS_PARTY and not PARTY.search(sentence):
                continue
            found[name] = {"signal": name, "direction": way,
                           "excerpt": re.sub(r"\s+", " ", sentence)[:260]}
    return list(found.values())


def _is_contest(form) -> bool:
    upper = str(form or "").upper().strip()
    return any(upper.startswith(f) for f in CONTEST_FORMS)


# **누가 쓴 글인지 반드시 표시한다.** 인용문 대부분이 한쪽의 주장이다. 예컨대
# "Fermi abandons its own lawsuit rather than explain its entrenched late night 70%
# supermajority bylaw actions"는 DFAN14A, 즉 분쟁측이 쓴 문장이지 중립 사실이 아니다.
# 서식 접두사가 제출자를 그대로 말해 준다 — N/AN 계열은 비경영진, A/DEF 계열은 회사다.
DISSIDENT_FORMS = ("DFAN14A", "PREN14A", "DEFN14A", "PRRN14A")
COMPANY_FORMS = ("DEF 14A", "DEFA14A", "PRE 14A", "PRER14A")


def author(form) -> str:
    upper = str(form or "").upper().strip()
    if upper.startswith(DISSIDENT_FORMS):
        return "분쟁측"
    if upper.startswith(COMPANY_FORMS):
        return "회사측"
    return "미상"        # PREC14A·DEFC14A는 양측 다 낼 수 있다


def _health_due() -> bool:
    """건수 기록을 지금 남길 때인가.

    `findings()`는 화면을 그릴 때마다 불리는데 `diskcache.save_json`은 원자적이지 않다
    (임시파일 없이 그대로 덮는다). 매 리런마다 쓰면 크론과 겹쳐 파일이 깨질 수 있어,
    크론 주기보다 짧은 간격으로만 묶어 쓴다.
    """
    at = (dc.health().get(HEALTH) or {}).get("at")
    if not at:
        return True
    try:
        return (pd.Timestamp.now(tz="UTC") - pd.Timestamp(at)).total_seconds() >= HEALTH_EVERY
    except Exception:
        return True


def findings(filings: pd.DataFrame | None = None, read_text=None,
             limit: int = MAX_SCAN) -> dict:
    """접수번호 → {filed, form, signals}. 새 공시만 읽고 나머지는 캐시에서."""
    import sec_edgar as sec
    if filings is None:
        loader = getattr(sec.load_filings, "__wrapped__", sec.load_filings)
        filings = loader()
    stored = dc.load_json(CACHE, MAX_AGE) or {}
    if filings is None or filings.empty:
        return stored
    if read_text is None:
        import filing_review as fr
        read_text = fr._text

    target = filings[filings["form"].map(
        lambda f: str(f or "").upper().strip().startswith(ALL_PROXY_FORMS))]
    todo = target[~target["accn"].astype(str).isin(stored)]
    todo = todo.sort_values("filed", ascending=False).head(limit)
    changed = False
    for row in todo.itertuples():
        body = read_text(row.url, MAX_CHARS) if row.url else ""
        if not body:
            continue
        stored[str(row.accn)] = {
            "filed": str(pd.Timestamp(row.filed).date()),
            "form": str(row.form),
            "author": author(row.form),
            "contest": bool(_is_contest(row.form)),
            "signals": scan_text(body),
            "url": str(row.url or ""),
        }
        changed = True
    if changed:
        dc.save_json(CACHE, stored)
    # **변화가 없어도 '확인했다'를 남긴다.** 전에는 changed일 때만 기록해서, 새 서식이
    # 없는 조용한 날과 스캔이 죽은 날이 화면에서 똑같아 보였다 — 09-06자 기록이 닷새째
    # 그대로였는데 어느 쪽인지 가릴 방법이 없었다. 함정 ⑩과 같은 성질이다.
    if changed or _health_due():
        dc.record_health(HEALTH, len(stored))
    return stored


# 마지막 분쟁 서식 이후 이만큼 지나면 소강으로 본다. 2026년 5~7월 대결에서 권유가
# 붙어 있는 동안은 DFAN14A가 며칠 간격으로 쏟아졌고, 철회 뒤로는 뚝 끊겼다.
ACTIVE_DAYS = 21
DORMANT_DAYS = 120


def state(filings: pd.DataFrame | None = None, stored: dict | None = None) -> dict:
    """지금 대결이 어느 상태인가. {status, label, last_contest, days, counts, last_signal}."""
    stored = findings(filings) if stored is None else stored
    contest = {a: v for a, v in stored.items() if v.get("contest")}
    if not contest:
        return {"status": "없음", "label": "위임장 대결 없음", "last_contest": None,
                "days": None, "counts": {}, "last_signal": None}

    today = pd.Timestamp.today().normalize()
    last = max(pd.Timestamp(v["filed"]) for v in contest.values())
    days = int((today - last).days)

    # 가장 최근의 방향성 신호. 철회가 마지막이면 소강, 재개·지명이 마지막이면 활성이다.
    rows = []
    for v in contest.values():
        for sig in v.get("signals", []):
            rows.append((v["filed"], sig["signal"], sig["direction"],
                         sig.get("excerpt", ""), v.get("author", "미상")))
    rows.sort()
    last_signal = rows[-1] if rows else None

    if days <= ACTIVE_DAYS:
        status, label = "활성", f"분쟁 서식이 {days}일 전까지 이어지고 있다"
    elif days <= DORMANT_DAYS:
        status, label = "소강", f"마지막 분쟁 서식 이후 {days}일 조용하다"
    else:
        status, label = "휴면", f"마지막 분쟁 서식 이후 {days}일 — 사실상 멈췄다"
    if last_signal and last_signal[1] == "권유 재개" and days <= DORMANT_DAYS:
        status, label = "활성", "권유 재개가 확인됐다"

    counts = {}
    for v in contest.values():
        counts[v["form"]] = counts.get(v["form"], 0) + 1
    return {"status": status, "label": label, "last_contest": str(last.date()),
            "days": days, "counts": counts,
            "last_signal": {"filed": last_signal[0], "signal": last_signal[1],
                            "direction": last_signal[2], "excerpt": last_signal[3],
                            "author": last_signal[4]}
            if last_signal else None}


def recent_signals(days: int = 30, stored: dict | None = None) -> list[dict]:
    """최근 신호들. 리포트와 알림이 쓴다."""
    stored = findings() if stored is None else stored
    cut = pd.Timestamp.today().normalize() - pd.Timedelta(days=days)
    out = []
    for accn, v in stored.items():
        when = pd.Timestamp(v["filed"])
        if when < cut:
            continue
        for sig in v.get("signals", []):
            out.append({"accn": accn, "filed": v["filed"], "form": v["form"],
                        "author": v.get("author", "미상"),
                        "url": v.get("url", ""), **sig})
    return sorted(out, key=lambda x: x["filed"], reverse=True)


# 주총 일정. 공시로 확정된 값이라 여기 둔다(2026-08-25 이사회 결의, 8-K 2026-08-31).
# 8-K Item 5.08은 세 기한(Rule 14a-8 주주제안 · 정관상 사전통지 · Rule 14a-19 보편위임장)을
# **모두 같은 날 2026-09-10**로 못박았다. 그래서 날짜가 하나면 된다.
ANNUAL_MEETING = "2026-10-30"
PROPOSAL_DEADLINE = "2026-09-10"
KEEP_AFTER_DAYS = 30

# 마감이 지났다고 해서 "지명이 없었다"가 곧바로 확인되지는 않는다. 사전통지는 회사에
# 직접 보내는 것이고 공시 의무가 붙지 않는다 — 분쟁측 PREC14A는 며칠~몇 주 뒤에 나올 수
# 있다. 확정은 회사 DEF 14A의 후보 명단에서 난다. 이 문장을 데이터에 붙여 보내는 이유는
# digest가 텍스트만 쓰기 때문이다.
DEADLINE_CAVEAT = ("사전통지는 회사에 직접 하는 것이라 마감 당일 공시로는 확인되지 않는다. "
                   "분쟁측 지명 여부는 회사 DEF 14A 후보 명단에서 확정된다.")


# 권유 중단 발표일. **이 뒤에 나온 지명·재개만** 대결이 되살아났다는 뜻이 된다 —
# 중단 이전의 지명 신호(2026-05~06에 여러 건 있다)를 그대로 세면 언제나 "지명 있음"이 된다.
SUSPENDED_ON = "2026-07-03"
REVIVAL_SIGNALS = ("이사 후보 지명", "권유 재개", "임시주총 소집 요구")
# 주총 지명 마감을 판정할 때는 **이 둘만** 본다. 임시주총 소집 요구는 다른 선로다 —
# 실제로 2026-07-08 DFAN14A에 그 신호가 있어서, 넣어두면 지명이 없어도 언제나
# "되살아났다"가 나온다.
NOMINATION_SIGNALS = ("이사 후보 지명", "권유 재개")


def revival(stored: dict | None = None, since: str = SUSPENDED_ON,
            until: str | None = None, signals: tuple = REVIVAL_SIGNALS) -> dict | None:
    """중단 이후 대결이 되살아난 신호. 없으면 None.

    마감이 지났을 때 "지명이 없었다"고 단정하기 전에 이걸 봐야 한다. 확인 없이 단정하면
    분쟁측이 실제로 후보를 냈는데도 끝났다고 알리게 된다.
    """
    stored = findings() if stored is None else stored
    start = pd.Timestamp(since)
    end = pd.Timestamp(until) if until else None
    best = None
    for accn, v in stored.items():
        if not v.get("contest"):
            continue
        filed = pd.to_datetime(v.get("filed"), errors="coerce")
        if pd.isna(filed) or filed < start or (end is not None and filed > end):
            continue
        for sig in v.get("signals", []):
            if sig["signal"] not in signals:
                continue
            if best is None or filed > pd.Timestamp(best["filed"]):
                best = {"accn": accn, "filed": str(filed.date()), "signal": sig["signal"],
                        "form": v.get("form", ""), "author": v.get("author", "미상")}
    return best


def dday(days: int) -> str:
    """D-3 / D-DAY / D+2. 지난 날을 `D-{-1}`로 찍지 않으려고 따로 둔다."""
    if days > 0:
        return f"D-{days}"
    return "D-DAY" if days == 0 else f"D+{-days}"


def calendar() -> list[dict]:
    """주총 분기점. D-day로 보여준다.

    **지난 항목을 버리지 않는다.** 마감일은 지나가는 순간이 가장 큰 정보다 — 분쟁측이
    09-10까지 후보를 지명하지 않으면 10-30 주총에서는 붙을 자리가 없다. `left >= 0`으로
    걸러내면 바로 그 순간에 항목이 화면에서 조용히 사라져, 마감을 지켰는지 넘겼는지
    화면만 보고는 알 수 없다. **아무 일도 없었던 것처럼 보이는 게 최악이다.**
    지난 것도 KEEP_AFTER_DAYS 동안 D+로 남긴다.
    """
    today = pd.Timestamp.today().normalize()
    out = []
    for when, what in ((PROPOSAL_DEADLINE, "Rule 14a-8 주주제안·이사 지명 사전통지 마감"),
                       (ANNUAL_MEETING, "제1회 정기 주주총회")):
        left = int((pd.Timestamp(when) - today).days)
        if left < -KEEP_AFTER_DAYS:
            continue
        item = {"date": when, "what": what, "days": left, "passed": left < 0}
        if left < 0 and when == PROPOSAL_DEADLINE:
            item["note"] = DEADLINE_CAVEAT
        out.append(item)
    return out
