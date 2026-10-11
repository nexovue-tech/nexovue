-- Which record first consumed each payload, for fifteen minutes (readiness
-- group 9, C2; `apps/workers/internal/pipeline/resend`).
--
-- When the receiver gives up on an export after its own retries the sender is
-- told 503 and sends it again, and the copy lands at new offsets — stored,
-- counted and billed twice, invisible to every offset-based guard. The receiver
-- stamps each record with a digest of its tenant, project, signal and payload;
-- the first record to carry one claims it here when it is consumed, and a later
-- record with the same digest at another offset is skipped.
--
-- A plain MergeTree with a TTL rather than KeeperMap: KeeperMap cannot expire
-- rows, and these are worthless after a sender's retry window. Fifteen minutes
-- is far past any sender's backoff. Holds no tenant column: a digest names
-- nothing a person could read back.
CREATE TABLE IF NOT EXISTS stored_payload_digests
(
    digest String,
    topic LowCardinality(String),
    partition Int32,
    offset Int64,
    claimed_at DateTime DEFAULT now()
)
ENGINE = MergeTree
ORDER BY digest
TTL claimed_at + INTERVAL 15 MINUTE
