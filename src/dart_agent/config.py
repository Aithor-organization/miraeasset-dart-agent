"""설정. 환경변수 우선, 없으면 안전한 기본값 (SPEC §6 AC-AB3, §7 AC-API4)."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

_HERE = Path(__file__).resolve()
PROJECT_ROOT = _HERE.parents[2]  # src/dart_agent/config.py → 프로젝트 루트

DEFAULT_CORPUS = PROJECT_ROOT / "docs" / "3.공시" / "corpus"
DEFAULT_DB = PROJECT_ROOT / "index" / "dart.sqlite"
DEFAULT_BM25 = PROJECT_ROOT / "index" / "bm25.pkl"

# 보유 코퍼스 기간 — abstention out_of_period 판정 기준 (실측: 2023-01-01~2026-03-31)
CORPUS_START = "20230101"
CORPUS_END = "20260331"


def _env_path(name: str, default: Path) -> Path:
    v = os.environ.get(name)
    return Path(v).expanduser() if v else default


def _env_float(name: str, default: float) -> float:
    try:
        return float(os.environ[name])
    except (KeyError, ValueError):
        return default


def _env_int(name: str, default: int) -> int:
    try:
        return int(os.environ[name])
    except (KeyError, ValueError):
        return default


@dataclass(frozen=True)
class Config:
    corpus_root: Path
    db_path: Path
    bm25_path: Path
    # abstention: 검색 최고점이 이 값 미달이면 근거 부족 판정 (AC-AB3)
    search_score_threshold: float
    request_timeout_s: int
    # CLOVA
    clova_api_key: str | None
    clova_base_url: str
    chat_model: str
    embedding_model: str
    # 검색
    bm25_k1: float
    bm25_b: float
    rrf_k: int
    top_k: int
    # 하이브리드 (파일럿 — 기본 OFF. 벡터 스토어 + CLOVA 키 둘 다 있어야 발동)
    hybrid_search: bool
    vectors_path: Path
    # 🔴 하이브리드 융합 파라미터는 rrf_k(=60, 레거시)와 **별개**다.
    #    실측으로 정한 값이라 같이 묶으면 한쪽을 고칠 때 다른 쪽이 깨진다.
    hybrid_rrf_k: int
    hybrid_vec_weight: float

    @property
    def has_llm(self) -> bool:
        """키가 없으면 StubProvider로 폴백한다 (AC-L2). 서버는 계속 동작한다."""
        return bool(self.clova_api_key)


def load_config() -> Config:
    return Config(
        corpus_root=_env_path("DART_CORPUS_ROOT", DEFAULT_CORPUS),
        db_path=_env_path("DART_DB_PATH", DEFAULT_DB),
        bm25_path=_env_path("DART_BM25_PATH", DEFAULT_BM25),
        search_score_threshold=_env_float("DART_SEARCH_THRESHOLD", 0.35),
        # 🔴 LLM 예산 (SPEC AC-API4). 25 → 120으로 상향 (2026-08-19).
        #
        #    이 상수는 SPEC에 있었지만 **코드 어디서도 읽히지 않았다** — 배선하며
        #    값도 실측에 맞췄다. 25초는 정상 서술을 자른다:
        #      골든셋 실측 지연 — 중앙 10.5s · p95 27.1s · 최대 65.4s
        #    120초면 관측된 성공을 모두 보존하면서 평가 타임아웃 300초의 40%에
        #    머문다. 예산이 끝나면 템플릿으로 강등되므로 **정확도 손실은 0**이다
        #    (LLM 전면 차단에서도 골든셋 177/177).
        request_timeout_s=_env_int("REQUEST_TIMEOUT_S", 120),
        clova_api_key=os.environ.get("CLOVA_API_KEY") or None,
        clova_base_url=os.environ.get(
            "CLOVA_BASE_URL", "https://clovastudio.stream.ntruss.com/v1/openai"
        ),
        chat_model=os.environ.get("CLOVA_CHAT_MODEL", "HCX-007"),
        embedding_model=os.environ.get("CLOVA_EMBEDDING_MODEL", "bge-m3"),
        bm25_k1=_env_float("BM25_K1", 1.2),
        bm25_b=_env_float("BM25_B", 0.75),
        rrf_k=_env_int("RRF_K", 60),
        top_k=_env_int("SEARCH_TOP_K", 8),
        # 🔴 기본 ON (2026-08-25). 골든셋 186/186 무회귀 + 검색 폴백 품질 개선을
        #    실측한 뒤 전환했다. 벡터 스토어나 CLOVA 키가 없으면 서버가 자동으로
        #    BM25 단독으로 강등하므로(`api/server.py`), 켜두는 쪽이 안전하다 —
        #    끄고 싶으면 `DART_HYBRID=0`. 실제 활성 여부는 `/ready`의 notes가 말한다.
        hybrid_search=os.environ.get("DART_HYBRID", "1").lower() not in ("0", "false", "off"),
        vectors_path=_env_path(
            "DART_VECTORS_PATH", PROJECT_ROOT / "index" / "embeddings.sqlite"
        ),
        # 🔴 홀드아웃(튜닝 미사용 30문항) 기준으로 고른 값 — 상세는 rrf_fuse docstring.
        #    골든셋만 보면 k=10/w=2가 더 좋지만 그건 튜닝셋이라 낙관 편향이 있다.
        hybrid_rrf_k=_env_int("HYBRID_RRF_K", 5),
        hybrid_vec_weight=_env_float("HYBRID_VEC_WEIGHT", 3.0),
    )


CONFIG = load_config()
