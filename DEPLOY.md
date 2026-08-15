# 배포

저장소: https://github.com/gtjdbfla/fermi-dashboard (**공개**)

기존 `stock_dashboard`와 같은 서버에 **독립 스택**으로 돌린다. 포트가 겹치지 않아 동시 기동된다.

| | stock_dashboard | fermi-dashboard |
|---|---|---|
| 앱 포트 | 8501 | **8502** |
| Caddy 포트 | 127.0.0.1:8080 | **127.0.0.1:8081** |
| 컨테이너 | hynix-* | fermi-dashboard / fermi-caddy |

## 자동화 구조

```
주 1회 클라우드 루틴 ──▶ EDGAR 공시 판독 ──▶ data/*.csv 커밋 ──▶ GitHub
                                                                    │
                                    30분마다 서버 크론이 git pull ◀──┘
                                                │
                          data/는 볼륨 마운트라 재시작 없이 즉시 반영
```

CSV만 바뀌면 **재빌드도 재시작도 필요 없다** — `data/`가 볼륨으로 물려 있고 Streamlit 캐시 TTL이
10분이라 저절로 갱신된다. `.py`나 `Dockerfile`이 바뀐 경우에만 `deploy.sh`가 재빌드한다.

## 최초 설치

```bash
cd ~ && git clone https://github.com/gtjdbfla/fermi-dashboard.git && cd fermi-dashboard
```

`.env`를 만든다. **SEC는 연락 가능한 이메일이 담긴 User-Agent가 없으면 403으로 막아** 화면이 통째로
비어 보인다. 저장소가 공개라 이 값은 코드에 두지 않고 여기에만 둔다.

```bash
cp .env.example .env && nano .env
```

```bash
docker compose up -d --build
```

확인:

```bash
docker compose ps && curl -fsS http://localhost:8502/_stcore/health && curl -sI http://127.0.0.1:8081 | head -1
```

## 자동 갱신 크론

`deploy.sh`가 원격 변경을 받아오고, 코드가 바뀐 경우에만 재빌드한다.

```bash
crontab -e
```

```
*/30 * * * * /home/yulimseo/fermi-dashboard/deploy.sh >> /home/yulimseo/fermi-dashboard/logs/deploy.log 2>&1
```

## 외부 공개 (Tailscale Funnel)

기존 스택이 443을 쓰므로 페르미는 8443에 붙인다. Funnel이 쓸 수 있는 포트는 443/8443/10000뿐이다.

```bash
sudo tailscale funnel --bg --https=8443 http://127.0.0.1:8081
```

주소는 `https://<머신이름>.<테일넷>.ts.net:8443`. 되돌리려면 `sudo tailscale funnel --https=8443 off`.

**인증이 없다.** 비밀번호를 걸려면 `Caddyfile`의 `reverse_proxy` 위에 아래를 추가하고 `.env`에
`DASHBOARD_USER` / `DASHBOARD_PASSWORD_HASH`를 넣는다.

```
basic_auth { {$DASHBOARD_USER} {$DASHBOARD_PASSWORD_HASH} }
```

해시는 `docker run --rm caddy:2-alpine caddy hash-password --plaintext '비밀번호'`로 만든다.

## 수동 갱신

비교기업 표본을 다시 받으려면:

```bash
docker compose run --rm fermi-dashboard python refresh_sector.py
```

## 문제가 생기면

```bash
docker compose logs -f fermi-dashboard
```

- **숫자가 전부 비어 있다** → SEC 403이다. `.env`의 `SEC_USER_AGENT`를 확인한다. 자리표시자로 돌고
  있으면 화면 상단에 경고가 뜬다.
- **Caddy가 502** → 재빌드로 컨테이너 IP가 바뀐 경우다. `Caddyfile`의 `dynamic a`가 5초마다 다시
  찾으므로 잠시 기다린다. 계속되면 `docker compose restart caddy`.
- **크론이 안 돈다** → `logs/deploy.log`를 본다.
