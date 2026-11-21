import type { DiffLine, Proposal, ProposalFile, ProposalFlag } from "./api";
import { Segments } from "./ExplanationSection";
import { DIFF_WORDS, flagAnchor, visibleEscapes } from "./fix-page";

const LINK = "text-brand-text underline-offset-2 hover:underline [overflow-wrap:anywhere]";

/**
 * A written suggestion's change (F5 design, *Section parts*, Diff; spec AC18, AC19b, AC36).
 * **Text nodes built by Nexovue:** every line is React text, never markup, and control and
 * bidirectional characters show as visible escapes. One native disclosure per file, open
 * by default only when the whole suggestion is short. Old and new numbers sit in a gutter
 * (the new number alone below 30rem); `+` and `-` stay the main marker, with a quiet fill
 * as a secondary sign. Lines wrap rather than scroll, so the diff has the window.
 */
export function FixDiff({ proposal, zone }: { proposal: Proposal; zone?: string }) {
  return (
    <div className="flex min-w-0 flex-col gap-3" data-testid="fix-diff">
      {proposal.flags.length > 0 ? <FlagList flags={proposal.flags} /> : null}
      <p className="max-w-prose text-ink-dim text-xs">{proposal.facts_note}</p>
      {proposal.files.map((file) => (
        <FileDiff
          key={file.ref}
          file={file}
          explanationLabel={proposal.explanation_label}
          zone={zone}
        />
      ))}
    </div>
  );
}

/** Above the diff, each naming a file and line, and jumping to it (FR18). */
function FlagList({ flags }: { flags: ProposalFlag[] }) {
  return (
    <ul className="flex flex-col gap-1 text-sm" aria-label={DIFF_WORDS.flags}>
      {flags.map((flag) => (
        <li
          key={`${flag.kind}-${flag.path}-${flag.side}-${flag.line}`}
          className="[overflow-wrap:anywhere]"
        >
          {flag.line === null ? (
            <span>{DIFF_WORDS.flagInFile(flag.label, flag.path)}</span>
          ) : (
            <a className={LINK} href={`#${flagAnchor(flag.path, flag.side, flag.line)}`}>
              {DIFF_WORDS.flagAtLine(flag.label, flag.path, flag.line)}
            </a>
          )}
        </li>
      ))}
    </ul>
  );
}

function FileDiff({
  file,
  explanationLabel,
  zone,
}: {
  file: ProposalFile;
  explanationLabel: string;
  zone?: string;
}) {
  return (
    <details open={file.open} className="min-w-0 rounded-sm border border-line">
      <summary className="flex min-h-11 cursor-pointer items-center gap-2 px-3 py-2 text-sm">
        <span className="min-w-0 font-mono [overflow-wrap:anywhere]">{file.path}</span>
        <span className="shrink-0 text-ink-dim">{DIFF_WORDS.counts(file.added, file.removed)}</span>
      </summary>
      <div className="flex min-w-0 flex-col gap-2 border-line border-t px-1 py-2">
        {file.hunks === null ? (
          <p className="px-2 text-ink-dim text-sm">{file.context_note}</p>
        ) : (
          file.hunks.map((hunk) => (
            <div key={hunk.header} className="min-w-0">
              <p className="px-2 font-mono text-ink-dim text-xs">{hunk.header}</p>
              <ol className="min-w-0 font-mono text-xs" aria-label={hunk.header}>
                {hunk.lines.map((line, index) => (
                  <DiffRow
                    // biome-ignore lint/suspicious/noArrayIndexKey: a hunk's rows are positional
                    key={index}
                    path={file.path}
                    line={line}
                  />
                ))}
              </ol>
            </div>
          ))
        )}
        {file.explanations.length > 0 ? (
          <div className="mx-2 flex flex-col gap-1 border-line border-l-2 pl-3">
            <p className="text-ink-dim text-xs">{explanationLabel}</p>
            {file.explanations.map((explanation) => (
              <p
                key={explanation.segments.map((each) => each.text ?? each.name).join("")}
                className="max-w-prose text-ink text-sm"
              >
                <Segments segments={explanation.segments} zone={zone} />
              </p>
            ))}
          </div>
        ) : null}
      </div>
    </details>
  );
}

const FILL: Record<DiffLine["marker"], string> = {
  "+": "bg-raised",
  "-": "bg-hover",
  " ": "",
};

function DiffRow({ path, line }: { path: string; line: DiffLine }) {
  const side = line.marker === "-" ? "old" : "new";
  const number = line.marker === "-" ? line.old : line.new;
  const Text = line.marker === "+" ? "ins" : line.marker === "-" ? "del" : "span";
  return (
    <li
      id={number === null ? undefined : flagAnchor(path, side, number)}
      className={`grid min-w-0 grid-cols-[auto_auto_minmax(0,1fr)] gap-x-2 px-2 ${FILL[line.marker]}`}
    >
      <span className="select-none text-right text-ink-dim tabular-nums" aria-hidden="true">
        <span className="hidden @[30rem]:inline">{line.old ?? ""}</span>{" "}
        <span>{line.new ?? (line.marker === "-" ? line.old : "")}</span>
      </span>
      <span className="select-none" aria-hidden="true">
        {line.marker}
      </span>
      <span className="min-w-0 whitespace-pre-wrap [overflow-wrap:anywhere]">
        <span className="sr-only">{DIFF_WORDS.marker(line.marker)} </span>
        <Text className="no-underline [text-decoration:none]">
          {visibleEscapes(line.text).map((piece, index) =>
            piece.escaped ? (
              <span
                // biome-ignore lint/suspicious/noArrayIndexKey: pieces of one line, in order
                key={index}
                className="rounded-sm border border-line px-0.5"
                title={DIFF_WORDS.escaped}
              >
                {piece.text}
              </span>
            ) : (
              piece.text
            ),
          )}
        </Text>
        {line.flags.length > 0 ? (
          <span className="ml-2 font-sans text-ink-dim">[{line.flags.join(", ")}]</span>
        ) : null}
      </span>
    </li>
  );
}
