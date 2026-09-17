import { ChevronDown } from "lucide-react";
import type { ReactNode } from "react";
import { useId } from "react";

/**
 * The compact inputs the request editor is made of. Native elements with the browser's
 * chrome removed, as `TextField` and `SelectField` are, at the density a row of
 * headers needs rather than the density a sign-up form wants.
 *
 * Every one carries its own label — visible, or given to assistive technology when the
 * column heading already says it — and its error wired through `aria-describedby`.
 */

const CONTROL =
  "w-full min-w-0 rounded-sm border bg-surface px-2 py-1 text-ink text-sm outline-none placeholder:text-ink-dim focus-visible:outline-2 focus-visible:outline-brand focus-visible:outline-offset-1 disabled:opacity-60";

function tone(error: string | undefined): string {
  return error ? "border-critical-solid" : "border-line hover:border-line-strong";
}

export function FieldError({ id, message }: { id: string; message: string | undefined }) {
  if (!message) return null;
  return (
    <p id={id} className="mt-1 text-critical-text text-xs">
      {message}
    </p>
  );
}

export function FieldLabel({ htmlFor, children }: { htmlFor: string; children: ReactNode }) {
  return (
    <label htmlFor={htmlFor} className="font-mono text-2xs text-ink-dim uppercase tracking-caps">
      {children}
    </label>
  );
}

export function InlineInput({
  label,
  value,
  onChange,
  error,
  placeholder,
  visibleLabel = false,
  type = "text",
  mono = false,
  inputMode,
  hint,
  autoComplete,
  className = "",
}: {
  label: string;
  value: string;
  onChange: (value: string) => void;
  error?: string;
  placeholder?: string;
  visibleLabel?: boolean;
  type?: "text" | "password" | "number" | "url";
  mono?: boolean;
  inputMode?: "numeric" | "url" | "text";
  hint?: string;
  autoComplete?: string;
  className?: string;
}) {
  const id = useId();
  const describedBy = error ? `${id}-error` : hint ? `${id}-hint` : undefined;
  return (
    <div className={`flex min-w-0 flex-col gap-1 ${className}`}>
      {visibleLabel ? <FieldLabel htmlFor={id}>{label}</FieldLabel> : null}
      <input
        id={id}
        type={type}
        value={value}
        placeholder={placeholder}
        inputMode={inputMode}
        autoComplete={autoComplete ?? "off"}
        spellCheck={false}
        aria-label={visibleLabel ? undefined : label}
        aria-invalid={error ? true : undefined}
        aria-describedby={describedBy}
        onChange={(event) => onChange(event.target.value)}
        className={`${CONTROL} ${tone(error)} ${mono ? "font-mono" : ""}`}
      />
      {error ? (
        <FieldError id={`${id}-error`} message={error} />
      ) : hint ? (
        <p id={`${id}-hint`} className="text-ink-dim text-xs">
          {hint}
        </p>
      ) : null}
    </div>
  );
}

export function InlineSelect<Value extends string>({
  label,
  value,
  options,
  onChange,
  error,
  visibleLabel = false,
  className = "",
}: {
  label: string;
  value: Value;
  options: ReadonlyArray<{ value: Value; label: string }>;
  onChange: (value: Value) => void;
  error?: string;
  visibleLabel?: boolean;
  className?: string;
}) {
  const id = useId();
  return (
    <div className={`flex min-w-0 flex-col gap-1 ${className}`}>
      {visibleLabel ? <FieldLabel htmlFor={id}>{label}</FieldLabel> : null}
      <div className="relative">
        <select
          id={id}
          value={value}
          aria-label={visibleLabel ? undefined : label}
          aria-invalid={error ? true : undefined}
          aria-describedby={error ? `${id}-error` : undefined}
          onChange={(event) => {
            const chosen = options.find((option) => option.value === event.target.value);
            if (chosen) onChange(chosen.value);
          }}
          className={`${CONTROL} ${tone(error)} cursor-pointer appearance-none pr-6`}
        >
          {options.map((option) => (
            <option key={option.value} value={option.value}>
              {option.label}
            </option>
          ))}
        </select>
        <ChevronDown
          size={12}
          aria-hidden="true"
          className="pointer-events-none absolute top-1/2 right-2 -translate-y-1/2 text-ink-dim"
        />
      </div>
      <FieldError id={`${id}-error`} message={error} />
    </div>
  );
}

export function Checkbox({
  label,
  checked,
  onChange,
  hint,
}: {
  label: string;
  checked: boolean;
  onChange: (checked: boolean) => void;
  hint?: string;
}) {
  const id = useId();
  return (
    <div className="flex items-start gap-2">
      <input
        id={id}
        type="checkbox"
        checked={checked}
        onChange={(event) => onChange(event.target.checked)}
        aria-describedby={hint ? `${id}-hint` : undefined}
        className="mt-0.5 accent-brand"
      />
      <div>
        <label htmlFor={id} className="text-ink text-sm">
          {label}
        </label>
        {hint ? (
          <p id={`${id}-hint`} className="text-ink-dim text-xs">
            {hint}
          </p>
        ) : null}
      </div>
    </div>
  );
}

/** A problem that belongs to a group of fields rather than one of them. */
export function GroupProblem({ message }: { message: string | undefined }) {
  if (!message) return null;
  return (
    <p
      role="alert"
      className="border-critical-solid border-l-2 px-2 py-1 text-critical-text text-xs"
    >
      {message}
    </p>
  );
}
