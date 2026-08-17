"""경영권 분쟁 추적.

**검증에서 지배구조는 판별력이 없다고 내렸다.** 그건 이사회 독립성 비율 같은 통상 지표 얘기다.
지금 페르미에서 벌어진 일은 다르다 — 공동창업자이자 최대주주가 임시주총을 소집해 이사회를
갈아치우고 **매각을 포함한 전략적 검토**를 요구했고, 회사가 동의 철회 권유로 맞섰다.

$1.2B를 이미 묻었고 계약 커버리지가 15%인 회사에서 경영진 교체 다툼이 벌어지는 것은
NuScale이 첫 고객을 잃은 것과 같은 종류의 실행 리스크다. 그래서 참고 축에서 따로 뗀다.

**중요한 건 이게 끝나지 않았다는 점이다.** 2026-07-03 철회는 판사 기피로 일정이 무너져서였고,
텍사스 상사법원 소송은 계속 진행 중이다. 권유는 재개될 수 있다.

사실은 전부 `data/governance.csv`에 공시 링크와 함께 있다. 추측은 넣지 않는다.
"""

from pathlib import Path

import pandas as pd
import streamlit as st

DATA_DIR = Path(__file__).parent / "data"

# 반대측·경쟁 위임장 서식만. DFAN14A(권유 보조자료)는 두 달에 47건이 나와서 넣으면 소음이 된다.
CONTESTED_FORMS = ("PRRN14A", "PREN14A", "DEFC14A", "PREC14A", "PRER14A", "DEFN14A")

PHASE_ICON = {"배경": "·", "전조": "·", "개시": "🔴", "격화": "🔴", "중단": "🟡", "이후": "·",
              "종결": "🟢"}


@st.cache_data(ttl=600, show_spinner=False)
def timeline() -> pd.DataFrame:
    path = DATA_DIR / "governance.csv"
    if not path.exists():
        return pd.DataFrame(columns=["date", "phase", "actor", "event", "detail", "form", "url"])
    frame = pd.read_csv(path)
    frame["date"] = pd.to_datetime(frame["date"], errors="coerce")
    return frame.sort_values("date", ascending=False).reset_index(drop=True)


def status(frame: pd.DataFrame | None = None) -> dict:
    """현재 국면. '종결'이 기록되기 전까지는 보류로 본다."""
    frame = timeline() if frame is None else frame
    if frame.empty:
        return {}
    phases = set(frame["phase"])
    if "종결" in phases:
        state, icon = "종결", "🟢"
        note = "분쟁이 마무리됐다."
    elif "중단" in phases:
        state, icon = "보류 — 재개 가능", "🟡"
        note = ("소집 권유는 철회됐지만 텍사스 상사법원 소송은 진행 중이다. "
                "창업자측은 권유를 재개할 수 있다고 명시했다.")
    else:
        state, icon = "진행 중", "🔴"
        note = "이사회 구성과 전략 방향을 둘러싼 표 대결이 진행 중이다."

    latest = frame.iloc[0]
    started = frame[frame["phase"] == "개시"]["date"].min()
    return {
        "state": state, "icon": icon, "note": note,
        "since": started, "last": latest["date"], "last_event": latest["event"],
        "days": int((pd.Timestamp.today().normalize() - pd.Timestamp(started)).days)
                if pd.notna(started) else None,
        "unresolved": "종결" not in phases,
    }


def contested_filings(filings: pd.DataFrame) -> pd.DataFrame:
    """반대측·경쟁 위임장 공시만 추린다."""
    if filings is None or filings.empty:
        return pd.DataFrame()
    upper = filings["form"].astype(str).str.upper()
    hit = upper.str.startswith(CONTESTED_FORMS)
    return filings[hit].sort_values("filed", ascending=False)


def view(frame: pd.DataFrame | None = None) -> pd.DataFrame:
    """화면용 표."""
    frame = timeline() if frame is None else frame
    if frame.empty:
        return frame
    out = pd.DataFrame({
        "": frame["phase"].map(PHASE_ICON).fillna("·"),
        "시점": frame["date"].dt.date,
        "주체": frame["actor"],
        "사건": frame["event"],
        "서식": frame["form"],
        "원문": frame["url"],
    })
    return out
