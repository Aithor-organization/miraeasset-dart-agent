"""Orchestrator — 질의 이해 → 도구 라우팅 → 근거 조립 → 답변 → 검증 (SPEC §1, AC-0).

🔴 핵심 설계: **결정론 fast-path가 정답 생성 주체**다 (AC-0).
   LLM(HCX-007)은 서술 품질을 올리는 보강재이며, 키가 없어도 T1/T3/T4/T5는 정답이 나온다.
   수치는 fact 조회 결과를 그대로 문장에 넣고, 검증기가 근거 대조로 재확인한다 (D1).
"""

from __future__ import annotations

import re
import sqlite3
import time
from collections import OrderedDict
from dataclasses import dataclass, field

from .. import counters
from ..config import Config
from ..metrics import from_query
from ..models import CONSOLIDATED
from ..numbers import fmt_krw, josa
from ..retrieval.section_map import (parse_basis, parse_period, parse_scope,
                                     parse_years, paths_for)
from ..store import alias
from . import tools
from . import pii
from . import route_section
from .abstention import Abstention, decide
from .narrate import narrate
from .verifier import strip_failing_sentences, verify

# 질의 유형 (SPEC §3-3 T1~T6)
T_FACT, T_SECTION, T_COMPARE, T_EVENT, T_LIFECYCLE, T_TIMESERIES = (
    "T1_fact", "T2_section", "T3_compare", "T4_event", "T5_lifecycle", "T6_timeseries"
)

_CITE_ID = re.compile(r"\[(C\d+)\]")
_CMP = re.compile(r"더\s*(큰|많은|높은|작은|적은|낮은)|비교|중\s*어(디|느)|순위|가장\s*(큰|많은|높은)")
_DELTA = re.compile(r"증감|증가율|감소율|변화율|얼마나\s*(늘|줄|증가|감소)|대비")
_EVENT_FUND = re.compile(r"자금\s*조달|유상증자|전환사채|CB|BW|EB|신주인수권|교환사채|자기주식")
_EVENT_CONTRACT = re.compile(r"계약|수주|공급|해지|시설\s*투자")
_EVENT_HOLDING = re.compile(r"지분|보유\s*비율|대량\s*보유|최대주주|5%")
_LIFECYCLE = re.compile(r"이후\s*해지|해지된|변경\s*이력|정정|후속|취소된")
_TIMESERIES = re.compile(r"변화|추이|어떻게\s*(변|바뀌)|비교했을\s*때")
_SECTOR = re.compile(r"(2차전지|반도체|자동차|조선|방산|바이오|게임|건설|철강|통신|엔터|로봇|원전|전력기기|신재생|물류|유통|금융|플랫폼)")

# 질의 표현 → event_kind 필터. 지정하지 않으면 event_query가 전 유형을 반환해
# "자기주식 내역"에 무관한 상장/상장폐지 공시가 섞인다 (실측 결함).
_KIND_PATTERNS: tuple[tuple[str, str], ...] = (
    (r"유상증자", "유상증자"),
    (r"무상증자", "무상증자"),
    (r"전환사채|\bCB\b", "전환사채"),
    (r"신주인수권부사채|\bBW\b", "신주인수권부사채"),
    (r"교환사채|\bEB\b", "교환사채"),
    (r"조건부자본증권", "조건부자본증권"),
    (r"자기주식\s*취득", "자기주식취득"),
    (r"자기주식\s*처분", "자기주식처분"),
    (r"자기주식", "자기주식"),
    (r"합병", "합병"),
    (r"분할", "분할"),
    (r"감자", "감자"),
    (r"해지", "해지"),
    (r"체결|수주", "체결"),
    (r"신규\s*시설\s*투자|시설\s*투자", "신규시설투자"),
    (r"투자\s*판단", "투자판단관련"),
)


def _event_kinds(question: str) -> list[str]:
    """질의에서 이벤트 종류 필터를 추출한다. 없으면 빈 리스트(전 유형)."""
    out: list[str] = []
    for pat, kind in _KIND_PATTERNS:
        if re.search(pat, question) and kind not in out:
            out.append(kind)
    # '자기주식취득'과 '자기주식'이 함께 잡히면 구체적인 것만 남긴다
    if "자기주식취득" in out or "자기주식처분" in out:
        out = [k for k in out if k != "자기주식"]
    return out


@dataclass
class QuerySpec:
    question: str
    corp_codes: list[str] = field(default_factory=list)
    corp_names: list[str] = field(default_factory=list)
    sector: str | None = None
    years: list[int] = field(default_factory=list)
    year: int | None = None
    doc_subtype: str | None = None
    # 기간 범위 (FY/HYA/HYQ/QTA/QTQ). 누적↔당기를 가른다 — 함정 7.
    scope: str | None = None
    basis: str | None = None
    metric_key: str | None = None
    paths: list[str] = field(default_factory=list)
    # 목차 주소를 LLM이 골랐을 때의 사유 (think_trace 표시용). 규칙이 찾았으면 빈 문자열.
    path_route_why: str = ""
    qtype: str = T_FACT
    # 전체 요구사항 (think_trace 표시용)
    requirements: list[str] = field(default_factory=list)
    # V3가 답변 텍스트에서 토큰 매칭으로 **검증 가능한** 부분집합.
    # "존재 여부 판정" 같은 구조적 요구는 텍스트 매칭 대상이 아니므로 제외한다
    # (제외하지 않으면 V3가 항상 실패해 정상 답변이 폐기된다 — 실측 결함).
    verify_requirements: list[str] = field(default_factory=list)
    mentions_company: bool = False


@dataclass
class Answer:
    question_id: str
    question: str
    retrieved_context: str
    think_trace: str
    answer: str
    citations: list[dict] = field(default_factory=list)
    confidence: str = "medium"
    abstained: bool = False
    abstain_reason: str | None = None
    latency_ms: int = 0
    verify_summary: str = ""
    # 🔴 LLM 강등 여부를 **응답 필드로** 노출한다.
    #    think_trace에만 있으면 조용한 저하다 — 사용자도 지표도 모른다
    #    (AITHOR `lifecycle-operator`: "조용한 저하는 장애 은폐").
    degraded: bool = False
    degrade_reason: str = ""

    def to_payload(self) -> dict:
        """주최측 명시 5필드 + 부가 필드 (AC-API1)."""
        return {
            "question_id": self.question_id,
            "question": self.question,
            "retrieved_context": self.retrieved_context,
            "think_trace": self.think_trace,
            "answer": self.answer,
            "citations": self.citations,
            "confidence": self.confidence,
            "abstained": self.abstained,
            "abstain_reason": self.abstain_reason,
            "latency_ms": self.latency_ms,
            "verification": self.verify_summary,
            "degraded": self.degraded,
            "degrade_reason": self.degrade_reason,
        }


class Orchestrator:
    def __init__(self, conn: sqlite3.Connection, cfg: Config, index=None, llm=None,
                 vectors=None, embedder=None):
        self.conn = conn
        self.cfg = cfg
        self.index = index
        self.llm = llm
        # 하이브리드 검색 (파일럿) — 둘 다 있어야 doc_search가 RRF 융합한다
        self.vectors = vectors
        self.embedder = embedder
        self._cache: OrderedDict[tuple[str, str], tuple[float, Answer]] = OrderedDict()
        self._cache_ttl_s = 900.0
        self._cache_max = 2048

    # ── 질의 이해 ───────────────────────────────────────────────────────
    def understand(self, question: str, deadline: float | None = None) -> QuerySpec:
        q = QuerySpec(question=question)
        q.corp_codes = alias.find_in_text(self.conn, question)
        if q.corp_codes:
            rows = self.conn.execute(
                f"SELECT corp_code,corp_name FROM company WHERE corp_code IN "
                f"({','.join('?' * len(q.corp_codes))})", q.corp_codes
            ).fetchall()
            names = {r["corp_code"]: r["corp_name"] for r in rows}
            q.corp_names = [names.get(c, c) for c in q.corp_codes]

        sm = _SECTOR.search(question)
        if sm:
            full = self._match_sector(sm.group(1))
            if full:
                q.sector = full
                if not q.corp_codes:
                    q.corp_codes = alias.by_sector(self.conn, full)
                    q.corp_names = [
                        self.conn.execute(
                            "SELECT corp_name FROM company WHERE corp_code=?", (c,)
                        ).fetchone()["corp_name"] for c in q.corp_codes
                    ]

        q.mentions_company = bool(
            q.corp_codes or q.sector
            or re.search(r"기업|회사|사|㈜|주식회사", question)
        )
        q.years = parse_years(question)
        q.year, q.doc_subtype = parse_period(question)
        q.scope = parse_scope(question)  # 누적/당기 — 없으면 None (연간 우선 유지)
        q.basis = parse_basis(question)
        mdef = from_query(question)
        q.metric_key = mdef.key if mdef else None
        q.paths = paths_for(question)

        # 🔴 규칙이 목차 주소를 못 찾았을 때만 HCX에게 묻는다.
        #    규칙이 맞을 때 부르면 비용·429 위험만 늘고 정확도는 그대로다
        #    (실측: 규칙 매칭 시 section 21/21). 실패 시에는 규칙이 구조적으로
        #    약하다 — "배당"은 아예 규칙이 없었고, "사업**의** 개요"는 패턴이
        #    가운데 조사에 막혔다. 둘 다 semantic matching이라 LLM이 맞다.
        #    반환 주소는 route_section.CATALOG 화이트리스트로 걸러진다.
        q.path_route_why = ""
        if not q.paths and q.mentions_company and not q.metric_key:
            q.paths, q.path_route_why = route_section.route(
                self.llm, question, deadline=deadline)

        q.qtype = self._classify(question, q)
        q.requirements, q.verify_requirements = self._requirements(question, q)
        return q

    def _match_sector(self, token: str) -> str | None:
        for s in alias.all_sectors(self.conn):
            if token in s:
                return s
        return None

    @staticmethod
    def _classify(question: str, q: QuerySpec) -> str:
        if _LIFECYCLE.search(question):
            return T_LIFECYCLE
        if _EVENT_FUND.search(question) or (
            _EVENT_CONTRACT.search(question) and not q.metric_key
        ) or _EVENT_HOLDING.search(question):
            return T_EVENT
        if len(q.years) >= 2 and _TIMESERIES.search(question):
            return T_TIMESERIES
        if (len(q.corp_codes) >= 2 or q.sector) and (
            _CMP.search(question) or q.metric_key
        ):
            return T_COMPARE
        if _DELTA.search(question) and q.metric_key:
            return T_COMPARE
        if q.metric_key:
            return T_FACT
        if q.paths:
            return T_SECTION
        return T_FACT

    @staticmethod
    def _requirements(question: str, q: QuerySpec) -> tuple[list[str], list[str]]:
        """요구사항 분해 (평가지표 3). 반환: (전체, V3 검증 가능 부분집합)."""
        checkable: list[str] = []
        structural: list[str] = []
        if q.metric_key:
            from ..metrics import by_key
            m = by_key(q.metric_key)
            label = m.label if m else q.metric_key
            for y in (q.years or ([q.year] if q.year else [])):
                checkable.append(f"{y}년 {label}")
            if not checkable:
                checkable.append(label)
        if q.qtype == T_COMPARE and len(q.corp_names) >= 2:
            structural.append("비교 결과")
        if q.qtype == T_EVENT:
            structural.append("이벤트 유형별 내역")
        if q.qtype == T_LIFECYCLE:
            structural.append("존재 여부 판정")
        if q.qtype in (T_SECTION, T_TIMESERIES):
            structural.append("요청 항목 요약")
        allreq = checkable + structural
        return (allreq or ["질의 응답"]), checkable

    # ── 실행 ────────────────────────────────────────────────────────────
    def answer(self, question_id: str, question: str) -> Answer:
        # 🔴 외부 LLM 호출 전 경계: `understand()`의 목차 라우팅도 LLM을 호출할 수 있다.
        #    그러므로 PII 요청을 도구 조회·LLM 라우팅보다 먼저 차단한다. 이 요청은
        #    캐시에도 저장하지 않아 민감한 질문을 프로세스 메모리에 오래 남기지 않는다.
        if pii.is_sensitive_request(question):
            return Answer(
                question_id=question_id, question=question,
                retrieved_context="(민감 입력 보호를 위해 근거 본문을 표시하지 않습니다)",
                think_trace=("[1] 입력 경계 — 민감 요청 차단 (외부 모델·도구 미호출)\n"
                             "[5] 결론\n    민감정보 제외 + 회사 단위 정보 안내"),
                answer=pii.REFUSAL, citations=[], confidence="low",
                abstained=True,
                abstain_reason=("pii_request" if pii.is_pii_request(question)
                                else "sensitive_input"),
            )

        # 🔴 캐시 키는 (question_id, question) 쌍이다. question_id만 쓰면 평가측이
        #    같은 id로 다른 문항을 보낼 때 **이전 답변을 그대로 반환**한다 —
        #    한 번 어긋나면 이후 전 문항이 오답이 되는 실패 모드라 id 단독 키는 금지.
        key = (question_id, question.strip())
        cached = self._cache.get(key)
        if cached is not None and time.monotonic() - cached[0] < self._cache_ttl_s:
            self._cache.move_to_end(key)
            return cached[1]
        if cached is not None:
            self._cache.pop(key, None)
        t0 = time.time()
        # 🔴 LLM 예산 데드라인 (SPEC AC-API4 `REQUEST_TIMEOUT_S` 배선).
        #    이 상수는 SPEC에 규정돼 있었으나 **코드 어디서도 읽히지 않았다**
        #    (AITHOR `resilience-audit` 지적 → 전수 grep으로 확인).
        #    배선하지 않으면 재시도·페이싱이 누적돼 최악 지연이 질의당 680초까지
        #    간다 — 평가 타임아웃 300초를 넘겨 **정확도가 아니라 타임아웃으로 0점**이다.
        #    결정론 경로는 지연 중앙값 0.00초이므로, 이 예산은 사실상 LLM 전용이고
        #    소진되면 템플릿으로 강등된다(정확도 손실 0 — LLM 차단 시에도 177/177).
        deadline = time.monotonic() + self.cfg.request_timeout_s
        try:
            ans = self._run(question_id, question, deadline)
        except Exception as exc:  # AC-API2: 500 금지
            ans = Answer(
                question_id=question_id, question=question,
                retrieved_context="(처리 중 오류)",
                think_trace=f"[오류] {type(exc).__name__}: {exc}",
                answer="요청을 처리하는 중 오류가 발생해 답변을 생성하지 못했습니다.",
                abstained=True, abstain_reason="internal_error", confidence="low",
            )
        ans.latency_ms = int((time.time() - t0) * 1000)
        self._cache[key] = (time.monotonic(), ans)
        self._cache.move_to_end(key)
        while len(self._cache) > self._cache_max:
            self._cache.popitem(last=False)
        return ans

    def _run(self, question_id: str, question: str,
             deadline: float | None = None) -> Answer:
        q = self.understand(question, deadline)
        trace: list[str] = [self._trace_understand(q)]

        facts: list[tools.FactHit] = []
        events: list[tools.EventHit] = []
        sections: list[dict] = []
        search_hits = []
        comp: tools.ComputeResult | None = None
        chain: tools.ChainResult | None = None

        plan: list[str] = []
        years = q.years or ([q.year] if q.year else [])

        # 도구 실행 (주소 지정 → 사실 조회 → 검색 순서, D2)
        if q.qtype in (T_FACT, T_COMPARE, T_TIMESERIES) and q.metric_key and q.corp_codes:
            plan.append(
                f"fact_query(corp={len(q.corp_codes)}, metric={q.metric_key}, "
                f"fy={years}{', scope=' + q.scope if q.scope else ''})"
            )
            facts = tools.fact_query(
                self.conn, corp=q.corp_codes, metric=q.metric_key,
                fy=years or [2025, 2024, 2023],
                basis=q.basis or (CONSOLIDATED if q.qtype != T_SECTION else None),
                scope=q.scope,
            )
            # 🔴 지정한 기간 범위에 값이 없으면 **범위를 풀지 않는다.**
            #    "상반기 매출"에 연간 값을 주는 것은 오답이지 폴백이 아니다.
            #    빈 결과는 아래 기권 판정으로 흘러가 "확인되지 않음"이 된다.
        if q.qtype == T_EVENT and q.corp_codes:
            domain = ("capital" if _EVENT_FUND.search(question)
                      else "holding" if _EVENT_HOLDING.search(question) else "contract")
            kinds = _event_kinds(question)
            plan.append(f"event_query(domain={domain}, kinds={kinds or '전체'}, year={q.year})")
            events = tools.event_query(
                self.conn, corp=q.corp_codes, domain=domain,
                kinds=kinds or None, year=q.year,
            )
        if q.qtype == T_LIFECYCLE and q.corp_codes:
            plan.append("trace_chain(contract_lifecycle)")
            chain = tools.trace_chain(
                self.conn, mode="contract_lifecycle",
                corp_code=q.corp_codes[0], year=q.year
            )
        if q.paths and q.corp_codes:
            plan.append(f"get_section(paths={q.paths}, year={q.year})")
            sections = tools.get_section(
                self.conn, corp=q.corp_codes, paths=q.paths,
                year=q.year, doc_subtype=q.doc_subtype,
            )
        if not facts and not events and not sections and not (chain and chain.nodes):
            hybrid_on = self.vectors is not None and self.embedder is not None
            plan.append("doc_search(BM25+vec RRF)" if hybrid_on
                        else "doc_search(hybrid BM25)")
            search_hits = tools.doc_search(
                self.index, question, corp=q.corp_codes or None,
                years=years or None, top_k=self.cfg.top_k,
                vectors=self.vectors, embedder=self.embedder, conn=self.conn,
                rrf_k=self.cfg.hybrid_rrf_k, vec_weight=self.cfg.hybrid_vec_weight,
            )

        # 연산
        if q.qtype == T_COMPARE and len(facts) >= 2:
            op = "delta_pct" if (_DELTA.search(question) and len({f.corp_code for f in facts}) == 1) \
                else "compare"
            ordered = (sorted(facts, key=lambda f: -f.fy) if op == "delta_pct" else facts)
            plan.append(f"compute({op})")
            comp = tools.compute(op, ordered[:2] if op == "delta_pct" else ordered)

        trace.append("[2] 계획\n    " + ("\n    ".join(plan) or "(도구 없음)"))
        top_score = max((h.score for h in search_hits), default=0.0)
        trace.append(self._trace_exec(facts, events, sections, search_hits, comp, chain))

        # Abstention 판정
        available = [
            f"{f.corp_name} {f.fy}년 {f.label_clean} {fmt_krw(f.value_krw)}" for f in facts[:3]
        ]
        ab = decide(
            question=question, corp_codes=q.corp_codes, years=years,
            metric_key=q.metric_key,
            has_facts=bool(facts or events or sections or (chain and chain.nodes)),
            top_search_score=top_score, threshold=self.cfg.search_score_threshold,
            unit_low_confidence=any(f.unit_confidence != "high" for f in facts),
            is_comparison=(q.qtype == T_COMPARE), mentions_company=q.mentions_company,
            available_facts=available,
        )
        if comp is not None and not comp.ok and q.qtype == T_COMPARE and ab is None:
            ab = Abstention("low_unit_confidence", comp.refused_reason or "연산 불가",
                            available_facts=available)

        ctx, cites = self._build_context(facts, events, sections, search_hits, chain)

        if ab is not None:
            trace.append(f"[4] Abstention 발동 — {ab.reason}")
            trace.append("[5] 결론\n    한계 고지 + 확인 가능 사실 제시")
            ab_body = ab.render()
            return Answer(
                question_id=question_id, question=question,
                retrieved_context=ctx or "(검색 결과 없음)",
                think_trace="\n".join(trace), answer=ab_body,
                citations=self._cited_only(ab_body, cites),
                confidence="low", abstained=True, abstain_reason=ab.reason,
            )

        body = self._compose(q, facts, events, sections, search_hits, comp, chain, cites)

        # 🔴 서술 계층 — 결정론 답변을 HCX가 다듬는다 (D1의 뒷 절반).
        #    검증 **직전**에 두는 것이 핵심이다. 아래 V1~V5가 LLM 출력도 그대로 검사하므로,
        #    LLM이 수치를 흘리면 여기서 잡히고 문장이 폐기된다.
        #    narrate() 자체도 수치 동일성을 보고 어긋나면 템플릿을 되돌린다 (2중 방어).
        #    (기권 경로는 위에서 이미 return했으므로 여기는 답변이 있는 경우뿐이다)
        body, narr_why = narrate(self.llm, body, question=question, deadline=deadline)
        trace.append(f"[3-b] 서술 — {narr_why}")
        # LLM이 붙지 못한 경우를 응답과 카운터 양쪽에 남긴다.
        degraded = "LLM 서술 적용" not in narr_why and "stub" not in narr_why
        if degraded:
            counters.bump("llm_degraded")

        # 검증 (V1~V5, 결정론)
        # 🔴 grounded에는 원문값뿐 아니라 **그 값에서 결정론적으로 파생된 표기**도 넣는다.
        #    fmt_krw("300,870,903백만원") → "300.9조원"은 우리가 계산한 값이므로 근거가 있다.
        #    이걸 빼면 V1이 자기 시스템의 정당한 파생값을 환각으로 오판한다 (실측 결함).
        grounded: set[str] = set()
        for f in facts:
            grounded.add(f.raw_value)
            if f.value_krw is not None:
                grounded.add(str(f.value_krw))
                grounded.add(fmt_krw(f.value_krw))
        by_kind_amt: dict[str, list[int]] = {}
        for e in events:
            if e.amount_krw is not None:
                grounded.add(str(e.amount_krw))
                grounded.add(fmt_krw(e.amount_krw))
                by_kind_amt.setdefault(e.event_kind, []).append(e.amount_krw)
        # 유형별 합계도 우리가 더한 값이라 근거가 있다 — 넣지 않으면 V1이
        # 자기 시스템의 합계를 환각으로 오판해 정상 답변을 폐기한다.
        for amts in by_kind_amt.values():
            grounded.add(str(sum(amts)))
            grounded.add(f"{sum(amts):,}")
            grounded.add(fmt_krw(sum(amts)))
        if comp and comp.ok and comp.value is not None:
            grounded.add(f"{abs(comp.value):.1f}")
            grounded.add(f"{comp.value:.1f}")
            grounded.add(comp.detail)
            if comp.unit == "원":
                grounded.add(fmt_krw(int(comp.value)))
        # 이벤트 건수 집계도 결정론 파생값
        grounded.add(str(len(events)))
        if chain and chain.linked:
            grounded.add(str(len(chain.linked)))
        rep = verify(
            body, context=ctx, citation_ids={c["id"] for c in cites},
            requirements=q.verify_requirements, grounded_values=grounded,
        )
        if not rep.ok:
            cleaned = strip_failing_sentences(body, rep)
            rep2 = verify(cleaned, context=ctx, citation_ids={c["id"] for c in cites},
                          requirements=q.verify_requirements, grounded_values=grounded)
            trace.append(f"[4] 검증 재시도 — 1차: {rep.summary()}")
            body, rep = (cleaned or body), rep2
        trace.append(f"[4] 검증 — {rep.summary()}")
        trace.append("[5] 결론\n    " + body.split("\n")[0][:120])

        return Answer(
            question_id=question_id, question=question,
            retrieved_context=ctx or "(검색 결과 없음)",
            think_trace="\n".join(trace), answer=body,
            citations=self._cited_only(body, cites),
            # 🔴 강등이면 confidence를 한 단계 낮춘다 — 같은 "high"로 두면
            #    응답만 보고는 LLM이 죽었는지 알 수 없다.
            confidence=(("high" if (facts and rep.ok) else "medium" if rep.ok else "low")
                        if not degraded else ("medium" if rep.ok else "low")),
            verify_summary=rep.summary(),
            degraded=degraded, degrade_reason=(narr_why if degraded else ""),
        )

    # ── trace / context / compose ───────────────────────────────────────
    def _trace_understand(self, q: QuerySpec) -> str:
        return (
            "[1] 질의 해석\n"
            f"    기업: {', '.join(q.corp_names) or '(미특정)'}"
            f"{f' [섹터 {q.sector}]' if q.sector else ''}\n"
            f"    지표: {q.metric_key or '(없음)'} · 기간: {q.years or q.year or '(미특정)'}"
            f" · 기준: {q.basis or '연결(기본)'}\n"
            f"    유형: {q.qtype} · 섹션주소: {q.paths or '(없음)'}"
            f"{f' ({q.path_route_why})' if q.path_route_why else ''}\n"
            f"    요구사항: {'; '.join(q.requirements)}"
        )

    @staticmethod
    def _trace_exec(facts, events, sections, hits, comp, chain) -> str:
        lines = ["[3] 도구 실행"]
        for f in facts[:6]:
            lines.append(
                f"    fact#{f.fact_id} {f.corp_name} FY{f.fy} {f.label_ko}="
                f"{f.raw_value}{f.raw_unit or ''} → {fmt_krw(f.value_krw)} "
                f"[{f.source}/{f.src_section or '-'}]"
            )
        for e in events[:6]:
            lines.append(f"    event {e.corp_name} {e.event_kind} {fmt_krw(e.amount_krw)} {e.dt}")
        for s in sections[:4]:
            lines.append(f"    section {s['corp_name']} {s['path']} {s['title'][:24]} ({len(s['text'])}자)")
        for h in hits[:5]:
            lines.append(f"    search {h.doc.corp_code} {h.doc.path} {h.doc.title[:22]} score={h.score:.3f}")
        if chain and chain.nodes:
            lines.append(f"    chain nodes={len(chain.nodes)} linked={len(chain.linked)}")
        if comp:
            lines.append(f"    compute({comp.op}) ok={comp.ok} {comp.detail or comp.refused_reason}")
        if len(lines) == 1:
            lines.append("    (결과 없음)")
        return "\n".join(lines)

    @staticmethod
    def _cited_only(answer: str, cites: list[dict]) -> list[dict]:
        """답변이 **실제로 인용한** 근거만 남긴다.

        🔴 실측 결함: "투자 의견은 제공하지 않습니다"라고 기권하면서 근거 6건을
           달고 있었다 (`[C1] III-7-1` 등). 답변이 참조하지 않는 인용은 근거가
           아니라 노이즈이고, "근거 없음"이라 말하면서 근거를 붙이는 건 자기모순이다.

        평가지표 2(근거 완전성)는 **답변과 근거의 대응**을 보는 것이지 검색 결과를
        많이 붙이는 걸 보는 게 아니다. 검색된 원문 전체는 `retrieved_context`에
        그대로 남으므로 정보가 사라지지도 않는다.
        """
        used = set(_CITE_ID.findall(answer))
        return [c for c in cites if c.get("id") in used]

    def _build_context(self, facts, events, sections, hits, chain):
        """retrieved_context — 평가지표 2(근거 완전성) 채점 대상 산출물 (D4)."""
        parts: list[str] = []
        cites: list[dict] = []
        n = 0
        for f in facts:
            n += 1
            cid = f"C{n}"
            parts.append(f"[{cid}] {f.citation_text()}")
            cites.append({
                "id": cid, "doc_id": f.doc_id, "corp_name": f.corp_name,
                "report_nm": f.report_nm, "rcept_no": f.rcept_no,
                "section": f.src_section, "source": f.source, "fact_id": f.fact_id,
            })
        for e in events[:20]:
            n += 1
            cid = f"C{n}"
            parts.append(f"[{cid}] {e.citation_text()}")
            cites.append({
                "id": cid, "doc_id": e.doc_id, "corp_name": e.corp_name,
                "report_nm": e.report_nm, "rcept_no": e.rcept_no, "source": "event",
            })
        for s in sections[:6]:
            n += 1
            cid = f"C{n}"
            # 🔴 근거 본문도 마스킹한다. 답변만 가리고 `retrieved_context`를
            #    평문으로 두면 개인정보가 그대로 나간다 — 채점 대상 산출물이다.
            #    주민번호·이메일·전화는 전 섹션, 생년월일은 PII 섹션 한정
            #    (`_BIRTH`가 회계기간 "2024년 12월"과 형태가 겹친다).
            body_txt = pii.mask_always(s["text"][:1200])
            if pii.is_pii_section(s.get("path"), s.get("title")):
                body_txt = pii.mask(body_txt)
            parts.append(
                f"[{cid}] {s['corp_name']} {s['report_nm']} · 접수번호 {s['rcept_no']} · "
                f"{s['path']} {s['title']}\n      {body_txt}"
            )
            cites.append({
                "id": cid, "doc_id": s["doc_id"], "corp_name": s["corp_name"],
                "report_nm": s["report_nm"], "rcept_no": s["rcept_no"],
                "section": f"{s['path']} {s['title']}", "source": "section",
            })
        for h in hits[:6]:
            n += 1
            cid = f"C{n}"
            hit_txt = pii.mask_always(h.doc.text[:600])
            if pii.is_pii_section(h.doc.path, h.doc.title):
                hit_txt = pii.mask(hit_txt)
            parts.append(
                f"[{cid}] {h.doc.path} {h.doc.title} (score={h.score:.3f})\n"
                f"      {hit_txt}"
            )
            cites.append({
                "id": cid, "doc_id": h.doc.doc_id, "corp_name": "",
                "report_nm": "", "rcept_no": "", "section": h.doc.path, "source": "search",
            })
        if chain and chain.linked:
            n += 1
            cid = f"C{n}"
            desc = []
            for l in chain.linked[:6]:
                t = l["terminated"]
                desc.append(
                    f"해지 {t.get('rcept_no')} {t.get('counterparty') or ''} "
                    f"{fmt_krw(t.get('amount_krw'))}"
                )
            parts.append(f"[{cid}] 계약 생애주기 링크\n      " + "\n      ".join(desc))
            cites.append({"id": cid, "doc_id": "", "corp_name": "", "report_nm": "",
                          "rcept_no": "", "section": "contract_lifecycle", "source": "chain"})
        return "\n".join(parts), cites

    def _compose(self, q, facts, events, sections, hits, comp, chain, cites) -> str:
        """결정론 문장 조립. 수치는 fact 값 그대로 넣는다 (D1)."""
        cid_of = {c.get("fact_id"): c["id"] for c in cites if c.get("fact_id")}
        lines: list[str] = []

        if q.qtype == T_COMPARE and comp and comp.ok:
            if comp.op == "compare":
                top = comp.operands[0]
                parts = [
                    "{} {}년 {} {} [{}]".format(
                        o.corp_name, o.fy, o.label_clean, fmt_krw(o.value_krw),
                        cid_of.get(o.fact_id, "C1"),
                    )
                    for o in comp.operands
                ]
                lines.append(f"{top.corp_name}{josa(top.corp_name)} 더 큽니다. " + ", ".join(parts) + ".")
            else:
                a, b = comp.operands[0], comp.operands[1]
                lines.append(
                    f"{a.corp_name}의 {a.label_clean}은 {b.fy}년 {fmt_krw(b.value_krw)} "
                    f"[{cid_of.get(b.fact_id, 'C2')}]에서 {a.fy}년 {fmt_krw(a.value_krw)} "
                    f"[{cid_of.get(a.fact_id, 'C1')}]으로 {comp.detail}했습니다."
                )
        elif facts:
            for f in facts[:6]:
                basis_ko = "연결기준" if f.basis == CONSOLIDATED else "별도기준"
                lines.append(
                    f"{f.corp_name}의 {f.fy}년 {basis_ko} {f.label_clean}은 "
                    f"{f.raw_value}{f.raw_unit or ''}"
                    f"({fmt_krw(f.value_krw)})입니다 [{cid_of.get(f.fact_id, 'C1')}]."
                )

        if events:
            # 🔴 이벤트 줄에도 인용을 단다. 없으면 수치를 제시하면서 근거가 0건이 된다
            #    (실측: `_cited_only` 도입 후 계약금액 답변의 근거가 전부 사라졌다).
            #    `_build_context`가 `events[:20]`을 그 순서대로 넣으므로 위치로 대응된다.
            ev_cids = [c["id"] for c in cites if c.get("source") == "event"]
            cid_at = {id(e): ev_cids[i] for i, e in enumerate(events[:len(ev_cids)])}

            by_kind: dict[str, list] = {}
            for e in events:
                by_kind.setdefault(e.event_kind, []).append(e)
            lines.append(f"{q.corp_names[0] if q.corp_names else ''} 관련 공시 {len(events)}건을 유형별로 정리하면 다음과 같습니다.")
            for kind, group in list(by_kind.items())[:8]:
                amts = [g.amount_krw for g in group if g.amount_krw]
                # 🔴 "계약금액은 얼마인가"에 `1.3조원`만 답하면 정확성 채점에서 떨어진다.
                #    반올림 표기는 읽기용이고, **정확값이 정본**이다 (D1). 둘 다 싣는다.
                #    실측: 이 한 줄이 없어 골드셋 마지막 실패 1건이 남아 있었다
                #    (기대 1,316,166,122,950 · 답변 "1.3조원" → 0.5% 허용치 밖).
                total = f" 합계 {sum(amts):,}원({fmt_krw(sum(amts))})" if amts else ""
                marks = "".join(f"[{c}]" for c in
                                dict.fromkeys(cid_at[id(g)] for g in group if id(g) in cid_at))
                lines.append(f"- {kind}: {len(group)}건{total} {marks}".rstrip())

        if chain and chain.nodes:
            term = [l for l in chain.linked]
            if term:
                lines.append(f"해지 공시가 {len(term)}건 확인됩니다.")
                for l in term[:5]:
                    t = l["terminated"]
                    lines.append(
                        f"- {t.get('decision_dt') or ''} {t.get('counterparty') or ''} "
                        f"{fmt_krw(t.get('amount_krw'))} (접수번호 {t.get('rcept_no')})"
                    )
            else:
                lines.append("해당 기간에 체결 이후 해지된 계약 공시는 확인되지 않습니다.")

        if sections and not facts:
            # 🔴 같은 회사·같은 목차를 보고서만 바꿔 3번 반복하지 않는다.
            #    실측: "배당에 관한 사항" 질의에 분기보고서·[기재정정]분기보고서·
            #    사업보고서의 **거의 동일한 원문**이 연달아 출력됐다. 정보량은 1개인데
            #    분량은 3배라 읽는 사람에게는 손해다.
            #    조회가 `base_year DESC, base_month DESC` 정렬이므로 **첫 항목이 최신**이다.
            seen: set[tuple] = set()
            for s in sections:
                key = (s.get("corp_name"), s.get("path"))
                if key in seen:
                    continue
                seen.add(key)
                cid = next((c["id"] for c in cites if c["doc_id"] == s["doc_id"]
                            and c.get("section", "").startswith(s["path"])), "C1")
                snippet = re.sub(r"\s+", " ", s["text"])[:400]
                if pii.is_pii_section(s.get("path"), s.get("title")):
                    snippet = pii.mask(snippet)
                lines.append(f"{s['corp_name']} {s['report_nm']} {s['title']}: {snippet} [{cid}]")
                if len(seen) >= 3:
                    break

        if not lines and hits:
            lines.append(
                "질의와 관련된 공시 구간을 검색했습니다. 아래 근거를 참고해 주세요 "
                f"[{cites[0]['id'] if cites else 'C1'}]."
            )
        if not lines:
            lines.append("제공된 공시 데이터에서 해당 내용을 확인할 수 없습니다.")
        return "\n".join(lines)
