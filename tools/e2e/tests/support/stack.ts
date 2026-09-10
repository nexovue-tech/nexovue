/**
 * Where the compose stack publishes itself, and who is already inside it.
 *
 * The suite never starts anything: the stack is brought up by `docker compose` and
 * tests/stack.setup.ts says so plainly when it is not. Every origin is overridable so
 * the same specs can be pointed at a deployed environment.
 */

export const WEB_ORIGIN = process.env.NEXOVUE_WEB_ORIGIN ?? "http://localhost:15173";
export const API_ORIGIN = process.env.NEXOVUE_API_ORIGIN ?? "http://localhost:18000";
export const RECEIVER_ORIGIN = process.env.NEXOVUE_RECEIVER_ORIGIN ?? "http://localhost:14318";

/**
 * The account `just seed` creates. Only the returning-user path uses it — every other
 * spec signs up for its own organisation so runs cannot see each other's traces.
 */
export const SEEDED_USER = {
  email: process.env.NEXOVUE_SEED_EMAIL ?? "nexovue@nexovue.com",
  password: process.env.NEXOVUE_SEED_PASSWORD ?? "nexovue-dev",
};

/**
 * The trace list's column headings. They render only in the branch that has rows under
 * them, which makes them the honest way to ask "did this list find anything?" without
 * naming a service — the seeded organisation is a shared bin and whichever service is
 * loudest in it changes from hour to hour.
 */
export const TRACE_LIST_COLUMNS = ["Request", "Took", "When"] as const;
