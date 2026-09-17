import { CircleAlert } from "lucide-react";
import { type KeyboardEvent, type ReactNode, useId, useRef } from "react";

/**
 * The WAI-ARIA tabs pattern, with automatic activation: arrow keys move focus and
 * selection together, so the list is one tab stop and the panel follows the keyboard.
 * https://www.w3.org/WAI/ARIA/apg/patterns/tabs/
 *
 * Only the selected panel is rendered, so every tab controls the one panel element
 * and that panel is labelled by whichever tab is selected.
 */

export interface TabItem {
  id: string;
  label: string;
  /** Shown after the label, e.g. "Headers (3)". Omitted when undefined. */
  count?: number;
  /** Marks a tab holding a validation problem, with an accessible text, not only colour. */
  invalid?: boolean;
}

/** Read after the label, so "Body" is announced as "Body, has a problem". */
const INVALID_TAB_TEXT = ", has a problem";

export function Tabs({
  label,
  items,
  selected,
  onSelect,
  children,
}: {
  /** Accessible name of the tab list. */
  label: string;
  items: readonly TabItem[];
  selected: string;
  onSelect: (id: string) => void;
  /** The selected tab's panel. */
  children: ReactNode;
}) {
  const baseId = useId();
  const tabElements = useRef<(HTMLButtonElement | null)[]>([]);
  const panelId = `${baseId}-panel`;
  const tabIdAt = (index: number) => `${baseId}-tab-${index}`;

  const selectedIndex = items.findIndex((item) => item.id === selected);
  // A selection naming no tab would leave the list with no tab stop at all.
  const tabStopIndex = Math.max(selectedIndex, 0);

  function activate(index: number) {
    const item = items[index];
    if (!item) return;
    tabElements.current[index]?.focus();
    onSelect(item.id);
  }

  function handleKeyDown(event: KeyboardEvent<HTMLButtonElement>, index: number) {
    const target = targetIndex(event.key, index, items.length);
    if (target === undefined) return;
    event.preventDefault();
    activate(target);
  }

  return (
    <div className="flex min-w-0 flex-col">
      {/* Scrolls sideways rather than wrapping, so a narrow screen keeps one row of tabs. */}
      <div
        role="tablist"
        aria-label={label}
        aria-orientation="horizontal"
        className="flex overflow-x-auto border-line border-b"
      >
        {items.map((item, index) => {
          const isSelected = index === selectedIndex;
          return (
            <button
              key={item.id}
              ref={(element) => {
                tabElements.current[index] = element;
              }}
              id={tabIdAt(index)}
              type="button"
              role="tab"
              aria-selected={isSelected}
              aria-controls={panelId}
              tabIndex={index === tabStopIndex ? 0 : -1}
              onClick={() => activate(index)}
              onKeyDown={(event) => handleKeyDown(event, index)}
              className={`-mb-px flex shrink-0 items-center gap-1.5 whitespace-nowrap border-b-2 px-3 py-1.5 text-sm hover:text-ink focus-visible:outline-2 focus-visible:outline-brand focus-visible:-outline-offset-2 ${
                isSelected ? "border-brand font-medium text-ink" : "border-transparent text-ink-dim"
              }`}
            >
              <span>
                {item.label}
                {item.count === undefined ? null : (
                  <span className="font-mono text-2xs text-ink-dim"> ({item.count})</span>
                )}
              </span>
              {item.invalid ? (
                <>
                  <CircleAlert size={12} aria-hidden="true" className="text-critical-text" />
                  <span className="sr-only">{INVALID_TAB_TEXT}</span>
                </>
              ) : null}
            </button>
          );
        })}
      </div>

      <div
        id={panelId}
        role="tabpanel"
        aria-labelledby={selectedIndex >= 0 ? tabIdAt(selectedIndex) : undefined}
        // biome-ignore lint/a11y/noNoninteractiveTabindex: the APG tabs pattern makes the panel a tab stop.
        tabIndex={0}
        className="min-w-0 focus-visible:outline-2 focus-visible:outline-brand focus-visible:-outline-offset-2"
      >
        {children}
      </div>
    </div>
  );
}

/** Where a key moves focus from `index`, wrapping at both ends; undefined for any other key. */
function targetIndex(key: string, index: number, count: number): number | undefined {
  if (count === 0) return undefined;
  switch (key) {
    case "ArrowRight":
      return (index + 1) % count;
    case "ArrowLeft":
      return (index - 1 + count) % count;
    case "Home":
      return 0;
    case "End":
      return count - 1;
    default:
      return undefined;
  }
}
