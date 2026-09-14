import type { ReactNode } from "react";

import { SETUP_DOCS_PATH } from "@/shared/setup-docs";

/**
 * The other half of the split the setup documentation opens with. The variables above
 * redirect an exporter that already exists; a service that has never been instrumented
 * has a step before them, and a screen that does not say so reads as "this did not
 * work" to exactly the customer least able to tell the difference.
 *
 * Both sections are collapsed, because the customer this screen is built for — already
 * exporting OpenTelemetry — needs neither and should not have to read past them.
 */

interface LanguageStart {
  language: string;
  commands: string[];
  note: string;
}

/** The shortest true path from nothing to a first span. The pages are the long version. */
const LANGUAGE_STARTS: LanguageStart[] = [
  {
    language: "Node.js",
    commands: [
      "npm install @opentelemetry/api @opentelemetry/auto-instrumentations-node",
      "node --require '@opentelemetry/auto-instrumentations-node/register' app.js",
    ],
    note: "Zero-code: the register module patches Express, pg, ioredis and the rest as they load, so it has to run before your application requires them. NODE_OPTIONS does the same when the start command is not yours to change.",
  },
  {
    language: "Python",
    commands: [
      "pip install opentelemetry-distro opentelemetry-exporter-otlp",
      "opentelemetry-bootstrap -a install",
      "opentelemetry-instrument python app.py",
    ],
    note: "The launcher goes in front of your existing start command, gunicorn and uvicorn included. A pre-fork server needs a post-fork hook, or spans are created in workers and never sent.",
  },
  {
    language: "Java",
    commands: ["java -javaagent:/opt/opentelemetry-javaagent.jar -jar app.jar"],
    note: "One JAR from the opentelemetry-java-instrumentation releases, attached to the JVM. Nothing in your build or your code changes. JAVA_TOOL_OPTIONS attaches it when the start command is not yours to change.",
  },
  {
    language: "Go",
    commands: [
      "go get go.opentelemetry.io/otel/sdk go.opentelemetry.io/otel/exporters/otlp/otlptrace/otlptracehttp",
    ],
    note: "Go is compiled, so there is no agent to attach: about twenty lines of tracer-provider setup once, then wrap your handlers and clients with otelhttp. More work on the first day and less afterwards.",
  },
];

interface PlatformPlacement {
  platform: string;
  syntax: string;
  note: string;
}

const PLATFORM_PLACEMENTS: PlatformPlacement[] = [
  {
    platform: "Docker",
    syntax: "docker run -e …",
    note: "One -e per variable, or an --env-file the service account cannot read. Never an ENV line in the Dockerfile for the key: docker history prints it to everyone who can pull the image.",
  },
  {
    platform: "Docker Compose",
    syntax: "environment:",
    note: "On each service, with the key interpolated from a .env that is not committed. OTEL_SERVICE_NAME belongs per service and never in a shared block, or every container reports as the same service.",
  },
  {
    platform: "Kubernetes",
    syntax: "env:",
    note: "On the container. The key comes from a Secret through secretKeyRef, and Kubernetes' own $(NEXOVUE_KEY) expansion assembles the header — it resolves only a variable declared earlier in the same list.",
  },
  {
    platform: "AWS ECS",
    syntax: "secrets:",
    note: "Endpoint and protocol in environment, key in secrets. ECS interpolates nothing, so store the whole header value — x-nexovue-key= and the key — as one secret named OTEL_EXPORTER_OTLP_HEADERS.",
  },
  {
    platform: "EC2, bare metal",
    syntax: "Environment=",
    note: "In a systemd drop-in, with the key in an EnvironmentFile owned by root at 0600. systemctl show --property=Environment prints what the service will actually get.",
  },
  {
    platform: "AWS Lambda",
    syntax: "AWS_LAMBDA_EXEC_WRAPPER",
    note: "The variables alone are not enough here. Lambda freezes the environment the moment your handler returns, so the SDK's background exporter never sends and nothing raises an error. Use the OpenTelemetry Lambda layer, which flushes before the freeze.",
  },
];

export function SetupGuides() {
  return (
    <div className="mt-8">
      <Disclosure summary="My service is not instrumented yet">
        <p className="mb-4 max-w-prose text-ink-dim text-sm leading-relaxed">
          Every language also wants OTEL_SERVICE_NAME. Without it each unnamed service you run is
          grouped into one row called (unknown).
        </p>
        <dl className="space-y-4">
          {LANGUAGE_STARTS.map(({ language, commands, note }) => (
            <div key={language}>
              <dt className="font-medium text-ink text-sm">{language}</dt>
              <dd>
                {commands.map((command) => (
                  <code
                    key={command}
                    className="mt-1 block select-all break-all font-mono text-ink text-xs"
                  >
                    {command}
                  </code>
                ))}
                <p className="mt-1.5 max-w-prose text-ink-dim text-xs leading-relaxed">{note}</p>
              </dd>
            </div>
          ))}
        </dl>
      </Disclosure>

      <Disclosure summary="Where these variables go, per platform">
        <dl className="space-y-3">
          {PLATFORM_PLACEMENTS.map(({ platform, syntax, note }) => (
            <div key={platform} className="sm:flex sm:gap-4">
              <dt className="shrink-0 text-ink text-sm sm:w-36">
                {platform}
                <code className="mt-0.5 block break-all font-mono text-ink-dim text-2xs">
                  {syntax}
                </code>
              </dt>
              <dd className="mt-1 max-w-prose text-ink-dim text-xs leading-relaxed sm:mt-0">
                {note}
              </dd>
            </div>
          ))}
        </dl>
      </Disclosure>

      <p className="mt-3 max-w-prose text-ink-dim text-xs leading-relaxed">
        The long versions — every language, every platform, the Collector, the browser SDK and what
        each refusal code means — are in{" "}
        <a
          href={SETUP_DOCS_PATH}
          className="text-brand-text underline underline-offset-4"
          target="_blank"
          rel="noreferrer"
        >
          the setup documentation
        </a>
        . It opens in a new tab, so this screen keeps watching for your first span.
      </p>
    </div>
  );
}

/* Native disclosure: keyboard, screen reader and the open/closed marker all come free,
   and nothing here needs behaviour <details> does not already have. */
function Disclosure({ summary, children }: { summary: string; children: ReactNode }) {
  return (
    <details className="border-line-subtle border-b">
      <summary className="cursor-pointer py-2.5 text-ink-dim text-sm hover:text-ink">
        {summary}
      </summary>
      <div className="pb-5">{children}</div>
    </details>
  );
}
