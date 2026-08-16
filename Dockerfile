FROM python:3.11-slim

ENV TZ=Asia/Seoul
# 차트 글자는 Plotly가 브라우저에서 그리므로 컨테이너에 한글 폰트는 필요 없다.
# 공시 기준일 표기가 한국 시간으로 나오게 tzdata만 넣는다.
RUN apt-get update && apt-get install -y --no-install-recommends tzdata curl && \
    ln -snf /usr/share/zoneinfo/$TZ /etc/localtime && echo $TZ > /etc/timezone && \
    rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY app.py sec_edgar.py fundamentals.py market.py market_flow.py sector.py roadmap.py \
     news.py ai_review.py theme.py refresh_sector.py smoke_test.py ./
COPY .streamlit/ .streamlit/
# data/는 compose에서 볼륨으로 덮어쓰지만, 볼륨 없이 단독 실행해도 동작하도록 이미지에도 넣는다.
COPY data/ data/

EXPOSE 8502

# SEC는 연락처가 담긴 User-Agent가 없으면 403으로 막는다. compose의 .env에서 주입한다.
ENV SEC_USER_AGENT="fermi-dashboard/1.0 (set-me@example.com)"

HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
    CMD curl -fsS http://localhost:8502/_stcore/health || exit 1

CMD ["streamlit", "run", "app.py"]
