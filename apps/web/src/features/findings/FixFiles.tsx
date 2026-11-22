import { useEffect, useState } from "react";

import { ApiError, isConflict, problemCode } from "@/shared/api-client";
import { Button } from "@/shared/components/Button";
import { ConfirmPanel } from "@/shared/components/ConfirmPanel";
import { TextField } from "@/shared/components/TextField";
import { rememberLinkReturn } from "@/shared/github-link-return";
import type { AskedRound, FindingSourceFix, Proposal, SourceFiles, SourceFix } from "./api";
import {
  patchDownloadUrl,
  useAskSourceFix,
  useCancelSourceFix,
  useChangeSourceFiles,
  useCheckMyAccess,
  usePreparePatch,
  useSendSourceFiles,
  useStartGitHubLink,
} from "./api";
import { FixDiff } from "./FixDiff";
import { FixDraft } from "./FixDraft";
import { FixVerdict } from "./FixVerdict";
import { askedAgo, DIFF_WORDS, minutesLeft, SOURCE_WORDS, sourceTitle } from "./fix-page";

const MAX_TYPED = 5;
const LINK = "text-brand-text underline-offset-2 hover:underline [overflow-wrap:anywhere]";

/**
 * Rung 3 (F5 Step 3): Ask, then the list of files that would be sent, then Send or Cancel.
 * Every sentence comes from the API in this person's shape; a viewer's read holds no path
 * (AC10). Send hands the list to the AI role; the written suggestion (Step 4) reads back
 * here, with its diff, for a person who may read its code.
 */
export function SourceSection({
  findingId,
  source,
  now,
}: {
  findingId: string;
  source: FindingSourceFix;
  now: number;
}) {
  const { latest, offer } = source;
  useEffect(() => {
    const title = sourceTitle(latest?.state);
    if (title === null) return;
    const before = document.title;
    document.title = title;
    return () => {
      document.title = before;
    };
  }, [latest?.state]);
  return (
    <div className="flex min-w-0 flex-col gap-3" data-testid="source-fix">
      {source.support_view_note ? (
        <p className="text-ink-dim text-sm">{source.support_view_note}</p>
      ) : null}
      {latest ? <SourceState findingId={findingId} fix={latest} now={now} /> : null}
      {source.line ? (
        <p className="max-w-prose text-ink-dim text-sm">{source.line.sentence}</p>
      ) : null}
      {offer && (!latest || !inFlight(latest.state)) ? (
        offer.needs_link ? (
          <LinkStep sentence={offer.link_sentence} />
        ) : (
          <AskForm findingId={findingId} source={source} />
        )
      ) : null}
    </div>
  );
}

function inFlight(state: string): boolean {
  return state === "gathering" || state === "gathered" || state === "proposing";
}

function LinkStep({ sentence }: { sentence: string | null }) {
  const start = useStartGitHubLink();
  return (
    <div className="flex flex-col gap-2">
      <p className="max-w-prose text-ink text-sm">{sentence}</p>
      <div>
        <Button
          variant="secondary"
          pending={start.isPending}
          onClick={() =>
            start.mutate(undefined, {
              onSuccess: (answer) => {
                rememberLinkReturn(window.location.pathname, answer.attempt_id);
                window.location.assign(answer.authorize_url);
              },
            })
          }
        >
          {SOURCE_WORDS.link}
        </Button>
      </div>
    </div>
  );
}

function AskForm({ findingId, source }: { findingId: string; source: FindingSourceFix }) {
  const offer = source.offer;
  const ask = useAskSourceFix(findingId);
  const [paths, setPaths] = useState<string[]>([""]);
  const [notify, setNotify] = useState(true);
  if (offer === null) return null;
  const named = offer.result === "ready_without_stack";
  const typed = paths.map((path) => path.trim()).filter(Boolean);
  const refused = ask.error && !isConflict(ask.error) ? problemCode(ask.error) : undefined;
  return (
    <form
      className="flex min-w-0 flex-col gap-2"
      onSubmit={(event) => {
        event.preventDefault();
        ask.mutate({ paths: typed, notify });
      }}
    >
      <p className="max-w-prose text-ink-dim text-sm">{offer.label}</p>
      {named ? <p className="max-w-prose text-ink text-sm">{offer.name_files_lead}</p> : null}
      {named
        ? paths.map((path, index) => (
            <TextField
              // biome-ignore lint/suspicious/noArrayIndexKey: rows are positions a person fills in
              key={index}
              label={SOURCE_WORDS.pathLabel(index)}
              name={`path-${index}`}
              value={path}
              error={refused && index === paths.length - 1 ? errorDetail(ask.error) : undefined}
              onChange={(value) => setPaths(paths.map((each, at) => (at === index ? value : each)))}
            />
          ))
        : null}
      {named && paths.length < MAX_TYPED ? (
        <div>
          <Button variant="secondary" onClick={() => setPaths([...paths, ""])}>
            {SOURCE_WORDS.addPath}
          </Button>
        </div>
      ) : null}
      <label className="flex items-center gap-2 text-ink text-sm">
        <input
          type="checkbox"
          checked={notify}
          onChange={(event) => setNotify(event.target.checked)}
        />
        {SOURCE_WORDS.email}
      </label>
      <div className="flex flex-wrap items-center gap-3">
        <Button type="submit" pending={ask.isPending} disabled={named && typed.length === 0}>
          {offer.action}
        </Button>
        <span className="text-ink-dim text-sm">{SOURCE_WORDS.triesLeft(offer.tries_left)}</span>
      </div>
      {ask.error && isConflict(ask.error) ? (
        <p role="alert" className="max-w-prose text-ink text-sm">
          {errorDetail(ask.error)}
        </p>
      ) : null}
    </form>
  );
}

/** The API's own sentence for a refusal, shown as it says it. */
function errorDetail(error: unknown): string | undefined {
  return error instanceof ApiError ? error.problem?.detail : undefined;
}

function SourceState({ findingId, fix, now }: { findingId: string; fix: SourceFix; now: number }) {
  return (
    <div className="flex min-w-0 flex-col gap-2" data-testid="source-state">
      <h3 className="font-medium text-ink text-sm">{fix.heading}</h3>
      {fix.sentence ? <p className="max-w-prose text-ink text-sm">{fix.sentence}</p> : null}
      {fix.files ? <FileList findingId={findingId} fix={fix} files={fix.files} now={now} /> : null}
      {fix.proposal ? <Written proposal={fix.proposal} now={now} /> : null}
      {fix.code_withheld ? <CheckMyAccess findingId={findingId} fix={fix} /> : null}
      {fix.patch_action || fix.patch ? <PreparePatch findingId={findingId} fix={fix} /> : null}
      {fix.draft ? (
        <FixDraft
          findingId={findingId}
          fixId={fix.id}
          draft={fix.draft}
          linkStep={(sentence) => <LinkStep sentence={sentence} />}
        />
      ) : null}
      {fix.verdict ? (
        <FixVerdict findingId={findingId} fixId={fix.id} verdict={fix.verdict} />
      ) : null}
    </div>
  );
}

/**
 * Rung 4 (Step 5): Prepare patch asks this person's own check run to re-read the files from
 * GitHub and rebuild the patch; while it works the read polls, and once it stands the
 * Download is a plain link to the file the API serves from that check alone (UR6, FR20a).
 */
function PreparePatch({ findingId, fix }: { findingId: string; fix: SourceFix }) {
  const prepare = usePreparePatch(findingId, fix.id);
  const patch = fix.patch;
  return (
    <div className="flex min-w-0 flex-col gap-2" data-testid="prepare-patch">
      {patch ? (
        <>
          <h4 className="font-medium text-ink text-sm">{patch.heading}</h4>
          {patch.sentence ? (
            <p className="max-w-prose text-ink text-sm [overflow-wrap:anywhere]">
              {patch.sentence}
            </p>
          ) : null}
        </>
      ) : null}
      <div className="flex flex-wrap items-center gap-3">
        {patch?.downloadable ? (
          <a
            className="inline-flex min-h-11 items-center font-medium text-brand-text text-sm underline-offset-2 hover:underline"
            href={patchDownloadUrl(findingId, fix.id)}
            download
          >
            {SOURCE_WORDS.downloadPatch}
          </a>
        ) : null}
        {fix.patch_action ? (
          <Button variant="secondary" pending={prepare.isPending} onClick={() => prepare.mutate()}>
            {fix.patch_action}
          </Button>
        ) : null}
      </div>
      {prepare.error ? (
        <p role="alert" className="max-w-prose text-ink text-sm">
          {errorDetail(prepare.error)}
        </p>
      ) : null}
    </div>
  );
}

/** The written suggestion (Step 4): lead, starts-from, the diff, files read, not checked,
    and tokens with age. Everything is the API's checked text in this person's shape. */
function Written({ proposal, now }: { proposal: Proposal; now: number }) {
  return (
    <div className="flex min-w-0 flex-col gap-3" data-testid="source-proposal">
      <p className="max-w-prose text-ink text-sm [overflow-wrap:anywhere]">{proposal.lead}</p>
      {proposal.starts_from ? (
        <p className="max-w-prose text-ink-dim text-sm">{proposal.starts_from}</p>
      ) : null}
      <FixDiff proposal={proposal} />
      <div className="flex flex-col gap-1 text-sm">
        <h4 className="text-ink-dim">{DIFF_WORDS.filesRead}</h4>
        <ul className="flex flex-col gap-1">
          {proposal.files_read.map((file) => (
            <li key={file.path} className="min-w-0 text-ink-dim">
              {file.github_url ? (
                <a
                  className={`font-mono ${LINK}`}
                  href={file.github_url}
                  rel="noreferrer"
                  target="_blank"
                >
                  {file.path}
                </a>
              ) : (
                <span className="font-mono [overflow-wrap:anywhere]">{file.path}</span>
              )}{" "}
              · {file.why}
            </li>
          ))}
        </ul>
      </div>
      {proposal.left_out.length > 0 ? (
        <div className="flex flex-col gap-1 text-sm">
          <h4 className="text-ink-dim">{DIFF_WORDS.leftOut}</h4>
          <ul className="flex flex-col gap-1">
            {proposal.left_out.map((left) => (
              <li key={left.path} className="text-ink-dim [overflow-wrap:anywhere]">
                <span className="font-mono">{left.path}</span>: {left.why}
              </li>
            ))}
          </ul>
        </div>
      ) : null}
      <div className="flex flex-col gap-1 text-sm">
        <h4 className="text-ink-dim">{DIFF_WORDS.notChecked}</h4>
        <ul className="flex flex-col gap-1">
          {proposal.not_checked.map((line) => (
            <li key={line} className="max-w-prose text-ink-dim [overflow-wrap:anywhere]">
              {line}
            </li>
          ))}
        </ul>
      </div>
      <p className="text-ink-dim text-xs">
        {DIFF_WORDS.written(
          proposal.written_at ? askedAgo(proposal.written_at, now) : "—",
          proposal.commit,
          proposal.tokens,
        )}
      </p>
    </div>
  );
}

/** D20: the owner limits quoted code; this person's own check opens it for an hour. */
function CheckMyAccess({ findingId, fix }: { findingId: string; fix: SourceFix }) {
  const check = useCheckMyAccess(findingId, fix.id);
  const running = fix.check?.status === "running";
  return (
    <div className="flex min-w-0 flex-col gap-2" data-testid="check-my-access">
      <p className="max-w-prose text-ink text-sm">{fix.code_withheld}</p>
      {fix.check && !fix.check.passed && fix.check.sentence ? (
        <p className="max-w-prose text-ink text-sm">{fix.check.sentence}</p>
      ) : null}
      {fix.check_action ? (
        <div>
          <Button
            variant="secondary"
            pending={check.isPending || running}
            onClick={() => check.mutate()}
          >
            {fix.check_action}
          </Button>
        </div>
      ) : null}
      {check.error ? (
        <p role="alert" className="max-w-prose text-ink text-sm">
          {errorDetail(check.error)}
        </p>
      ) : null}
    </div>
  );
}

/** FR15b at round 2 or 3: what the AI asked for, why, and its own words, labelled. */
function AskedFiles({ asked }: { asked: AskedRound }) {
  return (
    <div className="flex min-w-0 flex-col gap-2 text-sm" data-testid="asked-round">
      <p className="text-ink">{asked.lead}</p>
      <ul className="flex flex-col gap-2">
        {asked.asked.map((file) => (
          <li key={file.path} className="flex min-w-0 flex-col gap-0.5">
            <span className="font-mono [overflow-wrap:anywhere]">{file.path}</span>
            <span className="text-ink-dim [overflow-wrap:anywhere]">{file.because}</span>
            {file.model_words ? (
              <span className="text-ink-dim">
                {asked.model_words_label}:{" "}
                <span className="text-ink [overflow-wrap:anywhere]">{file.model_words}</span>
              </span>
            ) : null}
          </li>
        ))}
      </ul>
      {asked.refused.map((refused) => (
        <p key={refused.path} className="text-ink-dim [overflow-wrap:anywhere]">
          {DIFF_WORDS.refusedAsked(refused.path, refused.why)}
        </p>
      ))}
      {asked.unmatched > 0 ? (
        <p className="text-ink-dim">{SOURCE_WORDS.unmatched(asked.unmatched)}</p>
      ) : null}
      <p className="text-ink-dim">{asked.round_sentence}</p>
    </div>
  );
}

function FileList({
  findingId,
  fix,
  files,
  now,
}: {
  findingId: string;
  fix: SourceFix;
  files: SourceFiles;
  now: number;
}) {
  const change = useChangeSourceFiles(findingId, fix.id);
  const send = useSendSourceFiles(findingId, fix.id);
  const cancel = useCancelSourceFix(findingId, fix.id);
  const [removed, setRemoved] = useState<string[]>([]);
  const [added, setAdded] = useState("");
  const [hint, setHint] = useState("");
  const [confirmTop, setConfirmTop] = useState(false);
  const changed = removed.length > 0 || added.trim() !== "";
  return (
    <div className="flex min-w-0 flex-col gap-3">
      {files.asked_round ? <AskedFiles asked={files.asked_round} /> : null}
      {files.commit_sentence ? (
        <p className="text-ink-dim text-sm">{files.commit_sentence}</p>
      ) : null}
      <ul className="flex flex-col gap-2">
        {files.files.map((file) => (
          <li key={file.path} className="flex min-w-0 flex-col gap-0.5 text-sm">
            <span className="flex flex-wrap items-baseline gap-x-2">
              {file.github_url ? (
                <a
                  className={`font-mono ${LINK}`}
                  href={file.github_url}
                  rel="noreferrer"
                  target="_blank"
                >
                  {file.path}
                </a>
              ) : (
                <span className="font-mono [overflow-wrap:anywhere]">{file.path}</span>
              )}
              <label className="flex items-center gap-1 text-ink-dim">
                <input
                  type="checkbox"
                  checked={removed.includes(file.path)}
                  onChange={(event) =>
                    setRemoved(
                      event.target.checked
                        ? [...removed, file.path]
                        : removed.filter((path) => path !== file.path),
                    )
                  }
                />
                {SOURCE_WORDS.remove}
              </label>
            </span>
            <span className="text-ink-dim">
              {file.why} · {SOURCE_WORDS.bytes(file.bytes)}
              {file.lines_sent ? ` · ${SOURCE_WORDS.linesSent(file.lines_sent)}` : ""}
            </span>
            {file.moved_on_default ? (
              <span className="text-ink-dim">{SOURCE_WORDS.moved}</span>
            ) : null}
          </li>
        ))}
      </ul>
      {files.left_out.length > 0 ? (
        <div className="flex flex-col gap-1 text-sm">
          <h4 className="text-ink-dim">{SOURCE_WORDS.leftOut}</h4>
          <ul className="flex flex-col gap-1">
            {files.left_out.map((left) => (
              <li key={left.path} className="text-ink-dim">
                <span className="font-mono [overflow-wrap:anywhere]">{left.path}</span>: {left.why}
              </li>
            ))}
          </ul>
        </div>
      ) : null}
      {files.unmatched > 0 ? (
        <p className="text-ink-dim text-sm">{SOURCE_WORDS.unmatched(files.unmatched)}</p>
      ) : null}
      {files.typed_missing.map((miss) => (
        <p key={miss.path} className="text-ink text-sm [overflow-wrap:anywhere]">
          {miss.sentence}
        </p>
      ))}
      <p className="max-w-prose text-ink-dim text-sm">{files.forgeable}</p>
      <p className="text-ink-dim text-sm">
        {SOURCE_WORDS.lapses(minutesLeft(files.lapses_at, now))}
      </p>
      <TextField label={SOURCE_WORDS.addPath} name="add-path" value={added} onChange={setAdded} />
      {changed ? (
        <div>
          <Button
            variant="secondary"
            pending={change.isPending}
            onClick={() =>
              change.mutate({ remove: removed, add: added.trim() ? [added.trim()] : [] })
            }
          >
            {SOURCE_WORDS.read}
          </Button>
        </div>
      ) : (
        <div className="min-w-0">
          <ConfirmPanel label={files.send}>
            <TextField label={SOURCE_WORDS.hint} name="hint" value={hint} onChange={setHint} />
            {files.top_left_out ? (
              <label className="flex items-center gap-2 text-ink text-sm">
                <input
                  type="checkbox"
                  checked={confirmTop}
                  onChange={(event) => setConfirmTop(event.target.checked)}
                />
                {SOURCE_WORDS.confirmTop}
              </label>
            ) : null}
            <p className="text-ink text-sm [overflow-wrap:anywhere]">{files.send_host}</p>
            <div className="flex flex-wrap gap-2">
              <Button
                pending={send.isPending}
                onClick={() =>
                  send.mutate({
                    files_digest: files.digest,
                    confirm_top_left_out: confirmTop,
                    hint: hint.trim() || null,
                  })
                }
              >
                {files.send}
              </Button>
              <Button
                variant="secondary"
                pending={cancel.isPending}
                onClick={() => cancel.mutate({})}
              >
                {SOURCE_WORDS.cancel}
              </Button>
            </div>
            {send.error ? (
              <p role="alert" className="max-w-prose text-ink text-sm">
                {errorDetail(send.error)}
              </p>
            ) : null}
          </ConfirmPanel>
        </div>
      )}
    </div>
  );
}
