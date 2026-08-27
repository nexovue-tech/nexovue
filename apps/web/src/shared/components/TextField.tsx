import { useId } from "react";

/**
 * A labelled text input in the same register as SelectField: a native element with the
 * browser's chrome removed, not a rebuilt one. Autofill, password managers, mobile
 * keyboards and validation bubbles all keep working.
 *
 * The error sits under the field and is wired through `aria-describedby`, because a
 * field problem the screen reader does not announce is a field problem nobody fixes.
 */
export function TextField({
  label,
  name,
  type = "text",
  value,
  onChange,
  error,
  autoComplete,
  hint,
  required = true,
}: {
  label: string;
  name: string;
  type?: "text" | "email" | "password";
  value: string;
  onChange: (value: string) => void;
  /** Inline field message. Present only when this field is the problem. */
  error?: string;
  autoComplete?: string;
  hint?: string;
  required?: boolean;
}) {
  const id = useId();
  const errorId = `${id}-error`;
  const hintId = `${id}-hint`;

  return (
    <div className="flex flex-col gap-1.5">
      <label htmlFor={id} className="font-mono text-2xs text-ink-dim uppercase tracking-caps">
        {label}
      </label>

      <input
        id={id}
        name={name}
        type={type}
        value={value}
        required={required}
        autoComplete={autoComplete}
        onChange={(event) => onChange(event.target.value)}
        aria-invalid={error ? true : undefined}
        aria-describedby={error ? errorId : hint ? hintId : undefined}
        className={`w-full rounded-sm border bg-surface px-2.5 py-1.5 text-ink text-base outline-none placeholder:text-ink-dim focus-visible:outline-2 focus-visible:outline-brand focus-visible:outline-offset-1 ${
          error ? "border-critical-solid" : "border-line hover:border-line-strong"
        }`}
      />

      {error ? (
        <p id={errorId} className="text-critical-text text-xs">
          {error}
        </p>
      ) : hint ? (
        <p id={hintId} className="text-ink-dim text-xs">
          {hint}
        </p>
      ) : null}
    </div>
  );
}
