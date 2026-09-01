"""app.py가 실제로 끝까지 렌더링되는지 확인한다.

    python smoke_test.py

왜 필요한가: `/_stcore/health`는 스크립트를 실행하지 않는다. 그래서 app.py 최상단이 깨져도
컨테이너는 healthy로 뜨고, 화면에 들어가야만 에러가 보인다. 실제로 그 틈으로 NameError가
배포된 적이 있다 — 들여쓰기가 어긋나 `m = fd.compute(...)`가 조건문 안으로 들어갔는데,
그 조건이 로컬(SEC_USER_AGENT 미설정)에서만 참이라 로컬 테스트는 통과했다.

AppTest는 app.py를 최상단부터 끝까지 실행하므로 이런 환경 의존 버그를 잡는다.
환경변수 설정 유무 양쪽으로 돌려서, 한쪽에서만 도는 코드가 없는지 확인한다.
"""

import os
import sys

from streamlit.testing.v1 import AppTest

HERE = os.path.dirname(os.path.abspath(__file__))


EXPECTED_TABS = 10        # 하나라도 줄면 화면이 끝까지 안 그려진 것이다


def run_once(label: str, user_agent: str | None) -> bool:
    if user_agent is None:
        os.environ.pop("SEC_USER_AGENT", None)
    else:
        os.environ["SEC_USER_AGENT"] = user_agent
    # sec_edgar는 임포트 시점에 환경변수를 읽으므로 매번 새로 읽히게 한다.
    for module in ("sec_edgar", "fundamentals", "sector", "market", "app"):
        sys.modules.pop(module, None)

    app = AppTest.from_file(os.path.join(HERE, "app.py"), default_timeout=300).run()
    if app.exception:
        print(f"[FAIL] {label}")
        for exception in app.exception:
            print("   ", exception.value)
        return False

    # **예외가 없다고 통과시키면 안 된다.** app.py에 구문 오류가 나면 AppTest가 예외를
    # 올리지 않고 빈 화면을 돌려주는데, 그때 "탭 0개, 예외 없음"으로 OK가 찍혔다.
    # 화면이 실제로 그려졌는지를 확인해야 검사다.
    if len(app.tabs) < EXPECTED_TABS:
        print(f"[FAIL] {label} — 탭이 {len(app.tabs)}개뿐이다(기대 {EXPECTED_TABS}개). "
              "화면이 끝까지 그려지지 않았다.")
        return False
    print(f"[ OK ] {label} — 탭 {len(app.tabs)}개, 예외 없음")
    return True


def filings_reach_digest() -> bool:
    """**공시가 일일 리포트 창에 실제로 잡히는지** 확인한다.

    이 검사가 없어서 놓친 적이 있다. load_filings의 filed는 filingDate(날짜)라 항상
    자정인데 워터마크는 정밀 시각이라, 02:10 UTC에 도는 리포트에서
    `filed(당일 00:00) >= 직전 리포트(당일 02:10)`가 False가 됐다. SEC 공시는 대부분
    20~21시 UTC에 오므로 **사실상 모든 공시가 리포트에서 빠졌다.**
    즉시 알림은 accn 기준이라 정상 동작했고, 그래서 결함이 드러나지 않았다.

    가장 최근 공시를 잡아, 그 접수시각 직전을 기준선으로 삼았을 때 리포트의 신규
    공시 블록에 반드시 나타나야 한다.
    """
    import pandas as pd
    import digest as dg
    import sec_edgar as sec

    loader = getattr(sec.load_filings, "__wrapped__", sec.load_filings)
    filings = loader()
    if filings is None or filings.empty:
        print("[FAIL] 공시→리포트 — 공시를 하나도 못 받았다")
        return False
    if "accepted" not in filings.columns:
        print("[FAIL] 공시→리포트 — load_filings에 accepted(접수시각) 컬럼이 없다")
        return False

    row = filings.iloc[0]
    accepted = pd.to_datetime(row["accepted"], errors="coerce", utc=True)
    if pd.isna(accepted):
        print("[SKIP] 공시→리포트 — 최신 공시에 접수시각이 없다")
        return True
    mark = accepted - pd.Timedelta(minutes=1)      # 접수 1분 전을 기준선으로
    lines, payload = dg._new_filings(filings, mark)
    if not payload:
        print(f"[FAIL] 공시→리포트 — {row['form']}({row['accn']}) 접수 {accepted}가 "
              f"창({mark} 이후)에 잡히지 않는다")
        return False
    print(f"[ OK ] 공시→리포트 — 최신 {row['form']}이 신규 블록에 잡힌다")
    return True


if __name__ == "__main__":
    original = os.environ.get("SEC_USER_AGENT")
    contact = original or "fermi-dashboard-smoke/1.0 (smoke@example.com)"
    results = [
        run_once("SEC_USER_AGENT 설정됨 (운영 환경)", contact),
        run_once("SEC_USER_AGENT 미설정 (자리표시자 경로)", None),
    ]
    os.environ["SEC_USER_AGENT"] = contact
    for module in ("sec_edgar", "digest"):
        sys.modules.pop(module, None)
    results.append(filings_reach_digest())
    if original is not None:
        os.environ["SEC_USER_AGENT"] = original
    sys.exit(0 if all(results) else 1)
