import { type ThemePreference, useTheme } from "./use-theme";

const OPTIONS: Array<{ value: ThemePreference; label: string; title: string }> = [
  { value: "system", label: "auto", title: "Follow the operating system" },
  { value: "dark", label: "dark", title: "Always dark" },
  { value: "light", label: "light", title: "Always light" },
];

/**
 * Three states, spelled out. A two-state switch cannot say "follow the OS", and a moon
 * icon cannot say which of the three you are in.
 */
export function ThemeToggle() {
  const { preference, setPreference } = useTheme();

  return (
    <fieldset className="flex items-center gap-0 border border-line">
      <legend className="sr-only">Theme</legend>
      {OPTIONS.map((option) => {
        const selected = preference === option.value;
        return (
          <button
            key={option.value}
            type="button"
            title={option.title}
            aria-pressed={selected}
            onClick={() => setPreference(option.value)}
            className={`px-2 py-0.5 font-mono text-2xs lowercase tracking-caps transition-colors ${
              selected ? "bg-raised text-ink" : "text-ink-dim hover:text-ink"
            }`}
          >
            {option.label}
          </button>
        );
      })}
    </fieldset>
  );
}
