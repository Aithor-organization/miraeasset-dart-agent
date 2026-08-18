# 공시 Agent — 평가용 API 서버 (SPEC §7-2)
#
# 🔴 인덱스는 이미지에 굽지 않는다.
#    - 코퍼스 5.3GB / BM25 캐시 수백MB → 이미지 비대화
#    - 인덱스 교체 시 이미지 재빌드가 필요해짐
#    → 볼륨 마운트로 주입한다 (아래 실행 예시 참조).

FROM python:3.11-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

# lxml 빌드에 필요한 헤더 (slim 이미지에 없음)
RUN apt-get update \
 && apt-get install -y --no-install-recommends build-essential libxml2-dev libxslt1-dev curl \
 && rm -rf /var/lib/apt/lists/*

# 의존성 레이어 분리 — 소스만 바뀔 때 재설치를 피한다
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY src/ ./src/
COPY scripts/ ./scripts/
COPY run_server.py pytest.ini ./

# 인덱스·코퍼스 마운트 지점
VOLUME ["/data"]
ENV DART_CORPUS_ROOT=/data/corpus \
    DART_DB_PATH=/data/index/dart.sqlite \
    DART_BM25_PATH=/data/index/bm25.pkl \
    HOST=0.0.0.0 \
    PORT=8000

EXPOSE 8000

# 평가기간 상시 가용을 위한 헬스체크 (SPEC §7-3)
HEALTHCHECK --interval=30s --timeout=5s --start-period=120s --retries=3 \
  CMD curl -fsS http://127.0.0.1:8000/health || exit 1

CMD ["python3", "run_server.py"]

# ── 사용법 ────────────────────────────────────────────────────────────────
# 1) 인덱스 빌드 (1회, 컨테이너 내부에서):
#    docker run --rm -v $PWD/data:/data dart-agent \
#      python3 scripts/build_index.py
#
# 2) 서버 실행 — 🔴 외부는 표준 포트 80으로 노출한다 (주최측 요구사항).
#    앱은 컨테이너 안에서 8000을 듣고, 매핑으로 80을 연다.
#    컨테이너 내부에서 80을 직접 바인딩하면 root 권한이 필요해지므로 이 방식이 맞다.
#
#    docker run -d --name dart-agent --restart always -p 80:8000 \
#      -v $PWD/data:/data -e CLOVA_API_KEY=nv-xxxx dart-agent
#
#    HTTPS로 갈 경우: -p 443:8000 + 앞단 TLS 종단 (self-signed 허용됨)
#
# 3) 확인 (외부에서 — 표준 포트라 포트 표기 생략):
#    curl -G http://<공인IP>/answer \
#      --data-urlencode "question_id=Q-001" \
#      --data-urlencode "question=삼성전자의 2024년 연결기준 매출액은?"
