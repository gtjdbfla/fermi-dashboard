"""하루 한 번 텔레그램으로 보내는 종합 리포트.

즉시 알림은 **판정을 바꾸는 사건**만 받는다(8-K 계약, 테넌트 악재, 분기 실적). 그런데 개별로는
알릴 값어치가 없어도 하루치를 모으면 방향이 보이는 것들이 있다 — 그날 들어온 공시, 애널리스트
목표가 조정, 주가 흐름, 로드맵 진척.

**지난 리포트 이후 새로 생긴 것만 신규로 표시한다.** 매일 같은 표를 다시 보내면 읽지 않게 된다.
기준 시각은 캐시에 남겨 두고, 다음 리포트가 그 시점 이후만 센다.

    docker compose exec -T fermi-dashboard python digest.py
"""

import sys

import pandas as pd

import alerts
import diskcache as dc

WATERMARK = "digest_watermark"
FALLBACK_HOURS = 24        # 처음 보내는 경우 하루치를 본다.

FORM_LABEL = {
    "8-K": "수시공시", "4": "내부자 거래", "3": "내부자 최초신고",
    "SCHEDULE 13D": "대량보유(경영참가)", "SCHEDULE 13G": "대량보유(단순투자)",
    "10-Q": "분기보고", "10-K": "연차보고", "424B": "증권 발행", "S-3": "일괄신고",
    "DEF 14A": "주주총회", "DEFA14A": "주주총회 추가자료", "DFAN14A": "위임장 자료",
}


def _label(form: str) -> str:
    upper = (form or "").upper()
    for prefix, name in FORM_LABEL.items():
        if upper.startswith(prefix):
            return name
    return form or "기타"


def since() -> pd.Timestamp:
    stored = dc.load_json(WATERMARK, 86400 * 30) or {}
    mark = pd.to_datetime(stored.get("at"), errors="coerce", utc=True)
    if pd.isna(mark):
        return pd.Timestamp.now(tz="UTC") - pd.Timedelta(hours=FALLBACK_HOURS)
    return mark


def stamp() -> None:
    dc.save_json(WATERMARK, {"at": pd.Timestamp.now(tz="UTC").isoformat()})


def _verdicts(verdicts) -> list[str]:
    lines = ["<b>핵심 판정</b>"]
    for item in verdicts:
        lines.append(f"{alerts.status_icon(item.get('status'))} "
                     f"{alerts._escape(item['label'])} — {alerts._escape(item['value'])}")
    return lines


def _roadmap(state: dict) -> list[str]:
    if not state:
        return []
    line = f"로드맵 <b>{state['done']}/{state['total']}단계</b>"
    if state.get("current"):
        line += f" · 진행 중: {alerts._escape(state['current'])}"
    out = [line]
    if state.get("overdue"):
        out.append(f"⚠️ 회사가 공언한 일정을 넘긴 단계 {state['overdue']}개")
    return out


def _price(frame: pd.DataFrame) -> list[str]:
    if frame is None or frame.empty or len(frame) < 2:
        return []
    data = frame.dropna(subset=["close"])
    if len(data) < 2:
        return []
    last, prev = data.iloc[-1], data.iloc[-2]
    day = (last["close"] / prev["close"] - 1) * 100
    week = data[pd.to_datetime(data["date"]) >= pd.Timestamp(last["date"]) - pd.Timedelta(days=7)]
    weekly = (last["close"] / week.iloc[0]["close"] - 1) * 100 if len(week) > 1 else None
    text = f"주가 <b>${last['close']:,.2f}</b> · 전일 {day:+.1f}%"
    if weekly is not None:
        text += f" · 주간 {weekly:+.1f}%"
    return [text]


def _new_filings(filings: pd.DataFrame, mark: pd.Timestamp) -> list[str]:
    if filings is None or filings.empty:
        return []
    fresh = filings[filings["filed"] >= mark.tz_localize(None)]
    if fresh.empty:
        return []
    counts = fresh["form"].map(_label).value_counts()
    lines = [f"📄 공시 {len(fresh)}건 — " + ", ".join(f"{n} {c}" for n, c in counts.items())]
    for row in fresh.head(4).itertuples():
        items = f" [{row.items}]" if getattr(row, "items", "") else ""
        lines.append(f"    · {row.filed.date()} {row.form}{items}")
    return lines


def _new_articles(articles: pd.DataFrame, mark: pd.Timestamp) -> list[str]:
    if articles is None or articles.empty:
        return []
    published = pd.to_datetime(articles["published"], errors="coerce", utc=True)
    fresh = articles[published >= mark]
    if fresh.empty:
        return []
    hits = fresh[fresh["group"] == "계약·테넌트"] if "group" in fresh.columns else fresh.head(0)
    lines = [f"📰 기사 {len(fresh)}건 (계약·테넌트 {len(hits)}건)"]
    for row in hits.head(3).itertuples():
        lines.append(f"    · {alerts._escape(str(row.title)[:70])}")
    return lines


def _new_actions(actions: pd.DataFrame, mark: pd.Timestamp) -> list[str]:
    if actions is None or actions.empty:
        return []
    when = pd.to_datetime(actions["시점"], errors="coerce")
    fresh = actions[when >= mark.tz_localize(None).normalize()]
    if fresh.empty:
        return []
    lines = [f"📊 애널리스트 {len(fresh)}건"]
    for row in fresh.head(4).to_dict("records"):
        bit = f"    · {row['증권사']} {row['행동']}"
        if row["목표가"] != "–":
            bit += f" {row['목표가']}" + (f" (이전 {row['이전']})" if row["이전"] != "–" else "")
        if row["언급된 이유"] != "–":
            bit += f" — {alerts._escape(row['언급된 이유'])}"
        lines.append(bit)
    return lines


def _staleness(m: dict, price_frame) -> list[str]:
    """크론이 조용히 멈췄으면 리포트에서 드러나야 한다."""
    import freshness as fresh
    rows = fresh.rows(m, price_frame)
    late = rows[rows["상태"].astype(str).str.startswith("⚠️")]
    if late.empty:
        return []
    return ["⚠️ <b>갱신 지연</b> — " + ", ".join(f"{r['데이터']}({r['경과']})"
                                             for _, r in late.iterrows())]


def compose(m, verdicts, state, filings, articles, actions, price_frame, mark) -> str:
    today = pd.Timestamp.now(tz="Asia/Seoul").strftime("%Y-%m-%d")
    lines = [f"📅 <b>페르미 일일 리포트</b> · {today}", ""]
    lines += _verdicts(verdicts) + [""]
    lines += _roadmap(state) + _price(price_frame)

    new_blocks = (_new_filings(filings, mark) + _new_articles(articles, mark)
                  + _new_actions(actions, mark))
    lines += ["", f"<b>🆕 지난 리포트 이후</b> ({mark.tz_convert('Asia/Seoul').strftime('%m-%d %H:%M')} 기준)"]
    lines += new_blocks if new_blocks else ["    새로 들어온 것 없음"]

    warn = _staleness(m, price_frame)
    if warn:
        lines += [""] + warn
    if alerts.DASHBOARD_URL:
        lines.append(f'\n<a href="{alerts._escape(alerts.DASHBOARD_URL)}">대시보드</a>')
    return "\n".join(lines)


def main() -> int:
    if not alerts.configured():
        print("[skip] TELEGRAM_BOT_TOKEN/CHAT_ID 없음")
        return 0

    import analyst as an
    import fundamentals as fd
    import market
    import news as nw
    import roadmap as rm
    import sec_edgar as sec
    import sector as sc

    raw = lambda function: getattr(function, "__wrapped__", function)  # noqa: E731
    mark = since()
    try:
        price_frame, price_meta = raw(market.load_price)("FRMI")
        m = raw(fd.compute)(raw(sec.load_company_facts)(), price_meta)
        m["staleness_asof"] = raw(fd.staleness_asof)()
        steps = rm.evaluate(m)
        articles = raw(nw.cached_articles)()
        text = compose(m, sc.fermi_position(m), rm.progress(steps),
                       raw(sec.load_filings)(), articles,
                       an.merged_actions(articles), price_frame, mark)
    except Exception as error:
        print(f"[fail] 리포트 생성 실패: {type(error).__name__}: {error}")
        return 1

    ok, error = alerts.send(text)
    if ok:
        stamp()     # 보낸 것이 확인된 뒤에만 기준 시각을 민다
        print("[ok] 일일 리포트 발송")
        return 0
    print(f"[fail] 전송 실패: {error}")
    return 1


if __name__ == "__main__":
    sys.exit(main())
