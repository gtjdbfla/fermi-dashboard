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


if __name__ == "__main__":
    original = os.environ.get("SEC_USER_AGENT")
    contact = original or "fermi-dashboard-smoke/1.0 (smoke@example.com)"
    results = [
        run_once("SEC_USER_AGENT 설정됨 (운영 환경)", contact),
        run_once("SEC_USER_AGENT 미설정 (자리표시자 경로)", None),
    ]
    if original is not None:
        os.environ["SEC_USER_AGENT"] = original
    sys.exit(0 if all(results) else 1)
