"""페르미(FRMI) 펀더멘탈 대시보드.

화면 구조는 검증 결과를 그대로 따른다. 전력·에너지 인프라 13개사로 항목별 판별력을 검증했더니
처음 세웠던 7개 축 중 3개는 생존/붕괴를 가르지 못했다(sector.py, data/axis_validation.csv).

  핵심 — 계약 커버리지 · 현금흐름 전환   ← 검증을 통과했다. 맨 위에 크게 둔다.
  참고 — 런웨이 · 이자 자본화 · 희석 · 지배구조 · 밸류에이션  ← 판정에서 내리고 접어 둔다.
"""

import pandas as pd
import plotly.graph_objects as go
import streamlit as st

import fundamentals as fd
import market
import market_flow as mflow
import sec_edgar as sec
import sector as sc
import theme as th

st.set_page_config(page_title="페르미(FRMI) 펀더멘탈", page_icon="⚡", layout="wide")

st.markdown(
    """
    <style>
      div[data-testid="stMetricValue"] { font-size: 1.4rem; }
      div[data-testid="stMetricLabel"] { color: #52514e; }
      section.main > div { padding-top: 1rem; }
    </style>
    """,
    unsafe_allow_html=True,
)


# ── 렌더 헬퍼 ─────────────────────────────────────────────────────────────────
def bar(frame, x, y, color, unit="M$", digits=0, height=300, horizontal=False, colors=None):
    """막대 하나짜리 차트. 팔레트 대비가 3:1 미만인 색이 있어 항상 값 레이블을 붙인다."""
    figure = go.Figure()
    marker = dict(color=colors if colors is not None else color, line=dict(color=th.SURFACE, width=2))
    text = [f"{value:,.{digits}f}" for value in frame[y]]
    if horizontal:
        figure.add_bar(y=frame[x], x=frame[y], orientation="h", marker=marker, text=text,
                       textposition="outside", cliponaxis=False,
                       hovertemplate="%{y}<br>%{x:,.1f} " + unit + "<extra></extra>")
        figure.update_yaxes(autorange="reversed")
        th.style(figure, height=height, ygrid=False)
        figure.update_xaxes(showgrid=True, gridcolor=th.GRID)
    else:
        figure.add_bar(x=frame[x], y=frame[y], marker=marker, text=text,
                       textposition="outside", cliponaxis=False,
                       hovertemplate="%{x}<br>%{y:,.1f} " + unit + "<extra></extra>")
        th.style(figure, height=height)
    figure.update_traces(marker_cornerradius=4, textfont=dict(color=th.INK_SECONDARY, size=11))
    return figure


def grouped_bar(frame, x, series, unit="M$", height=320, stacked=False):
    """같은 단위의 계열 2~3개. 이중 축은 쓰지 않는다."""
    figure = go.Figure()
    for index, (column, name) in enumerate(series):
        labels = None if stacked else [f"{value:,.0f}" for value in frame[column]]
        figure.add_bar(x=frame[x], y=frame[column], name=name,
                       marker=dict(color=th.SERIES[index], line=dict(color=th.SURFACE, width=2)),
                       text=labels, textposition="outside", cliponaxis=False,
                       hovertemplate="%{x} · " + name + "<br>%{y:,.0f} " + unit + "<extra></extra>")
    th.style(figure, height=height, legend=True)
    figure.update_layout(barmode="stack" if stacked else "group")
    figure.update_traces(marker_cornerradius=4, textfont=dict(color=th.INK_SECONDARY, size=11))
    return figure


def line(frame, x, y, name, unit="", height=300):
    figure = go.Figure()
    figure.add_scatter(x=frame[x], y=frame[y], mode="lines", name=name,
                       line=dict(color=th.SERIES[0], width=2),
                       hovertemplate="%{x|%Y-%m-%d}<br>%{y:,.2f} " + unit + "<extra></extra>")
    th.style(figure, height=height)
    figure.update_layout(hovermode="x unified")
    return figure


def compare_line(frame, x, series, unit="", height=320):
    """같은 축의 계열 2개를 겹쳐 그린다. 이중 축은 쓰지 않는다."""
    figure = go.Figure()
    for index, (column, name) in enumerate(series):
        figure.add_scatter(x=frame[x], y=frame[column], mode="lines", name=name,
                           line=dict(color=th.SERIES[index], width=2.5 if index == 0 else 2,
                                     dash="solid" if index == 0 else "dot"),
                           hovertemplate="%{x|%Y-%m-%d} · " + name + "<br>%{y:,.1f} " + unit
                                         + "<extra></extra>")
    th.style(figure, height=height, legend=True)
    figure.update_layout(hovermode="x unified")
    return figure


def table(frame, **kwargs):
    """대비가 낮은 팔레트를 쓰는 만큼 차트 옆에는 같은 값을 읽을 수 있는 표를 둔다."""
    st.dataframe(frame, use_container_width=True, hide_index=True, **kwargs)


def metric_row(items):
    for column, (label, value, help_text) in zip(st.columns(len(items)), items):
        column.metric(label, value, help=help_text or None)


# ── 데이터 ────────────────────────────────────────────────────────────────────
with st.spinner("SEC EDGAR · 시세 불러오는 중..."):
    try:
        facts = sec.load_company_facts()
    except Exception as error:
        st.error(f"SEC EDGAR 접속 실패: {error}\n\n"
                 "SEC는 연락처가 담긴 User-Agent를 요구한다. 환경변수 SEC_USER_AGENT를 확인하세요.")
        st.stop()
    price_frame, price_meta = market.load_price(sec.TICKER)
    m = fd.compute(facts, price_meta)

if sec.USER_AGENT_IS_PLACEHOLDER:
    st.warning(
        "환경변수 `SEC_USER_AGENT`가 설정되지 않아 자리표시자를 쓰고 있습니다. SEC는 연락 가능한 "
        "이메일이 담긴 User-Agent를 요구하며, 자리표시자로는 곧 차단될 수 있습니다. `.env`를 확인하세요.",
        icon="⚠️",
    )

profile = sec.company_profile()

# ── 헤더 ──────────────────────────────────────────────────────────────────────
head_left, head_right = st.columns([0.58, 0.42])
with head_left:
    st.title("⚡ 페르미 펀더멘탈 점검")
    st.caption(f"{profile.get('name') or sec.COMPANY_KO} · {sec.EXCHANGE}: {sec.TICKER} · "
               f"{profile.get('sic', '')} · CIK {int(sec.CIK)}")
with head_right:
    metric_row([
        ("주가", f"${m['price']:,.2f}" if m.get("price") else "–", "Yahoo Finance 지연 시세"),
        ("시가총액", fd.usd(m.get("market_cap")), "주가 × 최근 보고 발행주식수"),
        ("최신 재무 기준일", str(pd.Timestamp(m["asof"]).date()) if m.get("asof") is not None else "–",
         f"출처: {m.get('asof_source', '–')}"),
    ])

# ── 수동 데이터 노후화 경보 ───────────────────────────────────────────────────
# 핵심 판정 ①(계약 커버리지)의 분자·분모가 전부 수동 CSV라, 새 공시가 올라와도 화면은 옛 숫자를
# 그대로 보여준다. 공시 피드는 30분마다 자동으로 받고 있으니 CSV 근거일과 비교해 먼저 알린다.
stale = fd.staleness(m)
if stale["count"] > 0:
    with st.container(border=True):
        st.warning(
            f"**수동 데이터가 낡았을 수 있습니다.** 마지막 갱신 근거일"
            f"({pd.Timestamp(stale['asof']).date()}) 이후 새 공시 {stale['count']}건이 올라왔습니다. "
            "계약 MW·전력 용량·자금조달 수치는 자동 갱신되지 않습니다.",
            icon="🔔",
        )
        feed = stale["filings"].copy()
        feed["접수일"] = feed["filed"].dt.date
        table(
            feed.rename(columns={"form": "종류", "group": "구분", "title": "제목", "url": "링크"})
            [["접수일", "종류", "구분", "제목", "링크"]],
            column_config={"링크": st.column_config.LinkColumn("링크", display_text="열기")},
        )
        st.caption(
            "`data/`의 CSV를 고치면 재빌드 없이 반영됩니다. 읽어보고 바꿀 것이 없다면 "
            "`data/review_log.csv`에 검토 완료 지점을 남기면 이 배너가 꺼집니다."
        )
else:
    reviews = fd.load_csv("review_log.csv")
    last_note = reviews.iloc[-1] if not reviews.empty else None
    st.caption(
        f"✅ 수동 데이터는 {pd.Timestamp(stale['asof']).date()}까지 반영됨"
        + (f" · 마지막 검토: {last_note['verdict']} — {last_note['note']}" if last_note is not None else "")
    )

# ── 핵심 판정 ─────────────────────────────────────────────────────────────────
st.subheader("핵심 판정")
st.caption(
    "전력·에너지 인프라 13개사로 검증했을 때 생존과 붕괴를 실제로 가른 지표만 남겼다. "
    "판별력이 없던 항목은 아래 **참고 지표** 탭으로 내렸다. 정보 정리 목적의 도구이며 투자 권유가 아니다."
)
for column, item in zip(st.columns(3), sc.fermi_position(m)):
    with column.container(border=True):
        st.markdown(f"**{item['label']}**")
        st.markdown(f"### {item['value']}")
        st.markdown(f"{th.STATUS_ICON[item['status']]} **{item['verdict']}**")
        if item["detail"]:
            st.caption(item["detail"])

tabs = st.tabs(["① 계약 커버리지", "② 현금흐름 전환", "③ 섹터 검증", "④ 시장·수급",
                "참고 지표", "원본 데이터"])

# ── ① 계약 커버리지 ───────────────────────────────────────────────────────────
with tabs[0]:
    st.markdown("#### 지을 용량을 사 줄 고객이 계약돼 있는가")
    st.caption(
        "검증에서 가장 깨끗하게 갈린 지표다. 유지 그룹 6곳은 전부 장기 take-or-pay로 부채 만기를 덮었고, "
        "붕괴 4곳은 계약이 없거나(Tellurian) 만기가 어긋났다(New Fortress)."
    )
    metric_row([
        ("구속력 있는 계약", fd.num(m.get("mw_contracted"), 0, " MW"), "서명 완료된 리스 기준"),
        ("반입 설비 대비", fd.pct(m.get("contracted_vs_landed")), "확보한 설비 중 팔린 비중"),
        ("장기 목표 대비", fd.pct(m.get("contracted_vs_target")), "17GW 목표 대비 실체"),
        ("고객 수", fd.num(m.get("customer_count"), 0, "개사"), "집중도가 100%면 단일 계약 리스크 노출"),
    ])
    metric_row([
        ("계약 총액", fd.usd((m.get("backlog_musd") or 0) * 1e6), "계약 기간 전체 합산 예상 매출"),
        ("옵션 포함 최대", fd.num(m.get("mw_contracted_option"), 0, " MW"), "확장옵션 전량 행사 가정"),
        ("MW·년당 단가", fd.usd(m.get("revenue_per_mw_year")), "계약 총액 ÷ 계약 MW ÷ 계약 연수"),
        ("계약 총액 ÷ 누적 투입", fd.num(
            (m.get("backlog_musd") or 0) * 1e6 / m["ppe_gross"] if m.get("ppe_gross") else None, 2, "배"),
         "쌓은 자산이 계약으로 얼마나 회수되는지"),
    ])

    left, right = st.columns(2)
    with left:
        st.markdown("**섹터 대비 계약 커버리지** (%)")
        summary = sc.summary()
        bench = summary.dropna(subset=["coverage"])[["company", "group", "coverage"]].copy()
        coverage_now = (m.get("mw_contracted") or 0) / (m.get("mw_landed") or 1) * 100
        bench = pd.concat([bench, pd.DataFrame([
            {"company": "Fermi ← 현재", "group": "대상", "coverage": coverage_now}])])
        bench["라벨"] = bench["company"] + " (" + bench["group"].astype(str) + ")"
        bench = bench.sort_values("coverage", ascending=False)
        st.plotly_chart(bar(bench, "라벨", "coverage", th.SERIES[0], unit="%", digits=0,
                            horizontal=True, height=300), use_container_width=True)
        table(bench[["라벨", "coverage"]].rename(columns={"라벨": "기업", "coverage": "커버리지(%)"}).round(0))
    with right:
        st.markdown("**반입 설비의 계약 충족도** (MW)")
        landed = m.get("mw_landed") or 0
        contracted = m.get("mw_contracted") or 0
        gap = pd.DataFrame({"구분": ["계약 완료", "미계약"],
                            "MW": [contracted, max(landed - contracted, 0)]})
        st.plotly_chart(bar(gap, "구분", "MW", th.SERIES[0], unit="MW", horizontal=True, height=180,
                            colors=[th.SERIES[0], th.ORDINAL_BLUE[0]]), use_container_width=True)
        st.markdown("**확정도별 전력 용량** (MW · 진할수록 확정)")
        stages = m["power_stages"].sort_values("order")
        st.plotly_chart(bar(stages, "stage", "mw", th.SERIES[0], unit="MW", horizontal=True, height=300,
                            colors=list(reversed(th.ordinal_colors(len(stages))))),
                        use_container_width=True)

    st.markdown("**계약 내역**")
    if not m["contracts"].empty:
        table(m["contracts"].rename(columns={
            "customer": "고객", "signed": "체결일", "binding": "구속력", "phase1_mw": "1단계 MW",
            "option_mw": "옵션 포함 MW", "total_revenue_musd": "계약총액(백만$)", "term_years": "기간(년)",
            "extension": "연장옵션", "delivery_start": "인도 개시", "source": "출처", "note": "비고"}))
    st.markdown("**확정도별 단계 원본**")
    table(stages.rename(columns={"order": "순서", "stage": "단계", "mw": "MW", "certainty": "확정도",
                                 "as_of": "기준일", "source": "출처", "note": "비고"}))

# ── ② 현금흐름 전환 ───────────────────────────────────────────────────────────
with tabs[1]:
    st.markdown("#### 가동 후 현금이 실제로 들어오기 시작하는가")
    st.caption(
        "유지 그룹 6곳은 전부 영업현금흐름 흑자에 도달했고, 붕괴 4곳은 도달하지 못했거나"
        "(Plug Power·FuelCell) 도달한 뒤 되돌아갔다(New Fortress). 매출의 유무가 아니라 "
        "**매출이 현금을 만드는가**가 갈랐다 — Core Scientific은 매출 $640M을 내면서 파산했다."
    )
    metric_row([
        ("현재 매출", "$0", "pre-revenue 개발단계"),
        ("분기 영업현금흐름", fd.usd(-(m.get("op_burn_q") or 0)), "최근 분기"),
        ("T0 이후 경과", "1년차", "대규모 자본 투입 시작(2025) 기준"),
        ("첫 상업 전력 목표", "약 200MW", "회사 발표 기준 6개월 내"),
    ])

    left, right = st.columns(2)
    with left:
        st.markdown("**페르미 분기 영업현금흐름** (백만 달러)")
        ops = m["op_cf_series"].copy()
        ops["금액"] = ops["val"] / 1e6
        st.plotly_chart(bar(ops, "label", "금액", th.SERIES[0], digits=1), use_container_width=True)
        st.caption("아직 전 분기 적자다. 이 곡선이 0을 넘어 유지되는 시점이 판별 지점이다.")
    with right:
        st.markdown("**섹터: T0에서 흑자 전환까지 걸린 연수**")
        summary = sc.summary()
        turn = summary.dropna(subset=["years_to_turn"])[["company", "group", "years_to_turn"]].copy()
        turn["라벨"] = turn["company"] + " (" + turn["group"].astype(str) + ")"
        turn = turn.sort_values("years_to_turn")
        if not turn.empty:
            st.plotly_chart(bar(turn, "라벨", "years_to_turn", th.SERIES[0], unit="년", digits=0,
                                horizontal=True, height=280), use_container_width=True)
        st.caption("붕괴 그룹은 이 막대가 아예 없다 — 전환에 도달하지 못했기 때문이다. "
                   "페르미는 T0+1년차라 아직 이 표에 오를 수 없다.")

    st.markdown("**마일스톤 이행 현황**")
    milestones = m["milestones"].copy()
    if not milestones.empty:
        milestones["date"] = milestones["date"].dt.date
        table(milestones.rename(columns={"date": "일자", "category": "구분", "milestone": "내용",
                                         "status": "상태", "source": "출처"}))

# ── ③ 섹터 검증 ───────────────────────────────────────────────────────────────
with tabs[2]:
    st.markdown("#### 이 항목들이 실제로 결과를 갈랐는가")
    st.caption(
        "매출보다 먼저 대규모 인프라를 지은 상장사 13곳. 결과는 주가가 아니라 객관적 재무 사건으로 "
        "나눴다(주가로 나누면 순환논증이 된다). 그런 다음 항목별 값을 두 그룹에 대조했다."
    )

    st.markdown("##### 항목별 검증 결과")
    axes = sc.load_axis_validation()
    if not axes.empty:
        table(axes.rename(columns={"label": "항목", "verdict": "판정", "maintained_value": "유지 그룹",
                                   "collapsed_value": "붕괴 그룹", "evidence": "근거", "keep": "처리"})
              [["항목", "판정", "유지 그룹", "붕괴 그룹", "처리", "근거"]])
    st.warning(
        "**처음 세웠던 7개 축 중 3개는 판별력이 없었다.** 런웨이는 유지 9.0개월 vs 붕괴 7.2개월로 "
        "사실상 차이가 없었고(Cheniere는 2.8개월에서 생존), 이자 자본화는 프로젝트 규모의 반영일 뿐이었다. "
        "그 항목들은 참고 지표로 내렸다.",
        icon="⚠️",
    )

    st.markdown("##### 표본 13개사")
    summary = sc.summary()
    view = summary.copy()
    view["그룹"] = view["group"].astype(str)
    # 고점 대비 수익률은 뺐다. 기업마다 고점 시점이 달라 펀더멘탈이 아니라 '고점이 언제였나'를
    # 재는 숫자가 된다. 같은 질문은 아래 T0 정렬 비교가 제대로 답한다.
    table(view.rename(columns={
        "ticker": "티커", "company": "기업", "sub": "세부업종", "coverage": "계약 커버리지(%)",
        "t0": "T0", "opcf_turn": "흑자 전환", "years_to_turn": "T0→흑자(년)",
        "outcome_basis": "결과 근거", "what_broke": "무엇이 무너졌나",
    })[["티커", "기업", "세부업종", "그룹", "계약 커버리지(%)", "T0", "흑자 전환", "T0→흑자(년)",
        "결과 근거", "무엇이 무너졌나"]])

    st.markdown("##### 지금의 페르미와 같은 위치였던 해, 그 뒤 주가는")
    st.caption(
        f"페르미는 {sc.FERMI_T0}년에 대규모 자본 투입을 시작했고 지금은 **T0+{sc.FERMI_STAGE_OFFSET}년차**다. "
        "각 기업이 같은 연차에 있던 해를 찾아 그때부터 지금까지의 주가를 쟀다. 보유 기간이 제각각이라 "
        "총수익률만 보면 오래 보유한 쪽이 유리해 보이므로 **연환산**을 함께 둔다."
    )

    stage = sc.stage_matched()
    priced = stage.dropna(subset=["cagr_pct"]).copy()
    if not priced.empty:
        priced["라벨"] = priced["company"] + " (" + priced["group"].astype(str) + " · " \
                        + priced["matched_year"].astype(str) + "년)"
        st.plotly_chart(
            bar(priced.sort_values("cagr_pct", ascending=False), "라벨", "cagr_pct", th.SERIES[0],
                unit="%/년", digits=1, horizontal=True, height=320),
            use_container_width=True,
        )

    view = stage.copy()
    view["그룹"] = view["group"].astype(str)
    view["그해 매출"] = view["revenue_then"].map(lambda v: fd.usd(v) if pd.notna(v) else "0 / 미상")
    view["배수"] = view["multiple"].map(lambda v: f"{v:,.2f}배" if pd.notna(v) else "–")
    view["경과"] = view["years_held"].map(lambda v: f"{v:,.1f}년" if pd.notna(v) else "–")
    for column, source in [("그때 주가", "price_then"), ("현재 주가", "price_now")]:
        view[column] = view[source].map(lambda v: f"${v:,.2f}" if pd.notna(v) else "–")
    for column, source in [("총수익률(%)", "total_return_pct"), ("연환산(%)", "cagr_pct")]:
        view[column] = view[source].map(lambda v: f"{v:+,.1f}" if pd.notna(v) else "–")
    table(view.rename(columns={"ticker": "티커", "company": "기업", "t0": "T0",
                               "matched_year": "같은 위치였던 해", "gap_reason": "시세 공백 사유"})
          [["티커", "기업", "그룹", "T0", "같은 위치였던 해", "그해 매출", "그때 주가", "현재 주가",
            "배수", "경과", "총수익률(%)", "연환산(%)", "시세 공백 사유"]])

    if not priced.empty:
        st.markdown("**그룹별 연환산 수익률** (%/년)")
        table(priced.groupby("group", observed=True)["cagr_pct"]
              .agg(["count", "mean", "min", "max"]).round(1).reset_index()
              .rename(columns={"group": "그룹", "count": "기업 수", "mean": "평균",
                               "min": "최악", "max": "최선"}))

    st.warning(
        "**이 표를 그대로 페르미에 대입하면 안 되는 이유가 셋 있다.** "
        "① 시세를 구할 수 있는 기업이 그룹당 3곳뿐이다. "
        "② 유지 그룹의 기준 시점이 2023~2024년인 곳(Bloom·Applied Digital)은 AI 인프라 랠리 구간과 "
        "겹쳐 펀더멘탈만의 효과가 아니다. 12.7년에 걸친 Cheniere의 연 15.6%가 순수한 인프라 사이클에 더 가깝다. "
        "③ **유지 그룹 3곳은 그 시점에 이미 매출이 있었다.** 그때도 매출이 0이었던 곳은 NextDecade와 "
        "Oklo뿐이고 둘 다 아직 미결이다. 페르미와 진짜로 같은 위치였던 기업들은 아직 답을 내놓지 않았다.",
        icon="⚠️",
    )
    gaps = stage[stage["price_then"].isna()]
    if not gaps.empty:
        st.info(
            "**시세를 구하지 못한 4곳은, 구하지 못한 이유가 곧 결과다.** Tellurian은 상장폐지로 이력이 "
            "사라졌고, Core Scientific은 챕터11로 기존 주식이 소각·재발행됐다. 표에 -99%로 적히지 "
            "않았을 뿐 실질은 그보다 나쁘다. 사유는 위 표 마지막 열에 있다.",
            icon="🚫",
        )
        st.caption(
            "붕괴는 느리게 온다 — New Fortress는 6.7년, FuelCell은 15.7년에 걸쳐 내려갔다. "
            "지금의 페르미 위치에서 결론이 나기까지는 수년이 걸린다."
        )

    st.markdown("##### 연도별 원본 재무")
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
        st.caption("단위 백만 달러. SEC EDGAR XBRL — `python refresh_sector.py`로 갱신한다.")

# ── ④ 시장·수급 ───────────────────────────────────────────────────────────────
with tabs[3]:
    st.markdown("#### 주가가 왜 움직였는가")
    st.caption(
        "**이 탭은 펀더멘탈이 아니다.** 회사의 건강이 아니라 주가 움직임을 설명하는 층이다. "
        "상장 후 219거래일로 실측한 결과 페르미는 AI 인프라 동종주와 상관 0.39~0.45로 붙어 다녔고, "
        "금리(10년 실질 -0.13)와 천연가스(-0.05)는 예상보다 훨씬 약했다. 그래서 여기서는 "
        "**테마에서 오는 몫을 걷어내고 페르미 고유 움직임만** 남기는 데 집중한다."
    )

    with st.spinner("시장·수급 데이터 불러오는 중..."):
        basket = mflow.basket_frame()
        theme = mflow.theme_view(basket)
        stats = mflow.theme_stats(basket)
        shorts = mflow.short_interest()

    st.markdown("##### 축 A — 테마 동조도")
    if stats:
        metric_row([
            ("바스켓 상관 (전체)", fd.num(stats.get("corr_full"), 3),
             "일간 수익률 기준. 1에 가까울수록 테마와 같이 움직인다"),
            (f"바스켓 상관 (최근 {mflow.ROLLING_WINDOW}일)", fd.num(stats.get("corr_rolling"), 3),
             "최근 들어 동조도가 커졌는지 작아졌는지"),
            ("1개월 상대강도", fd.num(stats.get("rs_1개월"), 1, "%p"),
             f"페르미 {fd.num(stats.get('fermi_1개월'), 1, '%')} vs 바스켓 {fd.num(stats.get('basket_1개월'), 1, '%')}"),
            ("3개월 상대강도", fd.num(stats.get("rs_3개월"), 1, "%p"),
             f"페르미 {fd.num(stats.get('fermi_3개월'), 1, '%')} vs 바스켓 {fd.num(stats.get('basket_3개월'), 1, '%')}"),
        ])

    if not theme.empty:
        st.markdown("**상장일을 100으로 맞춘 추이**")
        st.plotly_chart(compare_line(theme, "date", [("페르미", "페르미"), ("바스켓", "AI 인프라 바스켓")]),
                        use_container_width=True)
        last = theme.iloc[-1]
        st.caption(
            f"상장 이후 페르미 **{last['페르미']:,.0f}** vs 바스켓 **{last['바스켓']:,.0f}** (기준 100). "
            "두 선이 벌어진 폭이 테마로 설명되지 않는 페르미 고유의 몫이다. "
            f"바스켓은 {', '.join(stats.get('members', []))} 동일가중이며, 시총가중으로 하면 "
            "CoreWeave 하나가 지수를 지배해 '테마'가 아니라 'CoreWeave 대비'가 된다."
        )
        table(theme.assign(날짜=theme["date"].dt.date).rename(
            columns={"페르미": "페르미(100기준)", "바스켓": "바스켓(100기준)"})
            [["날짜", "페르미(100기준)", "바스켓(100기준)"]].tail(10).round(1))

    peers = mflow.peer_correlations(basket)
    if not peers.empty:
        st.markdown("**구성종목별 상관**")
        left, right = st.columns([0.55, 0.45])
        with left:
            st.plotly_chart(bar(peers, "종목", "상관", th.SERIES[0], unit="", digits=3,
                                horizontal=True, height=240), use_container_width=True)
        with right:
            table(peers)

    st.divider()
    st.markdown("##### 축 B — 종목 수급")
    if not shorts.empty:
        latest = shorts.iloc[-1]
        shares_out = m.get("shares_out")
        metric_row([
            ("공매도 잔고", fd.num(latest["shares"] / 1e6, 1, "백만주"),
             f"결제일 {latest['date'].date()} · 격주 공시"),
            ("발행주식 대비", fd.pct(latest["shares"] / shares_out) if shares_out else "–",
             "발행주식수 대비 공매도 비율"),
            ("Days to cover", fd.num(latest["days_to_cover"], 2, "일"),
             "평균 거래량 기준 환매수에 걸리는 일수"),
            ("기관 보유", mflow.ownership().get("SharesOutstandingPCT", "–"),
             "13F 기준 · 분기 공시라 시차가 있다"),
        ])
        chart = shorts.copy()
        chart["결제일"] = chart["date"].dt.strftime("%y-%m-%d")
        chart["잔고(백만주)"] = chart["shares"] / 1e6
        st.plotly_chart(bar(chart, "결제일", "잔고(백만주)", th.SERIES[0], unit="백만주", digits=1),
                        use_container_width=True)

        hedge = mflow.convertible_hedge(shorts, m.get("conv_shares"))
        if hedge:
            st.info(
                f"**공매도 급증분은 하락 베팅이 아닐 수 있다.** "
                f"{hedge['before_date'].date()} {hedge['before']/1e6:,.1f}백만주 → "
                f"{hedge['after_date'].date()} {hedge['after']/1e6:,.1f}백만주로 "
                f"**{hedge['increase']/1e6:,.1f}백만주** 늘었는데, 그 사이 "
                f"{hedge['issue_date'].date()}에 전환사채가 발행됐다. 전환 가능 주식수 "
                f"{hedge['conv_shares']/1e6:,.1f}백만주의 **{hedge['coverage']*100:,.0f}%**에 해당한다.\n\n"
                "전환사채를 산 쪽은 보통 주식을 빌려 팔아 델타를 중립으로 맞춘다. 초기 헤지는 통상 "
                "전환주식수의 30~60% 선이라, 이 증가분은 헤지로 설명되는 범위 안에 있다. "
                "하락 베팅과 섞어 읽으면 수급을 정반대로 해석하게 된다.",
                icon="🔁",
            )
        table(shorts.assign(결제일=shorts["date"].dt.date).rename(columns={
            "shares": "공매도 잔고", "avg_volume": "평균 거래량", "days_to_cover": "Days to cover"})
            [["결제일", "공매도 잔고", "평균 거래량", "Days to cover"]].iloc[::-1].round(2))
    else:
        st.warning("공매도 잔고를 불러오지 못했다(Nasdaq API). 잠시 후 다시 시도하면 된다.", icon="⚠️")

    insiders = mflow.insider_activity()
    if not insiders.empty:
        st.markdown("**내부자 거래 요약**")
        table(insiders)
        st.caption(
            "건수만 집계된 값이다. 금액과 개별 내역은 **참고 지표 → 지배구조** 탭의 SEC 공시 "
            "목록에서 Form 4를 직접 열어 확인한다."
        )

    st.warning(
        "**이 지표들로 펀더멘탈을 판단하지 않는다.** 상관 0.46은 인과가 아니라 같은 테마에 실려 있다는 "
        "뜻일 뿐이고, 관측 구간도 219거래일에 불과하다. 쓰임새는 하나다 — 공시가 났을 때 주가가 "
        "움직인 것이 **그 공시 때문인지 그날 AI주가 다 움직인 것인지** 가르는 것.",
        icon="⚠️",
    )


# ── 참고 지표 ─────────────────────────────────────────────────────────────────
with tabs[4]:
    st.markdown("#### 판정에서 내린 항목들")
    st.caption(
        "섹터 검증에서 생존과 붕괴를 가르지 못한 항목이다. 지우면 맥락을 잃으므로 참고로만 남긴다. "
        "각 항목을 펼치면 왜 내렸는지가 먼저 나온다."
    )
    for card in fd.reference_cards(m):
        with st.expander(f"{th.STATUS_ICON[card['status']]}  {card['axis']} — {card['headline']}"):
            st.caption(f"**왜 참고로 내렸나** — {card.get('demoted', '')}")
            st.markdown(f"측정: {card['caption']}")
            if card["note"]:
                st.markdown(f"메모: {card['note']}")

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
                st.plotly_chart(grouped_bar(burn, "label", [("설비투자", "설비투자"), ("영업소진", "영업소진")],
                                            stacked=True), use_container_width=True)
                cash = m["cash_series"].copy()
                cash["현금(백만$)"] = (cash["val"] / 1e6).round(1)
                table(cash[["label", "현금(백만$)", "reported"]].rename(
                    columns={"label": "분기", "reported": "출처"}))

            elif card["axis"].startswith("자본투입"):
                metric_row([
                    ("PP&E 총액", fd.usd(m.get("ppe_gross")), "유형자산 취득원가 누계"),
                    ("누적 현금 설비투자", fd.usd(m.get("capex_cumulative")), ""),
                    ("비현금 증가분", fd.usd(m.get("noncash_additions")),
                     "미지급 capex · 자본화 이자 · 자본화 주식보상"),
                    ("반입 MW당 투입자본", fd.usd(m.get("capex_per_landed_mw")), ""),
                ])
                ppe = m["ppe_series"].copy()
                ppe["금액"] = ppe["val"] / 1e6
                st.plotly_chart(bar(ppe, "label", "금액", th.SERIES[0]), use_container_width=True)

            elif card["axis"].startswith("자본구조"):
                metric_row([
                    ("발행주식수", fd.num(m.get("shares_out"), 0, "주"), ""),
                    ("주식보상 ÷ 판관비", fd.pct(m.get("sbc_ratio")),
                     f"기준 {pd.Timestamp(m['sbc_ratio_asof']).date()}" if m.get("sbc_ratio_asof") is not None else ""),
                    ("총차입금(프로포마)", fd.usd(m.get("debt_proforma")), "분기말 + 분기 후 전환사채"),
                    ("순차입금(프로포마)", fd.usd(m.get("net_debt_proforma")), ""),
                ])
                shares = m["shares_series"].copy()
                shares["기준일"] = pd.to_datetime(shares["end"]).dt.strftime("%y-%m-%d")
                shares["주식수"] = shares["val"] / 1e6
                st.plotly_chart(bar(shares, "기준일", "주식수", th.SERIES[0], unit="백만주", digits=1),
                                use_container_width=True)
                if not m["capital_events"].empty:
                    table(m["capital_events"].rename(columns={
                        "period": "시기", "date": "일자", "instrument": "수단", "gross_musd": "총액(백만$)",
                        "net_musd": "순유입(백만$)", "dilutive": "희석", "terms": "조건", "source": "출처"}))

            elif card["axis"].startswith("지배구조"):
                feed = m["filings"].head(50).copy()
                if not feed.empty:
                    feed["접수일"] = feed["filed"].dt.date
                    table(feed.rename(columns={"form": "종류", "group": "구분", "title": "제목", "url": "링크"})
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
                    st.plotly_chart(line(price_frame, "date", "close", "종가", unit="USD", height=300),
                                    use_container_width=True)

# ── 원본 데이터 ───────────────────────────────────────────────────────────────
with tabs[5]:
    st.markdown("#### 데이터 출처")
    st.markdown(
        f"""
| 계층 | 출처 | 갱신 |
|---|---|---|
| 페르미 재무제표 | SEC EDGAR XBRL `companyfacts` (CIK {int(sec.CIK)}) | 10-Q/10-K 제출 시 자동 |
| 최신 분기 발표치 | `data/latest_reported.csv` — 10-Q 이전 8-K 수치 | 수동 |
| 전력 용량·계약·마일스톤 | `data/power_stages.csv`, `contracts.csv`, `milestones.csv` | 수동 |
| 자금조달 이력 | `data/capital_events.csv` | 수동 |
| 섹터 표본 재무·시세 | `data/sector_annuals.csv`, `sector_prices.csv` | `python refresh_sector.py` |
| 섹터 분류·검증 | `data/sector_profiles.csv`, `axis_validation.csv` | 수동 |
| 공시 피드 | SEC EDGAR `submissions` | 30분 캐시 |
| 시세 | Yahoo Finance 차트 API | 5분 캐시 |
"""
    )
    st.caption(
        "EDGAR는 현금흐름 항목을 회계연도 기초부터 누적해서 담는다. 그대로 쓰면 4분기 막대가 연간값이 "
        "되므로 `sec_edgar.periodic_series()`가 누적을 분기 구간으로 되돌린 뒤 화면에 올린다."
    )

    st.markdown("#### 주요 XBRL 태그 분기 시계열")
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

    st.markdown("#### 재무상태표 주요 항목")
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
