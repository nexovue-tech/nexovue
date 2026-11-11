import { useState } from "react";

import { EditableRows } from "@/shared/components/EditableRows";
import { TextField } from "@/shared/components/TextField";

import type { DashboardLink } from "./api";
import { MAX_LINKS } from "./draft";

/**
 * A dashboard's links, label and `https` address (F20, T3.5; FR56). The API's validator
 * judges each address; this shows the host it opens beside the field, because a label
 * alone tells a reader at 3 a.m. nothing about where it goes.
 */

interface LinkRow extends DashboardLink {
  key: string;
}

const HTTPS = "https:";

/** The host an address opens, or undefined until it reads as an `https` address. */
export function hostOf(url: string): string | undefined {
  try {
    const parsed = new URL(url);
    return parsed.protocol === HTTPS && parsed.host !== "" ? parsed.host : undefined;
  } catch {
    return undefined;
  }
}

export function LinksEditor({
  links,
  onChange,
}: {
  links: readonly DashboardLink[];
  onChange: (links: DashboardLink[]) => void;
}) {
  // Rows keep their own keys, so removing one never hands its neighbour's input over.
  const [keys, setKeys] = useState<string[]>(() => links.map(() => crypto.randomUUID()));
  const rows: LinkRow[] = links.map((link, index) => ({
    ...link,
    key: keys[index] ?? `link-${index}`,
  }));

  return (
    <fieldset className="flex flex-col gap-2">
      <legend className="mb-1 font-mono text-2xs text-ink-dim uppercase tracking-caps">
        Links
      </legend>
      <EditableRows
        label="Links"
        rows={rows}
        max={MAX_LINKS}
        addLabel="Add a link"
        emptyLabel="No links. A runbook or a team page goes here."
        newRow={() => ({ key: crypto.randomUUID(), label: "", url: "" })}
        onChange={(next) => {
          setKeys(next.map((row) => row.key));
          onChange(next.map(({ label, url }) => ({ label, url })));
        }}
        renderRow={(row, update) => {
          const host = hostOf(row.url);
          return (
            <>
              <div className="sm:w-48">
                <TextField
                  label="Label"
                  name={`link-label-${row.key}`}
                  value={row.label}
                  onChange={(label) => update({ ...row, label })}
                />
              </div>
              <div className="min-w-0 flex-1">
                <TextField
                  label="Address"
                  name={`link-url-${row.key}`}
                  value={row.url}
                  onChange={(url) => update({ ...row, url })}
                  hint={host ? `Opens ${host}` : "An https address."}
                />
              </div>
            </>
          );
        }}
      />
    </fieldset>
  );
}
