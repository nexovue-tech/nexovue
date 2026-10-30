// @vitest-environment jsdom

import { afterEach, describe, expect, it } from "vitest";

import {
  alertReads,
  button,
  channel,
  choose,
  click,
  field,
  json,
  mountAt,
  OWNERS,
  problem,
  readable,
  stubApi,
  typeInto,
  unmountAll,
  waitFor,
} from "./render-harness.test-util";

afterEach(unmountAll);

describe("the channels list", () => {
  it("puts a failing channel first and says since when (FR21)", async () => {
    stubApi(
      alertReads({
        channels: [
          OWNERS,
          channel({
            health: "failing",
            failing_since: new Date(Date.now() - 7_200_000).toISOString(),
          }),
        ],
      }),
    );
    const { container } = await mountAt("/settings/channels");

    const first = await waitFor(
      () => container.querySelector('ul[aria-label="Channels"] li'),
      "the list",
    );
    expect(readable(first)).toContain("#incidents");
    expect(readable(first)).toContain("Failing since");
  });

  it("shows a webhook's signing secret once, after connecting it", async () => {
    const api = stubApi({
      ...alertReads(),
      "POST /channels": json(
        {
          ...channel({
            id: "ch-hook",
            kind: "webhook",
            name: "Bot",
            destination: "hooks.example.test",
          }),
          signing_secret: "whsec_c2hvd24tb25jZQ==",
        },
        201,
      ),
    });
    const { container } = await mountAt("/settings/channels");

    await click(await waitFor(() => button(container, "Connect a channel"), "the button"));
    await choose(container.querySelector("select") as HTMLSelectElement, "webhook");
    await typeInto(field(container, "channel-name"), "Bot");
    await typeInto(field(container, "channel-url"), "https://hooks.example.test/n");
    await click(button(container, "Connect Webhook"));

    await waitFor(() => readable(container).includes("whsec_c2hvd24tb25jZQ=="), "the secret");
    expect(readable(container)).toContain("shown now and never again");
    const [sent] = api.to("POST", "/channels");
    expect(sent.body).toEqual({
      kind: "webhook",
      name: "Bot",
      config: { url: "https://hooks.example.test/n" },
    });
  });
});

describe("one channel", () => {
  it("says why a test did not arrive", async () => {
    stubApi({
      ...alertReads({ channels: [channel()] }),
      "POST /channel-tests": json({ request_id: "req-1" }, 202),
      "GET /channel-tests/req-1": json({
        pending: false,
        delivered: false,
        http_status: 404,
        reason: "the receiver answered 404: no_service",
      }),
    });
    const { container } = await mountAt("/settings/channels/ch-slack");

    await click(await waitFor(() => button(container, "Send test"), "send test"));
    await waitFor(
      () => readable(container).includes("Not delivered: the receiver answered 404: no_service."),
      "the outcome",
    );
  });

  it("names the rules a removal would silence (FR25)", async () => {
    stubApi(
      alertReads({
        channels: [channel({ rules: [{ id: "r1", name: "Checkout health", project_id: "p" }] })],
      }),
    );
    const { container } = await mountAt("/settings/channels/ch-slack");

    await click(await waitFor(() => button(container, "Remove this channel"), "remove"));
    expect(readable(container)).toContain("These rules will stop sending here: Checkout health.");
  });

  it("offers the owners channel no settings and no removal", async () => {
    stubApi(alertReads());
    const { container } = await mountAt("/settings/channels/ch-owners");

    await waitFor(() => readable(container).includes("Send a test"), "the page");
    expect(readable(container)).not.toContain("Remove this channel");
    expect(container.querySelector('input[name="channel-name"]')).toBeNull();
  });
});

describe("quiet hours", () => {
  it("are set in the reader's own time zone, for channels people read", async () => {
    const api = stubApi({
      ...alertReads({ channels: [channel()] }),
      "PUT /channels/ch-slack/quiet-hours": json(channel()),
    });
    const { container } = await mountAt("/settings/channels/ch-slack");

    await click(await waitFor(() => button(container, "Set quiet hours"), "the quiet hours"));
    const [sent] = await waitFor(() => api.to("PUT", "/channels/ch-slack/quiet-hours"), "the save");
    expect(sent.body).toEqual({
      quiet_hours: {
        timezone: Intl.DateTimeFormat().resolvedOptions().timeZone,
        days: [0, 1, 2, 3, 4],
        start: "22:00",
        end: "07:00",
      },
    });
  });

  it("are not offered for PagerDuty", async () => {
    stubApi(alertReads({ channels: [channel({ id: "ch-pd", kind: "pagerduty", name: "PD" })] }));
    const { container } = await mountAt("/settings/channels/ch-pd");

    await waitFor(() => readable(container).includes("Send a test"), "the page");
    expect(readable(container)).not.toContain("Quiet hours");
  });
});

describe("a webhook's settings", () => {
  it("edit the URL in place, which is configuration and shown whole", async () => {
    const hook = channel({
      id: "ch-hook",
      kind: "webhook",
      name: "Bot",
      destination: "hooks.example.test",
      config: { url: "https://hooks.example.test/old" },
    });
    const api = stubApi({
      ...alertReads({ channels: [hook] }),
      "PATCH /channels/ch-hook": json(hook),
    });
    const { container } = await mountAt("/settings/channels/ch-hook");

    await waitFor(
      () =>
        container.querySelector<HTMLInputElement>('input[name="channel-url"]')?.value ===
        "https://hooks.example.test/old",
      "the URL",
    );
    await typeInto(field(container, "channel-url"), "https://hooks.example.test/new");
    await click(button(container, "Save"));

    const [sent] = await waitFor(() => api.to("PATCH", "/channels/ch-hook"), "the save");
    expect(sent.body).toEqual({ name: "Bot", config: { url: "https://hooks.example.test/new" } });
  });
});

describe("an email channel's settings", () => {
  it("count as saved once the server has stored them its own way", async () => {
    const mail = channel({
      id: "ch-mail",
      kind: "email",
      name: "On call",
      destination: null,
      secret_set: false,
      config: { addresses: ["a@x.test"] },
    });
    const stored = { ...mail, config: { addresses: ["a@x.test", "b@x.test"] } };
    const api = stubApi({
      ...alertReads({ channels: [mail] }),
      "PATCH /channels/ch-mail": json(stored),
    });
    const { container } = await mountAt("/settings/channels/ch-mail");

    const textarea = await waitFor(
      () => container.querySelector("textarea"),
      "the addresses field",
    );
    await typeInto(textarea, "a@x.test\n B@X.test ");
    await click(button(container, "Save"));
    await waitFor(() => readable(container).includes("Saved."), "the save");

    await click(button(container, "Save"));
    const sends = await waitFor(
      () =>
        api.to("PATCH", "/channels/ch-mail").length === 2
          ? api.to("PATCH", "/channels/ch-mail")
          : null,
      "the second save",
    );
    expect(sends[1].body).toEqual({ name: "On call" });
  });
});

/**
 * A bookmark outlives the thing it points at.
 *
 * Somebody removes a channel; a teammate who kept its page open, or bookmarked
 * it, opens it again. That used to read "The channel could not be loaded" over a
 * "Try again" button that can only ever fail again. A deleted check's page has
 * said "No such check" with a way back since F18; this is the same answer.
 */
describe("a channel that does not exist", () => {
  it("says so, with the way back, instead of offering a retry that cannot work", async () => {
    stubApi({
      ...alertReads({ channels: [] }),
      "GET /channels/gone": problem(404, "No channel with this id in this organization."),
    });
    const { container } = await mountAt("/settings/channels/gone");

    await waitFor(() => readable(container).includes("No such channel"), "the not-found state");
    // Two links point at the list — the header's "All channels" and this one — so
    // the assertion is that one of them is the way out of the empty state, not
    // that the first link on the page is.
    const back = [...container.querySelectorAll('a[href="/settings/channels"]')].map(
      (link) => link.textContent,
    );
    expect(back).toContain("Back to every channel");
    expect(readable(container)).not.toContain("Try again");
  });
});
