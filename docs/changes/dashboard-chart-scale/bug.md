# Bug: a dashboard chart's x scale is intermittently null, a lone reading spans 2.7 years, and a drag reads twice

F20 open decision 39. Three separate faults sat under one symptom; each is fixed at its own root.

## Reported behavior

On a dashboard, a chart's x scale was `null` after mount, intermittently, in dev and in a production
build: the cursor readout stuck to the first point, no line was drawn, linked charts were one point off,
and a drag zoomed to a window in 2027 (so no "Zoomed to" line). `dashboards-chart.spec.ts` and the AC103
case of `dashboards-link.spec.ts` failed. Adding a log that read `chart.scales.x` made it vanish.

## Reproduction

Recorded 2026-11-22 on this machine's own stack (own Postgres, ClickHouse, Kafka; receiver, worker, API and
Vite from source), Playwright 1.64.1 on Chromium 1243.

1. **The two specs, before any change:** `dashboards-chart` and `dashboards-link`, 2 failed, 3 passed, on
   every run: `expect(timeOnFourth).toBe(timeOnFirst)` (the cursor readout on chart 4 stayed on its first
   point) and `getByText(/^Zoomed to/)` not found.
2. **A probe of the real app with its API mocked** (30 `service_trend` panels, 4 readings each; uPlot wrapped
   to keep each instance): after load, **15 of 15 charts on screen had `scales.x.min === null`, on 6 of 6
   page loads**, with the data (4 points) and the width (313 px) both right. A forced `chart.batch(() =>
   chart.setData(chart.data))` then gave the scale at once, so nothing threw and uPlot was working; its first
   scale pass had simply been lost.
3. **The call log of one instance** showed `redraw()` at +62 ms and +116 ms after construction and uPlot's own
   first commit (a microtask it queues in the constructor) only at +134 ms, `x.min=null` throughout.
4. **uPlot alone**, no React (real uPlot 1.6.32 in the page): construct, then `redraw()` before any `await`
   gives `scales.x = [null, null]`; construct then `setSize()` gives the scale; construct, `await`, then
   `redraw()` gives the scale.
5. **The AC103 spec after fault 1 was fixed** still failed. The page's read for `range=6h` had **one**
   point: a 6 h window is read in 360 s buckets, so the spec's two minutes (6 and 4 minutes ago) fall in one
   bucket. The chart's x scale was `[1795338720, 1881738720]`, 86,400,000 s wide, and the drag's address was
   `from=2027-08-18…&to=2028-07-07…`. That is the "2027 window".
6. **The chart spec after faults 1 and 2** failed on `reads.length` (expected 2, received 3): after a drag
   the page asked for panels 1 to 6, then 44 ms later for panels 1 to 9. The probe measured why: with the
   readings cleared the placeholders were 217 px tall, a row of drawn charts 277 px, so panel 7 moved from
   851 px to 681 px and into the 720 px viewport, which joined it to the read (FR53).

## Root cause

**1. `TrendChart.tsx:164` (before the change `chartRef.current?.redraw()`), against uPlot's `redraw`, `uPlot.esm.js:4919`.** uPlot works out its first scales in a microtask queued by its constructor.
`redraw()` with no argument runs `_setScale(x, scaleX.min, scaleX.max)`, which asks to keep the x scale at its
present min and max; before that first microtask those are `null`, and the request replaces the pending
auto-range, so the scale stays `null` for good. `TrendChart` calls `redraw()` whenever `markers`, `spans` or the band
change. On a dashboard these arrive as new arrays in the same React flush that mounts the chart (the
readout's state update re-renders the panel), before the microtask runs. Whether the flush and the
microtask interleave that way depends on how much work the flush holds, which is why it came and went, and
why a log (which forces work in between) hid it. Not StrictMode (the probe has it on and off), not the extra
`setData` (it re-sets the range from the data, which is harmless).

**2. `trend-chart-options.ts`, `scales.x`, against uPlot 1.6.32 `autoScaleX` (`uPlot.esm.js:3919-3920`).**
For a time scale whose data is one point, uPlot widens the range by `round(86400 / ms)` where `ms` is
`1e-3` in seconds mode, which is 86,400,000 seconds (about 2.7 years), not one day. Read in the source and
seen in the browser; whether upstream knows is **unverified** (a search found no matching issue). Any chart
with a single reading, which is every new service's first minutes and every wide window with a narrow history,
drew its one point in a 2.7-year axis, and a drag across it asked for a 2027 window.

**3. `DashboardScreen.tsx` `PanelCell` and `PanelFrame.tsx`.** A change of window clears the readings
(FR31), so every panel shows a placeholder sized by its chart alone; a drawn chart panel also holds its legend
rows and "View as table", and in a grid a row is as tall as its tallest panel (a latency chart has three
legend rows). The page shrank by a third on every drag and every window change, pulling the rows below into
view, and the page then read them in a second request. FR53 and AC31 say one drag is one read.

## Specification impact

None to the specification. `dashboards-chart.spec.ts` carried a line that said the address must not change
on a drag "until T6.2"; T6.2 is built (the address carries the window), so the spec now asserts that the
address's `from` and `to` are the two the read asked for. One open decision below: the span drawn around a
lone reading.

## Regression test

- `TrendChart.test.ts` (new, the real uPlot in jsdom with a canvas that draws nothing):
  - *keeps its x scale when a mark arrives before uPlot has first drawn*: seen failing against the old
    `redraw()` with `expected null to be 1795338000`, passing with `redraw(false)`.
  - *keeps its x scale when marks arrive after it has drawn*: passes with and without the fix (it holds the
    ordinary case in place).
  - *gives a single reading a span around it*: seen failing with `expected 1795338000 to be less than
    1795338000`, passing with the range function.
- `trend-chart-options.test.ts`: the options snapshot gains `scales.x.range`, one line in each of two.
- Fault 3 is layout, which jsdom cannot measure; it is held by `dashboards-chart.spec.ts` ("one drag reads
  once"), seen failing three reads for two before `holdHeight` and passing after. The spec's own line was
  corrected as above.

## Fix plan and validation

1. `TrendChart.tsx`: the overlay effect calls `redraw(false)`. Marks, spans and the band are painted in
   `drawClear`, so the series' paths need no rebuild, and `redraw(false)` only queues a commit, never
   touching a scale.
2. `trend-chart-options.ts`: `scales.x.range` is `readingsRange`: one point gets `ONE_READING_SPAN_SECONDS`
   (an hour, centred), else uPlot's own range unchanged.
3. `PanelFrame.tsx`: `holdHeight` keeps the frame's last height while a placeholder stands where a reading
   was; `DashboardScreen.tsx` passes it when the panel has no answer. Nothing is held on a first load.

Validation is in `status.md`.

## Status

Implemented, uncommitted. See `status.md`.
