import { ChevronDown } from "lucide-react";
import { useId } from "react";

/**
 * A labelled select that reads as a sentence: `PROJECT  production ⌄`.
 *
 * It stays a native `<select>`. A custom listbox would mean rebuilding keyboard
 * navigation, type-ahead, screen-reader semantics and the mobile picker — all of
 * which the platform already does better than we would. What was wrong was never
 * the element, only that it was left wearing the browser's chrome inside
 * deliberately-set typography.
 *
 * `appearance-none` removes that chrome; the chevron is ours and sits under the
 * select, which keeps the whole control one hit target.
 *
 * **Two shapes, because a select appears in two places.** Inline — label beside
 * the control — is the toolbar shape: the project selector, the range picker, a
 * filter above a list. `stacked` is the form shape, label above a bordered box of
 * the same height as a `TextField`'s input.
 *
 * The shapes are not interchangeable, and mixing them is a defect with a look.
 * An inline select in a row of text fields is 26px tall against their 34px, so
 * `items-end` flushes the bottoms and the select still reads as sitting low —
 * which is what the `pb-1` and `pb-2` shims on the admin and invite forms were
 * guessing at. A form row is homogeneous now, and lines up by construction.
 */
export function SelectField<T extends { id: string }>({
  label,
  value,
  options,
  onChange,
  optionLabel,
  capped = false,
  compactLabel = false,
  stacked = false,
}: {
  label: string;
  value: string;
  options: readonly T[];
  onChange: (id: string) => void;
  optionLabel: (option: T) => string;
  /**
   * Holds the control to a readable width. A native select is as wide as its longest
   * option, and some option sets carry names no one chose — a whole-host profiler's
   * `containerd-shim-<64 hex>` pushed every control after it off a phone's screen.
   */
  capped?: boolean;
  /** Keeps the label for assistive technology only on a phone, where the bar has no room. */
  compactLabel?: boolean;
  /**
   * The form shape: label above a bordered box matching `TextField`'s input. Use it
   * for every select that sits in a row with text fields; leave it off in a toolbar.
   */
  stacked?: boolean;
}) {
  const id = useId();

  if (stacked) {
    return (
      <div className="flex flex-col gap-1.5">
        <label htmlFor={id} className="font-mono text-2xs text-ink-dim uppercase tracking-caps">
          {label}
        </label>

        <div className="group relative">
          <select
            id={id}
            value={value}
            onChange={(event) => onChange(event.target.value)}
            // The same box as a text input, to the pixel: `text-base` and
            // `py-1.5` are what make the two the same height, so a row of them
            // needs no shim.
            className="w-full cursor-pointer appearance-none truncate rounded-sm border border-line bg-surface py-1.5 pr-7 pl-2.5 text-ink text-base outline-none hover:border-line-strong focus-visible:outline-2 focus-visible:outline-brand focus-visible:outline-offset-1"
          >
            {options.map((option) => (
              <option key={option.id} value={option.id}>
                {optionLabel(option)}
              </option>
            ))}
          </select>

          <ChevronDown
            size={14}
            aria-hidden="true"
            className="pointer-events-none absolute top-1/2 right-2.5 -translate-y-1/2 text-ink-dim transition-colors group-hover:text-ink"
          />
        </div>
      </div>
    );
  }

  return (
    <div className="flex items-baseline gap-2">
      <label
        htmlFor={id}
        className={`${compactLabel ? "sr-only sm:not-sr-only " : ""}font-mono text-2xs text-ink-dim uppercase tracking-caps`}
      >
        {label}
      </label>

      <div className="group relative">
        <select
          id={id}
          value={value}
          onChange={(event) => onChange(event.target.value)}
          // pr-5 leaves room for the chevron; the select still owns the whole box.
          className={`${capped ? "max-w-48 truncate " : ""}cursor-pointer appearance-none rounded-sm border-none bg-transparent py-0.5 pr-5 pl-1 font-medium text-ink text-sm outline-none hover:bg-hover focus-visible:outline-2 focus-visible:outline-brand focus-visible:outline-offset-1`}
        >
          {options.map((option) => (
            <option key={option.id} value={option.id}>
              {optionLabel(option)}
            </option>
          ))}
        </select>

        <ChevronDown
          size={12}
          aria-hidden="true"
          className="pointer-events-none absolute top-1/2 right-1 -translate-y-1/2 text-ink-dim transition-colors group-hover:text-ink"
        />
      </div>
    </div>
  );
}
