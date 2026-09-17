// @vitest-environment jsdom

import { afterEach, describe, expect, it } from "vitest";

import type { CheckDefinition } from "./api";
import {
  buttonLabelled,
  checkDefinition,
  choose,
  click,
  mountAt,
  never,
  readable,
  resultSummary,
  stubApi,
  syntheticsReads,
  unmountAll,
  waitFor,
} from "./render-harness.test-util";

/**
 * The defects these guard: a kind missing from the list or the filter (AC29), a down
 * check told apart only by colour (principle 6), and a filter that matches nothing
 * reading as "you have no checks" (principle 3).
 */

afterEach(unmountAll);

const EVERY_KIND: CheckDefinition[] = [
  checkDefinition({ id: "chk-api", name: "Orders API", displayed_state: "up" }),
  checkDefinition({
    id: "chk-multi",
    name: "Checkout journey",
    type: "multistep",
    config: { type: "multistep", steps: [] },
    displayed_state: "down",
  }),
  checkDefinition({
    id: "chk-beat",
    name: "Nightly export",
    type: "heartbeat",
    config: { type: "heartbeat", grace_seconds: 300 },
    displayed_state: "paused",
    enabled: false,
  }),
  checkDefinition({
    id: "chk-tcp",
    name: "Database port",
    type: "tcp",
    config: { type: "tcp", host: "db.example.com", port: 5432, timeout_seconds: 10 },
    displayed_state: "degraded",
  }),
  checkDefinition({
    id: "chk-dns",
    name: "Shop domain",
    type: "dns",
    config: {
      type: "dns",
      name: "shop.example.com",
      record_type: "A",
      resolver: null,
      assertions: [],
      timeout_seconds: 10,
    },
    displayed_state: null,
    confirmed_state: null,
    last_run_at: null,
  }),
];

/** The row that links to one check. */
function row(container: HTMLElement, checkId: string) {
  return container.querySelector<HTMLAnchorElement>(`a[href="/synthetics/${checkId}"]`);
}

const listedIds = (container: HTMLElement) =>
  [...container.querySelectorAll<HTMLAnchorElement>('a[href^="/synthetics/chk-"]')].map((link) =>
    link.getAttribute("href"),
  );

async function mountList(url = "/synthetics", checks = EVERY_KIND) {
  stubApi(syntheticsReads({ checks, results: [resultSummary({ check_id: "chk-api", runs: 60 })] }));
  const screen = await mountAt(url);
  await waitFor(
    () => !screen.container.querySelector('[aria-label="Loading checks"]'),
    "the checks to load",
  );
  return screen;
}

describe("the synthetics list", () => {
  it("holds its layout with a skeleton while the checks load", async () => {
    stubApi({ ...syntheticsReads(), "GET /checks": never });
    const { container } = await mountAt("/synthetics");

    expect(container.querySelector('[aria-label="Loading checks"]')).not.toBeNull();
    expect(readable(container)).not.toContain("No checks yet");
  });

  it("says there are no checks yet and offers both ways to make one", async () => {
    stubApi(syntheticsReads());
    const { container } = await mountAt("/synthetics");
    await waitFor(() => readable(container).includes("No checks yet"), "the empty state");

    const empty = [...container.querySelectorAll("h2")].find(
      (heading) => heading.textContent === "No checks yet",
    )?.parentElement;
    expect(empty?.querySelector('a[href="/synthetics/new"]')?.textContent).toBe("New check");
    expect(empty?.querySelector('a[href="/synthetics/import"]')?.textContent).toBe(
      "Import cURL, OpenAPI or Postman",
    );
    expect(readable(container)).not.toContain("POST");
  });

  it("lists a check of every kind, each with its state in words", async () => {
    const { container } = await mountList();

    const listed = EVERY_KIND.map((check) => {
      const [state, described] = row(container, check.id)?.children ?? [];
      return [
        state?.textContent,
        described?.children[0]?.textContent,
        described?.children[1]?.textContent?.split(" · ")[0],
      ];
    });
    expect(listed).toEqual([
      ["up", "Orders API", "API"],
      ["down", "Checkout journey", "Multistep API"],
      ["paused", "Nightly export", "Heartbeat"],
      ["degraded", "Database port", "TCP"],
      ["not judged yet", "Shop domain", "DNS"],
    ]);
  });

  it("marks only the down check with a filled mark, beside the word", async () => {
    const { container } = await mountList();

    const marks = container.querySelectorAll(".bg-critical-solid");
    expect(marks).toHaveLength(1);
    expect(marks[0]?.closest("a")?.getAttribute("href")).toBe("/synthetics/chk-multi");
    expect(marks[0]?.nextElementSibling?.textContent).toBe("down");
  });

  it("rules the cells of a check with no readings rather than printing a zero", async () => {
    const { container } = await mountList();

    const cells = [...(row(container, "chk-tcp")?.children ?? [])].map((cell) => cell.textContent);
    // Uptime and p95: nothing measured in this window is not 0%.
    expect(cells[2]).toBe("—");
    expect(cells[3]).toBe("—");
    expect(readable(row(container, "chk-api") as Element)).toContain("95.0%");
  });

  it("holds a reading's cell with a placeholder while readings load, not an ellipsis", async () => {
    stubApi({
      ...syntheticsReads({ checks: EVERY_KIND }),
      "GET /synthetics/checks": never,
    });
    const { container } = await mountAt("/synthetics");
    await waitFor(() => row(container, "chk-tcp"), "the rows");

    const cells = [...(row(container, "chk-tcp")?.children ?? [])];
    expect(cells[2]?.textContent).toBe("");
    expect(cells[2]?.querySelector(".animate-pulse")).not.toBeNull();
    expect(readable(container)).not.toContain("…");
  });

  it("links each row to its check page", async () => {
    const { container } = await mountList();

    expect(listedIds(container)).toEqual(EVERY_KIND.map((check) => `/synthetics/${check.id}`));
  });

  it("offers exactly the five kinds in the Kind filter", async () => {
    const { container } = await mountList();
    const kind = container.querySelector("select") as HTMLSelectElement;

    expect([...kind.options].map((option) => option.value)).toEqual([
      "",
      "api",
      "multistep",
      "heartbeat",
      "tcp",
      "dns",
    ]);
  });

  it.each([
    ["api", "chk-api"],
    ["multistep", "chk-multi"],
    ["heartbeat", "chk-beat"],
    ["tcp", "chk-tcp"],
    ["dns", "chk-dns"],
  ])("narrows to the %s kind when it is chosen", async (kind, checkId) => {
    const { container, router } = await mountList();

    await choose(container.querySelector("select") as HTMLSelectElement, kind);

    expect(router.state.location.search).toEqual({ check_type: kind });
    expect(listedIds(container)).toEqual([`/synthetics/${checkId}`]);
  });

  it("narrows to what is down when Only down is ticked", async () => {
    const { container, router } = await mountList();

    await click(container.querySelector('input[type="checkbox"]') as HTMLInputElement);

    expect(router.state.location.search).toEqual({ down_only: "true" });
    expect(listedIds(container)).toEqual(["/synthetics/chk-multi"]);
  });

  it("names a filter that matched nothing, and clears it on request", async () => {
    const { container, router } = await mountList();
    await choose(container.querySelector("select") as HTMLSelectElement, "tcp");
    await click(container.querySelector('input[type="checkbox"]') as HTMLInputElement);

    expect(router.state.location.search).toEqual({ check_type: "tcp", down_only: "true" });
    expect(readable(container)).toContain("No checks match these filters");
    expect(readable(container)).toContain("5 checks exist, but none match.");
    expect(readable(container)).not.toContain("No checks yet");

    await click(buttonLabelled(container, "Show every check"));

    expect(router.state.location.search).toEqual({});
    expect(listedIds(container)).toHaveLength(EVERY_KIND.length);
  });

  it("keeps a kind that arrives in the address", async () => {
    const { container } = await mountList("/synthetics?check_type=dns");

    expect(listedIds(container)).toEqual(["/synthetics/chk-dns"]);
  });
});
