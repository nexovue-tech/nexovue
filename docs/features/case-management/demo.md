# Demo script: case management on the seeded development stack

For showing F22 to someone in about ten minutes (plan T8.1; spec, *Questions a buyer will ask*:
on live traffic no case can exist for at least 3 days, because a finding needs history and a real
anomaly). Every step uses the real detection job and the real routes; only the telemetry is
shaped. **Walked through by hand: not yet** (written 2026-11-22 from the tools and screens as
built; Step 8 ran each part's tests, not this sequence in a browser).

## Before

```bash
just dev                                   # stack, migrations, seed, demo traffic
just detection-seed                        # four weeks of history, so detection judges at once
```

Sign in as the seeded owner. Open the feed.

## 1. A finding, and taking it

```bash
just detection-fault --service checkout    # checkout reads slower than normal
```

Within a couple of minutes the project job opens a finding on `checkout`. Open it. Point out:
nothing started by itself, and there is no form. Click **Take this**: the case line reads that
you took it, and the feed's *Assigned to me* shows it.

## 2. A note

Write a note: "Restarted the pool; watching." It appears in the timeline in your own words. Say
what the note box says: notes stay as the organization's record, and on a self-hosted install a
resolved case and its notes are deleted after the project's retention (30 days by default).

## 3. Recovery does not end the work

```bash
just detection-fault --service checkout --clear
```

The finding closes as recovered. The case stays: the line reads that it recovered and is still
being investigated. Nothing was sent to any tracker.

## 4. It returns

```bash
just detection-fault --service checkout
```

A new finding opens on `checkout` and joins the active case: the case shows it, and the joined
line names the signal that moved. One service, one active case.

## 5. An issue, created and closed

This needs a GitHub connection (`docs/product/setup/trackers/github.md`). **On the development
stack there is no simulated GitHub to connect to;** the step needs a real test organization,
which is the founder's after Phase 2 (foundation item 28). With one connected:

- On the case, **Create an issue**, choose the repository, read the preview, confirm. The link
  appears once GitHub has the issue. Open it: it is assigned to nobody, so nobody was notified.
- Close the issue in GitHub. While the signal is still measured, the case holds and says so; clear
  the fault (step 3's command) and the case ends.
- Or resolve in nexovue: the issue closes, with one fixed comment.

## After

```bash
just detection-fault --service checkout --clear
```
