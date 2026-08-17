"""차트 팔레트와 공통 Plotly 레이아웃.

라이트/다크 두 벌을 둔다. 다크는 라이트를 자동으로 뒤집은 것이 아니라, 어두운 서피스(#1a1a19)
기준으로 색각 이상 분리도와 대비를 따로 확인한 값이다. 색을 그대로 두고 배경만 바꾸면
어두운 화면에서 파랑·보라 계열이 서로 뭉개진다.

두 모드 모두 aqua·yellow·magenta 계열은 배경 대비가 3:1 미만이라, 모든 막대에 값 레이블을
붙이고 차트마다 같은 값을 읽을 수 있는 표를 함께 둔다.
"""

import streamlit as st

LIGHT = {
    "surface": "#fcfcfb",
    "ink": "#0b0b0b",
    "ink_soft": "#52514e",
    "ink_muted": "#898781",
    "grid": "#e1e0d9",
    "baseline": "#c3c2b7",
    "series": ["#2a78d6", "#eb6834", "#1baf7a", "#eda100", "#e87ba4", "#008300"],
    # 순서형 램프. 라이트에서는 250단계(#86b6ef)보다 밝게 가면 배경에 묻힌다.
    "ordinal": ["#86b6ef", "#6da7ec", "#5598e7", "#3987e5", "#2a78d6", "#256abf", "#1c5cab", "#184f95"],
}

DARK = {
    "surface": "#1a1a19",
    "ink": "#ffffff",
    "ink_soft": "#c3c2b7",
    "ink_muted": "#898781",
    "grid": "#2c2c2a",
    "baseline": "#383835",
    "series": ["#3987e5", "#d95926", "#199e70", "#c98500", "#d55181", "#008300"],
    # 다크에서는 반대로 600단계(#184f95)보다 어두우면 배경에 묻힌다.
    "ordinal": ["#184f95", "#1c5cab", "#256abf", "#2a78d6", "#3987e5", "#5598e7", "#6da7ec", "#86b6ef"],
}

# 판정 아이콘. 색만으로 뜻을 전달하지 않도록 아이콘과 문구를 함께 쓴다.
STATUS_ICON = {"good": "🟢", "warning": "🟡", "serious": "🟠", "critical": "🔴", "info": "⚪"}
FONT_FAMILY = 'system-ui, -apple-system, "Segoe UI", "Malgun Gothic", sans-serif'


def palette() -> dict:
    """지금 화면이 쓰는 팔레트. 사용자가 우측 상단 메뉴에서 테마를 바꾸면 따라간다."""
    try:
        mode = st.context.theme.type
    except Exception:
        mode = "light"
    return DARK if mode == "dark" else LIGHT



def ordinal_colors(count: int) -> list[str]:
    """단계 수에 맞춰 램프를 균등 샘플링한다. 진한 쪽이 확정도가 높은 단계다."""
    ramp = palette()["ordinal"]
    if count <= 1:
        return [ramp[-1]]
    last = len(ramp) - 1
    return [ramp[round(index * last / (count - 1))] for index in range(count)]


def style(figure, height: int = 320, legend: bool = False, ygrid: bool = True):
    """모든 차트에 같은 크롬을 입힌다. 격자·축은 뒤로 물리고 데이터가 앞에 오게 한다."""
    colors = palette()
    figure.update_layout(
        height=height,
        template="plotly_dark" if colors is DARK else "plotly_white",
        paper_bgcolor=colors["surface"],
        plot_bgcolor=colors["surface"],
        font=dict(family=FONT_FAMILY, size=12, color=colors["ink_soft"]),
        margin=dict(l=8, r=8, t=28, b=8),
        hoverlabel=dict(font_family=FONT_FAMILY, font_size=12),
        showlegend=legend,
        legend=dict(orientation="h", yanchor="bottom", y=1.02, x=0, title_text=""),
        bargap=0.35,
    )
    figure.update_xaxes(showgrid=False, linecolor=colors["baseline"],
                        tickfont=dict(color=colors["ink_muted"]), title_text="")
    figure.update_yaxes(showgrid=ygrid, gridcolor=colors["grid"], zerolinecolor=colors["baseline"],
                        linecolor="rgba(0,0,0,0)", tickfont=dict(color=colors["ink_muted"]),
                        title_text="")
    return figure
