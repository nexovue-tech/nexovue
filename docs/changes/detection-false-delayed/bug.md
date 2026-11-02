# Bug: Findings says nexovue is behind on a healthy stack

## Reported behavior

With the demo running and nothing wrong, the Findings page read "nexovue is behind on reading your data since 17:12 UTC — your services may be fine".

## Reproduction

`just dev`, then `just detection-seed`, then open Findings.

- The scheduler logged `the pipeline is behind` on 15 of 17 sweeps, naming all 12 partitions.
- Decisive reading, 2026-11-01: the sweep at 17:28:17 had `current_minute=17:25` and `settled_minute=17:23`, 2 short. The same gap appeared at 17:27:17 and 17:29:18.
- Progress first changed between these samples, each time through about second 58 of the minute before:

  | Sampled | Newest progress |
  |---|---|
  | 17:27:23 | 17:26:59 |
  | 17:28:24 | 17:27:59 |
  | 17:29:20 | 17:28:59 |

Ruled out:

- **A slow worker:** Kafka lag was about 170 records a partition, and progress moved every minute.
- **Stopped detection:** all 16 services were judged within 5 minutes, and `detection project judged` ran every minute.

## Root cause

`pipeline_behind` in `apps/api/src/nexovue_api/detection/service.py` called the pipeline behind at a gap of 2 minutes, on the stated assumption that a healthy gap is at most 1. It can be 2. The worker writes progress once a minute, after the 10 s late grace, and that progress reaches only into the minute before. A sweep just ahead of that write, here at second 17, reads progress nearly 2 minutes old. `Settled.behind` in `settle.py` was stricter still: any gap counted, so its warning fired on almost every sweep.

## Customer impact

No customer is affected today, because nexovue is pre-deployment. A customer with steady traffic would have seen this warning whenever the sweep ran ahead of the write, which here was nearly always. It also took the place of the learning and all-normal messages, and it would teach people to ignore a real delay.

## Fix

`PIPELINE_BEHIND_BY` moves to `nexovue_platform.detection` at 3 minutes. The API and the scheduler now read the same limit. AC23's partition held 3 minutes behind still reads *data delayed*.

## Specification impact

None. AC23 asks for 3 minutes. A hold under 3 minutes no longer shows *data delayed*; those minutes are still not judged until they settle.

## Regression test

- `apps/scheduler/tests/test_detection_settle.py::test_the_worker_s_ordinary_minute_of_progress_is_not_behind`
- `apps/api/tests/test_detection_status_api_live.py::test_the_worker_s_ordinary_minute_of_progress_is_not_delayed`

Seen failing before the fix:

```
>       assert not settled.behind
E       assert not True
E       AssertionError: assert 'data_delayed' == 'judging'
```
