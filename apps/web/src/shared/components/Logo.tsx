/**
 * The product's mark, and the only place it is drawn.
 *
 * **It already existed and was used nowhere.** `public/favicon.svg` has carried this
 * waveform since the app was created, so a browser tab was branded and every screen
 * inside it was not — the app showed a wordmark in a serif and nothing else. This is the
 * same path, so the tab and the console cannot drift into two different marks.
 *
 * The line is the product's own idiom rather than an abstract shape: a signal that rises,
 * spikes and settles is what every screen here draws, and `Sparkline` is the component
 * the whole design system is built around.
 *
 * `currentColor` rather than the favicon's literal green, because inside the app it sits
 * on a surface that changes with the theme and beside text it should agree with. The
 * favicon keeps its literal, since a browser chrome has no tokens to read.
 */
export function Logo({ size = 18 }: { size?: number }) {
  return (
    <svg
      width={size}
      height={size}
      viewBox="0 0 32 32"
      aria-hidden="true"
      className="shrink-0 text-brand-text"
    >
      <path
        d="M5 22 L11 22 L15 10 L19 26 L23 16 L27 16"
        fill="none"
        stroke="currentColor"
        strokeWidth={3}
        strokeLinecap="square"
        strokeLinejoin="miter"
      />
    </svg>
  );
}

/**
 * The mark and the name together, which is what "the product" looks like.
 *
 * One component so the three places it appears — the shell, the signed-out screens and
 * onboarding — cannot end up spelling or spacing it differently.
 */
export function Wordmark({ size }: { size?: number }) {
  return (
    <span className="flex items-center gap-2">
      <Logo size={size} />
      <span className="font-semibold font-serif text-ink text-md tracking-tight">nexovue</span>
    </span>
  );
}
