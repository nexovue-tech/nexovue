/**
 * What this screen and the setup documentation have to agree on.
 *
 * `docs/product/setup/` is Markdown in this repository and there is no documentation
 * site, so there is no URL to send anyone to. The screen names the path and carries
 * short extracts inline instead: a path is something a reader can check, and an
 * invented hostname is a broken promise the first time somebody follows it. When the
 * site exists this becomes its base URL and the extracts collapse into links.
 */
export const SETUP_DOCS_PATH = "docs/product/setup/";

/**
 * The protocol the setup pages set. An OpenTelemetry SDK's default is not the same
 * across languages and has changed between releases within one — Java's and Python's
 * both — and a gRPC exporter aimed at this HTTP endpoint fails with an error that
 * names neither the protocol nor the port.
 */
export const OTLP_PROTOCOL = "http/protobuf";
