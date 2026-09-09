#!/bin/sh
# 원격 변경을 받아와 필요한 만큼만 반영한다. 크론으로 30분마다 돈다.
#
#   */30 * * * * /home/yulimseo/fermi-dashboard/deploy.sh >> /home/yulimseo/fermi-dashboard/logs/deploy.log 2>&1
#
# data/의 CSV만 바뀐 경우에는 아무것도 하지 않는다. data/는 컨테이너에 볼륨으로 물려 있고
# Streamlit 캐시 TTL이 10분이라 저절로 반영된다. 매번 재빌드하면 그동안 화면이 끊긴다.
# .py / Dockerfile / requirements.txt / .streamlit 이 바뀐 경우에만 재빌드한다.

set -e
cd "$(dirname "$0")"

# 실패하면 로그에만 남는데 **그 로그를 읽는 사람이 없다.** 갱신이 멈춘 걸 몇 주 뒤
# 화면을 보고서야 알게 된다. 알림과 같은 통로로 보낸다.
# `.` 로 읽지 않고 값만 뽑는다 — .env를 실행하면 안 된다.
notify() {
    [ -f .env ] || return 0
    _t=$(grep -m1 '^TELEGRAM_BOT_TOKEN=' .env | cut -d= -f2-)
    _c=$(grep -m1 '^TELEGRAM_CHAT_ID=' .env | cut -d= -f2-)
    [ -n "$_t" ] && [ -n "$_c" ] || return 0
    curl -sS -m 10 -o /dev/null -X POST \
        -d "chat_id=$_c" --data-urlencode "text=$1" \
        "https://api.telegram.org/bot$_t/sendMessage" || true
    return 0
}

BEFORE=$(git rev-parse HEAD)
if ! git pull --quiet --ff-only; then
    echo "$(date '+%F %T') [fail] git pull 실패"
    # 대개 원인은 하나다 — 컨테이너나 손으로 data/를 건드려 작업트리가 더러워졌다.
    # 이 상태로 두면 이후 모든 갱신이 조용히 멈춘다.
    notify "🔧 페르미 배포 실패 — git pull --ff-only가 막혔다.
$(git status --porcelain | head -10)
작업트리를 되돌려야 갱신이 다시 돈다: git checkout -- <파일>"
    exit 1
fi
AFTER=$(git rev-parse HEAD)

[ "$BEFORE" = "$AFTER" ] && exit 0

CHANGED=$(git diff --name-only "$BEFORE" "$AFTER")
echo "$(date '+%F %T') [pull] $BEFORE -> $AFTER"
echo "$CHANGED" | sed 's/^/    /'

if echo "$CHANGED" | grep -qE '\.py$|^Dockerfile$|^requirements\.txt$|^\.streamlit/|^docker-compose\.yml$|^Caddyfile$'; then
    echo "$(date '+%F %T') [build] 코드 변경 감지 — 재빌드"
    if ! docker compose up -d --build; then
        echo "$(date '+%F %T') [BUILD FAIL] 재빌드 실패 — 이전 컨테이너가 그대로 돈다"
        notify "🔧 페르미 배포 실패 — 재빌드가 깨졌다 ($AFTER).
이전 버전이 계속 서비스 중이다. logs/deploy.log를 봐라."
        exit 1
    fi

    # /_stcore/health는 스크립트를 실행하지 않아서, app.py 최상단이 깨져도 healthy로 뜬다.
    # 실제로 그 틈으로 NameError가 배포된 적이 있다. 화면을 끝까지 그려보고 확인한다.
    sleep 10
    if docker compose exec -T fermi-dashboard python smoke_test.py > /tmp/fermi_smoke.log 2>&1; then
        echo "$(date '+%F %T') [smoke] 렌더링 정상"
    else
        echo "$(date '+%F %T') [SMOKE FAIL] 화면이 렌더링되지 않는다 — 아래 로그 확인"
        tail -20 /tmp/fermi_smoke.log | sed 's/^/    /'
        # **컨테이너는 healthy로 뜬다.** 여기서 안 알리면 깨진 화면이 그대로 서비스된다.
        notify "🔧 페르미 배포 경고 — 새 버전이 올라갔는데 화면이 끝까지 안 그려진다 ($AFTER).
$(tail -5 /tmp/fermi_smoke.log)"
    fi
    rm -f /tmp/fermi_smoke.log
else
    echo "$(date '+%F %T') [skip] 데이터만 변경 — 재빌드 없이 반영됨"
fi
