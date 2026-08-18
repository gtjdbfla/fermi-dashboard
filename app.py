"""페르미(FRMI) 펀더멘탈 대시보드.

화면 구조는 검증 결과를 그대로 따른다. 전력·에너지 인프라 13개사로 항목별 판별력을 검증했더니
처음 세웠던 7개 축 중 3개는 생존/붕괴를 가르지 못했다(sector.py, data/axis_validation.csv).

  핵심 — 계약 커버리지 · 현금흐름 전환
  참고 — 런웨이 · 이자 자본화 · 희석 · 지배구조 · 밸류에이션 (판정에서 내림)

**화면에는 숫자만 두고 설명은 제목 옆 ? 뒤에 넣는다.** 설명이 길게 깔려 있으면 정작 봐야 할
수치가 묻힌다. 근거를 지우지는 않되 눌러야 보이게 한다.
"""

import pandas as pd
import plotly.graph_objects as go
import streamlit as st

import ai_review
import analyst as an
import filing_review as fr
import freshness as fresh
import fundamentals as fd
import market
import maturity as mt
import market_flow as mflow
import news as nw
import roadmap as rm
import sec_edgar as sec
import sector as sc
import theme as th

st.set_page_config(page_title="페르미(FRMI) 펀더멘탈", page_icon="⚡", layout="wide")

st.markdown(
    """
    <style>
      div[data-testid="stMetricValue"] { font-size: 1.4rem; }
      section.main > div { padding-top: 1rem; }
      /* 제목 옆 물음표 칸은 아이콘 크기만큼만 차지하게 한다 */
      div[class*="st-key-help_"] div[data-testid="stHorizontalBlock"] { gap: .25rem; }
      div[class*="st-key-help_"] button { padding: 0 .3rem; min-height: 1.6rem; }
      div[class*="st-key-help_"] p { margin-bottom: 0; }
    </style>
    """,
    unsafe_allow_html=True,
)

_help_ids = {}


def heading(title: str, help_text: str = "", size: str = "#####"):
    """제목 옆 물음표. 눌렀을 때만 설명이 열린다.

    st.markdown(help=...) 툴팁은 마우스를 올려야 열려 모바일에서 쓰기 어렵다.
    """
    if not help_text:
        st.markdown(f"{size} {title}")
        return
    key = f"help_{len(_help_ids)}"
    _help_ids[key] = title
    with st.container(key=key):
        left, right = st.columns([0.92, 0.08], vertical_alignment="center")
        left.markdown(f"{size} {title}")
        with right.popover("", icon=":material/help:"):
            st.markdown(help_text)


def note(help_text: str, label: str = "설명"):
    """제목이 없는 자리에 설명만 접어 두는 경우."""
    key = f"help_{len(_help_ids)}"
    _help_ids[key] = label
    with st.container(key=key):
        with st.popover(label, icon=":material/help:"):
            st.markdown(help_text)


# ── 렌더 헬퍼 ─────────────────────────────────────────────────────────────────
def bar(frame, x, y, color=None, unit="M$", digits=0, height=300, horizontal=False, colors=None,
        key=None):
    palette = th.palette()
    figure = go.Figure()
    marker = dict(color=colors if colors is not None else (color or palette["series"][0]),
                  line=dict(color=palette["surface"], width=2))
    text = [f"{value:,.{digits}f}" for value in frame[y]]
    if horizontal:
        figure.add_bar(y=frame[x], x=frame[y], orientation="h", marker=marker, text=text,
                       textposition="outside", cliponaxis=False,
                       hovertemplate="%{y}<br>%{x:,.1f} " + unit + "<extra></extra>")
        figure.update_yaxes(autorange="reversed")
        th.style(figure, height=height, ygrid=False)
        figure.update_xaxes(showgrid=True, gridcolor=palette["grid"])
    else:
        figure.add_bar(x=frame[x], y=frame[y], marker=marker, text=text,
                       textposition="outside", cliponaxis=False,
                       hovertemplate="%{x}<br>%{y:,.1f} " + unit + "<extra></extra>")
        th.style(figure, height=height)
    figure.update_traces(marker_cornerradius=4,
                         textfont=dict(color=palette["ink_soft"], size=11))
    return figure


def grouped_bar(frame, x, series, unit="M$", height=320, stacked=False):
    palette = th.palette()
    figure = go.Figure()
    for index, (column, name) in enumerate(series):
        # 쌓은 막대에서는 작은 조각의 숫자가 잘려 오히려 읽기 어렵다. 값은 표로 읽는다.
        labels = None if stacked else [f"{value:,.0f}" for value in frame[column]]
        figure.add_bar(x=frame[x], y=frame[column], name=name,
                       marker=dict(color=palette["series"][index],
                                   line=dict(color=palette["surface"], width=2)),
                       text=labels, textposition="outside", cliponaxis=False,
                       hovertemplate="%{x} · " + name + "<br>%{y:,.0f} " + unit + "<extra></extra>")
    th.style(figure, height=height, legend=True)
    figure.update_layout(barmode="stack" if stacked else "group")
    figure.update_traces(marker_cornerradius=4, textfont=dict(color=palette["ink_soft"], size=11))
    return figure


def line(frame, x, y, name, unit="", height=300):
    palette = th.palette()
    figure = go.Figure()
    figure.add_scatter(x=frame[x], y=frame[y], mode="lines", name=name,
                       line=dict(color=palette["series"][0], width=2),
                       hovertemplate="%{x|%Y-%m-%d}<br>%{y:,.2f} " + unit + "<extra></extra>")
    th.style(figure, height=height)
    figure.update_layout(hovermode="x unified")
    return figure


def compare_line(frame, x, series, unit="", height=320):
    palette = th.palette()
    figure = go.Figure()
    for index, (column, name) in enumerate(series):
        figure.add_scatter(x=frame[x], y=frame[column], mode="lines", name=name,
                           line=dict(color=palette["series"][index],
                                     width=2.5 if index == 0 else 2,
                                     dash="solid" if index == 0 else "dot"),
                           hovertemplate="%{x|%Y-%m-%d} · " + name + "<br>%{y:,.1f} " + unit
                                         + "<extra></extra>")
    th.style(figure, height=height, legend=True)
    figure.update_layout(hovermode="x unified")
    return figure


def table(frame, **kwargs):
    st.dataframe(frame, use_container_width=True, hide_index=True, **kwargs)


def metric_row(items):
    for column, (label, value, help_text) in zip(st.columns(len(items)), items):
        column.metric(label, value, help=help_text or None)


# ── 데이터 ────────────────────────────────────────────────────────────────────
with st.spinner("불러오는 중..."):
    try:
        facts = sec.load_company_facts()
    except Exception as error:
        st.error(f"SEC EDGAR 접속 실패: {error}\n\n환경변수 SEC_USER_AGENT를 확인하세요.")
        st.stop()
    price_frame, price_meta = market.load_price(sec.TICKER)
    m = fd.compute(facts, price_meta)

if sec.USER_AGENT_IS_PLACEHOLDER:
    st.warning("`SEC_USER_AGENT` 미설정 — 자리표시자로 동작 중이며 곧 차단될 수 있다.", icon="⚠️")

profile = sec.company_profile()

# ── 헤더 ──────────────────────────────────────────────────────────────────────
head_left, head_right = st.columns([0.55, 0.45])
with head_left:
    st.title("⚡ 페르미 펀더멘탈")
    st.caption(f"{profile.get('name') or sec.COMPANY_KO} · {sec.EXCHANGE}: {sec.TICKER} · "
               f"CIK {int(sec.CIK)}")
with head_right:
    metric_row([
        ("주가", f"${m['price']:,.2f}" if m.get("price") else "–", "Yahoo Finance 지연 시세"),
        ("시가총액", fd.usd(m.get("market_cap")), "주가 × 최근 보고 발행주식수"),
        ("재무 기준일", str(pd.Timestamp(m["asof"]).date()) if m.get("asof") is not None else "–",
         f"출처: {m.get('asof_source', '–')}"),
    ])
    m["staleness_asof"] = fd.staleness_asof()
    with st.popover(fresh.summary_line(), icon=":material/schedule:",
                    use_container_width=True):
        st.markdown("**데이터별 갱신 시각**")
        table(fresh.rows(m, price_frame))
        st.caption(
            "층마다 갱신 주기가 다르다. '최신 시점'은 데이터 자체의 기준일이고, '경과'는 마지막으로 "
            "받아온 뒤 흐른 시간이다. 갱신 주기의 3배를 넘으면 ⚠️ 지연으로 표시한다 — 크론이 "
            "조용히 멈춘 경우를 여기서 알아챈다."
        )

# ── 수동 데이터 노후화 ────────────────────────────────────────────────────────
stale = fd.staleness(m)
if stale["count"] > 0:
    st.warning(f"**수동 데이터가 낡았을 수 있다** — {pd.Timestamp(stale['asof']).date()} 이후 "
               f"새 공시 {stale['count']}건.", icon="🔔")
    review = fr.cached()
    if review.get("text"):
        with st.container(border=True):
            st.markdown("**🤖 공시 판독**")
            st.markdown(review["text"])
        st.caption("서버 크론이 30분마다 새 공시를 읽어 판정한다. **CSV는 자동으로 바뀌지 않는다** — "
                   "확정 반영은 저장소에 커밋해야 한다.")
    elif review.get("error"):
        st.caption(f"공시 판독 대기 — {review['error']}")
    feed = stale["filings"].copy()
    feed["접수일"] = feed["filed"].dt.date
    table(feed.rename(columns={"form": "종류", "title": "제목", "url": "링크"})
          [["접수일", "종류", "제목", "링크"]],
          column_config={"링크": st.column_config.LinkColumn("링크", display_text="열기")})
else:
    reviews = fd.load_csv("review_log.csv")
    last = reviews.iloc[-1] if not reviews.empty else None
    st.caption(f"✅ 수동 데이터 {pd.Timestamp(stale['asof']).date()}까지 반영"
               + (f" · {last['verdict']}" if last is not None else ""))

# ── 핵심 판정 ─────────────────────────────────────────────────────────────────
heading(
    "핵심 판정", size="####",
    help_text=(
        "전력·에너지 인프라 13개사로 검증해 **실제로 생존과 붕괴를 가른 지표만** 남겼다. "
        "판별력이 없던 항목(런웨이·이자 자본화·희석)은 **참고 지표** 탭으로 내렸다.\n\n"
        "정보 정리 목적이며 투자 권유가 아니다."
    ),
)
for column, item in zip(st.columns(3), sc.fermi_position(m)):
    with column.container(border=True):
        st.markdown(f"**{item['label']}**")
        st.markdown(f"### {item['value']}")
        st.markdown(f"{th.STATUS_ICON[item['status']]} **{item['verdict']}**")
        if item["detail"]:
            with st.popover("근거", icon=":material/help:"):
                st.markdown(item["detail"])

# 원문자는 핵심 판정 ①②③과 **같은 뜻으로만** 쓴다. 예전에는 탭 ②가 현금흐름(판정 ③)이라
# 같은 기호가 두 곳에서 다른 걸 가리켰고, 판정 ②는 볼 화면이 아예 없었다.
tabs = st.tabs(["① 계약 커버리지", "② 만기 정합", "③ 현금흐름 전환", "로드맵", "섹터 검증",
                "시장·수급", "뉴스·소문", "애널리스트", "참고 지표", "원본 데이터"])

# ── ① 계약 커버리지 ───────────────────────────────────────────────────────────
with tabs[0]:
    heading("지을 용량을 사 줄 고객이 계약돼 있는가", size="####", help_text=(
        "검증에서 가장 깨끗하게 갈린 지표다. 유지 그룹 6곳은 전부 장기 take-or-pay로 부채 만기를 "
        "덮었고, 붕괴 4곳은 계약이 없거나(Tellurian) 만기가 어긋났다(New Fortress)."))
    st.caption(fresh.tab_line("contract", m, price_frame))
    metric_row([
        ("구속력 있는 계약", fd.num(m.get("mw_contracted"), 0, " MW"), "서명 완료된 리스 기준"),
        ("반입 설비 대비", fd.pct(m.get("contracted_vs_landed")), "확보한 설비 중 팔린 비중"),
        ("장기 목표 대비", fd.pct(m.get("contracted_vs_target")), "17GW 목표 대비 실체"),
        ("고객 수", fd.num(m.get("customer_count"), 0, "개사"), "집중도 100%면 단일 계약 리스크"),
    ])
    metric_row([
        ("계약 총액", fd.usd((m.get("backlog_musd") or 0) * 1e6), "계약 기간 전체 합산"),
        ("옵션 포함 최대", fd.num(m.get("mw_contracted_option"), 0, " MW"), "확장옵션 전량 행사 가정"),
        ("MW·년당 단가", fd.usd(m.get("revenue_per_mw_year")), "계약 총액 ÷ MW ÷ 연수"),
        ("계약 ÷ 누적 투입", fd.num(
            (m.get("backlog_musd") or 0) * 1e6 / m["ppe_gross"] if m.get("ppe_gross") else None, 2, "배"),
         "쌓은 자산이 계약으로 회수되는 비율"),
    ])

    left, right = st.columns(2)
    with left:
        heading("섹터 대비 커버리지 (%)", help_text=(
            "유지 그룹의 관측 범위는 74~92%다. Tellurian은 0%에서 매각됐다."))
        summary = sc.summary()
        bench = summary.dropna(subset=["coverage"])[["company", "group", "coverage"]].copy()
        coverage_now = (m.get("mw_contracted") or 0) / (m.get("mw_landed") or 1) * 100
        bench = pd.concat([bench, pd.DataFrame([
            {"company": "Fermi ← 현재", "group": "대상", "coverage": coverage_now}])])
        bench["라벨"] = bench["company"] + " (" + bench["group"].astype(str) + ")"
        bench = bench.sort_values("coverage", ascending=False)
        st.plotly_chart(bar(bench, "라벨", "coverage", unit="%", digits=0, horizontal=True,
                            height=280), use_container_width=True)
    with right:
        heading("반입 설비의 계약 충족도 (MW)")
        landed, contracted = m.get("mw_landed") or 0, m.get("mw_contracted") or 0
        gap = pd.DataFrame({"구분": ["계약 완료", "미계약"],
                            "MW": [contracted, max(landed - contracted, 0)]})
        st.plotly_chart(bar(gap, "구분", "MW", unit="MW", horizontal=True, height=160,
                            colors=[th.palette()["series"][0], th.palette()["ordinal"][0]]),
                        use_container_width=True)
        heading("확정도별 전력 용량 (MW)", help_text=(
            "색이 진할수록 확정도가 높다. **목표(17GW)와 실제 가동(0MW) 사이 거리가 곧 실행 리스크**이고, "
            "그 사이 단계들이 예정대로 내려오는지가 점검 대상이다."))
        stages = m["power_stages"].sort_values("order")
        st.plotly_chart(bar(stages, "stage", "mw", unit="MW", horizontal=True, height=280,
                            colors=list(reversed(th.ordinal_colors(len(stages))))),
                        use_container_width=True)

    heading("계약 내역")
    if not m["contracts"].empty:
        table(m["contracts"].rename(columns={
            "customer": "고객", "signed": "체결일", "binding": "구속력", "phase1_mw": "1단계 MW",
            "option_mw": "옵션 포함 MW", "total_revenue_musd": "계약총액(백만$)", "term_years": "기간(년)",
            "extension": "연장옵션", "delivery_start": "인도 개시", "source": "출처", "note": "비고"}))
    heading("확정도별 단계 원본")
    table(stages.rename(columns={"order": "순서", "stage": "단계", "mw": "MW", "certainty": "확정도",
                                 "as_of": "기준일", "source": "출처", "note": "비고"}))

# ── ② 만기 정합 ───────────────────────────────────────────────────────────────
with tabs[1]:
    heading("계약 기간이 부채 만기를 덮는가", size="####", help_text=(
        "**계약이 있어도 무너질 수 있다.** New Fortress가 그랬다 — 계약 자체는 있었는데 "
        "계약에서 현금이 들어오기 전에 부채 만기가 먼저 와서 재융자에 실패했고, 영업현금흐름이 "
        "+$602M에서 -$583M으로 뒤집혔다. 붕괴 4곳 중 **계약을 갖고도** 무너진 유일한 사례다.\n\n"
        "그래서 두 시점의 순서만 본다 — 리스에서 돈이 들어오는 때와, 갚아야 하는 때.\n\n"
        "값은 `data/contracts.csv`(리스 기간)와 `data/capital_events.csv`(차입 만기)에서 "
        "계산한다. 문장에 박아두면 새 사채가 발행돼도 화면이 그대로다."))
    st.caption(fresh.tab_line("maturity", m, price_frame))

    mv = mt.verdict(m)
    schedule = mt.schedule()
    leases = schedule[schedule["구분"] == "리스 유입"].dropna(subset=["종료"])
    debts = schedule[schedule["구분"] == "부채 만기"]
    known = debts.dropna(subset=["종료"])
    metric_row([
        ("판정", f"{th.STATUS_ICON[mv['status']]} {mv['verdict'].split(' — ')[0]}",
         mv["verdict"]),
        ("리스 종료", str(leases["종료"].max().year) + "년" if not leases.empty else "–",
         "구속력 있는 계약의 마지막 해"),
        ("가장 이른 부채 만기", str(known["종료"].min().year) + "년" if not known.empty else "–",
         "이 시점이 관문이다"),
        ("여유", f"{mv['gap_years']:+.0f}년" if mv.get("gap_years") is not None else "–",
         "리스 종료 − 최초 만기"),
    ])

    if not schedule.empty:
        # 유입과 상환을 같은 시간축에 놓아야 순서가 눈에 들어온다.
        palette = th.palette()
        figure = go.Figure()
        for index, row in enumerate(schedule.dropna(subset=["시작"]).to_dict("records")):
            end = row["종료"] if pd.notna(row["종료"]) else row["시작"] + pd.DateOffset(months=6)
            inflow = row["구분"] == "리스 유입"
            figure.add_scatter(
                x=[row["시작"], end], y=[index, index], mode="lines+markers",
                line=dict(color=palette["series"][0] if inflow else palette["series"][1],
                          width=10 if inflow else 6,
                          dash="solid" if pd.notna(row["종료"]) else "dot"),
                marker=dict(size=8), name=row["항목"], showlegend=False,
                hovertemplate=f"{row['항목']}<br>%{{x|%Y-%m}}<extra></extra>")
        th.style(figure, height=260)
        figure.update_yaxes(showticklabels=False)
        st.plotly_chart(figure, use_container_width=True)
        st.caption("굵은 선이 리스 유입, 가는 선이 차입. **점선은 만기를 확인하지 못한 것**이다.")

    table(mt.view(schedule))

    note(mv["detail"], label="이 판정의 근거")

# ── ② 현금흐름 전환 ───────────────────────────────────────────────────────────
with tabs[2]:
    heading("가동 후 현금이 실제로 들어오기 시작하는가", size="####", help_text=(
        "유지 그룹 6곳은 전부 영업현금흐름 흑자에 도달했고, 붕괴 4곳은 도달하지 못했거나"
        "(Plug Power·FuelCell) 도달한 뒤 되돌아갔다(New Fortress).\n\n"
        "**매출의 유무가 아니라 매출이 현금을 만드는가**가 갈랐다 — Core Scientific은 매출 $640M을 "
        "내면서 파산했다."))
    st.caption(fresh.tab_line("cashflow", m, price_frame))
    metric_row([
        ("현재 매출", fd.usd(m.get("revenue_q")) if m.get("revenue_q") else "$0",
         "XBRL 매출 태그 기준. 태그가 없으면 pre-revenue다"),
        ("분기 영업현금흐름", fd.usd(m.get("op_cf_q")), "최근 분기 · 부호 그대로"),
        ("T0 이후 경과", "1년차", "대규모 자본 투입 시작(2025) 기준"),
        ("첫 상업 전력 목표", "약 200MW", "회사 발표 기준"),
    ])

    left, right = st.columns(2)
    with left:
        heading("분기 영업현금흐름 (백만$)", help_text=(
            "아직 전 분기 적자다. **이 곡선이 0을 넘어 유지되는 시점**이 판별 지점이다."))
        ops = m["op_cf_series"].copy()
        ops["금액"] = ops["val"] / 1e6
        st.plotly_chart(bar(ops, "label", "금액", digits=1), use_container_width=True)
    with right:
        heading("섹터: T0에서 흑자까지 (년)", help_text=(
            "붕괴 그룹은 이 막대가 아예 없다 — 전환에 도달하지 못했기 때문이다. "
            "페르미는 T0+1년차라 아직 이 표에 오를 수 없다."))
        turn = sc.summary().dropna(subset=["years_to_turn"])[["company", "group", "years_to_turn"]].copy()
        turn["라벨"] = turn["company"] + " (" + turn["group"].astype(str) + ")"
        if not turn.empty:
            st.plotly_chart(bar(turn.sort_values("years_to_turn"), "라벨", "years_to_turn",
                                unit="년", digits=0, horizontal=True, height=280),
                            use_container_width=True)

    heading("마일스톤 이행 현황")
    milestones = m["milestones"].copy()
    if not milestones.empty:
        milestones["date"] = milestones["date"].dt.date
        table(milestones.rename(columns={"date": "일자", "category": "구분", "milestone": "내용",
                                         "status": "상태", "source": "출처"}))

# ── ③ 로드맵 ──────────────────────────────────────────────────────────────────
with tabs[3]:
    steps = rm.evaluate(m)
    state = rm.progress(steps)
    heading("유지 기업들의 길을 가려면", size="####", help_text=(
        "**왜 '유지 기업 평균 소요 시간'이 아닌가.** 뽑으려고 계산했지만 나오지 않았다. "
        "T0 이전에 이미 매출이 있던 회사가 많아서다 — Cheniere $292M(재기화), Bloom $972M(장비), "
        "Core Scientific $544M(비트코인). 이들에게 '첫 매출까지'는 음수가 되고 평균은 -0.4년이 된다. "
        "**페르미처럼 무매출로 시작해 사다리를 끝까지 올라간 표본이 사실상 없다.**\n\n"
        "그래서 1차 잣대는 회사가 공시에 적은 목표 시점으로 두고, 개별 기업 사례를 옆에 붙인다. "
        "목표 시점은 회사가 제시한 것이지 제3자가 검증한 것이 아니다. 다만 **자기가 공언한 일정을 "
        "반복해서 넘기는 것**은 붕괴한 기업들이 공통으로 보인 모습이었다.\n\n"
        "단계 정의와 목표는 `data/roadmap.csv`에 있다."))
    st.caption(fresh.tab_line("roadmap", m, price_frame))
    if state:
        metric_row([
            ("진척", f"{state['done']} / {state['total']} 단계", "달성한 단계 수"),
            ("현재 단계", f"{state['current_step']}. {state['current']}" if state.get("current") else "완료",
             "앞 단계를 끝내야 다음으로 넘어간다"),
            ("다음 목표",
             str(pd.Timestamp(state["next_target"]).date()) if pd.notna(state.get("next_target")) else "미제시",
             "회사가 공시에 적은 목표"),
            ("남은 일수", f"D{int(state['next_days']):+d}" if pd.notna(state.get("next_days")) else "–",
             "음수면 공언한 일정을 넘겼다는 뜻"),
        ])
        if state["overdue"]:
            st.error(f"공언한 일정을 넘긴 단계 {state['overdue']}개", icon="⏰")

    STEP_ICON = {rm.STATUS_DONE: "🟢", rm.STATUS_ACTIVE: "🔵", rm.STATUS_WAITING: "⚪"}
    for row in steps.itertuples():
        with st.container(border=True):
            head = f"**{STEP_ICON[row.status]} {row.step}단계 · {row.name}** — {row.status}"
            if row.overdue:
                head += " ⏰ **일정 초과**"
            st.markdown(head)
            left, right = st.columns([0.45, 0.55])
            with left:
                target_text = "미제시"
                if pd.notna(row.target):
                    target_text = str(pd.Timestamp(row.target).date())
                    if pd.notna(row.days_left):
                        target_text += f" (D{int(row.days_left):+d})"
                metric_row([("현재", row.display, row.definition),
                            ("회사 목표", target_text, row.target_source or "")])
            with right:
                with st.popover("참고 사례", icon=":material/help:"):
                    st.markdown(f"{row.peer_reference}\n\n{row.note}")

# ── ④ 섹터 검증 ───────────────────────────────────────────────────────────────
with tabs[4]:
    heading("이 항목들이 실제로 결과를 갈랐는가", size="####", help_text=(
        "매출보다 먼저 대규모 인프라를 지은 상장사 13곳. 결과는 주가가 아니라 **객관적 재무 사건**으로 "
        "나눴다(주가로 나누면 순환논증이 된다). 그런 다음 항목별 값을 두 그룹에 대조했다.\n\n"
        "**처음 세웠던 7개 축 중 3개는 판별력이 없었다.** 런웨이는 유지 9.0개월 vs 붕괴 7.2개월로 "
        "차이가 없었고(Cheniere는 2.8개월에서 생존), 이자 자본화는 프로젝트 규모의 반영일 뿐이었다."))
    st.caption(fresh.tab_line("sector", m, price_frame))
    axes = sc.load_axis_validation()
    if not axes.empty:
        table(axes.rename(columns={"label": "항목", "verdict": "판정", "maintained_value": "유지 그룹",
                                   "collapsed_value": "붕괴 그룹", "evidence": "근거", "keep": "처리"})
              [["항목", "판정", "유지 그룹", "붕괴 그룹", "처리", "근거"]])

    heading("기업별 — 주가가 많이 오른 곳부터", help_text=(
        f"페르미는 {sc.FERMI_T0}년에 대규모 자본 투입을 시작했고 지금은 **T0+{sc.FERMI_STAGE_OFFSET}년차**다. "
        "각 기업이 같은 연차에 있던 해를 찾아 그때부터 지금까지의 주가를 쟀다. 보유 기간이 1.6~15.7년으로 "
        "제각각이라 **연환산**을 함께 둔다.\n\n"
        "그 해에 상장 전이었거나 회생 중이던 기업은 **상장(재상장) 첫 관측치**부터 쟀다. Talen과 "
        "Core Scientific은 파산 직후 재상장이라 바닥에서 시작해 수익률이 크게 잡힌다 — 같은 잣대로 "
        "비교하면 안 된다.\n\n"
        "**해석 주의 3가지** — ① 시세를 구할 수 있는 기업이 그룹당 3곳뿐. ② 유지 그룹 중 2023~24년 "
        "기준인 곳(Bloom·Applied Digital)은 AI 랠리와 겹쳐 펀더멘탈만의 효과가 아니다. "
        "③ **유지 그룹 3곳은 그 시점에 이미 매출이 있었다** — 무매출이었던 곳은 NextDecade와 Oklo뿐이고 "
        "둘 다 미결이다."))

    companies = sc.company_view()
    priced = companies.dropna(subset=["total_return_pct"]).copy()
    rank_col, cap_col = st.columns(2)
    with rank_col:
        if not priced.empty:
            st.markdown("**총수익률 (%)**")
            overview = priced.sort_values("total_return_pct", ascending=False).copy()
            overview["라벨"] = overview.apply(
                lambda r: f"{r['company']} ({r['group']}"
                          + (" · 상장 후" if r["basis"] == "상장/재상장 후" else "") + ")", axis=1)
            st.plotly_chart(bar(overview, "라벨", "total_return_pct", unit="%", digits=1,
                                horizontal=True, height=330), use_container_width=True)
    with cap_col:
        caps = companies.dropna(subset=["market_cap"]).copy()
        if not caps.empty:
            st.markdown("**시가총액 (십억$)**")
            caps["십억$"] = caps["market_cap"] / 1e9
            caps["라벨"] = caps["company"] + " (" + caps["group"].astype(str) + ")"
            if m.get("market_cap"):
                caps = pd.concat([caps, pd.DataFrame([
                    {"라벨": "★ Fermi ← 현재", "십억$": m["market_cap"] / 1e9}])])
            st.plotly_chart(bar(caps.sort_values("십억$", ascending=False), "라벨", "십억$",
                                unit="십억$", digits=1, horizontal=True, height=330),
                            use_container_width=True)

    STATUS_BY_GROUP = {"유지": "good", "진행중": "info", "붕괴": "critical"}
    for rank, row in enumerate(companies.itertuples(), start=1):
        group = str(row.group)
        with st.container(border=True):
            st.markdown(f"**{rank}. {row.company} ({row.ticker})** &nbsp; "
                        f"{th.STATUS_ICON[STATUS_BY_GROUP.get(group, 'info')]} {group} "
                        f"&nbsp;·&nbsp; {row.sub}")
            metric_row([
                ("시가총액", fd.usd(row.market_cap) if pd.notna(row.market_cap) else "–", ""),
                ("계약 커버리지", fd.num(row.coverage, 0, "%") if pd.notna(row.coverage) else "미공개",
                 "대규모 자본 투입 시점 기준"),
                ("총수익률", fd.num(row.total_return_pct, 1, "%") if pd.notna(row.total_return_pct) else "–",
                 f"{row.basis_asof} → 현재 ({row.basis})" if pd.notna(row.basis_asof) else ""),
                ("연환산", fd.num(row.cagr_pct, 1, "%/년") if pd.notna(row.cagr_pct) else "–",
                 f"보유 {row.years_held:,.1f}년" if pd.notna(row.years_held) else ""),
                ("T0→흑자", fd.num(row.years_to_turn, 0, "년") if pd.notna(row.years_to_turn) else "미도달",
                 "대규모 투자 시작에서 영업현금흐름 흑자까지"),
            ])
            factors = []
            if pd.notna(row.coverage):
                factors.append({"지표": "계약 커버리지(%)", "값": float(row.coverage)})
            if pd.notna(row.cagr_pct):
                factors.append({"지표": "연환산(%/년)", "값": float(row.cagr_pct)})
            chart_col, table_col = st.columns([0.42, 0.58])
            with chart_col:
                if factors:
                    st.plotly_chart(bar(pd.DataFrame(factors), "지표", "값", unit="%", digits=1,
                                        horizontal=True, height=150),
                                    use_container_width=True, key=f"factors_{row.ticker}")
            with table_col:
                detail = [
                    ("같은 위치였던 해", str(int(row.matched_year)) if pd.notna(row.matched_year) else "–"),
                    ("수익률 기준", f"{row.basis} ({row.basis_asof} 시작)"
                     if pd.notna(row.basis_asof) else "산출 불가"),
                    ("그해 매출", fd.usd(row.revenue_then) if pd.notna(row.revenue_then) else "0 / 미상"),
                    ("그때 → 현재",
                     f"${row.price_then:,.2f} → ${row.price_now:,.2f}"
                     if pd.notna(row.price_then) and pd.notna(row.price_now) else "–"),
                    ("배수", f"{row.multiple:,.2f}배" if pd.notna(row.multiple) else "–"),
                    ("계약 내용", row.contract_detail if pd.notna(row.contract_detail) else "–"),
                    ("결과 근거", row.outcome_basis),
                    ("무엇이 무너졌나", row.what_broke if pd.notna(row.what_broke) else "–"),
                    ("시세 공백 사유", row.gap_reason if pd.notna(row.gap_reason) else "–"),
                ]
                table(pd.DataFrame(detail, columns=["항목", "내용"]))

    if not priced.empty:
        heading("그룹별 연환산 (%/년)")
        table(priced.groupby("group", observed=True)["cagr_pct"]
              .agg(["count", "mean", "min", "max"]).round(1).reset_index()
              .rename(columns={"group": "그룹", "count": "기업 수", "mean": "평균",
                               "min": "최악", "max": "최선"}))

    heading("연도별 원본 재무")
    annuals = sc.load_annuals()
    if not annuals.empty:
        picked = st.selectbox("기업", sorted(annuals["company"].unique()))
        detail = annuals[annuals["company"] == picked].sort_values("fy").copy()
        for column in ["revenue", "op_cf", "capex", "net_income", "cash", "equity", "debt"]:
            detail[column] = (detail[column] / 1e6).round(0)
        detail["shares"] = (detail["shares"] / 1e6).round(1)
        table(detail.rename(columns={
            "fy": "회계연도", "revenue": "매출", "op_cf": "영업CF", "capex": "설비투자",
            "net_income": "순손익", "cash": "현금", "equity": "자기자본", "debt": "장기차입",
            "shares": "주식수(백만)"})[["회계연도", "매출", "영업CF", "설비투자", "순손익", "현금",
                                        "자기자본", "장기차입", "주식수(백만)"]])

# ── ⑤ 시장·수급 ───────────────────────────────────────────────────────────────
with tabs[5]:
    heading("주가가 왜 움직였는가", size="####", help_text=(
        "**이 탭은 펀더멘탈이 아니다.** 회사의 건강이 아니라 주가 움직임을 설명하는 층이다.\n\n"
        "상장 후 219거래일 실측: AI 인프라 동종주 상관 0.39~0.45, 광의 시장 0.28~0.33, "
        "금리(10년 실질) -0.13, 천연가스 -0.05. **금리와 가스는 예상보다 훨씬 약했다.**\n\n"
        "쓰임새는 하나다 — 공시가 났을 때 주가가 움직인 것이 **그 공시 때문인지 그날 AI주가 다 움직인 "
        "것인지** 가르는 것. 상관 0.46은 인과가 아니라 같은 테마에 실려 있다는 뜻일 뿐이다."))
    st.caption(fresh.tab_line("flow", m, price_frame))

    basket = mflow.basket_frame()
    theme_view = mflow.theme_view(basket)
    stats = mflow.theme_stats(basket)
    shorts = mflow.short_interest()

    heading("축 A — 테마 동조도")
    if stats:
        metric_row([
            ("바스켓 상관 (전체)", fd.num(stats.get("corr_full"), 3), "일간 수익률 기준"),
            (f"상관 (최근 {mflow.ROLLING_WINDOW}일)", fd.num(stats.get("corr_rolling"), 3),
             "최근 동조도 변화"),
            ("1개월 상대강도", fd.num(stats.get("rs_1개월"), 1, "%p"),
             f"페르미 {fd.num(stats.get('fermi_1개월'), 1, '%')} vs 바스켓 {fd.num(stats.get('basket_1개월'), 1, '%')}"),
            ("3개월 상대강도", fd.num(stats.get("rs_3개월"), 1, "%p"),
             f"페르미 {fd.num(stats.get('fermi_3개월'), 1, '%')} vs 바스켓 {fd.num(stats.get('basket_3개월'), 1, '%')}"),
        ])
    if not theme_view.empty:
        last = theme_view.iloc[-1]
        heading(f"상장일=100 기준 · 페르미 {last['페르미']:,.0f} vs 바스켓 {last['바스켓']:,.0f}",
                help_text=(
                    "두 선이 벌어진 폭이 **테마로 설명되지 않는 페르미 고유의 몫**이다.\n\n"
                    f"바스켓은 {', '.join(stats.get('members', []))} 동일가중이다. 시총가중으로 하면 "
                    "CoreWeave 하나가 지수를 지배해 '테마'가 아니라 'CoreWeave 대비'가 된다."))
        st.plotly_chart(compare_line(theme_view, "date",
                                     [("페르미", "페르미"), ("바스켓", "AI 인프라 바스켓")]),
                        use_container_width=True)

    peers = mflow.peer_correlations(basket)
    if not peers.empty:
        left, right = st.columns([0.55, 0.45])
        with left:
            heading("구성종목별 상관")
            st.plotly_chart(bar(peers, "종목", "상관", unit="", digits=3, horizontal=True,
                                height=240), use_container_width=True)
        with right:
            heading("표")
            table(peers)

    st.divider()
    heading("축 B — 종목 수급")
    if not shorts.empty:
        latest = shorts.iloc[-1]
        shares_out = m.get("shares_out")
        metric_row([
            ("공매도 잔고", fd.num(latest["shares"] / 1e6, 1, "백만주"),
             f"결제일 {latest['date'].date()} · 격주 공시"),
            ("발행주식 대비", fd.pct(latest["shares"] / shares_out) if shares_out else "–", ""),
            ("Days to cover", fd.num(latest["days_to_cover"], 2, "일"), "평균 거래량 기준"),
            ("기관 보유", mflow.ownership().get("SharesOutstandingPCT", "–"), "13F · 분기 공시"),
        ])
        chart = shorts.copy()
        chart["결제일"] = chart["date"].dt.strftime("%y-%m-%d")
        chart["잔고(백만주)"] = chart["shares"] / 1e6
        st.plotly_chart(bar(chart, "결제일", "잔고(백만주)", unit="백만주", digits=1),
                        use_container_width=True)

        hedge = mflow.convertible_hedge(shorts, m.get("conv_shares"))
        if hedge:
            st.info(f"**공매도 급증분은 하락 베팅이 아닐 수 있다** — "
                    f"{hedge['increase']/1e6:,.1f}백만주 증가 = 전환주식수의 "
                    f"{hedge['coverage']*100:,.0f}%", icon="🔁")
            note(
                f"{hedge['before_date'].date()} {hedge['before']/1e6:,.1f}백만주 → "
                f"{hedge['after_date'].date()} {hedge['after']/1e6:,.1f}백만주로 "
                f"{hedge['increase']/1e6:,.1f}백만주 늘었는데, 그 사이 {hedge['issue_date'].date()}에 "
                f"전환사채가 발행됐다. 전환 가능 주식수 {hedge['conv_shares']/1e6:,.1f}백만주의 "
                f"**{hedge['coverage']*100:,.0f}%**에 해당한다.\n\n"
                "전환사채를 산 쪽은 보통 주식을 빌려 팔아 델타를 중립으로 맞춘다. 초기 헤지는 통상 "
                "전환주식수의 30~60% 선이라 이 증가분은 헤지로 설명되는 범위 안에 있다. "
                "하락 베팅과 섞어 읽으면 수급을 정반대로 해석하게 된다.", label="자세히")
        table(shorts.assign(결제일=shorts["date"].dt.date).rename(columns={
            "shares": "공매도 잔고", "avg_volume": "평균 거래량", "days_to_cover": "Days to cover"})
            [["결제일", "공매도 잔고", "평균 거래량", "Days to cover"]].iloc[::-1].round(2))

    insiders = mflow.insider_activity()
    if not insiders.empty:
        heading("내부자 거래", help_text=(
            "건수만 집계된 값이다. 금액과 개별 내역은 **참고 지표 → 지배구조**의 SEC 공시 목록에서 "
            "Form 4를 직접 열어 확인한다."))
        table(insiders)

# ── ⑥ 뉴스·소문 ───────────────────────────────────────────────────────────────
with tabs[6]:
    heading("계약 숫자를 바꿀 소식이 떴는가", size="####", help_text=(
        "**뉴스는 펀더멘탈이 아니다.** 목적은 하나 — 핵심 판정 ①(계약 커버리지 "
        f"{fd.pct(m.get('contracted_vs_landed'))})을 바꿀 소식을 먼저 알아채는 것.\n\n"
        "**확정은 SEC 공시로만 한다.** 계약 MW는 8-K를 근거로만 갱신된다. 서버가 30분마다 새 "
        "8-K를 AI로 판독해 `공시 판독`에 남기지만, `data/contracts.csv`를 자동으로 고치지는 "
        "않는다 — 서버에서 파일을 고치면 배포가 쓰는 `git pull --ff-only`가 깨진다. 반영은 "
        "사람이 저장소에 커밋한다.\n\n"
        "이 탭은 서버 크론이 30분마다 채워 둔 파일만 읽는다. 화면에서 직접 받으면 Nasdaq 응답이 "
        "5초 가까이 걸리는데, Streamlit은 어느 탭을 보든 모든 탭 코드를 실행해서 뉴스 탭을 안 보는 "
        "사람까지 그 시간을 물게 된다."))
    st.caption(fresh.tab_line("news", m, price_frame))

    articles, chatter = nw.cached_articles(), nw.cached_community()
    age = nw.cache_age()
    if age is not None:
        minutes = int(age.total_seconds() // 60)
        if minutes > 90:
            st.warning(f"마지막 수집 {minutes}분 전 — 수집 크론이 멈췄을 수 있다.", icon="⏳")
        else:
            st.caption(f"🕒 마지막 수집 {minutes}분 전 · 30분마다 갱신")

    if articles.empty:
        st.warning("수집된 뉴스가 없다. 서버에서 `python refresh_news.py`를 한 번 실행하면 된다.",
                   icon="⚠️")
    else:
        heading("🤖 AI 정리", help_text=(
            "**AI가 정리한 것이지 검증한 것이 아니다.** 기사에 없는 내용을 지어낼 수 있고, 커뮤니티 글은 "
            "애초에 검증되지 않은 개인 의견이다. 대시보드의 어떤 숫자도 이 정리를 근거로 바뀌지 않는다.\n\n"
            "새 기사가 뜨면 지문이 바뀌어 크론이 다시 분석한다. 뉴스가 그대로면 같은 결과를 재사용해 "
            "API를 다시 부르지 않는다."))
        review, review_error, review_key = ai_review.run(articles, chatter, m)
        if review:
            with st.container(border=True):
                st.markdown(review)
            st.caption(f"{ai_review.MODEL} · 기사 {min(len(articles), ai_review.MAX_ARTICLES)}건 + "
                       f"커뮤니티 {min(len(chatter), ai_review.MAX_POSTS)}건 · 지문 `{review_key}`")
        elif review_error:
            st.info(f"AI 정리 없음 — {review_error}", icon="🤖")

        st.divider()
        counts = articles["group"].value_counts()
        metric_row([
            ("전체 기사", f"{len(articles)}건", "Google·Yahoo·Nasdaq 합산, 중복 제거"),
            ("🎯 계약·테넌트", f"{counts.get('계약·테넌트', 0)}건", "핵심 판정을 바꿀 수 있는 소식"),
            ("💰 자금조달", f"{counts.get('자금조달', 0)}건", "증자·사채·차입"),
            ("⚠️ 일정·리스크", f"{counts.get('일정·리스크', 0)}건", "지연·취소·소송·공매도 리포트"),
        ])

        heading("🎯 계약·테넌트 관련")
        hits = nw.contract_hits(articles)
        if hits.empty:
            st.caption("최근 계약·테넌트 관련 기사 없음")
        else:
            feed = hits.head(25).copy()
            feed["날짜"] = feed["published"].dt.tz_convert("Asia/Seoul").dt.strftime("%m-%d %H:%M")
            table(feed.rename(columns={"title": "제목", "source": "매체", "url": "링크"})
                  [["날짜", "제목", "매체", "링크"]],
                  column_config={"링크": st.column_config.LinkColumn("링크", display_text="열기")},
                  height=300)

        heading("전체 기사")
        picked_group = st.selectbox("분류", ["전체"] + list(counts.index), index=0)
        feed = articles if picked_group == "전체" else articles[articles["group"] == picked_group]
        feed = feed.head(80).copy()
        feed["날짜"] = feed["published"].dt.tz_convert("Asia/Seoul").dt.strftime("%m-%d %H:%M")
        feed["분류"] = feed["group"].map(lambda g: f"{nw.GROUP_ICON.get(g, '·')} {g}")
        table(feed.rename(columns={"title": "제목", "source": "매체", "url": "링크"})
              [["날짜", "분류", "제목", "매체", "링크"]],
              column_config={"링크": st.column_config.LinkColumn("링크", display_text="열기")},
              height=400)

    heading("커뮤니티 (Stocktwits)", help_text=(
        "**검증되지 않은 개인 게시글이다.** 추측·루머·의도적 허위가 섞일 수 있고, 여기 적힌 수치는 "
        "어떤 것도 확인된 사실이 아니다. 대시보드의 어떤 숫자도 이 글들을 근거로 바뀌지 않는다."))
    if chatter.empty:
        st.caption("커뮤니티 글을 받지 못했다 — Stocktwits가 서버 IP를 봇 차단(Cloudflare)으로 막고 있다. "
                   "차단 우회는 하지 않는다.")
    else:
        view = chatter.head(30).copy()
        view["날짜"] = view["published"].dt.tz_convert("Asia/Seoul").dt.strftime("%m-%d %H:%M")
        view["분류"] = view["group"].map(lambda g: f"{nw.GROUP_ICON.get(g, '·')} {g}")
        table(view.rename(columns={"body": "내용", "user": "작성자", "url": "링크"})
              [["날짜", "분류", "내용", "작성자", "링크"]],
              column_config={"링크": st.column_config.LinkColumn("링크", display_text="열기")},
              height=320)

# ── ⑦ 애널리스트 ──────────────────────────────────────────────────────────────
with tabs[7]:
    heading("애널리스트는 이 회사를 무엇으로 보고 있는가", size="####", help_text=(
        "**증권사 리포트 원문은 유료다.** 공개된 세 갈래로 같은 내용을 재구성한다 — Nasdaq "
        "컨센서스 API(목표주가·의견 분포), Nasdaq 실적 추정 API(EPS 컨센서스), 그리고 뉴스 "
        "제목에 실리는 개별 액션(\"Mizuho cuts price target to $8 on tenant lease delay\").\n\n"
        "**애널리스트 의견은 펀더멘탈이 아니다.** 목표주가는 예측이지 사실이 아니고, 이 대시보드의 "
        "어떤 판정도 여기 숫자로 바뀌지 않는다. 그런데도 보는 이유는 **인하 사유가 무엇인지**가 "
        "핵심 판정 ①과 같은 축인지 확인하기 위해서다."))
    st.caption(fresh.tab_line("analyst", m, price_frame))

    consensus = an.consensus()
    if consensus.get("error"):
        st.warning(f"컨센서스를 받지 못했다 — {consensus['error']}", icon="⚠️")
    overview = consensus.get("overview") or {}
    if overview:
        target = overview.get("priceTarget")
        upside = (target / m["price"] - 1) * 100 if target and m.get("price") else None
        metric_row([
            ("컨센서스 목표주가", f"${target:,.2f}" if target else "–",
             "애널리스트 평균 예측 — 사실이 아니다"),
            ("현재가 대비", f"{upside:+.1f}%" if upside is not None else "–",
             f"현재 ${m['price']:,.2f}" if m.get("price") else ""),
            ("목표가 범위",
             f"${overview.get('lowPriceTarget', 0):,.0f} – ${overview.get('highPriceTarget', 0):,.0f}",
             "저·고 격차가 클수록 전망이 갈린다는 뜻"),
            ("의견 분포",
             f"매수 {overview.get('buy', 0)} · 보유 {overview.get('hold', 0)} · "
             f"매도 {overview.get('sell', 0)}", "커버 중인 증권사"),
        ])

    trail = an.history_frame(consensus)
    if not trail.empty:
        peak = trail.loc[trail["목표주가"].idxmax()]
        low = trail.loc[trail["목표주가"].idxmin()]
        latest = trail.iloc[-1]
        heading("목표주가 추이", help_text=(
            "**이 그래프가 이 탭의 핵심이다.** 고점 대비 얼마나 내려왔는지, 그리고 최근 방향이 "
            "어느 쪽인지가 아래 개별 리포트의 인하·상향 사유와 이어진다.\n\n"
            "의견 수가 함께 줄면 커버리지를 접은 증권사가 있다는 뜻이다."))
        st.plotly_chart(compare_line(trail, "시점", [("목표주가", "컨센서스 목표주가")],
                                     unit="$", height=300), use_container_width=True)
        st.caption(
            f"고점 ${peak['목표주가']:,.2f}({peak['시점'].strftime('%Y-%m')}) → "
            f"저점 ${low['목표주가']:,.2f}({low['시점'].strftime('%Y-%m')}, "
            f"{low['목표주가']/peak['목표주가']-1:+.0%}) → "
            f"현재 ${latest['목표주가']:,.2f}({latest['목표주가']/peak['목표주가']-1:+.0%})"
        )
        view = trail.copy()
        view["시점"] = view["시점"].dt.strftime("%Y-%m")
        view["목표주가"] = view["목표주가"].map(lambda v: f"${v:,.2f}")
        table(view.rename(columns={"buy": "매수", "hold": "보유", "sell": "매도",
                                   "consensus": "컨센서스"})
              [["시점", "목표주가", "매수", "보유", "매도", "컨센서스"]], height=260)

    actions = an.combined(an.merged_actions(nw.cached_articles()))
    heading("개별 증권사 액션", help_text=(
        "**리포트 원문이 아니라 기사 제목에서 뽑은 것이다.** 증권사 이름이 제목에 없으면 버린다 — "
        "\"FRMI Stock Price Prediction 2026\" 같은 글이 애널리스트 액션으로 섞이는 걸 막는 "
        "가장 확실한 기준이다.\n\n"
        "`언급된 이유`는 제목의 \"on ~\"·\"following ~\" 뒤를 그대로 옮긴 것이다. 같은 증권사가 "
        "같은 날 낸 것은 한 리포트로 보고 합친다 — 매체마다 목표가만 쓰거나 사유만 쓰기 때문이다.\n\n"
        "구글 뉴스는 같은 질의라도 호출마다 다른 묶음을 준다. 그래서 결과를 덮지 않고 **누적**한다."))
    if actions.empty:
        st.caption("최근 애널리스트 액션을 찾지 못했다.")
    else:
        window = actions[pd.to_datetime(actions["시점"], errors="coerce")
                         >= pd.Timestamp.today() - pd.Timedelta(days=90)]
        cuts = int(window["행동"].isin(["목표가 인하", "하향"]).sum())
        ups = int(window["행동"].isin(["목표가 상향", "상향"]).sum())
        reasons = [r for r in window["언급된 이유"] if r != "–"]
        metric_row([
            ("최근 90일 액션", f"{len(window)}건", "그 기간 잡힌 개별 리포트"),
            ("인하·하향", f"{cuts}건", "목표가 인하 + 투자의견 하향"),
            ("상향", f"{ups}건", "목표가 상향 + 투자의견 상향"),
            ("최신", str(actions.iloc[0]["시점"]) if len(actions) else "–",
             f"{actions.iloc[0]['증권사']} {actions.iloc[0]['행동']}" if len(actions) else ""),
        ])
        if reasons:
            st.caption("최근 언급된 사유 — " + " · ".join(dict.fromkeys(reasons)))
        table(actions, column_config={
            "링크": st.column_config.LinkColumn("링크", display_text="열기"),
            "": st.column_config.TextColumn("", width="small")}, height=380)

    eps = consensus.get("eps") or []
    if eps:
        heading("EPS 컨센서스", help_text=(
            "추정 수가 1~2개면 소수 의견이다. `4주 상향`·`4주 하향`은 최근 한 달간 추정치를 올린·"
            "내린 애널리스트 수로, 방향이 바뀌는 시점이 여기서 먼저 보인다."))
        table(pd.DataFrame(eps))

    heading("🤖 AI 정리", help_text=(
        "**AI가 정리한 것이지 검증한 것이 아니다.** 위 컨센서스·추이·액션과 대시보드의 확정 수치를 "
        "함께 넣고, 애널리스트 전제가 공시된 사실과 어긋나는 곳을 짚게 했다. 투자 판단이나 "
        "목표주가는 쓰지 않도록 지시했다.\n\n"
        "자료가 바뀔 때만 다시 만든다. 크론이 미리 채워 두므로 화면에서 기다리지 않는다."))
    cached_review = an.cached_review()
    if cached_review.get("text"):
        with st.container(border=True):
            st.markdown(cached_review["text"])
        st.caption(f"지문 `{cached_review.get('fingerprint', '')}` · "
                   f"생성 {str(cached_review.get('generated_at', ''))[:16]}")
    else:
        st.info("AI 정리 대기 — 서버 크론이 다음 실행에서 만든다.", icon="🤖")


# ── 참고 지표 ─────────────────────────────────────────────────────────────────
with tabs[8]:
    heading("판정에서 내린 항목들", size="####", help_text=(
        "섹터 검증에서 생존과 붕괴를 가르지 못한 항목이다. 지우면 맥락을 잃으므로 참고로만 남긴다. "
        "각 항목을 펼치면 왜 내렸는지가 먼저 나온다."))
    st.caption(fresh.tab_line("reference", m, price_frame))
    for card in fd.reference_cards(m):
        with st.expander(f"{th.STATUS_ICON[card['status']]}  {card['axis']} — {card['headline']}"):
            st.caption(f"**왜 내렸나** — {card.get('demoted', '')}")

            if card["axis"].startswith("자금여력"):
                metric_row([
                    ("분기말 현금", fd.usd(m.get("cash_total")), f"출처: {m.get('cash_source', '–')}"),
                    ("조달 반영 현금", fd.usd(m.get("cash_proforma")), "분기 후 전환사채 순유입 반영"),
                    ("분기 영업소진", fd.usd(m.get("op_burn_q")), ""),
                    ("분기 설비투자", fd.usd(m.get("capex_q")), ""),
                ])
                metric_row([
                    ("총소진 런웨이", fd.num(m.get("runway_total"), 1, "개월"), "영업소진 + capex 기준"),
                    ("운영만 런웨이", fd.num(m.get("runway_ops"), 1, "개월"), "capex 전면 중단 가정"),
                    ("분기 총소진", fd.usd(m.get("burn_q_total")), ""),
                    ("누적 설비투자", fd.usd(m.get("capex_cumulative")), ""),
                ])
                burn = m["capex_series"][["label", "end", "val"]].rename(columns={"val": "설비투자"})
                ops = m["op_cf_series"][["end", "val"]].copy()
                ops["영업소진"] = ops["val"].abs()
                burn = burn.merge(ops[["end", "영업소진"]], on="end", how="left").fillna({"영업소진": 0})
                burn["설비투자"] /= 1e6
                burn["영업소진"] /= 1e6
                st.plotly_chart(grouped_bar(burn, "label",
                                            [("설비투자", "설비투자"), ("영업소진", "영업소진")],
                                            stacked=True), use_container_width=True)

            elif card["axis"].startswith("자본투입"):
                metric_row([
                    ("PP&E 총액", fd.usd(m.get("ppe_gross")), ""),
                    ("누적 현금 설비투자", fd.usd(m.get("capex_cumulative")), ""),
                    ("비현금 증가분", fd.usd(m.get("noncash_additions")),
                     "미지급 capex · 자본화 이자 · 자본화 주식보상"),
                    ("반입 MW당 투입자본", fd.usd(m.get("capex_per_landed_mw")), ""),
                ])
                ppe = m["ppe_series"].copy()
                ppe["금액"] = ppe["val"] / 1e6
                st.plotly_chart(bar(ppe, "label", "금액"), use_container_width=True)

            elif card["axis"].startswith("자본구조"):
                metric_row([
                    ("발행주식수", fd.num(m.get("shares_out"), 0, "주"), ""),
                    ("주식보상 ÷ 판관비", fd.pct(m.get("sbc_ratio")), ""),
                    ("총차입금(프로포마)", fd.usd(m.get("debt_proforma")), "분기말 + 분기 후 전환사채"),
                    ("순차입금(프로포마)", fd.usd(m.get("net_debt_proforma")), ""),
                ])
                shares = m["shares_series"].copy()
                shares["기준일"] = pd.to_datetime(shares["end"]).dt.strftime("%y-%m-%d")
                shares["주식수"] = shares["val"] / 1e6
                st.plotly_chart(bar(shares, "기준일", "주식수", unit="백만주", digits=1),
                                use_container_width=True)
                if not m["capital_events"].empty:
                    table(m["capital_events"].rename(columns={
                        "period": "시기", "date": "일자", "instrument": "수단", "gross_musd": "총액(백만$)",
                        "net_musd": "순유입(백만$)", "dilutive": "희석", "terms": "조건", "source": "출처"}))

            elif card["axis"].startswith("지배구조"):
                feed = m["filings"].head(50).copy()
                if not feed.empty:
                    feed["접수일"] = feed["filed"].dt.date
                    table(feed.rename(columns={"form": "종류", "group": "구분", "title": "제목",
                                               "url": "링크"})
                          [["접수일", "종류", "구분", "제목", "링크"]],
                          column_config={"링크": st.column_config.LinkColumn("링크", display_text="열기")},
                          height=320)

            elif card["axis"].startswith("시장"):
                metric_row([
                    ("시가총액", fd.usd(m.get("market_cap")), ""),
                    ("EV(프로포마)", fd.usd(m.get("ev")), "시가총액 + 순차입금"),
                    ("EV ÷ 계약 MW", fd.usd(m.get("ev_per_contracted_mw")), ""),
                    ("EV ÷ 목표 MW", fd.usd(m.get("ev_per_target_mw")), ""),
                ])
                if not price_frame.empty:
                    st.plotly_chart(line(price_frame, "date", "close", "종가", unit="USD",
                                         height=300), use_container_width=True)

# ── 원본 데이터 ───────────────────────────────────────────────────────────────
with tabs[9]:
    heading("데이터 출처", size="####", help_text=(
        "EDGAR는 현금흐름 항목을 회계연도 기초부터 누적해서 담는다. 그대로 쓰면 4분기 막대가 연간값이 "
        "되므로 `sec_edgar.periodic_series()`가 누적을 분기 구간으로 되돌린 뒤 화면에 올린다."))
    st.caption(fresh.tab_line("raw", m, price_frame))
    st.markdown(
        f"""
| 계층 | 출처 | 갱신 |
|---|---|---|
| 페르미 재무제표 | SEC EDGAR XBRL (CIK {int(sec.CIK)}) | 10-Q/10-K 제출 시 자동 |
| 최신 분기 발표치 | `data/latest_reported.csv` | 수동 |
| 전력 용량·계약·마일스톤 | `data/power_stages.csv` 외 | 수동 |
| 섹터 표본 재무·시세 | `data/sector_*.csv` | `python refresh_sector.py` |
| 뉴스·AI 정리·공시 판독 | `data/.cache/` | 크론 30분 |
| 수급·바스켓·섹터 시총 | `data/.cache/` | 크론 하루 2회 (원본이 일봉·격주·분기) |
| 공시 피드 | SEC EDGAR `submissions` | 30분 캐시 |
| 시세 | Yahoo Finance | 5분 캐시 |
"""
    )

    heading("주요 XBRL 태그 분기 시계열")
    tag_options = {
        "영업활동 현금흐름": fd.TAG_OP_CF, "설비투자(PP&E 취득)": fd.TAG_CAPEX,
        "재무활동 현금흐름": fd.TAG_FIN_CF, "순손익": fd.TAG_NET_LOSS,
        "판매관리비": fd.TAG_GNA, "주식보상비용": fd.TAG_SBC, "자본화 이자": fd.TAG_CAP_INTEREST,
    }
    picked = st.selectbox("기간 항목", list(tag_options), index=0)
    series = sec.periodic_series(facts, tag_options[picked])
    if series.empty:
        st.write("데이터 없음")
    else:
        view = series.copy()
        view["시작"], view["종료"] = view["start"].dt.date, view["end"].dt.date
        view["금액(백만$)"] = (view["val"] / 1e6).round(2)
        view["구간(일)"] = view["days"]
        view["산출"] = view["derived"].map({True: "누적 차분", False: "직접 보고"})
        table(view[["시작", "종료", "구간(일)", "금액(백만$)", "산출"]])

    heading("재무상태표 주요 항목")
    instant_options = {
        "총자산": fd.TAG_ASSETS, "총부채": fd.TAG_LIABILITIES, "자기자본": fd.TAG_EQUITY,
        "PP&E 총액": fd.TAG_PPE_GROSS, "PP&E 순액": fd.TAG_PPE_NET, "현금+제한현금": fd.TAG_CASH_TOTAL,
    }
    picked_instant = st.selectbox("시점 항목", list(instant_options), index=0)
    instant = sec.instant_series(facts, instant_options[picked_instant])
    if instant.empty:
        st.write("데이터 없음")
    else:
        view = instant.copy()
        view["기준일"] = view["end"].dt.date
        view["금액(백만$)"] = (view["val"] / 1e6).round(2)
        table(view.rename(columns={"form": "출처"})[["기준일", "금액(백만$)", "출처"]])