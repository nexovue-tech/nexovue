-- One row per web vital reading from door 3 (P1-38, M5).
--
-- These readings already land in `logs`: the browser SDK emits each vital as an
-- OTLP log record with event name `browser.web_vital`. This table is a promoted
-- copy of the five attributes a RUM screen filters and aggregates on, for the
-- reason `trace_summaries` (000028) exists — answering "p75 LCP by page" out of
-- `logs` means reading and parsing the attributes JSON of every log row in the
-- window, a per-request cost that grows with the customer's log volume rather
-- than with the question.
--
-- `vital_name` holds the SDK's own spelling, which is lower case: `records.ts`
-- writes `metric.name.toLowerCase()`, so the values are cls, fcp, inp, lcp and
-- ttfb. Five values, and a screen asks about one vital at a time, so it sits
-- ahead of `timestamp` in the key exactly as `severity_number` does in `logs`.
-- There is no `service_name` before it as there is there: one installed script
-- reports one service — `config.ts` takes it from the customer's options and
-- otherwise from the site's own host — so the column would not divide anything.
--
-- `url_path` is a plain String and deliberately not LowCardinality. It is the
-- path the browser was actually on, not a route template: `pagecontext.ts` puts
-- `http.route` only on the page-view span and only when the customer configured
-- one, so a vital record carries no template to group by. A customer with
-- per-id URLs therefore produces unbounded distinct values, and ClickHouse's
-- own guidance is that LowCardinality wins below ~10,000 distinct values and
-- loses above ~100,000. Grouping paths into pages is a normalisation nothing in
-- the system performs yet.
--
-- `device_class` is written from `browser.mobile`, which the SDK puts on the
-- RESOURCE and not on the record — `internal/pipeline/vitals` reads it there,
-- and reading it off a record's own attributes fills this column for nobody.
-- Two values, mobile and desktop: the attribute is a boolean, so a tablet is
-- indistinguishable from a phone, and a browser that does not report it leaves
-- the column empty rather than defaulting to either.
--
-- `country` has no producer. There is no GeoIP anywhere in this system, so it
-- stays empty until something resolves the client address. Both are
-- LowCardinality because both are genuinely bounded once filled.
--
-- `session_id` is a String rather than FixedString(32), unlike `trace_id`. A
-- trace id is a fixed-width binary field of the OTLP message and cannot arrive
-- the wrong length; `session.id` is a free-form string attribute, and a
-- FixedString would make one malformed record fail the whole batch it travelled
-- in — a data loss between receiver and storage, which Phase 1 forbids.
--
-- `received_at` is our clock and carries both the partition key and the TTL;
-- `timestamp` is the browser's and is never trusted to expire data. ADR 0067
-- names door 3 as the reason, and this is the table it was written about: a
-- visitor's device clock can be set to any year at all.
--
-- The deduplication window is necessary and not sufficient. Measured on this
-- server: a repeated `INSERT ... VALUES` is dropped, a repeated `INSERT ...
-- SELECT ... FROM input(...)` — the form the Go writers use — is not, unless it
-- carries an explicit `insert_deduplication_token`. The writer must set one,
-- as `insertdedup` already does for the other rollups.
CREATE TABLE IF NOT EXISTS web_vitals
(
    `timestamp` DateTime64(9) CODEC(Delta(8), ZSTD(1)),
    `received_at` DateTime64(9) DEFAULT timestamp CODEC(Delta(8), ZSTD(1)),
    `retention_days` UInt16 DEFAULT 30 CODEC(T64, ZSTD(1)),
    `tenant_id` LowCardinality(String) CODEC(ZSTD(1)),
    `project_id` LowCardinality(String) CODEC(ZSTD(1)),
    `session_id` String CODEC(ZSTD(1)),
    -- cls | fcp | inp | lcp | ttfb, as the SDK spells them.
    `vital_name` LowCardinality(String) CODEC(ZSTD(1)),
    -- Milliseconds for fcp, inp, lcp and ttfb; a unitless ratio for cls. The
    -- conventions state no unit, so the SDK passes the value through unchanged.
    `value` Float64 CODEC(Gorilla(8), ZSTD(1)),
    `delta` Float64 CODEC(Gorilla(8), ZSTD(1)),
    -- good | needs-improvement | poor, as web-vitals reports it.
    `rating` LowCardinality(String) CODEC(ZSTD(1)),
    `navigation_type` LowCardinality(String) CODEC(ZSTD(1)),
    `url_path` String CODEC(ZSTD(1)),
    `device_class` LowCardinality(String) CODEC(ZSTD(1)),
    `country` LowCardinality(String) CODEC(ZSTD(1)),
    -- Everything else the record carried, including browser.* and the vital id.
    `attrs` JSON
)
ENGINE = MergeTree
PARTITION BY (toDate(received_at), retention_days)
ORDER BY (tenant_id, project_id, vital_name, timestamp)
TTL toDateTime(received_at) + toIntervalDay(retention_days)
SETTINGS index_granularity = 8192, non_replicated_deduplication_window = 1000
