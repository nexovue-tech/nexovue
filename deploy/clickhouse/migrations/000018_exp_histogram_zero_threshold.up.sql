-- ADR 0066 stores OTLP's data and drops only its envelope bookkeeping.
-- `zero_threshold` is data: it defines the width of the zero region an
-- exponential histogram's `zero_count` counts, so without it that count says
-- how many values were near zero without saying what "near" meant. The bucket
-- boundaries are reconstructible from scale and offset; this one is not
-- reconstructible from anything.
--
-- Missed when the five tables were designed. Free now — the table is empty and
-- P1-29's writer has not shipped.
ALTER TABLE metrics_exp_histogram
    ADD COLUMN IF NOT EXISTS zero_threshold Float64 CODEC(Gorilla(8), ZSTD(1))
