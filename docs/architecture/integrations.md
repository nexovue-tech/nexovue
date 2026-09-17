# External integrations architecture

## Current verified behavior

Synthetic checks run API and multistep API requests (with certificate expiry), TCP connections, DNS resolution and heartbeats; no customer-supplied code runs anywhere. The scheduler prevents probes from reaching loopback, private, link-local, and cloud-metadata addresses at connection time, including every redirect hop and a customer-chosen DNS resolver.

CloudWatch polling assumes a customer role with an externally generated ID; it does not store cloud credentials. Postgres monitoring uses IAM authentication and reads statistics/query hashes rather than SQL text. Deploy webhooks are handled by the API and stored in Postgres; they are not an envelope ingestion door.

Source-map bytes use an S3-compatible object store. Payment handling is adapter-based; a checkout does not change plans, while a signed, idempotent processor webhook does. Current provider configuration remains environment-dependent.
