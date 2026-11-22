# F20 measurement: pass marks, written before the run

**Written 2026-11-22, before `tools/dashboard-load/load.py` was run for the first time.** F20 plan T5.7 step 1
and T11.6. The numbers are *proposals* for the founder to accept or change; a result is judged against
them as written here, and a mark changed afterwards is recorded as changed, with the reason.

## What is measured, and where

| | |
|---|---|
| **Machine** | A Linux VM (kernel 6.18.44, Firecracker), 4 vCPU Intel Xeon @ 2.10 GHz, 15 GiB RAM, **shared with other builders' containers and test runs, whose load was not controlled**. Not the Mac of the plan's note, not Oracle. |
| **How it ran** | Own containers (Postgres, ClickHouse 26.3, Kafka, SeaweedFS); receiver, worker, API and web as processes beside them. **The API had no container memory limit** (the production replica's is 832 MiB), so its peak resident memory is recorded and compared with 832 MiB, not enforced. |
| **ClickHouse ceiling** | `max_server_memory_usage` 4,294,967,296 (4 GiB), container limit 5,120 MiB, the dev stack's own files. The API reads as `nexovue_api_reader` (2.4 GiB for the user, 12 at once, 1 GiB a query). |
| **Data volume** | `tools/detection-seed/seed.py --weeks 1`: 15 services, 7 days, 144,105 `service_stats_1m` rows, 373,117 endpoint rows, 144,253 version rows. **Rollups written directly: no raw spans, logs or metrics**, so raw readers (node, check, statement, metric series) read nothing here. Plus a steady 400 spans a second through the receiver while reads run. |
| **A result is from the demo** | and says so. Demo volume does not prove a customer's. A rerun on Oracle, against these same marks, is required before any cap is published (plan T5.7). |

## The worst-case dashboard

30 panels, every one a distinct read where the services allow: 23 one-service charts (over the 15 services in rotation), three top-N charts, a services table, an endpoints table, a note and the open-findings panel. The API's own count of its scans is read from the save, which refuses above 40.

## Pass marks

| # | Mark | Pass when |
|---|---|---|
| 1 | NFR4: one worst-case dashboard, 1-hour window, warm | batch time p95 at most **3,000 ms**, maximum at most **5,000 ms** |
| 2 | One person, one dashboard (AC129's spirit) | **0** panels `refused`, `failed`, `timed_out` or `over_limit` at level 1 |
| 3 | NFR11, ingest: worker consumer lag | the **peak** sampled every 2 s during a level is at most **the baseline level's peak + 2,000 records** (about 5 s of the 400 spans a second). *Changed 2026-11-22 after a trial run (no mark had yet been judged): the gauge is a sawtooth that falls at each commit, so one reading at the end of a level says nothing, and its baseline must run the same ingest with no reads, not no ingest* |
| 4 | NFR11, ingest: failed inserts | `nexovue_worker_dead_letter_records` rises by **0**; every receiver export answers 2xx |
| 5 | NFR14: the API account | no `MEMORY_LIMIT_EXCEEDED` (241) and peak query memory at most **1,024 MiB** at every level; `TOO_MANY_SIMULTANEOUS_QUERIES` (202) is **recorded**, not failed, above level 1 |
| 6 | ClickHouse resident memory | at most **4,000 MiB** at every level |
| 7 | API peak resident memory | at most **832 MiB** per process at every level |
| 8 | NFR11, scheduler: `detection_settled_minute_age_seconds`, `oldest_alert_waiting_seconds`, `oldest_ready_job_seconds` | **Not measurable by this tool, and no scheduler was running.** Recorded as *not run*; the gate stays open until it is |
| 9 | NFR12: 30 people on one dashboard, 10 zooming | **Not measurable by this tool:** it needs 30 signed-in people in one organization. Recorded as *not run* |

Levels 5 and 20 are one signed-in person reading several dashboards at once, which the per-person read limit is
built to refuse in part; their refusals are recorded and are not a failure of mark 2. They exist to load the
shared account and the stores.

## Levels, repeats and the incident script

- Baseline (level 0): no reads, ingest trickle only, for 30 s.
- **Every dashboard reads a window of its own** (one hour, ending whole minutes apart), so no two share a scan: the first trial read twenty identical dashboards and the API answered nineteen from the first one's reads, which measured the sharing and not the load.
- 1, 5 and 20 dashboards read at once, 3 rounds each, at **1 API process**, then at **2 API processes** (both started with `API_INSTANCES=2`, which halves each process's concurrency share).
- Incident script (plan T5.7 step 3): **not run.** It needs ClickHouse restarted under load; recorded as not run.

## What triggers a rerun

A change to the caps, to a statement a dashboard reads, to the API account's two settings, or to sizing; a new panel kind;
and any move to a customer-sized volume.
