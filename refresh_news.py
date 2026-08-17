"""뉴스·커뮤니티를 받아 디스크 캐시에 채우고, 새 내용이면 AI 정리까지 미리 만들어 둔다.

    docker compose exec -T fermi-dashboard python refresh_news.py

크론이 30분마다 이걸 돌린다. 화면은 이 캐시만 읽으므로 접속자가 HTTP를 기다리지 않는다.
Streamlit은 어느 탭을 보든 모든 탭 코드를 실행해서, 화면에서 직접 받으면 뉴스 탭을 안 보는
사람도 6초를 문다(실측: Google 1.9 + Yahoo 0.8 + Nasdaq 4.9, 병렬로도 Nasdaq이 바닥).

AI 정리는 기사 지문이 바뀔 때만 새로 만든다. 뉴스가 그대로면 API를 부르지 않는다.
"""

import sys

import ai_review
import news


def raw(function):
    """st.cache_data가 감싼 함수는 벗겨서 부르고, 아닌 것은 그대로 부른다.

    전부 캐시된 함수라고 가정하고 __wrapped__를 붙였다가 fundamentals.compute에서
    AttributeError가 났고, 그 바람에 AI 정리가 계약 0%라는 엉뚱한 전제로 만들어졌다.
    """
    return getattr(function, "__wrapped__", function)


def warm_market() -> None:
    """시세 바스켓·수급·시가총액도 미리 받아 디스크에 남긴다.

    순차 호출 시절 이 셋만 45초였다(시가총액 13종목 33초, 바스켓 4.3초, 수급 7.7초).
    병렬화로 9.7초까지 줄였지만, 그마저도 캐시가 빈 첫 접속자가 문다. 여기서 미리 채운다.
    """
    import market_flow as mf
    import sector as sc
    for label, call in [("바스켓", lambda: raw(mf.basket_frame)(force=True)),
                        ("수급", lambda: raw(mf._supply_raw)(force=True)),
                        ("시가총액", lambda: raw(sc.market_caps)(force=True))]:
        try:
            call()
            print(f"[ok] {label} 캐시 갱신")
        except Exception as error:
            print(f"[warn] {label} 갱신 실패: {type(error).__name__}: {error}")


def warm_filings() -> None:
    """새 SEC 공시를 AI로 판독해 캐시에 남긴다.

    주 1회 클라우드 루틴에 맡겼던 일인데, 그쪽 샌드박스가 SEC 도메인을 egress 차단해서
    (2026-08-17 실행에서 확인) 서버로 옮겼다. CSV는 고치지 않고 판정만 남긴다.
    """
    try:
        import fundamentals as fd
        import market
        import sec_edgar as sec
        import filing_review as fr
        m = raw(fd.compute)(raw(sec.load_company_facts)(), raw(market.load_price)("FRMI")[1])
        result = fr.run(m, fd.manual_data_asof())
        if result.get("error"):
            print(f"[warn] 공시 판독 실패: {result['error']}")
        elif result["count"] == 0:
            print("[ok] 신규 공시 없음")
        else:
            headline = (result["text"].splitlines() or [""])[0][:60]
            print(f"[ok] 공시 {result['count']}건 판독 — {headline}")
    except Exception as error:
        print(f"[warn] 공시 판독 실패: {type(error).__name__}: {error}")


def main(include_market: bool = False) -> int:
    """빠른층은 기본, 느린층(시세·수급·시총)은 --slow일 때만.

    원본이 바뀌는 주기가 층마다 다르다. 계약 소식은 8-K가 아무 때나 떨어지므로 놓치면 안 되지만,
    바스켓은 일봉이고 공매도는 격주 공시, 기관 보유는 분기 공시다. 전부 30분마다 받으면
    하루 1,392회를 부르는데 그중 대부분이 같은 값을 다시 받는 것이다.
    """
    if include_market:
        warm_market()
    warm_filings()
    articles = raw(news.collect)()
    chatter = raw(news.community)()
    if articles.empty:
        print("[fail] 기사를 하나도 받지 못했다 — 캐시를 덮지 않는다")
        return 1
    if chatter.empty:
        # Stocktwits는 서버 IP를 Cloudflare로 막는다. 봇 차단을 우회하지 않고 비운 채로 둔다.
        print("[warn] 커뮤니티 0건 — Stocktwits가 이 IP를 차단했을 수 있다")

    news.save_cache(articles, chatter)
    hits = len(news.contract_hits(articles))
    print(f"[ok] 기사 {len(articles)}건(계약·테넌트 {hits}건) · 커뮤니티 {len(chatter)}건 저장")

    key = ai_review.fingerprint(articles, chatter)
    if ai_review._read_cache().get(key):
        print(f"[skip] AI 정리 이미 있음 (지문 {key})")
        return 0
    if not ai_review.available():
        print("[skip] GEMINI_API_KEY 없음 — AI 정리 건너뜀")
        return 0

    try:
        import fundamentals as fd
        import market
        import sec_edgar as sec
        m = raw(fd.compute)(raw(sec.load_company_facts)(), raw(market.load_price)("FRMI")[1])
        facts = ai_review.context(m)
    except Exception as error:
        # 확정 사실 없이 만든 정리는 전제가 틀려 쓸모가 없다. 다음 실행에서 다시 시도한다.
        print(f"[fail] 확정 사실을 못 읽었다({type(error).__name__}: {error}) — AI 정리를 건너뛴다")
        return 1

    text, error = raw(ai_review.analyze)(key, ai_review._payload(articles, chatter), facts)
    if text:
        print(f"[ok] AI 정리 생성 (지문 {key}, {len(text)}자)")
        return 0
    print(f"[fail] AI 정리 실패: {error}")
    return 1


if __name__ == "__main__":
    sys.exit(main(include_market="--slow" in sys.argv))
