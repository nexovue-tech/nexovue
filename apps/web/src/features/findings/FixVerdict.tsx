import type { FixVerdict as Verdict, VerdictView } from "./api";
import { useRecordVerdict } from "./api";

/**
 * "Was this suggestion useful?" (F5 spec FR29, UR11): three answers, one recorded per
 * suggestion, so a native radio group: the keyboard moves between answers and a screen
 * reader hears which one stands. Every word is the API's. Choosing records it at once;
 * it changes nothing else, and the status line says so once an answer is stored (NFR6a).
 */
export function FixVerdict({
  findingId,
  fixId,
  verdict,
}: {
  findingId: string;
  fixId: string;
  verdict: VerdictView;
}) {
  const record = useRecordVerdict(findingId, fixId);
  const chosen: Verdict | null = record.data?.chosen ?? verdict.chosen;
  const recorded = record.data?.recorded ?? verdict.recorded;
  return (
    <fieldset className="flex min-w-0 flex-col gap-1" data-testid="fix-verdict">
      <legend className="text-ink text-sm">{verdict.question}</legend>
      <div className="flex flex-wrap gap-x-4">
        {verdict.answers.map((answer) => (
          <label
            key={answer.value}
            className="flex min-h-11 cursor-pointer items-center gap-2 text-ink text-sm"
          >
            <input
              type="radio"
              name={`verdict-${fixId}`}
              value={answer.value}
              checked={chosen === answer.value}
              disabled={record.isPending}
              onChange={() => record.mutate(answer.value)}
            />
            {answer.label}
          </label>
        ))}
      </div>
      <p role="status" className="text-ink-dim text-sm">
        {recorded ?? ""}
      </p>
    </fieldset>
  );
}
