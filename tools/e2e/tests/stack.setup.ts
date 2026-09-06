/**
 * The suite's one precondition, checked once and reported as a test.
 *
 * Nothing here starts a server. The stack is compose-managed, and a suite that tried to
 * boot it would either duplicate deploy/compose or quietly test something else. When a
 * door is shut, this fails first and says which one, instead of every spec timing out on
 * a blank page.
 */

import { expect, request as playwrightRequest, test as setup } from "@playwright/test";

import { API_ORIGIN, RECEIVER_ORIGIN, WEB_ORIGIN } from "./support/stack";

const START_THE_STACK = "Start it with `just dev`, then run `just e2e` again.";

setup("the compose stack is up", async () => {
  // A fresh context per probe: these are liveness checks, not requests on anyone's behalf.
  const request = await playwrightRequest.newContext();

  try {
    await expectReachable(
      () => request.get(WEB_ORIGIN),
      200,
      `The web app is not answering on ${WEB_ORIGIN}. ${START_THE_STACK}`,
    );

    await expectReachable(
      () => request.get(`${API_ORIGIN}/healthz`),
      200,
      `The API is not answering on ${API_ORIGIN}. ${START_THE_STACK}`,
    );

    // 401 is the door working: it authenticates before it does anything else.
    await expectReachable(
      () => request.post(`${RECEIVER_ORIGIN}/v1/traces`, { data: {} }),
      401,
      `The OTLP receiver is not answering on ${RECEIVER_ORIGIN}. ${START_THE_STACK}`,
    );
  } finally {
    await request.dispose();
  }
});

async function expectReachable(
  probe: () => Promise<{ status: () => number }>,
  expectedStatus: number,
  failure: string,
): Promise<void> {
  const status = await probe()
    .then((response) => response.status())
    .catch(() => undefined);

  expect(status, failure).toBe(expectedStatus);
}
