import { useId, useState } from "react";

import { formatBinaryBytes } from "@/shared/format/bytes";

import { useUploadCheckFile } from "./api";
import { problemMessageOf } from "./api-problems";
import { attachedFileLabel } from "./check-draft";
import { LIMITS } from "./check-model";
import { FieldError } from "./fields";

/**
 * Attaching a file a request sends. Both limits — the size and how many a request
 * may send — are stated before anything is chosen, refused here before the upload,
 * and enforced by the API on what actually arrives (FR4, AC6).
 *
 * **Refused before the upload, not at save.** A file that cannot belong to this
 * request should never reach the object store: it would sit there as an orphan
 * until the sweep, and the person would have waited for an upload we then refuse.
 */
export function FileAttach({
  label,
  projectId,
  fileId,
  fileName,
  requestIsFull = false,
  onAttached,
  error,
}: {
  label: string;
  projectId: string | undefined;
  fileId: string;
  fileName: string;
  /** This request already sends as many files as it may, and this row holds none. */
  requestIsFull?: boolean;
  onAttached: (file: { id: string; name: string }) => void;
  error?: string;
}) {
  const id = useId();
  const upload = useUploadCheckFile(projectId);
  // Refused before any upload, so the mutation holds no trace of it.
  const [tooLarge, setTooLarge] = useState(false);
  const full = requestIsFull && !fileId;
  const problem = tooLarge
    ? `That file is over ${formatBinaryBytes(LIMITS.maxFileBytes)}.`
    : full
      ? `This request already sends ${LIMITS.maxFilesPerRequest} files, the most it may.`
      : (error ??
        (upload.error
          ? (problemMessageOf(upload.error) ?? "The file could not be attached.")
          : undefined));

  return (
    <div className="flex min-w-0 flex-col gap-1">
      <div className="flex flex-wrap items-center gap-2">
        <label
          htmlFor={id}
          className={`rounded-sm border border-line px-2 py-1 text-sm focus-within:outline-2 focus-within:outline-brand ${
            full
              ? "cursor-not-allowed text-ink-dim opacity-60"
              : "cursor-pointer text-ink-dim hover:border-line-strong hover:text-ink"
          }`}
        >
          {upload.isPending ? "Uploading…" : fileId ? "Replace file" : "Attach file"}
          <input
            id={id}
            type="file"
            aria-label={label}
            className="sr-only"
            disabled={upload.isPending || full}
            aria-describedby={problem ? `${id}-error` : `${id}-hint`}
            onChange={(event) => {
              const file = event.target.files?.[0];
              event.target.value = "";
              if (!file || full) return;
              upload.reset();
              setTooLarge(file.size > LIMITS.maxFileBytes);
              if (file.size > LIMITS.maxFileBytes) return;
              upload.mutate(file, {
                onSuccess: (stored) => onAttached({ id: stored.id, name: stored.name }),
              });
            }}
          />
        </label>
        <span className="min-w-0 truncate font-mono text-ink text-xs">
          {fileId ? (
            attachedFileLabel(fileId, fileName)
          ) : (
            <span className="text-ink-dim">No file attached</span>
          )}
        </span>
      </div>
      {problem ? (
        <FieldError id={`${id}-error`} message={problem} />
      ) : (
        <p id={`${id}-hint`} className="text-ink-dim text-xs">
          Up to {formatBinaryBytes(LIMITS.maxFileBytes)} each, {LIMITS.maxFilesPerRequest} per
          request. Kept with the check; removed when it is deleted.
        </p>
      )}
    </div>
  );
}
