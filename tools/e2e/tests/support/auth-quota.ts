/**
 * The suite's share of the signup and login allowance, spent from one place.
 *
 * Signup and login are the credential-stuffing surface, so the API counts them per
 * client address: ten requests a minute. Every browser here reaches the API through the
 * web container's proxy, so the API sees one address for the whole suite — all workers,
 * all specs, one bucket. Left alone, the workers spend a minute's allowance in a second
 * and the run fails on 429s it caused itself.
 *
 * The limit is not the defect and must not be widened for us, so the harness paces
 * itself instead. Every worker process claims from one ledger before it touches an auth
 * door, and the API's own `Retry-After` parks all of them when the pacing is wrong
 * anyway. The ledger is deliberately not cleared between runs: two runs a minute apart
 * really do share one server window.
 */

import {
  closeSync,
  mkdirSync,
  openSync,
  readFileSync,
  rmSync,
  statSync,
  writeFileSync,
} from "node:fs";
import { dirname } from "node:path";
import { setTimeout as sleep } from "node:timers/promises";
import { fileURLToPath } from "node:url";

import { workerIndex } from "./worker-client";

/** AUTH_TIER in apps/api/src/nexovue_api/ratelimit/policy.py, per client address. */
const AUTH_LIMIT = 10;
const AUTH_WINDOW_MS = 60_000;

/** Left unspent, so a person on the dev stack can still sign in while a run is on. */
const RESERVED_FOR_PEOPLE = 2;
const SUITE_ALLOWANCE = AUTH_LIMIT - RESERVED_FOR_PEOPLE;

/** Build output, like every other file the suite writes. See playwright.config.ts. */
/**
 * One ledger per worker, because one worker is now one client.
 *
 * It was one ledger for the whole suite while every browser reached the API from
 * the proxy's address and shared its allowance. Each worker presents its own
 * address now (`worker-client.ts`), so each has its own ten a minute — and a
 * shared ledger would pace them against a limit none of them is subject to.
 *
 * Still a file rather than memory: a worker is a process, and two runs a minute
 * apart really do share one server window.
 */
const LEDGER_PATH = fileURLToPath(
  new URL(`../../node_modules/.playwright/auth-quota-${workerIndex()}.json`, import.meta.url),
);
const LOCK_PATH = `${LEDGER_PATH}.lock`;

/** A hold is one file read and one file write. Anything older belongs to a dead worker. */
const STALE_LOCK_MS = 5_000;
const LOCK_RETRY_MS = 25;

/** Workers released by the same expiry must not all lunge at the same slot. */
const MAX_JITTER_MS = 250;

const MS_PER_SECOND = 1_000;

interface Ledger {
  /** When each request that still counts against the window was made. Oldest first. */
  spentAtMs: number[];
  /** Set when the API refused one anyway; nobody asks again before it. */
  pausedUntilMs: number;
}

/** Blocks until this worker may make one signup or login request. */
export async function takeAuthAllowance(): Promise<void> {
  for (;;) {
    const waitMs = await withLedger(claimSlot);
    if (waitMs === 0) return;
    await sleep(waitMs + Math.random() * MAX_JITTER_MS);
  }
}

/**
 * The API refused a request anyway: hold every worker off for as long as it asked.
 * Without a stated interval, a whole window is the only safe assumption.
 */
export async function deferAuthRequests(retryAfterSeconds: number | undefined): Promise<void> {
  const waitMs =
    retryAfterSeconds !== undefined && retryAfterSeconds > 0
      ? retryAfterSeconds * MS_PER_SECOND
      : AUTH_WINDOW_MS;
  const until = Date.now() + waitMs;
  await withLedger((ledger) => {
    ledger.pausedUntilMs = Math.max(ledger.pausedUntilMs, until);
    return 0;
  });
}

/** Grants a slot and returns 0, or returns how long the caller has to wait for one. */
function claimSlot(ledger: Ledger): number {
  const now = Date.now();
  if (ledger.pausedUntilMs > now) return ledger.pausedUntilMs - now;

  // The server's window is a sliding log, so ours is too: an allowance comes back one
  // request at a time, not all at once on the minute.
  ledger.spentAtMs = ledger.spentAtMs.filter((at) => at > now - AUTH_WINDOW_MS);
  if (ledger.spentAtMs.length >= SUITE_ALLOWANCE) {
    return ledger.spentAtMs[0] + AUTH_WINDOW_MS - now;
  }

  ledger.spentAtMs.push(now);
  return 0;
}

/** Read, change and write the ledger with no other worker in between. */
async function withLedger<T>(change: (ledger: Ledger) => T): Promise<T> {
  await acquireLock();
  try {
    const ledger = readLedger();
    const result = change(ledger);
    writeFileSync(LEDGER_PATH, JSON.stringify(ledger));
    return result;
  } finally {
    rmSync(LOCK_PATH, { force: true });
  }
}

/** Exclusive creation is the lock: only one process can win the same `wx` open. */
async function acquireLock(): Promise<void> {
  mkdirSync(dirname(LEDGER_PATH), { recursive: true });
  for (;;) {
    try {
      closeSync(openSync(LOCK_PATH, "wx"));
      return;
    } catch {
      breakStaleLock();
      await sleep(LOCK_RETRY_MS);
    }
  }
}

function breakStaleLock(): void {
  try {
    if (Date.now() - statSync(LOCK_PATH).mtimeMs > STALE_LOCK_MS) {
      rmSync(LOCK_PATH, { force: true });
    }
  } catch {
    // The holder released it between the two calls. Nothing to break.
  }
}

function readLedger(): Ledger {
  try {
    const parsed: unknown = JSON.parse(readFileSync(LEDGER_PATH, "utf8"));
    if (isLedger(parsed)) return parsed;
  } catch {
    // No ledger yet, or one left by an older shape of this file.
  }
  return { spentAtMs: [], pausedUntilMs: 0 };
}

function isLedger(value: unknown): value is Ledger {
  if (typeof value !== "object" || value === null) return false;
  const candidate = value as Partial<Ledger>;
  return (
    Array.isArray(candidate.spentAtMs) &&
    candidate.spentAtMs.every((at) => typeof at === "number") &&
    typeof candidate.pausedUntilMs === "number"
  );
}
