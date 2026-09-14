/**
 * What this screen and the setup documentation have to agree on.
 *
 * **The documentation has an address now.** It was Markdown in this repository
 * with no site to read it on, so this file named the repo path and carried short
 * extracts inline — a path is something a reader can check, and an invented
 * hostname is a broken promise the first time somebody follows it. The pages are
 * published at `/docs` from the same source, unauthenticated, so this is the base
 * route its own comment said it would become.
 */
export const SETUP_DOCS_PATH = "/docs";

/** One page, by the slug the route carries. */
export function setupDocUrl(slug: string): string {
  return `${SETUP_DOCS_PATH}/${slug}`;
}

/**
 * The protocol the setup pages set. An OpenTelemetry SDK's default is not the same
 * across languages and has changed between releases within one — Java's and Python's
 * both — and a gRPC exporter aimed at this HTTP endpoint fails with an error that
 * names neither the protocol nor the port.
 */
export const OTLP_PROTOCOL = "http/protobuf";
