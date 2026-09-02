"""전기가 실제로 흐르기 시작했는지 잡는다.

**왜 따로 만드나.** 지금 대시보드의 세 관문(400MW·승인 고객계약·TTU NTP)은 전부
*계약*을 묻는다. 그런데 "사업이 진짜 진행되는가"에 유일하게 물리적으로 답하는 것은
**가동 중 MW가 0을 벗어나는 순간**이다. 지금 그 값은 0이고, 아무도 감시하지 않는다.

**확인해야 할 구체적 사실이 있다.** 2025-12 Xcel Energy 자회사 SPS와 맺은 전력공급
계약(ESA)은 86MW를 2026년 1월부터 공급 개시하고 **2026년 하반기에 통전**, 연말까지
추가 114MW를 목표로 한다고 발표됐다. 지금이 2026년 하반기인데 가동 MW는 0이고,
2026 Q2 10-Q에는 "86 MW"라는 표현이 한 번도 나오지 않는다.

**가장 큰 함정은 미래형이다.** 이 회사 자료에는 "통전할 예정", "연말까지 목표",
"6개월 내 첫 전력" 같은 문장이 널려 있다. 그걸 통전으로 읽으면 매주 헛알림이 난다.
그래서 legal.py와 같은 방식으로 **실제로 벌어졌다고 말하는 문장만** 남긴다.
"""

import re

import pandas as pd

# 통전을 가리키는 말.
TRIGGERS = re.compile(
    r"energiz\w*|first power|commercial operation|placed in service|"
    r"in-?service date|synchroniz\w*|delivering power|delivered power|"
    r"power flowing|went online|came online|now online|"
    r"통전|상업운전|송전 개시|전력 공급 개시", re.I)

# 벌어졌다고 말하는가.
#
# **`energized` 단독을 넣으면 안 된다.** "must be energized by 2028"이 확정으로
# 잡힌다 — 과거분사는 미래형에도 그대로 쓰인다. 조동사·시제와 묶어서 본다.
ACTUAL = re.compile(
    r"\b(?:has|have|had)\s+(?:\w+\s+){0,2}(?:been\s+)?"
    r"(?:energized|placed|delivered|begun|started|completed|achieved|synchronized)\b|"
    r"\b(?:was|were)\s+(?:\w+\s+){0,2}"
    r"(?:energized|placed|delivered|completed|synchronized|brought)\b|"
    r"\b(?:is|are)\s+now\b|"
    r"\bbegan\b|\bbegun\b|\bcommenced\b|\bstarted\s+delivering\b|"
    r"\bcame\s+online\b|\bwent\s+online\b|\bnow\s+online\b|\bnow\s+delivering\b|"
    r"\bsuccessfully\s+\w+ed\b|"
    r"했다|완료했다|개시했다|시작됐다", re.I)

# 미래형·목표면 버린다. **이게 이 감지기의 핵심이다.**
#
# **`project`를 넣으면 안 된다.** 이 회사 프로젝트 이름이 "Project Matador"라
# 거의 모든 문장이 미래형으로 판정된다(실측: 5건 중 3건이 이 이유로 실패했다).
# 미래를 뜻하는 건 `projected` 하나뿐이므로 그것만 남긴다.
# `must`도 넣는다 — "must be energized by 2028"은 의무이지 실적이 아니다.
FUTURE = re.compile(
    r"\bexpect\w*|\btarget(?:s|ed|ing)?\b|\bplan(?:s|ned|ning)?\b|\banticipat\w*|"
    r"\bwill\b|\bwould\b|\bmust\b|\bshould\b|\bscheduled\b|\bon track\b|\baims?\b|"
    r"\bprojected\b|\bforecast\w*|\bintend\w*|\bby (?:year|the) end\b|"
    r"\bby the end of\b|\bslated\b|\bset to\b|\bpath to\b|\bgoal\b|"
    r"\bover the next\b|예정|목표|계획", re.I)

# 이 사안의 고유명사·숫자. 있으면 신뢰도가 올라간다.
SPECIFIC = re.compile(
    r"\b86\s*MW\b|\b114\s*MW\b|\b200\s*MW\b|Xcel|Southwestern Public Service|\bSPS\b|"
    r"TM2500|Matador", re.I)


def judge(sentence: str) -> str | None:
    """한 문장의 판정. '확정' / '예고' / None."""
    if not TRIGGERS.search(sentence):
        return None
    if FUTURE.search(sentence):
        return "예고"
    if ACTUAL.search(sentence):
        return "확정"
    return None


def sentences(text: str) -> list[str]:
    return re.split(r"(?<=[.;])\s+(?=[A-Z(])", str(text or ""))


def from_text(text: str) -> list[dict]:
    """원문에서 통전 문장을 뽑는다. 확정만 돌려준다."""
    out = []
    for s in sentences(text):
        if judge(s) != "확정":
            continue
        out.append({"text": s.strip()[:500], "specific": bool(SPECIFIC.search(s))})
    return out


def from_headline(title: str) -> str | None:
    """제목 한 줄 판정. 제목에는 마침표가 없어 문장 분리가 안 먹는다."""
    return judge(str(title or ""))


def operating_mw(m: dict | None) -> float | None:
    """가동 중 MW. 이 값이 0을 벗어나는 것이 가장 단단한 확정이다."""
    value = (m or {}).get("mw_operating")
    try:
        return float(value)
    except (TypeError, ValueError):
        return None
