import { useState } from "react";

import { CopyButton } from "@/shared/components/CopyButton";
import { SelectField } from "@/shared/components/SelectField";

/**
 * How a customer points an AI assistant at their own data (F36, FR31, D13).
 *
 * **Client-neutral on purpose.** MCP is an open protocol and the server here is
 * an ordinary remote one: a URL and an `Authorization` header. An earlier draft
 * of this panel showed only a `claude mcp add …` line, which reads to anyone on
 * Cursor or VS Code as "not for me" — untrue, and the kind of untrue that loses
 * a customer quietly.
 *
 * The snippets carry a **placeholder**, never a real token: this panel sits on a
 * screen that lists tokens and shows none of their secrets, and a copyable line
 * holding a live credential would undo that.
 */

const MCP_PATH = "/api/mcp/";
const PLACEHOLDER = "nvt_live_…";

interface Client {
  id: string;
  label: string;
  /** Where the snippet goes, or how it is run. */
  where: string;
  snippet: (url: string) => string;
  /** The one thing that most often makes it look broken. Omitted when there is none. */
  note?: string;
}

const CLIENTS: readonly Client[] = [
  {
    id: "claude-code",
    label: "Claude Code",
    where: "Run this in your terminal",
    snippet: (url) =>
      `claude mcp add --transport http nexovue ${url} \\\n  --header "Authorization: Bearer ${PLACEHOLDER}"`,
  },
  {
    id: "cursor",
    label: "Cursor",
    where: "~/.cursor/mcp.json (or .cursor/mcp.json in a project)",
    snippet: (url) =>
      JSON.stringify(
        {
          mcpServers: {
            nexovue: { url, headers: { Authorization: `Bearer ${PLACEHOLDER}` } },
          },
        },
        null,
        2,
      ),
  },
  {
    id: "vscode",
    label: "VS Code (Copilot)",
    where: ".vscode/mcp.json",
    snippet: (url) =>
      JSON.stringify(
        {
          servers: {
            nexovue: {
              type: "http",
              url,
              headers: { Authorization: `Bearer ${PLACEHOLDER}` },
            },
          },
        },
        null,
        2,
      ),
    note: "Tools appear in agent mode only — in ask mode the server looks connected and offers nothing.",
  },
  {
    id: "codex",
    label: "Codex",
    where: "~/.codex/config.toml",
    snippet: (url) =>
      `[mcp_servers.nexovue]\nurl = "${url}"\nhttp_headers = { "Authorization" = "Bearer ${PLACEHOLDER}" }`,
    note: 'Or keep the token out of the file: bearer_token_env_var = "NEXOVUE_TOKEN" reads it from the environment. `codex mcp add` has no header flag, so a literal header is hand-edited.',
  },
  {
    id: "windsurf",
    label: "Windsurf",
    where: "~/.codeium/windsurf/mcp_config.json",
    snippet: (url) =>
      JSON.stringify(
        {
          mcpServers: {
            nexovue: { serverUrl: url, headers: { Authorization: `Bearer ${PLACEHOLDER}` } },
          },
        },
        null,
        2,
      ),
  },
  {
    id: "zed",
    label: "Zed",
    where: "settings.json",
    snippet: (url) =>
      JSON.stringify(
        {
          context_servers: {
            nexovue: { url, headers: { Authorization: `Bearer ${PLACEHOLDER}` } },
          },
        },
        null,
        2,
      ),
    note: "Without the Authorization header Zed starts an OAuth flow instead, which this server does not serve.",
  },
  {
    id: "gemini-cli",
    label: "Gemini CLI",
    where: "~/.gemini/settings.json",
    snippet: (url) =>
      JSON.stringify(
        {
          mcpServers: {
            nexovue: { httpUrl: url, headers: { Authorization: `Bearer ${PLACEHOLDER}` } },
          },
        },
        null,
        2,
      ),
    note: "The field is httpUrl — url means Server-Sent Events here, which is a different transport.",
  },
  {
    id: "claude-desktop",
    label: "Claude Desktop",
    where: "Settings → Connectors → Add custom connector",
    snippet: (url) => `URL:    ${url}\nHeader: authorization\nValue:  Bearer ${PLACEHOLDER}`,
    note: "Its config file ignores a remote URL — remote servers go through the Connectors screen. Request headers there are still in beta and not enabled for every organization.",
  },
];

export function ConnectAnAssistant() {
  const url = `${window.location.origin}${MCP_PATH}`;
  const [clientId, setClientId] = useState(CLIENTS[0].id);
  const client = CLIENTS.find((each) => each.id === clientId) ?? CLIENTS[0];
  const snippet = client.snippet(url);

  return (
    <section aria-label="Connect an AI assistant" className="mt-8 border-line border-t pt-6">
      <h2 className="font-semibold font-serif text-ink text-lg tracking-tight">
        Connect an AI assistant
      </h2>
      <p className="mt-1 text-ink-dim text-sm leading-relaxed">
        Any MCP client that reaches a remote server with a header can read this organization&rsquo;s
        services, errors, incidents and checks — with a token you create above, and nothing more. It
        reads; there is no tool that changes anything.
      </p>

      <dl className="mt-3 grid gap-x-3 gap-y-1 text-sm sm:grid-cols-[auto_1fr]">
        <dt className="text-ink-dim">Endpoint</dt>
        <dd className="min-w-0 break-all font-mono text-ink">{url}</dd>
        <dt className="text-ink-dim">Header</dt>
        <dd className="min-w-0 break-all font-mono text-ink">
          Authorization: Bearer {PLACEHOLDER}
        </dd>
      </dl>

      <div className="mt-4 flex flex-wrap items-center gap-3">
        <SelectField
          label="Set up in"
          value={clientId}
          options={CLIENTS}
          onChange={setClientId}
          optionLabel={(option) => option.label}
        />
        <span className="text-ink-dim text-xs">{client.where}</span>
      </div>

      <div className="mt-2 flex items-start gap-3 border border-line bg-surface px-3 py-2">
        <pre className="min-w-0 flex-1 overflow-x-auto whitespace-pre font-mono text-ink text-xs">
          {snippet}
        </pre>
        <CopyButton value={snippet} label={`${client.label} configuration`} />
      </div>

      {client.note ? (
        <p className="mt-2 text-ink-dim text-xs leading-relaxed">{client.note}</p>
      ) : null}

      <p className="mt-3 text-ink-dim text-xs leading-relaxed">
        Replace the placeholder with a token you created above. Revoking that token ends the
        assistant&rsquo;s access on its next call.
      </p>
    </section>
  );
}
