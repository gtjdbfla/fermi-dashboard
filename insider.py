"""내부자 거래(Form 4)를 거래 단위로 읽는다.

sec_edgar.load_filings()는 Form 4를 '내부자 거래' 그룹으로 세기만 한다. 건수만으로는
**부여인지 매수인지 매도인지 구분이 안 된다.** 이 차이가 전부다 —

  code A(부여)는 회사가 준 것이고, code P(매수)는 본인 돈을 넣은 것이다.

실제로 2026-08-14 신임 CEO의 468,750주가 커뮤니티에서 "CEO가 매수했다"로 돌았는데
원본 XML의 코드는 A, 단가 $0.00, 8-K에 적힌 $3M RSU 보상 그 자체였다. 건수만 세는
화면으로는 이 오독을 못 막는다. 그래서 원본을 직접 판다.

**URL 함정.** submissions 피드가 주는 Form 4 링크는 `/xslF345X05/ownership.xml` 처럼
사람이 보는 XSL 렌더링본이다. 그대로 파싱하면 XML이 아니라 HTML이 온다. 경로에서
`xslF345XNN/` 조각을 빼야 원본이 나온다.

**캐시는 누적한다.** 접수된 Form 4는 두 번 다시 바뀌지 않으므로, 한 번 판 접수번호는
다시 받지 않는다. SEC 요청도 아끼고 알림 크론도 빨라진다.
"""

import re
from xml.etree import ElementTree

import pandas as pd
import requests

import diskcache as dc
import sec_edgar as sec

CACHE = "insider_tx"
CACHE_MAX_AGE = 86400 * 3650     # 과거 거래는 변하지 않는다. 만료 개념이 없다.
TIMEOUT = 20

# SEC 거래 코드. 알림 여부를 가르는 것은 '본인 돈이 오갔는가'다.
CODES = {
    "P": ("공개시장 매수", "매수"),
    "S": ("공개시장 매도", "매도"),
    "A": ("무상 부여(보상)", "부여"),
    "M": ("옵션·RSU 행사", "행사"),
    "F": ("세금 납부용 원천공제", "원천공제"),
    "G": ("증여", "증여"),
    "C": ("전환", "전환"),
    "D": ("회사에 처분", "처분"),
    "X": ("옵션 행사", "행사"),
}

# 알림을 보낼 코드. 나머지는 보상 체계의 부산물이라 신호가 아니다.
#   A(부여)를 넣으면 매분기 베스팅마다 알림이 오고, 그게 '매수'로 오독되면 정반대 신호다.
#   F(원천공제)는 베스팅 시 세금 때문에 자동으로 나가는 것이라 본인 의사가 아니다.
SIGNAL_CODES = {"P", "S"}


def _xml_url(url: str) -> str:
    """XSL 렌더링본 경로를 원본 XML 경로로 되돌린다."""
    return re.sub(r"/xslF345X\d+/", "/", str(url or ""))


def _text(node, path: str, default: str = "") -> str:
    found = node.findtext(path)
    return (found or default).strip()


def _num(node, path: str):
    raw = node.findtext(path)
    try:
        return float(raw)
    except (TypeError, ValueError):
        return None


def _parse(accn: str, url: str, filed: str) -> list[dict]:
    """Form 4 하나를 거래 행들로 편다. 실패하면 빈 목록 — 알림 전체를 죽이지 않는다."""
    try:
        body = requests.get(_xml_url(url), headers=sec._HEADERS, timeout=TIMEOUT).content
        doc = ElementTree.fromstring(body)
    except Exception:
        return []

    person = _text(doc, ".//rptOwnerName", "?")
    roles = [label for tag, label in (("isDirector", "이사"), ("isOfficer", "임원"),
                                      ("isTenPercentOwner", "10%주주"))
             if _text(doc, f".//{tag}") in ("1", "true")]
    title = _text(doc, ".//officerTitle")
    # 10b5-1 사전계획 여부. 전용 태그가 붙기 시작한 게 2022년이라 아직 비어 있는 제출본이
    # 많다. 각주에 적는 관행이 더 오래됐으므로 양쪽을 다 본다.
    footnotes = " ".join((node.text or "") for node in doc.findall(".//footnote"))
    planned = (_text(doc, ".//aff10b5One") in ("1", "true")) or ("10b5-1" in footnotes)

    rows = []
    for node in doc.findall(".//nonDerivativeTransaction"):
        code = _text(node, ".//transactionCode", "?")
        shares = _num(node, ".//transactionShares/value") or 0.0
        price = _num(node, ".//transactionPricePerShare/value")
        rows.append({
            "accn": accn,
            "filed": filed,
            "date": _text(node, ".//transactionDate/value")[:10] or filed,
            "person": person,
            "roles": " / ".join(roles),
            "officer_title": title,
            "code": code,
            "action": CODES.get(code, (code, code))[1],
            "shares": shares,
            "price": price,
            "value": (shares * price) if price else 0.0,
            "acquired": _text(node, ".//transactionAcquiredDisposedCode/value"),
            "owned_after": _num(node, ".//sharesOwnedFollowingTransaction/value"),
            "planned": planned,
            "url": str(url or ""),
        })
    return rows


WHO_CACHE = "insider_who"


def who(accn: str, url: str) -> dict:
    """Form 3·4 한 건에서 '누가·어떤 자리인지'만 뽑는다. 실패하면 빈 dict.

    `_parse`는 거래 행이 있어야 이름을 내보낸다. 그런데 Form 3(최초신고)은 거래가
    아니라 보유 현황이라 거래 행이 없고, 신규 선임자는 대개 보유 0이라 아예 빈
    목록이 된다. 그래서 2026-09-03 신임 최고사업책임자 선임 신고가 리포트에 서식
    번호 '3' 한 글자로만 나갔다 — 이름도 직책도 없이. 사람이 누구인지가 곧 내용인
    서식에서 그건 아무것도 알리지 않은 것과 같다.

    한 번 제출된 신고서의 인적사항은 바뀌지 않으므로 접수번호로 영구 캐시한다.
    """
    key = str(accn or "").strip()
    store = dc.load_json(WHO_CACHE, CACHE_MAX_AGE) or {}
    if key and key in store:
        return store[key]
    try:
        body = requests.get(_xml_url(url), headers=sec._HEADERS, timeout=TIMEOUT).content
        doc = ElementTree.fromstring(body)
    except Exception:
        return {}
    roles = [label for tag, label in (("isDirector", "이사"), ("isOfficer", "임원"),
                                      ("isTenPercentOwner", "10%주주"))
             if _text(doc, f".//{tag}") in ("1", "true")]
    info = {
        "person": _text(doc, ".//rptOwnerName"),
        "roles": " / ".join(roles),
        "officer_title": _text(doc, ".//officerTitle"),
        # 선임 효력일. 제출일과 벌어져 있으면 지연 신고다(Form 3은 10일 내 제출 의무).
        "period": _text(doc, ".//periodOfReport")[:10],
    }
    if not info["person"]:
        return {}
    if key:
        store[key] = info
        dc.save_json(WHO_CACHE, store)
    return info


def describe(accn: str, url: str, form: str) -> str:
    """'Anna Bofa · 임원 Chief Commercial Officer' 같은 한 줄. 못 읽으면 빈 문자열."""
    if str(form or "").strip() not in ("3", "4"):
        return ""
    info = who(accn, url)
    if not info:
        return ""
    parts = [info["person"]]
    role = " ".join(x for x in (info.get("roles"), info.get("officer_title")) if x)
    if role:
        parts.append(role)
    return " · ".join(parts)


def transactions(filings: pd.DataFrame | None = None, refresh: bool = True) -> pd.DataFrame:
    """Form 4 거래 전체. 새 접수번호만 내려받고 나머지는 캐시에서 읽는다."""
    cached = dc.load_json(CACHE, CACHE_MAX_AGE) or {}
    rows = list(cached.get("rows") or [])
    known = {row.get("accn") for row in rows}

    if refresh:
        if filings is None:
            loader = getattr(sec.load_filings, "__wrapped__", sec.load_filings)
            filings = loader()
        if filings is not None and not filings.empty:
            pending = filings[filings["form"].astype(str).str.upper().isin({"4", "4/A"})]
            fetched = 0
            for row in pending.itertuples():
                if row.accn in known or not row.url:
                    continue
                parsed = _parse(row.accn, row.url, str(pd.Timestamp(row.filed).date()))
                # 파싱에 실패한 건은 기억하지 않는다. 다음 실행에서 다시 시도한다.
                if parsed:
                    rows += parsed
                    known.add(row.accn)
                    fetched += 1
            if fetched:
                dc.save_json(CACHE, {"rows": rows})
    dc.record_health("insider_form4", len(rows))

    if not rows:
        return pd.DataFrame(columns=["accn", "filed", "date", "person", "roles", "officer_title",
                                     "code", "action", "shares", "price", "value", "acquired",
                                     "owned_after", "planned", "url"])
    frame = pd.DataFrame(rows)
    frame["date"] = pd.to_datetime(frame["date"], errors="coerce")
    frame["filed"] = pd.to_datetime(frame["filed"], errors="coerce")
    return frame.sort_values("date", ascending=False).reset_index(drop=True)


def tally(frame: pd.DataFrame | None = None) -> dict:
    """누적 매수/매도. **이 대비가 개별 건보다 중요하다.**

    한 건의 매도는 집을 사는 것일 수도 있다. 상장 이래 매수가 0인 것은 다른 얘기다.
    """
    frame = transactions() if frame is None else frame
    if frame is None or frame.empty:
        return {"buy_shares": 0.0, "buy_value": 0.0, "sell_shares": 0.0, "sell_value": 0.0,
                "buyers": 0, "sellers": 0, "since": ""}
    buys = frame[frame["code"] == "P"]
    sells = frame[frame["code"] == "S"]
    return {
        "buy_shares": float(buys["shares"].sum()),
        "buy_value": float(buys["value"].sum()),
        "sell_shares": float(sells["shares"].sum()),
        "sell_value": float(sells["value"].sum()),
        "buyers": int(buys["person"].nunique()),
        "sellers": int(sells["person"].nunique()),
        "since": str(frame["date"].min().date()) if pd.notna(frame["date"].min()) else "",
    }


def by_filing(frame: pd.DataFrame | None = None, codes: set | None = None) -> list[dict]:
    """제출본 하나를 사건 하나로 묶는다.

    Form 4 하나에 거래 행이 여럿 들어간다 — 이틀에 걸쳐 나눠 판 건이 두 줄로 온다.
    행마다 알리면 같은 결정이 두 통이 된다.

    codes로 볼 거래 코드를 정한다. 기본은 SIGNAL_CODES(P/S)로 **알림용**이다.
    일일 리포트는 부여(A)까지 넘겨서 본다 — 알림으로 깨울 일은 아니지만
    "어제 내부자 쪽에서 무슨 일이 있었나"에는 부여도 답에 들어가야 한다.
    실제로 COO에게 RSU 14.7만 주가 부여된 날, 리포트에는 '내부자 거래 1건'이라는
    건수만 뜨고 내용이 없었다.
    """
    codes = codes or SIGNAL_CODES
    frame = transactions() if frame is None else frame
    if frame is None or frame.empty:
        return []
    out = []
    for accn, group in frame.groupby("accn", sort=False):
        signal = group[group["code"].isin(codes)]
        if signal.empty:
            continue
        # 한 제출본에 매수와 매도가 섞이는 일은 사실상 없지만, 섞이면 각각 사건으로 낸다.
        for code, part in signal.groupby("code", sort=False):
            shares = float(part["shares"].sum())
            value = float(part["value"].sum())
            prices = part["price"].dropna()
            # 잔여 주식은 마지막 거래 기준이다. 지분의 몇 %를 던졌는지가 규모보다 말이 된다.
            last = part.sort_values("date").iloc[-1]
            owned_after = last["owned_after"]
            share_pct = None
            if owned_after is not None and pd.notna(owned_after):
                before = owned_after + (shares if code == "S" else -shares)
                if before > 0:
                    share_pct = shares / before * 100
            out.append({
                "accn": accn,
                "code": code,
                "action": CODES.get(code, (code, code))[0],
                "person": last["person"],
                "roles": last["roles"],
                "officer_title": last["officer_title"],
                "filed": str(pd.Timestamp(last["filed"]).date()) if pd.notna(last["filed"]) else "",
                "date": str(pd.Timestamp(part["date"].min()).date()),
                "date_end": str(pd.Timestamp(part["date"].max()).date()),
                "shares": shares,
                "value": value,
                "price_low": float(prices.min()) if not prices.empty else None,
                "price_high": float(prices.max()) if not prices.empty else None,
                "owned_after": float(owned_after) if pd.notna(owned_after) else None,
                "share_pct": share_pct,
                "planned": bool(last["planned"]),
                "url": last["url"],
            })
    return sorted(out, key=lambda item: item["filed"], reverse=True)
