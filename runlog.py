"""크론 로그에 시각을 찍는다.

**왜 필요했나.** "아침 리포트가 왜 안 왔나"를 진단하려는데 logs/news.log에 시각이
한 줄도 없었다. `[ok] 일일 리포트 발송`이 11번 찍혀 있는데 각각 언제인지 알 수 없어,
캐시 워터마크를 역추적해야 했다. 알림이 조용히 어긋나는 걸 잡아야 하는 시스템에서
로그에 시각이 없는 건 그 자체로 결함이다.

crontab에서 awk로 찍는 방법도 있지만 cron은 `%`를 줄바꿈으로 해석해서
strftime 서식을 이스케이프해야 한다. 그 함정을 피하려고 파이썬 쪽에 둔다.

UTC와 KST를 함께 찍는다. 크론은 UTC로 돌고 사람은 KST로 읽는다.
"""

import builtins
import datetime

_INSTALLED = False


def install() -> None:
    """print()에 시각을 붙인다. 진입점에서 한 번만 부른다."""
    global _INSTALLED
    if _INSTALLED:
        return
    _INSTALLED = True
    original = builtins.print

    def stamped(*args, **kwargs):
        now = datetime.datetime.now(datetime.timezone.utc)
        kst = now + datetime.timedelta(hours=9)
        original(f"[{now:%m-%d %H:%M:%S}Z / {kst:%H:%M} KST]", *args, **kwargs)

    builtins.print = stamped


def banner(name: str) -> None:
    """실행 시작을 한 줄로 남긴다. 크론이 아예 안 돌았는지, 돌고 조용했는지를 가른다."""
    print(f"───── {name} 시작 ─────")
