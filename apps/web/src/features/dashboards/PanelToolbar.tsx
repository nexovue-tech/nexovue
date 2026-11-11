import { type KeyboardEvent, useRef, useState } from "react";

import type { StoredPanel } from "./api";
import type { DraftAction, Step } from "./draft";
import { canChangeHeight, canMove, canResize, MAX_PANELS, panelName } from "./draft";

/**
 * One panel's actions in the builder (F20, T3.4, T3.8; FR21, AC37).
 *
 * **One tab stop.** The WAI-ARIA toolbar pattern, as `Tabs` follows it: Tab reaches the
 * toolbar once and arrow keys, Home and End move along it, so thirty panels are thirty
 * stops and not 270. **Each action names its panel**, "Move Rate later", because a screen
 * reader hears the button and not the panel around it. **At an end the button is
 * `aria-disabled`, not `disabled`**: a disabled button drops focus, and a keyboard user who
 * just moved a panel to the top would land on the page body.
 *
 * **Below `lg`** every panel is full width, so width has no visible effect and is hidden,
 * the moves read Up and Down, and the rest sit in one menu per panel (T3.8). Every control
 * there is at least 44 px (AC108).
 */

/** Where focus belongs after a change: this panel's action of this key. */
export type ActionKey =
  | "earlier"
  | "later"
  | "wider"
  | "narrower"
  | "taller"
  | "shorter"
  | "duplicate"
  | "edit"
  | "remove"
  | "more";

interface ToolAction {
  key: ActionKey;
  label: string;
  name: string;
  enabled: boolean;
  run: () => void;
}

const TOUCH_SIZE = "min-h-11 min-w-11 md:min-h-0 md:min-w-0";
const ACTION_CLASS = `${TOUCH_SIZE} rounded-sm px-2 py-1 text-ink-dim text-xs hover:bg-hover hover:text-ink focus-visible:outline-2 focus-visible:outline-brand focus-visible:outline-offset-1 aria-disabled:cursor-not-allowed aria-disabled:opacity-50`;

export function PanelToolbar({
  panel,
  panels,
  narrow,
  onChange,
  onEdit,
}: {
  panel: StoredPanel;
  panels: readonly StoredPanel[];
  narrow: boolean;
  onChange: (action: DraftAction, focus: ActionKey) => void;
  onEdit: () => void;
}) {
  const [active, setActive] = useState(0);
  const [menuOpen, setMenuOpen] = useState(false);
  const buttons = useRef<(HTMLButtonElement | null)[]>([]);
  const name = panelName(panel);
  const id = panel.id;

  const move = (by: Step, key: ActionKey, label: string, verb: string): ToolAction => ({
    key,
    label,
    name: `Move ${name} ${verb}`,
    enabled: canMove(panels, id, by),
    run: () => onChange({ type: "move", id, by }, key),
  });
  const resize = (by: Step, key: ActionKey, label: string): ToolAction => ({
    key,
    label,
    name: `Make ${name} ${label.toLowerCase()}`,
    enabled: canResize(panel, by),
    run: () => onChange({ type: "resize", id, by }, key),
  });
  const height = (by: Step, key: ActionKey, label: string): ToolAction => ({
    key,
    label,
    name: `Make ${name} ${label.toLowerCase()}`,
    enabled: canChangeHeight(panel, by),
    run: () => onChange({ type: "height", id, by }, key),
  });
  const rest: ToolAction[] = [
    height(1, "taller", "Taller"),
    height(-1, "shorter", "Shorter"),
    {
      key: "duplicate",
      label: "Duplicate",
      name: `Duplicate ${name}`,
      enabled: panels.length < MAX_PANELS,
      run: () => onChange({ type: "duplicate", id }, "duplicate"),
    },
    { key: "edit", label: "Edit", name: `Edit ${name}`, enabled: true, run: onEdit },
    {
      key: "remove",
      label: "Remove",
      name: `Remove ${name}`,
      enabled: true,
      run: () => onChange({ type: "remove", id }, "remove"),
    },
  ];
  const actions: ToolAction[] = narrow
    ? [
        move(-1, "earlier", "Up", "up"),
        move(1, "later", "Down", "down"),
        {
          key: "more",
          label: "More",
          name: `More actions for ${name}`,
          enabled: true,
          run: () => setMenuOpen((open) => !open),
        },
      ]
    : [
        move(-1, "earlier", "Earlier", "earlier"),
        move(1, "later", "Later", "later"),
        resize(1, "wider", "Wider"),
        resize(-1, "narrower", "Narrower"),
        ...rest,
      ];
  const stop = Math.min(active, actions.length - 1);

  const focusAt = (index: number) => {
    const target = Math.max(0, Math.min(index, actions.length - 1));
    setActive(target);
    buttons.current[target]?.focus();
  };

  const keys = (event: KeyboardEvent<HTMLButtonElement>, index: number) => {
    const next = {
      ArrowRight: index + 1,
      ArrowLeft: index - 1,
      Home: 0,
      End: actions.length - 1,
    }[event.key];
    if (next === undefined) return;
    event.preventDefault();
    focusAt(next);
  };

  return (
    <div className="flex flex-col items-end gap-1">
      <div role="toolbar" aria-label={`${name} actions`} className="flex flex-wrap gap-1">
        {actions.map((action, index) => (
          <button
            key={action.key}
            ref={(element) => {
              buttons.current[index] = element;
            }}
            type="button"
            data-action={action.key}
            tabIndex={index === stop ? 0 : -1}
            aria-label={action.name}
            aria-disabled={action.enabled ? undefined : true}
            aria-expanded={action.key === "more" ? menuOpen : undefined}
            onFocus={() => setActive(index)}
            onKeyDown={(event) => keys(event, index)}
            // At an end the action stays put and says so, rather than dropping focus.
            onClick={action.run}
            className={ACTION_CLASS}
          >
            {action.label}
          </button>
        ))}
      </div>
      {narrow && menuOpen ? (
        <ul aria-label={`More actions for ${name}`} className="flex flex-col gap-1">
          {rest.map((action) => (
            <li key={action.key}>
              <button
                type="button"
                data-action={action.key}
                aria-label={action.name}
                aria-disabled={action.enabled ? undefined : true}
                onClick={() => {
                  if (action.key !== "edit") setMenuOpen(false);
                  action.run();
                }}
                className={`${ACTION_CLASS} w-full text-left`}
              >
                {action.label}
              </button>
            </li>
          ))}
        </ul>
      ) : null}
    </div>
  );
}
