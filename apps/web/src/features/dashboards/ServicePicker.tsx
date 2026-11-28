import { type MutableRefObject, useEffect, useId, useRef, useState } from "react";

import { visibleEscapes } from "@/shared/format/visible-escapes";
import type { WindowChoice } from "@/shared/selection";

import { useServiceChoices } from "./api";

/**
 * The dashboard's service picker (F20, FR48, FR53, AC44, AC140): one dashboard shows
 * whichever service the address names, so changing it saves nothing and makes no new row.
 *
 * **A native select**, each service shown through FR61's escapes while the option holds the
 * raw name, which is what the read binds. A name the list does not hold (a link's, or a
 * service that has not reported in this window) is still shown, quoted, truncated and marked
 * as coming from the link. If the list cannot be read, a typed field takes its place, since
 * the dashboard must still be usable (FR23).
 *
 * **Debounced:** a keyboard stepping through the options would change the address, and so
 * read the batch, once per step; the choice is sent once the person has stopped for
 * `PICKER_DEBOUNCE_MS`. Each change is a charged read, so this is the cheap place to stop it.
 */
export const PICKER_DEBOUNCE_MS = 400;
const NOT_CHOSEN = "";
const LINK_NAME_SHOWN = 40;

export function ServicePicker({
  projectId,
  choice,
  value,
  fromLink,
  onPick,
  focusKeeper,
}: {
  projectId: string | undefined;
  choice: WindowChoice;
  /** The service the address names, if it names one. */
  value: string | undefined;
  /** The address, not the person, named it: said so, since it may be in no list. */
  fromLink: boolean;
  /** Called once the person has settled on a choice; undefined clears it. */
  onPick: (service: string | undefined) => void;
  /**
   * Whether the picker held focus. The picker moves between the page line and the sticky
   * line when it is first chosen, and the new one takes the focus the old one had.
   */
  focusKeeper: MutableRefObject<boolean>;
}) {
  const services = useServiceChoices(projectId, choice);
  const id = useId();
  const field = useRef<HTMLSelectElement | HTMLInputElement>(null);
  // What is on screen while the address catches up: the person's last choice.
  const [shown, setShown] = useState(value ?? NOT_CHOSEN);
  const timer = useRef<ReturnType<typeof setTimeout> | undefined>(undefined);

  // The address moved (Back, a link): what is shown follows it.
  useEffect(() => setShown(value ?? NOT_CHOSEN), [value]);
  // A pending choice never outlives the picker, nor is sent for a page that has gone.
  useEffect(() => () => clearTimeout(timer.current), []);
  // Taken over from a picker that held focus.
  // biome-ignore lint/correctness/useExhaustiveDependencies: once, on mounting
  useEffect(() => {
    if (focusKeeper.current) field.current?.focus();
  }, []);

  const choose = (next: string) => {
    setShown(next);
    clearTimeout(timer.current);
    timer.current = setTimeout(() => {
      if (next !== (value ?? NOT_CHOSEN)) onPick(next === NOT_CHOSEN ? undefined : next);
    }, PICKER_DEBOUNCE_MS);
  };

  const names = services.data ?? [];
  const missing = shown !== NOT_CHOSEN && !names.includes(shown);
  const escaped = shown !== NOT_CHOSEN && visibleEscapes(shown) !== shown;
  const keepFocus = {
    onFocus: () => {
      focusKeeper.current = true;
    },
    onBlur: () => {
      focusKeeper.current = false;
    },
  };

  return (
    <div className="flex flex-wrap items-center gap-2 text-sm">
      <label htmlFor={id} className="text-ink">
        Service
      </label>
      {services.isError ? (
        <input
          id={id}
          ref={field as MutableRefObject<HTMLInputElement | null>}
          type="text"
          value={shown}
          placeholder="Type a name"
          aria-describedby={`${id}-help`}
          onChange={(event) => choose(event.target.value)}
          {...keepFocus}
          className={FIELD_CLASS}
        />
      ) : (
        <select
          id={id}
          ref={field as MutableRefObject<HTMLSelectElement | null>}
          value={shown}
          disabled={services.isPending}
          onChange={(event) => choose(event.target.value)}
          {...keepFocus}
          className={FIELD_CLASS}
        >
          <option value={NOT_CHOSEN}>
            {services.isPending ? "Loading services" : "Choose a service"}
          </option>
          {missing ? <option value={shown}>{unlisted(shown, fromLink)}</option> : null}
          {names.map((name) => (
            <option key={name} value={name}>
              {visibleEscapes(name)}
            </option>
          ))}
        </select>
      )}
      {services.isError ? (
        <span id={`${id}-help`} className="text-ink-dim">
          Could not list services. Type a name instead.
        </span>
      ) : null}
      {escaped ? (
        <span className="text-warning-text">
          This name holds hidden characters, shown as [U+…] codes.
        </span>
      ) : null}
    </div>
  );
}

const FIELD_CLASS =
  "min-h-11 rounded-sm border border-line bg-surface px-2 py-1 text-ink outline-none hover:border-line-strong focus-visible:outline-2 focus-visible:outline-brand md:min-h-0";

/** A name no list holds: quoted and cut short, and marked when the address is where it came from. */
function unlisted(name: string, fromLink: boolean): string {
  const safe = visibleEscapes(name);
  const cut = safe.length > LINK_NAME_SHOWN ? `${safe.slice(0, LINK_NAME_SHOWN)}…` : safe;
  return `“${cut}”${fromLink ? " (from the link)" : ""}`;
}
