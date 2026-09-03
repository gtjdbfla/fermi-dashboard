"""투자 가설의 사슬을 추적한다 — 백스톱 → 프로젝트 파이낸싱 → 테넌트 계약 실행 → 건설.

**왜 기존 감지기로 부족했나.** news_events는 '서명 동사 + 용량(MW) 명사'를 둘 다
요구하고, legal.py는 소환장 같은 *사건어*를 요구한다. 그런데 이 사슬의 어휘는
완전히 다르다 — backstop, project financing, notice to proceed, equity line에는
MW도 사건어도 없다.

실측으로 확인했다. 이 사슬에서 나올 법한 제목 13개를 넣었더니 **1개만 걸렸다.**
백스톱이 나와도, PF가 닫혀도, TTU가 NTP를 줘도, 착공해도 알림이 안 갔다.

**방향을 함께 판정한다.** 같은 항목이라도 "확보"와 "지연"과 "무산"은 전혀 다른
정보다. 제목의 동사로 세 갈래를 가른다.

    🟢 진전   secures / closes / receives / begins / completes
    🟡 지연   delayed / pushed / postponed / extended
    🔴 후퇴   collapse / terminated / scales back / halts / fails

지연과 후퇴를 구분하는 것이 핵심이다. 며칠 늦는 것과 무산되는 것은 다른 사건이다.
"""

import re

import pandas as pd

# 사슬의 각 고리. 고리마다 어휘가 다르다.
LINKS = {
    "백스톱": {
        "pattern": r"\bbackstop\w*|\bcredit support\b|\bcredit enhancement\b|"
                   r"\bletter of credit\b|\bguarant(?:y|ee|or)\w*|백스톱|백스탑|지급보증",
        "why": "TensorWave는 투자등급이 아니다. 신용을 보강해줄 상대가 있어야 "
               "$6.5B 리스가 금융 가능한 자산이 된다.",
    },
    "프로젝트 파이낸싱": {
        "pattern": r"\bproject financ\w*|\bproject-level (?:debt|capital|financ\w*)|"
                   r"\bterm sheet\b|\bconstruction loan\b|\bfinancial close\b|"
                   r"\bdebt facility\b|\bcredit facility\b|프로젝트 파이낸싱|\bPF\b",
        "why": "TTU가 notice to proceed를 주는 조건 중 하나가 '1단계 건설 자금조달 "
               "확보'다. PF 없이는 땅도 잃는다.",
    },
    "테넌트 계약 실행": {
        "pattern": r"(?:tensorwave|tenant|anchor customer|서브리스|테넌트)"
                   r"[^.]{0,60}(?:clos\w+|execut\w+|definitive|complet\w+|"
                   r"deliver\w+|amend\w+|scal\w+ back|reduc\w+|terminat\w+)|"
                   r"(?:clos\w+|execut\w+|complet\w+|terminat\w+|scal\w+ back)"
                   r"[^.]{0,60}(?:tensorwave|tenant|lease|서브리스)",
        "why": "서명과 실행(closing)은 다르다. 222MW가 실제로 이행 단계에 들어가야 "
               "커버리지 15%가 의미를 갖는다.",
    },
    "건설 진척": {
        "pattern": r"\bnotice to proceed\b|\bNTP\b|\bvertical construction\b|"
                   r"\bgroundbreaking\b|\bbreaks? ground\b|\bmobiliz\w+|"
                   r"\bhalt\w*\s+construction|\bsuspend\w*\s+construction|"
                   r"\bpause[sd]?\s+construction|착공|건설 중단|공사 중단",
        "why": "NTP는 TTU 지상권의 조건이고, 수직 건설은 그걸 받았다는 물리적 증거다.",
    },
    "희석": {
        "pattern": r"\bequity line\b|\bat-the-market\b|\bATM (?:program|offering|facility)\b|"
                   r"\bregistered direct\b|\bYorkville\b|\bdraws? (?:down )?under\b|"
                   r"\bshelf (?:registration|offering)\b|\bsecondary offering\b|"
                   r"\bissuing\s+[\d,.]+\s*(?:million\s+)?shares\b|유상증자|주식 발행",
        "why": "Yorkville 약정은 인출 시 주식으로 결제하고 한도가 4,000만 주다. "
               "현금 $62.5M인 회사가 인출을 시작하면 그 자체가 신호다.",
    },
}

# 방향. 후퇴를 먼저 본다 — "closing pushed"처럼 둘이 겹치면 나쁜 쪽이 이긴다.
RETREAT = re.compile(
    r"\bcollaps\w+|\bterminat\w+|\bwalk(?:s|ed)? away\b|\bscal\w+ back\b|"
    r"\breduc\w+|\bhalt\w+|\bsuspend\w+|\bfail\w+|\babandon\w+|\bcancel\w+|"
    r"\bpull\w* out\b|\bfalls? through\b|무산|해지|중단|철회|축소", re.I)
DELAY = re.compile(
    r"\bdelay\w*|\bpush\w+ (?:back|to)\b|\bpostpon\w+|\bslip\w*|\bextend\w+|"
    r"\bpush\w*ed\b|\bdefer\w+|\bmiss\w+ (?:the )?deadline\b|"
    r"지연|연기|미뤄", re.I)
ADVANCE = re.compile(
    r"\bsecur\w+|\bclos\w+|\breceiv\w+|\bcomplet\w+|\bexecut\w+|\bsign\w+|"
    r"\bbegin\w*|\bbegan\b|\bobtain\w+|\bfinaliz\w+|\bannounc\w+|\breach\w+|"
    # 희석 쪽은 '인출·발행·가격결정'이 곧 사건이다. 이게 없어서 Yorkville 인출
    # 기사가 '중립'으로 버려졌다(실측).
    r"\bdraws?\b|\bdrew\b|\bissu(?:e|es|ed|ing)\b|\bpric(?:e|es|ed|ing)\b|"
    r"확보|체결|수령|완료|개시|인출|발행", re.I)


def direction(text: str) -> str:
    """🔴 후퇴 / 🟡 지연 / 🟢 진전 / 중립."""
    if RETREAT.search(text):
        return "후퇴"
    if DELAY.search(text):
        return "지연"
    if ADVANCE.search(text):
        return "진전"
    return "중립"


def classify(text: str) -> list[dict]:
    """제목·문장이 사슬의 어느 고리에 걸리는가. 여러 고리에 걸릴 수 있다."""
    out = []
    for name, spec in LINKS.items():
        if not re.search(spec["pattern"], text, re.I):
            continue
        way = direction(text)
        # **희석에는 '진전'이 없다.** 증자가 진행되는 것은 사슬이 앞으로 간 게 아니라
        # 주주 몫이 줄어드는 사건이다. 방향 이름을 바꿔 오해를 막는다.
        if name == "희석" and way in ("진전", "중립"):
            way = "발생"
        out.append({"link": name, "direction": way, "why": spec["why"]})
    return out
