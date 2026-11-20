-- 차곡 AI 전용 법령 DB (BE DB와 별개)
CREATE EXTENSION IF NOT EXISTS vector;
CREATE EXTENSION IF NOT EXISTS pg_trgm;

-- 법령 (현행본 1건씩)
CREATE TABLE IF NOT EXISTS laws (
    id                BIGSERIAL PRIMARY KEY,
    law_id            TEXT NOT NULL UNIQUE,          -- 국가법령정보센터 법령ID
    law_mst           TEXT NOT NULL,                 -- 법령일련번호(MST), 개정되면 바뀜
    name              TEXT NOT NULL,
    law_type          TEXT,                          -- 법률 / 대통령령 ...
    ministry          TEXT,
    promulgation_date DATE,
    effective_date    DATE,
    url               TEXT,
    field_tags        TEXT[] NOT NULL DEFAULT '{}', -- housing_lease, labor, consumer ...
    updated_at        TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_laws_name ON laws (name);

-- 조·항 단위 조문 + 임베딩
CREATE TABLE IF NOT EXISTS law_articles (
    id             BIGSERIAL PRIMARY KEY,
    law_id         BIGINT NOT NULL REFERENCES laws(id) ON DELETE CASCADE,
    article_no     TEXT NOT NULL,                   -- 제5조, 제8조의2
    article_title  TEXT,
    paragraph_no   TEXT,                            -- NULL = 조 전체
    content        TEXT NOT NULL,
    content_hash   TEXT NOT NULL,                   -- 바뀐 조문만 임베딩 다시 만들기
    effective_date DATE,
    url            TEXT,
    field_tags     TEXT[] NOT NULL DEFAULT '{}',
    embedding      vector(1536),
    UNIQUE NULLS NOT DISTINCT (law_id, article_no, paragraph_no)
);
CREATE INDEX IF NOT EXISTS idx_articles_tags ON law_articles USING gin (field_tags);
CREATE INDEX IF NOT EXISTS idx_articles_trgm ON law_articles USING gin (content gin_trgm_ops);
CREATE INDEX IF NOT EXISTS idx_articles_embedding ON law_articles USING hnsw (embedding vector_cosine_ops);

-- 규칙 엔진 기준값 (시행일이 있는 표)
CREATE TABLE IF NOT EXISTS legal_thresholds (
    id             BIGSERIAL PRIMARY KEY,
    threshold_key  TEXT NOT NULL,                   -- MINIMUM_WAGE_HOURLY, PRIORITY_REPAYMENT ...
    region_group   TEXT,
    value          NUMERIC NOT NULL,
    value2         NUMERIC,
    effective_from DATE NOT NULL,
    effective_to   DATE,
    law_name       TEXT NOT NULL,
    article_no     TEXT NOT NULL,
    note           TEXT
);
CREATE INDEX IF NOT EXISTS idx_thresholds_key ON legal_thresholds (threshold_key, effective_from);

-- 규칙 목록 (켜고 끄기, 설명)
CREATE TABLE IF NOT EXISTS legal_rules (
    rule_code   TEXT PRIMARY KEY,
    description TEXT NOT NULL,
    doc_types   TEXT[] NOT NULL DEFAULT '{}',
    law_name    TEXT NOT NULL,
    article_no  TEXT NOT NULL,
    severity    TEXT NOT NULL,
    enabled     BOOLEAN NOT NULL DEFAULT true
);

-- 표준계약서 (나중에 PDF로 직접 넣음)
CREATE TABLE IF NOT EXISTS standard_contracts (
    id             BIGSERIAL PRIMARY KEY,
    name           TEXT NOT NULL,
    doc_type       TEXT NOT NULL,
    source_url     TEXT,
    published_date DATE
);
CREATE TABLE IF NOT EXISTS standard_contract_clauses (
    id          BIGSERIAL PRIMARY KEY,
    contract_id BIGINT NOT NULL REFERENCES standard_contracts(id) ON DELETE CASCADE,
    clause_no   TEXT,
    title       TEXT,
    content     TEXT NOT NULL,
    embedding   vector(1536)
);

-- 동기화 기록
CREATE TABLE IF NOT EXISTS sync_logs (
    id               BIGSERIAL PRIMARY KEY,
    started_at       TIMESTAMPTZ NOT NULL DEFAULT now(),
    finished_at      TIMESTAMPTZ,
    status           TEXT NOT NULL DEFAULT 'RUNNING',   -- RUNNING / SUCCESS / FAILED
    laws_updated     INT NOT NULL DEFAULT 0,
    articles_updated INT NOT NULL DEFAULT 0,
    message          TEXT
);
