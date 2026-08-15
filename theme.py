"""차트 팔레트와 공통 Plotly 레이아웃.

라이트 서피스(#fcfcfb) 기준으로 검증한 값이다(대비 검사에서 aqua/yellow/magenta가 3:1 미만이라
막대에는 항상 직접 레이블을 붙이고 각 차트 아래에 표를 함께 둔다). 앱 테마를 라이트로 고정한 이유도
이것으로, .streamlit/config.toml에서 배경색을 같은 값으로 맞춰 두었다.
"""

SURFACE = "#fcfcfb"
INK_PRIMARY = "#0b0b0b"
INK_SECONDARY = "#52514e"
INK_MUTED = "#898781"
GRID = "#e1e0d9"
BASELINE = "#c3c2b7"

# 카테고리 슬롯(고정 순서, 순환 금지)
SERIES = ["#2a78d6", "#eb6834", "#1baf7a", "#eda100", "#e87ba4", "#008300"]

# 상태색(시리즈 색과 절대 섞어 쓰지 않는다). 라이트 서피스에서 warning/serious는
# 대비가 3:1 미만이라 반드시 아이콘+문구와 함께 쓴다.
STATUS = {
    "good": "#0ca30c",
    "warning": "#fab219",
    "serious": "#ec835a",
    "critical": "#d03b3b",
}
STATUS_ICON = {"good": "🟢", "warning": "🟡", "serious": "🟠", "critical": "🔴", "info": "⚪"}
STATUS_TEXT = {"good": "양호", "warning": "관찰", "serious": "주의", "critical": "경고", "info": "판정보류"}

# 순서형 램프(파랑 단일 색조). 라이트에서는 250단계보다 밝게 가지 않는다.
ORDINAL_BLUE = ["#86b6ef", "#6da7ec", "#5598e7", "#3987e5", "#2a78d6", "#256abf", "#1c5cab", "#184f95"]

FONT_FAMILY = 'system-ui, -apple-system, "Segoe UI", "Malgun Gothic", sans-serif'


def ordinal_colors(count: int) -> list[str]:
    """단계 수에 맞춰 램프를 균등 샘플링한다. 어두울수록 확정도가 높은 단계에 쓴다."""
    if count <= 1:
        return [ORDINAL_BLUE[-1]]
    last = len(ORDINAL_BLUE) - 1
    return [ORDINAL_BLUE[round(index * last / (count - 1))] for index in range(count)]


def style(figure, height: int = 320, legend: bool = False, ygrid: bool = True):
    """모든 차트에 같은 크롬을 입힌다. 격자·축은 뒤로 물리고 데이터가 앞에 오게 한다."""
    figure.update_layout(
        height=height,
        template="plotly_white",
        paper_bgcolor=SURFACE,
        plot_bgcolor=SURFACE,
        font=dict(family=FONT_FAMILY, size=12, color=INK_SECONDARY),
        margin=dict(l=8, r=8, t=28, b=8),
        hoverlabel=dict(font_family=FONT_FAMILY, font_size=12),
        showlegend=legend,
        legend=dict(orientation="h", yanchor="bottom", y=1.02, x=0, title_text=""),
        bargap=0.35,
    )
    figure.update_xaxes(showgrid=False, linecolor=BASELINE, tickfont=dict(color=INK_MUTED), title_text="")
    figure.update_yaxes(
        showgrid=ygrid,
        gridcolor=GRID,
        zerolinecolor=BASELINE,
        linecolor="rgba(0,0,0,0)",
        tickfont=dict(color=INK_MUTED),
        title_text="",
    )
    return figure
