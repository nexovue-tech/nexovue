import { useQuery } from "@tanstack/react-query";
import { parse as parseStack } from "stacktrace-parser";

import { apiClient } from "@/shared/api-client";

/**
 * Turning a minified browser stacktrace back into the source the developer wrote.
 *
 * The parsing happens here rather than in the API because the formats are the
 * browser's and the maintained parsers for them are all JavaScript — there is no
 * equivalent Python library, and a hand-rolled regex is exactly where Safari's
 * `fn@file:line:col` and V8's eval frames quietly go wrong. `stacktrace-parser`
 * is what React Native uses; its only dependency is types-only, so it costs the
 * bundle nothing at runtime.
 *
 * The API takes frames, not a stack string, which is what makes this split work.
 */

/** One frame as the API expects it: 1-based line and column, as browsers report. */
export interface MinifiedFrame {
  file: string;
  line: number;
  column: number;
}

export interface SymbolicatedFrame {
  resolved: boolean;
  file: string;
  line: number;
  column: number;
  /** Empty whenever the map alone could not name the function, which is usual. */
  function_name: string;
  pre_context: string[];
  context_line: string;
  post_context: string[];
}

interface SymbolicateResponse {
  frames: SymbolicatedFrame[];
}

/**
 * A frame the parser could not place is dropped rather than sent.
 *
 * Two shapes get dropped, and both appear in one ordinary Chrome stack. A native
 * frame — `at Array.map (<anonymous>)` — has no position at all. An eval frame —
 * `at eval (eval at evaluate (:311:30), <anonymous>:1:37)` — parses to an *empty*
 * file rather than a null one, which is why the check is for a usable value and
 * not for null: sent as-is the API rejects the whole request as invalid, and one
 * eval frame then costs the trace its symbolication entirely.
 *
 * The frames that remain keep their order, so the trace still reads top-down.
 */
export function framesOf(stacktrace: string): MinifiedFrame[] {
  return parseStack(stacktrace)
    .filter((frame) => usableFile(frame.file) && positive(frame.lineNumber))
    .map((frame) => ({
      file: frame.file as string,
      line: frame.lineNumber as number,
      // A stack without a column is from an engine that omits it, and a column
      // of zero is one that counts from zero. Both become the first column,
      // which is what a 1-based mapping lookup needs.
      column: positive(frame.column) ? (frame.column as number) : 1,
    }));
}

function usableFile(file: string | null): boolean {
  return typeof file === "string" && file.trim() !== "";
}

function positive(value: number | null): boolean {
  return typeof value === "number" && value > 0;
}

/**
 * Whether this occurrence can be symbolicated at all.
 *
 * Both halves are required and neither is guaranteed: an SDK that stamped no
 * `serviceVersion` has no release to match a map on, and a log-shaped exception
 * from a server SDK usually carries no stack. Saying which one is missing is the
 * difference between a screen that looks broken and one that tells you what to
 * configure.
 */
export function canSymbolicate(stacktrace: string, release: string): boolean {
  return stacktrace.trim() !== "" && release.trim() !== "" && framesOf(stacktrace).length > 0;
}

export const symbolicationQueryKey = (projectId: string, release: string, stacktrace: string) =>
  ["symbolication", projectId, release, stacktrace] as const;

/** Asks the API to resolve these frames against the maps uploaded for the build. */
export async function symbolicate(
  projectId: string,
  release: string,
  stacktrace: string,
  signal?: AbortSignal,
): Promise<SymbolicatedFrame[]> {
  const frames = framesOf(stacktrace);
  const answer = await apiClient.post<SymbolicateResponse>(
    "source-maps/symbolicate",
    { frames, release },
    { searchParams: { project_id: projectId }, signal },
  );
  return answer.frames;
}

/**
 * What the view is given about symbolication: an answer, or why there is none.
 *
 * A plain shape rather than the query object, so the screen stays a function of
 * its props and can be rendered in a test with no query client behind it.
 */
/** One resolved frame with an identity, so a repeated frame is still a key. */
export interface IdentifiedFrame {
  id: string;
  frame: SymbolicatedFrame;
}

/**
 * Gives each frame an identity for rendering.
 *
 * Position is part of it because a recursive stack legitimately repeats the same
 * file, line and column, and two identical keys is a React error rather than a
 * cosmetic one.
 */
export function identified(frames: readonly SymbolicatedFrame[]): IdentifiedFrame[] {
  return frames.map((frame, position) => ({ id: `frame-${position}`, frame }));
}

export interface SymbolicationState {
  frames: SymbolicatedFrame[] | undefined;
  isPending: boolean;
  isError: boolean;
}

/**
 * The symbolicated frames for one occurrence, or nothing to show.
 *
 * The query lives beside the parsing rather than in `api.ts` because the two are
 * one concern: what is sent is a function of how the stack parses, and a hook
 * that could be called with an unparseable stack would be a hook that fetches
 * to be told nothing.
 *
 * It never runs when there is no release or no parseable frame, so a server-side
 * exception costs no request at all.
 */
export function useSymbolication(
  projectId: string | undefined,
  stacktrace: string,
  release: string,
) {
  const possible = projectId !== undefined && canSymbolicate(stacktrace, release);
  const query = useQuery({
    queryKey: symbolicationQueryKey(projectId ?? "", release, stacktrace),
    enabled: possible,
    // A build's maps do not change, so a resolved stack is worth keeping for the
    // session rather than re-fetching each time the panel reopens.
    staleTime: Number.POSITIVE_INFINITY,
    queryFn: ({ signal }) => symbolicate(projectId ?? "", release, stacktrace, signal),
  });

  return {
    frames: query.data,
    // A query that was never enabled is "pending" to react-query and idle to a
    // reader, and showing "looking for a source map…" forever is worse than
    // saying there is no release to look one up by.
    isPending: possible && query.isPending,
    isError: query.isError,
  };
}
