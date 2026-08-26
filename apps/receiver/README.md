# receiver

The ingestion edge. Terminates OTLP, authenticates, tags tenancy, normalizes, publishes to Kafka.

Rules that bind work in this directory are in `CLAUDE.md`.

    just dev          # run it with the rest of the stack
