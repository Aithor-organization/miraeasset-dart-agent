-- 공시 Agent Fact Store (SPEC §2-1)
-- 설계 원칙 D1: 수치는 LLM이 생성하지 않고 여기서 조회된다.
PRAGMA journal_mode = WAL;
PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS company (
  corp_code     TEXT PRIMARY KEY,   -- 8자리 문자열 (선행 0 보존)
  corp_name     TEXT NOT NULL,      -- DART 공식 법인명 = raw/ 폴더명 (조인 키)
  listed_name   TEXT,               -- 거래소 통용명 (현대차, KT, NC …)
  corp_eng_name TEXT,
  stock_code    TEXT,               -- 6자리 문자열
  market        TEXT,
  industry      TEXT,
  sector        TEXT,
  listing_date  TEXT,
  market_cap    INTEGER
);
CREATE INDEX IF NOT EXISTS ix_company_sector ON company(sector);

-- 별칭 → corp_code 는 1:1 (AC-A2). 충돌 시 빌드 실패시킨다.
CREATE TABLE IF NOT EXISTS company_alias (
  alias_norm TEXT PRIMARY KEY,      -- 정규화 키 (공백·(주)·주식회사 제거, lower)
  alias_raw  TEXT NOT NULL,
  corp_code  TEXT NOT NULL REFERENCES company(corp_code),
  alias_kind TEXT NOT NULL          -- corp_name|listed_name|eng|stock_code|manual
);

CREATE TABLE IF NOT EXISTS document (
  doc_id            TEXT PRIMARY KEY,
  corp_code         TEXT NOT NULL REFERENCES company(corp_code),
  doc_group         TEXT NOT NULL,
  doc_subtype       TEXT,
  report_nm         TEXT NOT NULL,
  rcept_no          TEXT NOT NULL,
  rcept_dt          TEXT NOT NULL,
  flr_nm            TEXT,
  is_correction     INTEGER NOT NULL DEFAULT 0,
  base_year         INTEGER,
  base_month        INTEGER,
  file_path         TEXT NOT NULL,
  file_format       TEXT,
  supersedes_doc_id TEXT,            -- 이 문서가 정정하는 원본 (AC-C1)
  is_effective      INTEGER NOT NULL DEFAULT 1,  -- 체인 종단 = 집계 대상
  parse_warnings    INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS ix_doc_corp   ON document(corp_code, doc_group, base_year);
CREATE INDEX IF NOT EXISTS ix_doc_dt     ON document(rcept_dt);
CREATE INDEX IF NOT EXISTS ix_doc_eff    ON document(is_effective);
CREATE INDEX IF NOT EXISTS ix_doc_super  ON document(supersedes_doc_id);

-- 재무 사실. value_krw 는 항상 '원' 단위 (AC-U1). unit_confidence='low' 는 비교 제외 (AC-U2).
CREATE TABLE IF NOT EXISTS fin_fact (
  fact_id         INTEGER PRIMARY KEY AUTOINCREMENT,
  doc_id          TEXT NOT NULL REFERENCES document(doc_id),
  corp_code       TEXT NOT NULL,
  acode           TEXT,
  label_ko        TEXT NOT NULL,
  metric_key      TEXT,
  fy              INTEGER NOT NULL,
  period_kind     TEXT NOT NULL,     -- instant|duration
  period_scope    TEXT,              -- FY|HYA|HYQ|QTA|QTQ  🔴 누적↔당기 혼합 방지 필터
  basis           TEXT NOT NULL,     -- consolidated|separate
  axis            TEXT,
  value_krw       INTEGER,
  raw_value       TEXT NOT NULL,
  raw_unit        TEXT,
  unit_confidence TEXT NOT NULL,     -- high|low
  source          TEXT NOT NULL,     -- xbrl|table
  src_section     TEXT,
  UNIQUE(doc_id, acode, label_ko, fy, period_kind, period_scope, basis, axis)
);
-- 핵심 조회 경로: (기업, 지표, 연도, 연결여부)
CREATE INDEX IF NOT EXISTS ix_fact_lookup ON fin_fact(corp_code, metric_key, fy, basis);
CREATE INDEX IF NOT EXISTS ix_fact_doc    ON fin_fact(doc_id);

CREATE TABLE IF NOT EXISTS section (
  section_id    TEXT PRIMARY KEY,    -- {doc_id}#III-2-2
  doc_id        TEXT NOT NULL REFERENCES document(doc_id),
  corp_code     TEXT NOT NULL,
  path          TEXT NOT NULL,       -- 법정 목차 주소 (D2)
  title         TEXT NOT NULL,
  level         INTEGER NOT NULL,
  text          TEXT NOT NULL,
  tables_md     TEXT NOT NULL DEFAULT '',
  char_len      INTEGER NOT NULL,
  content_class TEXT NOT NULL        -- prose|table_registry|financial_stmt
);
CREATE INDEX IF NOT EXISTS ix_section_addr  ON section(corp_code, path);
CREATE INDEX IF NOT EXISTS ix_section_doc   ON section(doc_id, path);
CREATE INDEX IF NOT EXISTS ix_section_class ON section(content_class);

CREATE TABLE IF NOT EXISTS contract_event (
  doc_id             TEXT PRIMARY KEY REFERENCES document(doc_id),
  corp_code          TEXT NOT NULL,
  event_kind         TEXT NOT NULL,  -- 체결|해지|신규시설투자|투자판단관련
  contract_kind      TEXT,
  detail             TEXT,
  counterparty       TEXT,
  amount_krw         INTEGER,
  recent_revenue_krw INTEGER,
  ratio_pct          REAL,
  start_dt           TEXT,
  end_dt             TEXT,
  decision_dt        TEXT
);
CREATE INDEX IF NOT EXISTS ix_contract_corp ON contract_event(corp_code, event_kind, decision_dt);

CREATE TABLE IF NOT EXISTS capital_event (
  doc_id      TEXT PRIMARY KEY REFERENCES document(doc_id),
  corp_code   TEXT NOT NULL,
  event_kind  TEXT NOT NULL,         -- 유상증자|전환사채(CB)|신주인수권부사채(BW)|…
  amount_krw  INTEGER,
  decision_dt TEXT,
  detail_json TEXT NOT NULL DEFAULT '{}'
);
CREATE INDEX IF NOT EXISTS ix_capital_corp ON capital_event(corp_code, event_kind, decision_dt);

CREATE TABLE IF NOT EXISTS holding_event (
  doc_id         TEXT PRIMARY KEY REFERENCES document(doc_id),
  corp_code      TEXT NOT NULL,
  reporter       TEXT,
  cnt_before     INTEGER,
  rate_before    REAL,
  cnt_after      INTEGER,
  rate_after     REAL,
  change_reason  TEXT,
  report_dt      TEXT,
  prev_report_dt TEXT                -- BFR_RPT_DT 명시 체인 포인터 (AC-C2)
);
CREATE INDEX IF NOT EXISTS ix_holding_corp ON holding_event(corp_code, report_dt);

CREATE TABLE IF NOT EXISTS correction_diff (
  id               INTEGER PRIMARY KEY AUTOINCREMENT,
  doc_id           TEXT NOT NULL REFERENCES document(doc_id),
  target_doc_kind  TEXT,
  target_submit_dt TEXT,
  reason           TEXT,
  item             TEXT,
  before_val       TEXT,
  after_val        TEXT
);
CREATE INDEX IF NOT EXISTS ix_corr_doc    ON correction_diff(doc_id);
CREATE INDEX IF NOT EXISTS ix_corr_target ON correction_diff(target_submit_dt);

CREATE TABLE IF NOT EXISTS registry_row (
  id            INTEGER PRIMARY KEY AUTOINCREMENT,
  doc_id        TEXT NOT NULL REFERENCES document(doc_id),
  registry_kind TEXT NOT NULL,
  row_json      TEXT NOT NULL,
  src_section   TEXT
);
CREATE INDEX IF NOT EXISTS ix_registry ON registry_row(doc_id, registry_kind);

-- 빌드 메타 (재현성 증빙 · /meta 엔드포인트)
CREATE TABLE IF NOT EXISTS build_meta (
  key   TEXT PRIMARY KEY,
  value TEXT NOT NULL
);
