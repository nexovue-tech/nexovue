import { useEffect, useState } from "react";

import { Button } from "@/shared/components/Button";
import { InlineFailure } from "@/shared/components/InlineFailure";
import { SkeletonRows } from "@/shared/components/Skeleton";
import { formatZonedTime } from "@/shared/format/zoned";
import { useCan } from "@/shared/identity";
import { useProjectSelection } from "@/shared/projects";

import {
  CHAT_DATA_LEVEL_WORDS,
  CHAT_LIMITS_LINE,
  CHAT_MASKING_LINE,
  CHAT_MCP_LINE,
  CHAT_NO_READS,
  chatNearCeiling,
  countWord,
  EMBEDDING_SENDS,
  embeddingCheckLine,
  embeddingProgress,
  KEYWORD_ONLY_WORDS,
  MEANING_SEARCH_ON,
  maskingOffLine,
  notesNotSearchable,
  SELLING_LINE,
} from "./ai-words";
import { CHECK_ANSWER_MS, useUsage } from "./api";
import {
  type ChatSettings,
  type DataLevel,
  useChatAvailability,
  useChatConsent,
  useChatPreview,
  useChatReads,
  useChatSettings,
  useCheckEmbedding,
  useEmbeddingConsent,
  useSaveChatSettings,
  useSaveEmbedding,
} from "./chat-api";

const HEADING = "font-mono text-2xs text-ink-dim uppercase tracking-caps";

/**
 * `/settings/ai`'s Chat section (F4 T2.12; design, *`/settings/ai`, Chat section*): on or off,
 * the owner's consent and its preview, the data level, the daily limit, counts with no text,
 * a warning near chat's ceiling, what was sent where, tested or not, and the MCP sentence.
 * The screen is `MANAGE_AI`'s already; agreeing is `CONSENT_AI_DISCLOSURE`'s.
 */
export function ChatSection({ now = Date.now() }: { now?: number }) {
  const settings = useChatSettings();
  if (settings.isPending) return <SkeletonRows rows={4} label="Loading chat's settings" />;
  if (settings.isError) {
    return (
      <InlineFailure
        title="Chat's settings could not be loaded"
        error={settings.error}
        onRetry={() => void settings.refetch()}
      />
    );
  }
  return <ChatSettingsView settings={settings.data} now={now} />;
}

function ChatSettingsView({ settings, now }: { settings: ChatSettings; now: number }) {
  const { projectId } = useProjectSelection();
  const availability = useChatAvailability(projectId);
  const usage = useUsage(true);
  const mayConsent = useCan("consent_ai_disclosure");
  const save = useSaveChatSettings();
  const consent = useChatConsent();
  const [dataLevel, setDataLevel] = useState<DataLevel>(settings.data_level);
  const [limit, setLimit] = useState(String(settings.daily_question_limit));
  const [showPreview, setShowPreview] = useState(false);
  const preview = useChatPreview(showPreview ? projectId : undefined);
  const near =
    usage.data !== undefined &&
    chatNearCeiling(
      usage.data.used_tokens + usage.data.reserved_tokens,
      usage.data.monthly_token_limit,
    );

  const saveWith = (enabled: boolean) =>
    save.mutate({
      enabled,
      daily_question_limit: Number(limit),
      data_level: dataLevel,
      mask_emails: settings.mask_emails,
    });

  return (
    <div className="flex flex-col gap-4">
      <p role="status" className="max-w-prose text-ink">
        Chat is {settings.enabled ? "on" : "off"}.{" "}
        {availability.data
          ? availability.data.tested
            ? "This model passed a dated real call: tested."
            : "This model has not passed a dated real call: not tested."
          : null}
      </p>
      {near ? (
        <p className="max-w-prose text-warning-text">
          This month's AI use has passed 80% of chat's share. Questions stop at that share, to keep
          room for critical finding explanations.
        </p>
      ) : null}

      <div className="flex flex-col gap-2">
        <h3 className={HEADING}>Consent</h3>
        {settings.consent_current && settings.consented_at ? (
          <p className="max-w-prose text-ink">
            {settings.consented_by_name ?? "An owner"} agreed on{" "}
            {formatZonedTime(settings.consented_at, { now, withDate: true })}, at "
            {CHAT_DATA_LEVEL_WORDS[settings.data_level]}".
          </p>
        ) : (
          <p className="max-w-prose text-ink">
            {availability.data && availability.data.waiting_for.length > 0
              ? `Waiting for an owner to agree: ${availability.data.waiting_for.join(", ")}.`
              : "No owner has agreed to what chat sends."}
          </p>
        )}
        <p className="max-w-prose text-ink-dim">{SELLING_LINE}</p>
        <p className="max-w-prose text-ink">{CHAT_LIMITS_LINE}</p>
        <div>
          <Button variant="secondary" onClick={() => setShowPreview(!showPreview)}>
            {showPreview ? "Hide what would be sent" : "Show what would be sent"}
          </Button>
        </div>
        {showPreview ? <PreviewView preview={preview} /> : null}
        {mayConsent && preview.data ? (
          <div>
            <Button
              pending={consent.isPending}
              pendingLabel="Agreeing…"
              onClick={() =>
                consent.mutate({
                  fingerprint: preview.data.consent_fingerprint,
                  disclosure_version: preview.data.disclosure_version,
                  data_level: dataLevel,
                })
              }
            >
              Agree to send this, at the level chosen below
            </Button>
          </div>
        ) : null}
        {consent.isSuccess ? (
          <p role="status" className="max-w-prose text-ink">
            Agreed. Once chat is on, members and admins can ask; answers go to the provider named
            above.
          </p>
        ) : null}
        {consent.isError ? (
          <InlineFailure title="The agreement was not recorded" error={consent.error} />
        ) : null}
      </div>

      <fieldset className="flex flex-col gap-2">
        <legend className={HEADING}>Data level</legend>
        {(["facts", "facts_and_text"] as const).map((level) => (
          <label key={level} className="flex items-start gap-2">
            <input
              type="radio"
              name="chat-data-level"
              checked={dataLevel === level}
              onChange={() => setDataLevel(level)}
            />
            <span className="max-w-prose">{CHAT_DATA_LEVEL_WORDS[level]}</span>
          </label>
        ))}
      </fieldset>

      <label className="flex max-w-xs flex-col gap-1">
        <span className={HEADING}>Questions a person may ask a day</span>
        <input
          type="number"
          min={1}
          max={1000}
          value={limit}
          onChange={(event) => setLimit(event.target.value)}
          className="rounded border border-line bg-page px-2 py-1"
        />
      </label>

      <div className="flex gap-2">
        <Button
          pending={save.isPending}
          pendingLabel="Saving…"
          onClick={() => saveWith(settings.enabled)}
        >
          Save
        </Button>
        <Button variant="secondary" onClick={() => saveWith(!settings.enabled)}>
          {settings.enabled ? "Turn chat off" : "Turn chat on"}
        </Button>
      </div>
      {save.isError ? (
        <InlineFailure title="Chat's settings were not saved" error={save.error} />
      ) : null}

      <SearchSection settings={settings} now={now} />
      <Counts settings={settings} />
      <Reads now={now} />
      <p className="max-w-prose text-ink-dim">{CHAT_MCP_LINE}</p>
    </div>
  );
}

/**
 * Meaning search (F4 T6.3; FR26, FR27, AC43, AC85): which model and connection, whether its
 * check passed, re-embedding's progress, and, whenever it is off, why search is keyword only.
 */
function SearchSection({ settings, now }: { settings: ChatSettings; now: number }) {
  const embedding = settings.embedding;
  const mayConsent = useCan("consent_ai_disclosure");
  const save = useSaveEmbedding();
  const agree = useEmbeddingConsent();
  const check = useCheckEmbedding();
  const [model, setModel] = useState(embedding.model ?? "");
  const [own, setOwn] = useState(embedding.source === "own");
  const [wireFormat, setWireFormat] = useState(embedding.own_wire_format ?? "openai_chat");
  const [address, setAddress] = useState(embedding.own_base_url ?? "");
  const [key, setKey] = useState("");
  const [waitingSince, setWaitingSince] = useState<number | null>(null);
  const waiting = waitingSince !== null;
  // Read again every 2 s while a check's result is awaited; the result lands on the settings.
  useChatSettings({ poll: waiting });
  const checkedAt = embedding.checked_at;
  useEffect(() => {
    if (waitingSince === null) return;
    if (checkedAt !== null && Date.parse(checkedAt) >= waitingSince) setWaitingSince(null);
    const timer = setTimeout(() => setWaitingSince(null), CHECK_ANSWER_MS);
    return () => clearTimeout(timer);
  }, [waitingSince, checkedAt]);

  const onSave = () =>
    save.mutate({
      model: model.trim() === "" ? null : model.trim(),
      own: own
        ? { wire_format: wireFormat, base_url: address.trim(), ...(key ? { key } : {}) }
        : null,
    });

  return (
    <div className="flex flex-col gap-2">
      <h3 className={HEADING}>Search</h3>
      <p role="status" className="max-w-prose text-ink">
        {embedding.keyword_only === null
          ? MEANING_SEARCH_ON
          : KEYWORD_ONLY_WORDS[embedding.keyword_only]}
      </p>
      {embedding.model !== null ? (
        <p className="max-w-prose text-ink-dim">
          Embedding model {embedding.model}, through{" "}
          {embedding.source === "own"
            ? `the connection for embeddings, at ${embedding.own_host ?? "—"}`
            : "the chat connection"}
          .{" "}
          {embedding.check === "passed" && embedding.checked_at
            ? `Its check passed on ${formatZonedTime(embedding.checked_at, { now })}.`
            : embedding.check === "failed"
              ? `Its check failed: ${embeddingCheckLine(embedding.check_reason)}`
              : "Not checked yet."}
        </p>
      ) : null}
      {embedding.model !== null ? (
        <p className="max-w-prose text-ink-dim">
          {embeddingProgress(embedding.chunks_embedded, embedding.chunks_total)}
        </p>
      ) : null}
      {notesNotSearchable(embedding.notes_not_searchable) ? (
        <p className="max-w-prose text-ink-dim">
          {notesNotSearchable(embedding.notes_not_searchable)}
        </p>
      ) : null}
      <p className="max-w-prose text-ink-dim">{EMBEDDING_SENDS}</p>

      <label className="flex max-w-md flex-col gap-1">
        <span className={HEADING}>Embedding model</span>
        <input
          value={model}
          onChange={(event) => setModel(event.target.value)}
          placeholder="Empty: search by keyword only"
          className="rounded border border-line bg-page px-2 py-1 font-mono"
        />
      </label>
      <label className="flex items-start gap-2">
        <input type="checkbox" checked={own} onChange={(event) => setOwn(event.target.checked)} />
        <span className="max-w-prose">Use a separate connection for embeddings only</span>
      </label>
      {own ? (
        <div className="flex flex-col gap-2">
          <label className="flex max-w-md flex-col gap-1">
            <span className={HEADING}>Format</span>
            <select
              value={wireFormat}
              onChange={(event) => setWireFormat(event.target.value)}
              className="rounded border border-line bg-page px-2 py-1"
            >
              <option value="openai_chat">OpenAI-style (OpenAI, Ollama, gateways)</option>
              <option value="bedrock_converse">Amazon Bedrock (Titan)</option>
            </select>
          </label>
          <label className="flex max-w-md flex-col gap-1">
            <span className={HEADING}>Address</span>
            <input
              value={address}
              onChange={(event) => setAddress(event.target.value)}
              className="rounded border border-line bg-page px-2 py-1 font-mono"
            />
          </label>
          <label className="flex max-w-md flex-col gap-1">
            <span className={HEADING}>Key</span>
            <input
              type="password"
              value={key}
              onChange={(event) => setKey(event.target.value)}
              placeholder={embedding.own_has_key ? "Saved; leave empty to keep it" : "None"}
              className="rounded border border-line bg-page px-2 py-1 font-mono"
            />
          </label>
        </div>
      ) : null}
      <div className="flex flex-wrap gap-2">
        <Button pending={save.isPending} pendingLabel="Saving…" onClick={onSave}>
          Save search settings
        </Button>
        {embedding.model !== null ? (
          <Button
            variant="secondary"
            pending={check.isPending || waiting}
            pendingLabel="Checking…"
            onClick={() =>
              check.mutate(undefined, { onSuccess: () => setWaitingSince(Date.now()) })
            }
          >
            Check this embedding model
          </Button>
        ) : null}
      </div>
      {save.isError ? (
        <InlineFailure title="Search settings were not saved" error={save.error} />
      ) : null}
      {check.isError ? <InlineFailure title="The check did not start" error={check.error} /> : null}
      {embedding.source === "own" && embedding.own_consent_fingerprint !== null ? (
        embedding.own_consent_current ? (
          <p className="max-w-prose text-ink-dim">
            An owner agreed to send text to {embedding.own_host ?? "this address"} for embedding.
          </p>
        ) : (
          <div className="flex flex-col gap-2">
            <p className="max-w-prose text-ink">
              Text goes to {embedding.own_host ?? "this address"} to be embedded only once an owner
              agrees: every runbook piece and fact line, and each search phrase, masked first.
            </p>
            {mayConsent ? (
              <div>
                <Button
                  pending={agree.isPending}
                  pendingLabel="Agreeing…"
                  onClick={() =>
                    agree.mutate({ fingerprint: embedding.own_consent_fingerprint ?? "" })
                  }
                >
                  Agree to send text there for embedding
                </Button>
              </div>
            ) : null}
            {agree.isError ? (
              <InlineFailure title="The agreement was not recorded" error={agree.error} />
            ) : null}
          </div>
        )
      ) : null}
    </div>
  );
}

function PreviewView({ preview }: { preview: ReturnType<typeof useChatPreview> }) {
  if (preview.isPending) return <SkeletonRows rows={3} label="Reading what would be sent" />;
  if (preview.isError) {
    return (
      <InlineFailure
        title="The preview could not be built"
        error={preview.error}
        onRetry={() => void preview.refetch()}
      />
    );
  }
  const data = preview.data;
  return (
    <div className="flex flex-col gap-3">
      <p className="max-w-prose text-ink">
        Sent to {data.provider_label}, at {data.address}, for the model {data.model}, from the
        project {data.project_name}.
      </p>
      <div>
        <h4 className={HEADING}>Everything chat can send</h4>
        <ul className="list-disc pl-5">
          {data.kinds.map((kind) => (
            <li key={kind.kind} className="max-w-prose">
              {kind.kind} — from {kind.source} ({CHAT_DATA_LEVEL_WORDS[kind.level].split(":")[0]})
            </li>
          ))}
        </ul>
      </div>
      {data.samples.map((sample) => (
        <div key={sample.kind} className="flex flex-col gap-1">
          <h4 className={HEADING}>
            {sample.kind}
            {sample.labelled_sample ? " — a built-in sample: this project has none yet" : ""}
          </h4>
          <pre className="overflow-x-auto whitespace-pre-wrap break-words rounded border border-line bg-page p-2 font-mono text-xs">
            {sample.sent}
          </pre>
        </div>
      ))}
      <p className="max-w-prose text-ink">{maskingOffLine(data.masking_rules_off)}</p>
      <p className="max-w-prose text-ink-dim">{CHAT_MASKING_LINE}</p>
      <p className="max-w-prose text-ink-dim">
        Email addresses are {data.mask_emails ? "masked" : "sent"}. Runbook pieces and fact lines
        sent at once to be embedded for search: {data.sent_for_embedding}.
      </p>
    </div>
  );
}

function Counts({ settings }: { settings: ChatSettings }) {
  const outcomes = Object.entries(settings.monthly_counts);
  return (
    <div className="flex flex-col gap-2">
      <h3 className={HEADING}>This month</h3>
      {outcomes.length === 0 ? (
        <p className="text-ink-dim">No questions yet.</p>
      ) : (
        <ul>
          {outcomes.map(([kind, count]) => (
            <li key={kind}>
              {countWord(kind)}: {count}
            </li>
          ))}
        </ul>
      )}
      {settings.people_this_month.length > 0 ? (
        <table className="max-w-md text-left">
          <thead>
            <tr>
              <th className={HEADING}>Person</th>
              <th className={HEADING}>Questions</th>
            </tr>
          </thead>
          <tbody>
            {settings.people_this_month.map((person) => (
              <tr key={person.name}>
                <td>{person.name}</td>
                <td>{person.questions}</td>
              </tr>
            ))}
          </tbody>
        </table>
      ) : null}
    </div>
  );
}

function Reads({ now }: { now: number }) {
  const reads = useChatReads();
  const [filter, setFilter] = useState("");
  if (reads.isPending) return <SkeletonRows rows={3} label="Loading what was sent" />;
  if (reads.isError) {
    return (
      <InlineFailure
        title="What was sent could not be read"
        error={reads.error}
        onRetry={() => void reads.refetch()}
      />
    );
  }
  const shown = reads.data.filter((row) =>
    `${row.person ?? ""} ${row.project ?? ""} ${row.tool}`
      .toLowerCase()
      .includes(filter.toLowerCase()),
  );
  return (
    <div className="flex flex-col gap-2">
      <h3 className={HEADING}>What was sent</h3>
      {reads.data.length === 0 ? (
        <p className="text-ink-dim">{CHAT_NO_READS}</p>
      ) : (
        <>
          <label className="flex max-w-xs flex-col gap-1">
            <span className="text-ink-dim text-xs">Filter by person, project or tool</span>
            <input
              value={filter}
              onChange={(event) => setFilter(event.target.value)}
              className="rounded border border-line bg-page px-2 py-1"
            />
          </label>
          <div className="overflow-x-auto">
            <table className="text-left text-xs">
              <thead>
                <tr>
                  {["Person", "Time", "Project", "Tool", "Range", "Rows", "Provider"].map(
                    (name) => (
                      <th key={name} className={HEADING}>
                        {name}
                      </th>
                    ),
                  )}
                </tr>
              </thead>
              <tbody>
                {shown.map((row) => (
                  <tr key={`${row.at}-${row.tool}-${row.person}`}>
                    <td>{row.person ?? "Former member"}</td>
                    <td>{formatZonedTime(row.at, { now })}</td>
                    <td>{row.project ?? "—"}</td>
                    <td className="font-mono">{row.tool}</td>
                    <td>
                      {row.from_time && row.to_time
                        ? `${formatZonedTime(row.from_time, { now })} to ${formatZonedTime(row.to_time, { now })}`
                        : "—"}
                    </td>
                    <td>{row.row_count ?? "—"}</td>
                    <td>{row.provider_label}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </>
      )}
    </div>
  );
}
