"""주간 요약 — 개별 알림감은 아니지만 모아 보면 의미 있는 것들.

즉시 알림은 '판정을 바꾸는 사건'만 받는다. 그런데 내부자 매도 한 건, 임원 교체 한 건,
주가 하루 등락은 각각으로는 알릴 값어치가 없어도 **한 주치를 쌓아 놓고 보면 방향이 보인다.**
그렇다고 실시간으로 보내면 알림이 시끄러워져 정작 계약 공시를 놓친다. 그래서 금요일 한 통.

    docker compose exec -T fermi-dashboard python digest.py
"""

import sys

import pandas as pd

import alerts

LOOKBACK_DAYS = 7
# 요약에 이름을 적어줄 서식. 즉시 알림에서 뺀 것들이 여기로 모인다.
FORM_LABEL = {
    "4": "내부자 거래", "3": "내부자 최초신고", "SCHEDULE 13D": "대량보유(경영참가)",
    "SCHEDULE 13G": "대량보유(단순투자)", "10-Q": "분기보고", "10-K": "연차보고",
    "424B": "증권 발행", "S-3": "일괄신고", "DEF 14A": "주주총회", "8-K": "수시공시",
}


def _label(form: str) -> str:
    upper = (form or "").upper()
    for prefix, name in FORM_LABEL.items():
        if upper.startswith(prefix):
            return name
    return form or "기타"


def _price_block(price_frame: pd.DataFrame) -> list[str]:
    if price_frame is None or price_frame.empty or len(price_frame) < 2:
        return []
    frame = price_frame.dropna(subset=["close"]).copy()
    if frame.empty:
        return []
    last = frame.iloc[-1]
    cutoff = pd.Timestamp(last["date"]) - pd.Timedelta(days=LOOKBACK_DAYS)
    window = frame[pd.to_datetime(frame["date"]) >= cutoff]
    if len(window) < 2:
        return []
    change = (last["close"] / window.iloc[0]["close"] - 1) * 100
    arrow = "▲" if change >= 0 else "▼"
    return [f"주가 <b>${last['close']:,.2f}</b> · 주간 {arrow} {abs(change):.1f}%"]


def _roadmap_block(steps: pd.DataFrame, state: dict) -> list[str]:
    if not state:
        return []
    lines = [f"로드맵 <b>{state['done']}/{state['total']}단계</b>"]
    if state.get("current"):
        # days_left는 목표일이 비어 있으면 NaN이 섞여 float가 된다. int()가 바로 터진다.
        days = state.get("next_days")
        when = ""
        if days is not None and pd.notna(days):
            days = int(days)
            when = f" · 목표까지 {days}일" if days >= 0 else f" · 목표 {abs(days)}일 초과"
        lines.append(f"진행 중: {state['current']}{when}")
    if state.get("overdue"):
        lines.append(f"⚠️ 회사가 공언한 일정을 넘긴 단계 {state['overdue']}개")
    return lines


def _filings_block(filings: pd.DataFrame) -> list[str]:
    if filings is None or filings.empty:
        return ["그 주 공시 없음"]
    cutoff = pd.Timestamp.today().normalize() - pd.Timedelta(days=LOOKBACK_DAYS)
    week = filings[filings["filed"] >= cutoff]
    if week.empty:
        return ["그 주 공시 없음"]
    counts = week["form"].map(_label).value_counts()
    return [f"공시 {len(week)}건 — " + ", ".join(f"{name} {n}" for name, n in counts.items())]


def compose(m: dict, steps: pd.DataFrame, state: dict, filings: pd.DataFrame,
            price_frame: pd.DataFrame, verdicts: list[dict]) -> str:
    today = pd.Timestamp.today().normalize().date()
    lines = [f"📅 <b>페르미 주간 요약</b> · {today}", ""]

    lines.append("<b>핵심 판정</b>")
    for item in verdicts:
        lines.append(f"{alerts.status_icon(item.get('status'))} "
                     f"{alerts._escape(item['label'])} — {alerts._escape(item['value'])}")
    lines.append("")

    body = _roadmap_block(steps, state) + _price_block(price_frame) + _filings_block(filings)
    lines += [alerts._escape(line) if "<b>" not in line else line for line in body]

    if alerts.DASHBOARD_URL:
        lines.append(f'\n<a href="{alerts._escape(alerts.DASHBOARD_URL)}">대시보드</a>')
    return "\n".join(lines)


def main() -> int:
    if not alerts.configured():
        print("[skip] TELEGRAM_BOT_TOKEN/CHAT_ID 없음")
        return 0
    import fundamentals as fd
    import market
    import roadmap as rm
    import sec_edgar as sec
    import sector as sc

    raw = lambda function: getattr(function, "__wrapped__", function)  # noqa: E731
    try:
        price_frame, price_meta = raw(market.load_price)("FRMI")
        m = raw(fd.compute)(raw(sec.load_company_facts)(), price_meta)
        steps = rm.evaluate(m)
        result = alerts.send(compose(m, steps, rm.progress(steps),
                                     raw(sec.load_filings)(), price_frame,
                                     sc.fermi_position(m)))
    except Exception as error:
        print(f"[fail] 주간 요약 실패: {type(error).__name__}: {error}")
        return 1
    ok, error = result
    print("[ok] 주간 요약 발송" if ok else f"[fail] 전송 실패: {error}")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
