/**
 * Invisible and direction-changing characters, made visible (F20 FR61, BR14).
 *
 * A service name is attacker-controlled text: `checkout` with a zero-width space, or with
 * a right-to-left override, looks exactly like `checkout`. So wherever a dashboard shows a
 * telemetry value or stored free text, such characters appear as `[U+202E]`. For display
 * only: the raw value is what is bound and stored, because it must match what a service
 * sends. Truncation and `dir="auto"` do not stop a look-alike; this does.
 *
 * Controls, format characters (zero-width, bidirectional, tags), line and paragraph
 * separators, private use and lone surrogates: the same categories the API's validator
 * refuses in titles and labels (`dashboards/definition.py`).
 */

const INVISIBLE = /[\p{Cc}\p{Cf}\p{Zl}\p{Zp}\p{Co}\p{Cs}]/gu;
const LINE_BREAK = "\n";
const HEX = 16;
const MIN_DIGITS = 4;

export interface EscapeOptions {
  /** A note keeps its line breaks; a name never has any. */
  keepLineBreaks?: boolean;
}

export function visibleEscapes(
  text: string,
  { keepLineBreaks = false }: EscapeOptions = {},
): string {
  return text.replace(INVISIBLE, (character) => {
    if (keepLineBreaks && character === LINE_BREAK) return character;
    const code = character.codePointAt(0) ?? 0;
    return `[U+${code.toString(HEX).toUpperCase().padStart(MIN_DIGITS, "0")}]`;
  });
}
