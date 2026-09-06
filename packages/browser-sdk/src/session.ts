import { SESSION_STORAGE_KEY_PREFIX } from "./constants";
import { newSessionId } from "./ids";

/**
 * A visitor's session id, the only join between their vitals, their errors and
 * their page views — there is no session table. It is promoted to a column in
 * `web_vitals` (migration 000030) and stays inside the attributes on everything
 * else, so a vital can be traced back to the visit without reading JSON.
 *
 * localStorage rather than sessionStorage: sessionStorage dies with the tab, so
 * every new tab would be a new session and a closed-then-reopened tab would lose
 * one inside the idle window. Every Safari storage mechanism is cleared after
 * seven days without interaction on the site, so a missing record is normal.
 */
interface StoredSession {
  id: string;
  startedAt: number;
  lastActivityAt: number;
}

export interface SessionOptions {
  siteKey: string;
  idleTimeoutMs: number;
  maxDurationMs: number;
}

export interface Session {
  /** The id to stamp on a record, rotating first if the stored one has expired. */
  currentId(now: number): string;
}

export function createSession(options: SessionOptions): Session {
  const storageKey = SESSION_STORAGE_KEY_PREFIX + options.siteKey;
  let inMemory: StoredSession | undefined;

  return {
    currentId(now: number): string {
      const stored = read(storageKey) ?? inMemory;
      const live = stored !== undefined && isLive(stored, now, options);
      const session = live && stored ? { ...stored, lastActivityAt: now } : startSession(now);
      inMemory = session;
      write(storageKey, session);
      return session.id;
    },
  };
}

function isLive(session: StoredSession, now: number, options: SessionOptions): boolean {
  const idle = now - session.lastActivityAt;
  const age = now - session.startedAt;
  // A clock that moved backwards reads as a negative age. Treat that as expired
  // rather than trusting a start time in the future.
  return idle >= 0 && idle < options.idleTimeoutMs && age >= 0 && age < options.maxDurationMs;
}

function startSession(now: number): StoredSession {
  return { id: newSessionId(), startedAt: now, lastActivityAt: now };
}

/** Storage can be absent, disabled, or throw on access; none of that is an error. */
function read(storageKey: string): StoredSession | undefined {
  try {
    const raw = localStorage.getItem(storageKey);
    if (!raw) return undefined;
    const parsed: unknown = JSON.parse(raw);
    return isStoredSession(parsed) ? parsed : undefined;
  } catch {
    return undefined;
  }
}

function write(storageKey: string, session: StoredSession): void {
  try {
    localStorage.setItem(storageKey, JSON.stringify(session));
  } catch {
    // Private mode, a full quota, or storage switched off. The in-memory copy stands.
  }
}

function isStoredSession(value: unknown): value is StoredSession {
  if (typeof value !== "object" || value === null) return false;
  const candidate = value as Partial<StoredSession>;
  return (
    typeof candidate.id === "string" &&
    candidate.id !== "" &&
    typeof candidate.startedAt === "number" &&
    typeof candidate.lastActivityAt === "number"
  );
}
