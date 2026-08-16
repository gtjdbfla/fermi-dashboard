"""전력·에너지 인프라 섹터 표본의 연도별 재무와 주가 결과를 data/로 굳힌다.

    python refresh_sector.py

이 표본은 "펀더멘탈 항목이 실제로 결과를 갈랐는가"를 검증하는 근거다. 앱이 켜질 때마다 13개사
companyfacts(각 수 MB)를 받으면 너무 느리고, 이미 끝난 과거 사이클이라 값도 바뀌지 않는다.

표본 기준: 매출보다 먼저 대규모 발전·에너지 인프라를 지은 미국 상장사.
결과 분류는 주가가 아니라 객관적 재무 사건으로만 한다(주가로 나누면 순환논증이 된다).
"""

import os
from pathlib import Path

import pandas as pd
import requests

# 저장소가 공개라 실제 연락처를 코드에 두지 않는다. 환경변수 SEC_USER_AGENT로 주입한다.
SEC_HEADERS = {"User-Agent": os.environ.get("SEC_USER_AGENT", "fermi-dashboard/1.0 (set-me@example.com)")}
YAHOO_HEADERS = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/120.0 Safari/537.36"}
DATA = Path(__file__).parent / "data"

# 티커: (회사명, CIK, 세부업종)
UNIVERSE = {
    "LNG":  ("Cheniere Energy", 3570, "LNG 수출"),
    "VG":   ("Venture Global", 2007855, "LNG 수출"),
    "NEXT": ("NextDecade", 1612720, "LNG 수출"),
    "TELL": ("Tellurian", 61398, "LNG 수출"),
    "NFE":  ("New Fortress Energy", 1749723, "LNG→발전"),
    "SMR":  ("NuScale Power", 1822966, "원자력"),
    "OKLO": ("Oklo", 1849056, "원자력"),
    "PLUG": ("Plug Power", 1093691, "수소"),
    "FCEL": ("FuelCell Energy", 886128, "연료전지"),
    "BE":   ("Bloom Energy", 1664703, "연료전지"),
    "CORZ": ("Core Scientific", 1839341, "데이터센터 전력"),
    "APLD": ("Applied Digital", 1144879, "데이터센터 전력"),
    "TLN":  ("Talen Energy", 1622536, "발전(merchant)"),
}

FLOW_TAGS = {
    "revenue": ["Revenues", "RevenueFromContractWithCustomerExcludingAssessedTax",
                "RevenueFromContractWithCustomerIncludingAssessedTax", "SalesRevenueNet"],
    "op_cf": ["NetCashProvidedByUsedInOperatingActivities",
              "NetCashProvidedByUsedInOperatingActivitiesContinuingOperations"],
    "capex": ["PaymentsToAcquirePropertyPlantAndEquipment", "PaymentsToAcquireProductiveAssets",
              "PaymentsForConstructionInProcess"],
    "net_income": ["NetIncomeLoss", "ProfitLoss"],
    "cap_interest": ["InterestCostsCapitalized", "InterestCostsIncurredCapitalized"],
}
INSTANT_TAGS = {
    "cash": ["CashCashEquivalentsRestrictedCashAndRestrictedCashEquivalents",
             "CashAndCashEquivalentsAtCarryingValue"],
    "equity": ["StockholdersEquity",
               "StockholdersEquityIncludingPortionAttributableToNoncontrollingInterest"],
    "debt": ["LongTermDebtNoncurrent", "LongTermDebt", "LongTermDebtAndCapitalLeaseObligations"],
    "shares": ["CommonStockSharesOutstanding", "EntityCommonStockSharesOutstanding"],
}

# 주가 비교 시작점. Plug Power와 FuelCell은 2000년 수소 버블에 고점이 있어, 그대로 재면
# 다른 회사와 시대가 달라진다. 2020년 이후로 잘라 같은 사이클 안에서 비교한다.
PRICE_SINCE = "2020-01-01"


def _facts(cik: int) -> dict:
    url = f"https://data.sec.gov/api/xbrl/companyfacts/CIK{cik:010d}.json"
    response = requests.get(url, headers=SEC_HEADERS, timeout=180)
    response.raise_for_status()
    return response.json().get("facts", {})


def _annual_flow(facts: dict, tags: list[str]) -> dict:
    """연간(330~400일) 구간만. 앞 태그를 우선하되 빈 연도만 뒤 태그로 채운다.

    도중에 태그를 갈아타는 회사가 흔하다(Applied Digital은 FY2023부터 capex 태그를 바꿨다).
    첫 태그에서 멈추면 그 뒤 연도가 통째로 빈다.
    """
    merged: dict = {}
    for tag in tags:
        node = facts.get("us-gaap", {}).get(tag)
        if not node:
            continue
        rows = []
        for row in node.get("units", {}).get("USD", []):
            if not row.get("start"):
                continue
            start, end = pd.Timestamp(row["start"]), pd.Timestamp(row["end"])
            if 330 <= (end - start).days <= 400:
                rows.append({"fy": end.year, "val": row["val"], "filed": pd.Timestamp(row.get("filed"))})
        if not rows:
            continue
        frame = pd.DataFrame(rows).sort_values("filed").drop_duplicates("fy", keep="last")
        for year, value in zip(frame["fy"], frame["val"]):
            merged.setdefault(year, value)
    return merged


def _annual_instant(facts: dict, tags: list[str], unit: str = "USD") -> dict:
    merged: dict = {}
    for tag in tags:
        node = facts.get("us-gaap", {}).get(tag) or facts.get("dei", {}).get(tag)
        if not node:
            continue
        rows = [
            {"fy": pd.Timestamp(row["end"]).year, "end": pd.Timestamp(row["end"]),
             "val": row["val"], "filed": pd.Timestamp(row.get("filed"))}
            for row in node.get("units", {}).get(unit, []) if not row.get("start")
        ]
        if not rows:
            continue
        frame = pd.DataFrame(rows).sort_values(["end", "filed"]).groupby("fy").last().reset_index()
        for year, value in zip(frame["fy"], frame["val"]):
            merged.setdefault(year, value)
    return merged


def _price_outcome(ticker: str) -> dict | None:
    try:
        response = requests.get(
            f"https://query1.finance.yahoo.com/v8/finance/chart/{ticker}",
            params={"range": "max", "interval": "1mo"}, headers=YAHOO_HEADERS, timeout=30,
        )
        result = response.json()["chart"]["result"][0]
        frame = pd.DataFrame({
            "date": pd.to_datetime(result["timestamp"], unit="s"),
            "close": result["indicators"]["quote"][0]["close"],
        }).dropna()
    except Exception as error:
        print(f"    시세 실패 {ticker}: {error}")
        return None
    frame = frame[frame["date"] >= PRICE_SINCE]
    if frame.empty:
        return None
    peak = frame.loc[frame["close"].idxmax()]
    after_peak = frame.loc[frame["close"].idxmax():]
    trough = after_peak.loc[after_peak["close"].idxmin()]
    current = frame.iloc[-1]
    return {
        "ticker": ticker,
        "peak_date": peak["date"].strftime("%Y-%m"),
        "peak": round(float(peak["close"]), 2),
        "trough": round(float(trough["close"]), 2),
        "current": round(float(current["close"]), 2),
        "from_peak_pct": round((current["close"] / peak["close"] - 1) * 100, 1),
        "max_drawdown_pct": round((trough["close"] / peak["close"] - 1) * 100, 1),
    }


def _yearly_prices(ticker: str) -> list[dict]:
    """연도별 마지막 종가. '각 기업이 지금의 페르미와 같은 위치였던 해'의 주가를 찾는 데 쓴다.

    분할 조정된 값이라 수익률 계산에 그대로 쓸 수 있다(FuelCell처럼 액면병합을 반복한 종목도
    같은 기준으로 비교된다).
    """
    try:
        response = requests.get(
            f"https://query1.finance.yahoo.com/v8/finance/chart/{ticker}",
            params={"range": "max", "interval": "1mo"}, headers=YAHOO_HEADERS, timeout=30,
        )
        result = response.json()["chart"]["result"][0]
        frame = pd.DataFrame({
            "date": pd.to_datetime(result["timestamp"], unit="s"),
            "close": result["indicators"]["quote"][0]["close"],
        }).dropna()
    except Exception as error:
        print(f"    연도별 시세 실패 {ticker}: {error}")
        return []
    if frame.empty:
        return []
    frame["year"] = frame["date"].dt.year
    last = frame.sort_values("date").groupby("year").last().reset_index()
    return [
        {"ticker": ticker, "year": int(row["year"]),
         "close": round(float(row["close"]), 4), "asof": row["date"].strftime("%Y-%m")}
        for _, row in last.iterrows()
    ]


def collect() -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    annual_records, price_records, history_records = [], [], []
    for ticker, (name, cik, sub) in UNIVERSE.items():
        print(f"  {ticker} ({name}) 내려받는 중...")
        facts = _facts(cik)
        columns = {key: _annual_flow(facts, tags) for key, tags in FLOW_TAGS.items()}
        for key, tags in INSTANT_TAGS.items():
            columns[key] = _annual_instant(facts, tags, "shares" if key == "shares" else "USD")
        for year in sorted({y for values in columns.values() for y in values}):
            annual_records.append({
                "ticker": ticker, "company": name, "sub": sub, "fy": year,
                **{key: values.get(year) for key, values in columns.items()},
            })
        outcome = _price_outcome(ticker)
        if outcome:
            price_records.append({**outcome, "company": name})
        history_records.extend(_yearly_prices(ticker))
    return (pd.DataFrame(annual_records), pd.DataFrame(price_records),
            pd.DataFrame(history_records))


if __name__ == "__main__":
    annuals, prices, history = collect()
    DATA.mkdir(parents=True, exist_ok=True)
    annuals.to_csv(DATA / "sector_annuals.csv", index=False, encoding="utf-8")
    prices.to_csv(DATA / "sector_prices.csv", index=False, encoding="utf-8")
    history.to_csv(DATA / "sector_price_history.csv", index=False, encoding="utf-8")
    print(f"\n재무 {len(annuals)}행 · 시세요약 {len(prices)}행 · 연도별시세 {len(history)}행 저장 → {DATA}")
