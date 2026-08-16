"""페르미가 유지 기업들의 길을 가려면 무엇을 언제까지 해야 하는가.

**왜 '유지 기업 평균 소요 시간'을 쓰지 않았나.** 처음에는 표본에서 단계별 평균 연수를 뽑으려 했는데
계산해 보니 나오지 않았다. T0(대규모 자본 투입 개시) 이전에 이미 매출이 있던 회사가 많아서다 —
Cheniere는 재기화 터미널 매출 $292M, Bloom Energy는 장비 판매 $972M, Core Scientific은
비트코인 $544M이 T0 전부터 있었다. 이들에게 '첫 매출까지 걸린 시간'은 음수가 되고, 평균을 내면
-0.4년 같은 값이 나온다. 페르미처럼 완전 무매출로 시작해 사다리를 끝까지 올라간 표본이 사실상 없다.

그래서 1차 잣대는 **회사가 스스로 공시에 적은 목표 시점**으로 둔다. 자기가 공언한 일정을 못 지키는
것이 실제 신호이고, 개별 기업 사례는 '이런 일이 보통 몇 년 걸리는가'의 감각으로만 옆에 붙인다.

단계 정의와 목표는 data/roadmap.csv에 있고, 현재 상태만 여기서 실시간 데이터로 판정한다.
"""

from pathlib import Path

import pandas as pd
import streamlit as st

DATA_DIR = Path(__file__).parent / "data"

STATUS_DONE, STATUS_ACTIVE, STATUS_WAITING = "달성", "진행 중", "대기"


@st.cache_data(ttl=600, show_spinner=False)
def load_steps() -> pd.DataFrame:
    path = DATA_DIR / "roadmap.csv"
    return pd.read_csv(path) if path.exists() else pd.DataFrame()


def _measure(metric: str, m: dict) -> tuple[float | None, str]:
    """단계별 현재 수치와 표시용 문자열."""
    if metric == "contracts_binding":
        count = m.get("customer_count") or 0
        return float(count), f"구속력 계약 {count}건"
    if metric == "contracted_vs_landed":
        value = m.get("contracted_vs_landed")
        return value, (f"{value*100:.0f}%" if value is not None else "산출 불가")
    if metric == "mw_operating":
        value = m.get("mw_operating")
        return value, (f"가동 {value:,.0f}MW" if value is not None else "산출 불가")
    if metric == "revenue":
        # 매출 태그가 XBRL에 아예 없으면 pre-revenue라는 뜻이다.
        return 0.0, "매출 없음"
    if metric == "op_cf":
        burn = m.get("op_burn_q")
        return (-burn if burn is not None else None,
                f"분기 영업CF -${burn/1e6:,.1f}M" if burn is not None else "산출 불가")
    return None, "–"


def evaluate(m: dict) -> pd.DataFrame:
    """각 단계의 현재 상태·목표까지 남은 일수를 붙인다. 매일 다시 계산된다."""
    steps = load_steps()
    if steps.empty:
        return steps

    today = pd.Timestamp.today().normalize()
    rows, previous_done = [], True
    for _, step in steps.iterrows():
        value, display = _measure(step["metric"], m)
        threshold = float(step["threshold"])
        done = value is not None and value >= threshold

        # 앞 단계가 끝나야 다음 단계가 '진행 중'이 된다. 사다리라서 순서가 있다.
        if done:
            status = STATUS_DONE
        elif previous_done:
            status = STATUS_ACTIVE
        else:
            status = STATUS_WAITING
        previous_done = done

        target = pd.to_datetime(step.get("company_target"), errors="coerce")
        days_left = int((target - today).days) if pd.notna(target) else None
        rows.append({
            "step": int(step["step"]), "name": step["name"], "definition": step["definition"],
            "value": value, "display": display, "threshold": threshold,
            "status": status, "target": target, "days_left": days_left,
            "target_source": step.get("target_source"),
            "peer_reference": step.get("peer_reference"), "note": step.get("note"),
            "overdue": bool(days_left is not None and days_left < 0 and not done),
        })
    return pd.DataFrame(rows)


def progress(frame: pd.DataFrame) -> dict:
    """사다리 전체 진척. 달성 단계 수와 지연 여부."""
    if frame.empty:
        return {}
    done = int((frame["status"] == STATUS_DONE).sum())
    active = frame[frame["status"] == STATUS_ACTIVE]
    return {
        "done": done,
        "total": len(frame),
        "current": active.iloc[0]["name"] if not active.empty else None,
        "current_step": int(active.iloc[0]["step"]) if not active.empty else None,
        "overdue": int(frame["overdue"].sum()),
        "next_target": active.iloc[0]["target"] if not active.empty else None,
        "next_days": active.iloc[0]["days_left"] if not active.empty else None,
    }
