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
 */
export function SelectField<T extends { id: string }>({
  label,
  value,
  options,
  onChange,
  optionLabel,
}: {
  label: string;
  value: string;
  options: readonly T[];
  onChange: (id: string) => void;
  optionLabel: (option: T) => string;
}) {
  const id = useId();

  return (
    <div className="flex items-baseline gap-2">
      <label htmlFor={id} className="font-mono text-2xs text-ink-dim uppercase tracking-caps">
        {label}
      </label>

      <div className="group relative">
        <select
          id={id}
          value={value}
          onChange={(event) => onChange(event.target.value)}
          // pr-5 leaves room for the chevron; the select still owns the whole box.
          className="cursor-pointer appearance-none rounded-sm border-none bg-transparent py-0.5 pr-5 pl-1 font-medium text-ink text-sm outline-none hover:bg-hover focus-visible:outline-2 focus-visible:outline-brand focus-visible:outline-offset-1"
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
