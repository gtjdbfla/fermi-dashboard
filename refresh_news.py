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


def main() -> int:
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

    facts = {"contracted": 0, "customers": 0, "landed": 0, "coverage": 0,
             "target": 0, "operating": 0, "debt": "–"}
    try:
        import fundamentals as fd
        import market
        import sec_edgar as sec
        m = raw(fd.compute)(raw(sec.load_company_facts)(), raw(market.load_price)("FRMI")[1])
        facts = {
            "contracted": m.get("mw_contracted") or 0, "customers": m.get("customer_count") or 0,
            "landed": m.get("mw_landed") or 0,
            "coverage": (m.get("contracted_vs_landed") or 0) * 100,
            "target": m.get("mw_target") or 0, "operating": m.get("mw_operating") or 0,
            "debt": f"${(m.get('debt_proforma') or 0)/1e6:,.0f}M",
        }
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
    sys.exit(main())
