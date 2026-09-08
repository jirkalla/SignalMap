-- SignalMap — Phase 1 schema (thin vertical slice)
-- Scope: create client, create prompts (with language/market), run against
-- one AI provider (Google Gemini), store + view raw response and citations.
-- Deliberately excludes: StrategyConfig, AnalysisSkill/AnalysisResult, auth/multi-tenancy,
-- scheduling. These come in later phases — see signalmap-conventions skill, "Build sequencing".

-- ============================================================
-- CLIENT (identification only in phase 1)
-- ============================================================

CREATE TABLE clients (
    id          SERIAL PRIMARY KEY,
    name        VARCHAR(200) NOT NULL,
    slug        VARCHAR(100) NOT NULL UNIQUE,
    industry    VARCHAR(120),
    notes       TEXT,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- ============================================================
-- MARKETS / PROMPTS
-- ============================================================

CREATE TABLE markets (
    id          SERIAL PRIMARY KEY,
    code        VARCHAR(20) NOT NULL UNIQUE,   -- 'de-DE', 'cs-CZ', 'en-US'
    language    VARCHAR(10) NOT NULL,          -- 'de', 'cs', 'en'
    country     VARCHAR(10),                   -- 'DE', 'CZ', 'US'
    label       VARCHAR(100)
);

CREATE TABLE prompt_sets (
    id          SERIAL PRIMARY KEY,
    client_id   INTEGER NOT NULL REFERENCES clients(id) ON DELETE CASCADE,
    name        VARCHAR(200) NOT NULL,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- Version column kept even in phase 1 — cheap now, per the project's
-- "never overwrite historical rows" rule.
CREATE TABLE prompts (
    id              SERIAL PRIMARY KEY,
    prompt_set_id   INTEGER NOT NULL REFERENCES prompt_sets(id) ON DELETE CASCADE,
    version         INTEGER NOT NULL DEFAULT 1,
    text            TEXT NOT NULL,
    market_id       INTEGER NOT NULL REFERENCES markets(id),
    topic           VARCHAR(150),
    is_active       BOOLEAN NOT NULL DEFAULT TRUE,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- ============================================================
-- PROVIDERS / MODELS
-- ============================================================

CREATE TABLE providers (
    id      SERIAL PRIMARY KEY,
    code    VARCHAR(30) NOT NULL UNIQUE,   -- 'google_gemini' in phase 1; 'anthropic' added in phase 2
    name    VARCHAR(100) NOT NULL
);

CREATE TABLE ai_models (
    id                      SERIAL PRIMARY KEY,
    provider_id             INTEGER NOT NULL REFERENCES providers(id),
    model_name              VARCHAR(100) NOT NULL,
    display_name            VARCHAR(150),
    capability_tier         VARCHAR(20) NOT NULL,    -- 'flagship' | 'standard' | 'economy'
    cost_per_1k_input_usd   NUMERIC(10,5),
    cost_per_1k_output_usd  NUMERIC(10,5),
    supports_web_search     BOOLEAN NOT NULL DEFAULT FALSE,
    is_active               BOOLEAN NOT NULL DEFAULT TRUE,
    notes                   TEXT,
    UNIQUE (provider_id, model_name)
);

-- ============================================================
-- RUNS / RAW DATA
-- ============================================================

CREATE TABLE runs (
    id                  SERIAL PRIMARY KEY,
    prompt_id           INTEGER NOT NULL REFERENCES prompts(id),
    model_id            INTEGER NOT NULL REFERENCES ai_models(id),
    trigger_type        VARCHAR(20) NOT NULL DEFAULT 'manual',  -- only 'manual' in phase 1
    status              VARCHAR(20) NOT NULL DEFAULT 'pending', -- 'pending'|'success'|'error'
    started_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
    finished_at         TIMESTAMPTZ,
    latency_ms          INTEGER,
    error_message       TEXT
);

CREATE TABLE raw_responses (
    id              SERIAL PRIMARY KEY,
    run_id          INTEGER NOT NULL UNIQUE REFERENCES runs(id) ON DELETE CASCADE,
    raw_payload     JSONB NOT NULL,
    rendered_text   TEXT,
    token_usage     JSONB,
    has_citations   BOOLEAN NOT NULL DEFAULT FALSE,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE citations (
    id                  SERIAL PRIMARY KEY,
    raw_response_id     INTEGER NOT NULL REFERENCES raw_responses(id) ON DELETE CASCADE,
    source_url          TEXT,
    source_title        VARCHAR(300),
    source_domain       VARCHAR(200),
    citation_position   INTEGER,
    cited_answer_span   TEXT
);

-- ============================================================
-- Seed data — needed before anything is usable
-- ============================================================

INSERT INTO markets (code, language, country, label) VALUES
    ('cs-CZ', 'cs', 'CZ', 'Czech (Czech Republic)'),
    ('de-DE', 'de', 'DE', 'German (Germany)'),
    ('en-US', 'en', 'US', 'English (United States)');

INSERT INTO providers (code, name) VALUES
    ('google_gemini', 'Google Gemini');

-- Two models: the one ordinary users actually get by default (most relevant
-- for perception testing), and a cheap one for high-volume dev iteration.
INSERT INTO ai_models (provider_id, model_name, display_name, capability_tier, supports_web_search, is_active)
VALUES
    (
        (SELECT id FROM providers WHERE code = 'google_gemini'),
        'gemini-3.5-flash',
        'Gemini 3.5 Flash',
        'standard',
        TRUE,
        TRUE
    ),
    (
        (SELECT id FROM providers WHERE code = 'google_gemini'),
        'gemini-3.1-flash-lite',
        'Gemini 3.1 Flash-Lite',
        'economy',
        TRUE,
        TRUE
    );

CREATE INDEX idx_runs_prompt ON runs(prompt_id);
CREATE INDEX idx_raw_payload_gin ON raw_responses USING GIN (raw_payload);
