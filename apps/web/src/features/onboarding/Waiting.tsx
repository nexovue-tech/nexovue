import { CopyButton } from "@/shared/components/CopyButton";

import { OTLP_PROTOCOL, setupDocUrl } from "@/shared/setup-docs";

/**
 * Waiting, and then waiting long enough that waiting is the wrong advice.
 *
 * A screen that says "listening" for ten minutes is indistinguishable from a broken
 * product, and this is where a customer leaves without telling anybody. So after a
 * silence the screen offers what is actually checkable — and names none of it as the
 * cause, because nothing has reached us and there is therefore nothing to diagnose
 * with. Every item is a check the customer can settle themselves in a minute.
 */

interface SilenceCheck {
  cause: string;
  detail: string;
}

const SILENCE_CHECKS: SilenceCheck[] = [
  {
    cause: "The service has not restarted",
    detail:
      "The variables are read once, at startup. Look at what the process actually has rather than at what the config file says: docker exec <container> env | grep OTEL, or systemctl show <unit> --property=Environment.",
  },
  {
    cause: "The endpoint has a path on it",
    detail:
      "It is a base URL, and the SDK appends /v1/traces to it itself. Adding the path yourself produces /v1/traces/v1/traces, which is a 404.",
  },
  {
    cause: "The key is missing, or shaped wrong",
    detail:
      "The headers variable holds name=value pairs, so its value is x-nexovue-key= followed by the key, never the key on its own. Settings → API keys says whether anything has ever authenticated with it; Never used means nothing reached us at all.",
  },
  {
    cause: "The exporter is speaking gRPC",
    detail: `This endpoint is OTLP over HTTP. An SDK that defaults to gRPC fails against it with an error naming neither the protocol nor the port, which is what the ${OTLP_PROTOCOL} variable above prevents.`,
  },
  {
    cause: "Nothing is getting out",
    detail:
      "The export is outbound HTTPS on 443 from the machine your service runs on, not from your laptop. A default-deny egress rule, a proxy the SDK does not know about and TLS interception all look the same from inside the service: silence.",
  },
];

export function Waiting({
  failed,
  silent,
  endpoint,
}: {
  failed: boolean;
  silent: boolean;
  endpoint: string;
}) {
  return (
    <div>
      {/* The live region carries the sentence that changes, not the reference material
          under it — a checklist read aloud in full on arrival is worse than silence. */}
      <div role="status" aria-live="polite">
        <h2 className="font-medium font-serif text-ink text-lg tracking-tight">
          Listening for your first span
        </h2>
        <p className="mt-2 max-w-prose text-ink-dim text-sm leading-relaxed">
          {waitingMessage(failed, silent)}
        </p>
      </div>

      {silent && !failed ? <SilenceChecks endpoint={endpoint} /> : null}
    </div>
  );
}

function waitingMessage(failed: boolean, silent: boolean): string {
  if (failed) return "We could not check just now. This page keeps trying, so leave it open.";
  if (silent)
    return "Still nothing. That is ordinary if your service has not restarted yet; if it has, one of these usually accounts for it. We cannot tell you which — nothing has reached us to look at.";
  return "Nothing has arrived yet. This page checks on its own and will say so the moment something does — leave it open while your service starts up.";
}

function SilenceChecks({ endpoint }: { endpoint: string }) {
  const reachability = `curl -sS -o /dev/null -w "%{http_code}\\n" ${endpoint}/v1/traces`;

  return (
    <div className="mt-6">
      <ol className="space-y-3">
        {SILENCE_CHECKS.map(({ cause, detail }) => (
          <li key={cause} className="border-line-subtle border-t pt-3">
            <p className="font-medium text-ink text-sm">{cause}</p>
            <p className="mt-1 max-w-prose text-ink-dim text-xs leading-relaxed">{detail}</p>
          </li>
        ))}
      </ol>

      <div className="mt-6 border-line-subtle border-t pt-3">
        <p className="max-w-prose text-ink-dim text-sm leading-relaxed">
          One command settles most of those. Run it from the machine the service runs on — your
          laptop's network is not the one that matters.
        </p>
        <div className="mt-2 flex items-center gap-3">
          <code className="min-w-0 flex-1 select-all break-all font-mono text-ink text-xs">
            {reachability}
          </code>
          <CopyButton value={reachability} label="the reachability check" />
        </div>
        <p className="mt-2 max-w-prose text-ink-dim text-xs leading-relaxed">
          405 is the answer you want: the name resolved, the network allowed the connection, TLS
          completed and the path is one we serve. A 404 is the second check above. Anything else — a
          timeout, a refusal, a certificate error — is the last one.
        </p>
      </div>

      <p className="mt-4 max-w-prose text-ink-dim text-xs leading-relaxed">
        The whole sequence, including what each refusal code means, is in{" "}
        <a
          href={setupDocUrl("troubleshooting")}
          className="text-brand-text underline underline-offset-4"
          target="_blank"
          rel="noreferrer"
        >
          Nothing has arrived
        </a>
        . It opens in a new tab, so this screen keeps watching.
      </p>
    </div>
  );
}
