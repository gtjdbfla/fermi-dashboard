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

BEFORE=$(git rev-parse HEAD)
git pull --quiet --ff-only || { echo "$(date '+%F %T') [fail] git pull 실패"; exit 1; }
AFTER=$(git rev-parse HEAD)

[ "$BEFORE" = "$AFTER" ] && exit 0

CHANGED=$(git diff --name-only "$BEFORE" "$AFTER")
echo "$(date '+%F %T') [pull] $BEFORE -> $AFTER"
echo "$CHANGED" | sed 's/^/    /'

if echo "$CHANGED" | grep -qE '\.py$|^Dockerfile$|^requirements\.txt$|^\.streamlit/|^docker-compose\.yml$|^Caddyfile$'; then
    echo "$(date '+%F %T') [build] 코드 변경 감지 — 재빌드"
    docker compose up -d --build

    # /_stcore/health는 스크립트를 실행하지 않아서, app.py 최상단이 깨져도 healthy로 뜬다.
    # 실제로 그 틈으로 NameError가 배포된 적이 있다. 화면을 끝까지 그려보고 확인한다.
    sleep 10
    if docker compose exec -T fermi-dashboard python smoke_test.py > /tmp/fermi_smoke.log 2>&1; then
        echo "$(date '+%F %T') [smoke] 렌더링 정상"
    else
        echo "$(date '+%F %T') [SMOKE FAIL] 화면이 렌더링되지 않는다 — 아래 로그 확인"
        tail -20 /tmp/fermi_smoke.log | sed 's/^/    /'
    fi
    rm -f /tmp/fermi_smoke.log
else
    echo "$(date '+%F %T') [skip] 데이터만 변경 — 재빌드 없이 반영됨"
fi
