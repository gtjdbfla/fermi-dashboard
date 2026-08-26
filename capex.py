"""분기 capex가 꺾이는 순간을 잡는다.

**왜 필요한가.** 건설이 늦춰지거나 멈추는 것은 공시로 따로 나오지 않는다. 회사는
"일정에 차질이 있다"고 먼저 말하지 않고, 분기보고서의 투자활동 현금흐름에 숫자로만
남는다. 실측하면 이미 한 번 벌어졌다 —

    2025Q4 $480M → 2026Q1 $441M → 2026Q2 $185M  (전분기 대비 -58%)

그리고 2026Q2는 CEO 해임(4/17)과 위임장 분쟁이 있던 바로 그 분기다. 가동 중 MW가
0인 회사에서 건설 속도는 유일하게 관측 가능한 실적이므로, 꺾이면 알아야 한다.

**capex는 원래 울퉁불퉁하다.** 터빈 대금 같은 큰 건이 한 분기에 몰리면 다음 분기는
자연히 준다. 그래서 직전 분기 하나만 보고 판정하지 않는다. **직전 분기 대비**와
**직전 4분기 평균 대비**가 둘 다 꺾였을 때만 사건으로 만든다.

측정 자체는 fundamentals.compute()가 이미 만들어 둔 capex_series를 쓴다. XBRL을
다시 받지 않으므로 알림 크론이 무거워지지 않는다.
"""

import pandas as pd

import sec_edgar as sec

# 두 조건을 **둘 다** 넘어야 사건이다.
DROP_VS_PREV = 0.40      # 직전 분기 대비 40% 이상 감소
DROP_VS_AVG = 0.40       # 직전 4분기 평균 대비 40% 이상 감소
BASELINE_QUARTERS = 4

# 초기 몇 분기는 부지 매입만 있어 금액이 작고 변동률이 무의미하다.
# 2025Q2 $40M → Q3 $49M 같은 구간에서 비율만 보면 헛알림이 난다.
MIN_BASELINE_USD = 50_000_000
MIN_BASELINE_QUARTERS = 2


def series(m: dict | None) -> pd.DataFrame:
    """분기 하나짜리 capex만 남긴 시계열. 합산 구간(반기·연간)은 뺀다."""
    frame = (m or {}).get("capex_series")
    if frame is None or not isinstance(frame, pd.DataFrame) or frame.empty:
        return pd.DataFrame(columns=["end", "val", "label"])
    out = frame.copy()
    if "days" in out.columns:
        # 라벨만 있고 days가 비는 8-K 발표치는 분기값으로 본다.
        out = out[out["days"].isna() | (out["days"] <= 100)]
    if out.empty:
        return pd.DataFrame(columns=["end", "val", "label"])
    out["end"] = pd.to_datetime(out["end"], errors="coerce")
    out = out.dropna(subset=["end"]).sort_values("end")
    if "label" not in out.columns:
        out["label"] = out["end"].map(sec.quarter_label)
    return out.reset_index(drop=True)


def assess(m: dict | None) -> dict | None:
    """최신 분기의 감속 여부. 판정 못 하면 None."""
    frame = series(m)
    if len(frame) < 3:
        return None
    latest = frame.iloc[-1]
    prev = frame.iloc[-2]
    # **기준선에서 램프 이전 분기를 빼야 한다.** 그냥 직전 4분기를 평균내면 부지 매입만
    # 있던 초기 분기($40M·$49M)가 평균을 끌어내려, 480·441 → 185처럼 명백한 급감도
    # '평균 대비 -27%'로 희석되어 발동하지 않는다(실측으로 확인).
    # 기준선은 '실제로 짓고 있던 분기'만으로 만든다.
    window = frame.iloc[-(BASELINE_QUARTERS + 1):-1]
    baseline_rows = window[window["val"] >= MIN_BASELINE_USD]
    previous = float(prev["val"])
    current = float(latest["val"])

    if len(baseline_rows) < MIN_BASELINE_QUARTERS or previous <= 0:
        return None
    baseline = float(baseline_rows["val"].mean())

    drop_prev = 1 - (current / previous) if previous else 0.0
    drop_avg = 1 - (current / baseline) if baseline else 0.0
    return {
        "quarter": str(latest.get("label") or sec.quarter_label(latest["end"])),
        "end": pd.Timestamp(latest["end"]),
        "current": current,
        "previous": previous,
        "prev_quarter": str(prev.get("label") or sec.quarter_label(prev["end"])),
        "baseline": baseline,
        "baseline_n": len(baseline_rows),
        "drop_prev": drop_prev,
        "drop_avg": drop_avg,
        "triggered": drop_prev >= DROP_VS_PREV and drop_avg >= DROP_VS_AVG,
        "trail": [(str(r.label), float(r.val)) for r in frame.tail(5).itertuples()],
    }


def reported_on(filings: pd.DataFrame | None, end: pd.Timestamp) -> tuple:
    """그 분기를 담은 정기보고서의 (접수일, 원문 URL).

    사건 날짜는 '분기말'이 아니라 '공시된 날'이다. 분기말을 쓰면 6/30이 되어
    나이 제한(14일)에 걸려 영원히 안 나간다. 시장이 알게 된 날은 10-Q 접수일이다.
    """
    if filings is None or filings.empty:
        return None, ""
    periodic = filings[filings["form"].astype(str).str.upper().isin(["10-Q", "10-K"])].copy()
    if periodic.empty:
        return None, ""
    periodic["filed"] = pd.to_datetime(periodic["filed"], errors="coerce")
    after = periodic[periodic["filed"] >= pd.Timestamp(end)].sort_values("filed")
    if after.empty:
        return None, ""
    row = after.iloc[0]
    return row["filed"], str(row.get("url") or "")
