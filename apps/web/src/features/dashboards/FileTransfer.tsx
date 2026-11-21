import { type ChangeEvent, useRef, useState } from "react";

import { isInvalid } from "@/shared/api-client";
import { Button } from "@/shared/components/Button";

import type { DashboardDocument } from "./api";
import { useValidateDashboard } from "./api";
import {
  downloadDashboard,
  EXPORT_WARNING,
  exportFileName,
  readDashboardFile,
  refusalWords,
} from "./transfer";

/**
 * Import and export as controls (F20, FR42, design *The six states*): import says
 * "Checking the file", "Could not check the file" with a retry, or the reasons in words
 * with nothing opened; export says what the file holds before it is made (AC119).
 */

type ImportState =
  | { kind: "idle" }
  | { kind: "refused"; reasons: string[] }
  | { kind: "failed"; document: unknown };

const NOT_CHECKED = "Could not check the file.";

/**
 * Choose a file, check it, and hand on only what passed. A viewer is never offered this
 * (FR42): the caller mounts it for someone who may edit.
 */
export function ImportFile({
  label,
  onOpened,
}: {
  label: string;
  /** The document the API passed, which opens as an unsaved draft. */
  onOpened: (document: DashboardDocument) => void;
}) {
  const input = useRef<HTMLInputElement>(null);
  const validate = useValidateDashboard();
  const [state, setState] = useState<ImportState>({ kind: "idle" });

  const check = (document: unknown) => {
    setState({ kind: "idle" });
    validate.mutate(document, {
      onSuccess: (passed) => onOpened(passed.document),
      onError: (error) =>
        setState(
          isInvalid(error)
            ? { kind: "refused", reasons: refusalWords(error, NOT_CHECKED) }
            : { kind: "failed", document },
        ),
    });
  };

  const chosen = async (event: ChangeEvent<HTMLInputElement>) => {
    const file = event.target.files?.[0];
    // Cleared, so choosing the same file again after fixing it is a new choice.
    event.target.value = "";
    if (!file) return;
    const reading = await readDashboardFile(file);
    if (reading.kind === "refused") {
      setState(reading);
      return;
    }
    check(reading.document);
  };

  return (
    <div className="flex flex-col items-start gap-2">
      <input
        ref={input}
        type="file"
        accept="application/json,.json"
        className="sr-only"
        tabIndex={-1}
        aria-hidden="true"
        onChange={(event) => void chosen(event)}
      />
      <Button
        variant="secondary"
        pending={validate.isPending}
        pendingLabel="Checking the file"
        onClick={() => input.current?.click()}
        className="min-h-11 md:min-h-0"
      >
        {label}
      </Button>
      {state.kind === "refused" ? (
        <div
          role="alert"
          className="rounded-sm border border-critical-border bg-critical-subtle p-3 text-critical-text text-sm"
        >
          <p className="font-medium">This file was not opened:</p>
          <ul className="list-disc pl-5">
            {state.reasons.map((reason) => (
              <li key={reason}>{reason}</li>
            ))}
          </ul>
        </div>
      ) : null}
      {state.kind === "failed" ? (
        <div role="alert" className="flex flex-wrap items-center gap-2 text-sm text-warning-text">
          <p>{NOT_CHECKED}</p>
          <Button variant="secondary" onClick={() => check(state.document)}>
            Try again
          </Button>
        </div>
      ) : null}
    </div>
  );
}

/** Export, saying first what the file holds (FR62, AC119). Anyone who can read may export. */
export function ExportFile({
  dashboard,
}: {
  dashboard: Pick<DashboardDocument, "title" | "description" | "definition">;
}) {
  const [open, setOpen] = useState(false);
  return (
    <div className="flex flex-col items-start gap-1">
      <button
        type="button"
        aria-expanded={open}
        onClick={() => setOpen((before) => !before)}
        className="inline-flex min-h-11 items-center text-brand text-sm underline md:min-h-0"
      >
        Export
      </button>
      {open ? (
        <div className="flex flex-col items-start gap-2 rounded-sm border border-line bg-raised p-2 text-sm">
          <p className="text-ink">{EXPORT_WARNING}</p>
          <div className="flex flex-wrap gap-2">
            <Button
              onClick={() => {
                downloadDashboard(dashboard);
                setOpen(false);
              }}
            >
              Download {exportFileName(dashboard.title)}
            </Button>
            <Button variant="secondary" onClick={() => setOpen(false)}>
              Cancel
            </Button>
          </div>
        </div>
      ) : null}
    </div>
  );
}
