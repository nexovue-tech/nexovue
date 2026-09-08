/**
 * An amount stored in minor units, rendered in its own currency.
 *
 * Lives in `shared` because two screens read the same figures — the usage screen
 * and the billing screen — and a second copy of the divisor rule is a second
 * chance to quote a bill a hundred times wrong.
 */

import { formatCount, NUMBER_LOCALE } from "./number";

const moneyFormatters = new Map<string, Intl.NumberFormat>();

function moneyFormatter(currency: string): Intl.NumberFormat | undefined {
  const cached = moneyFormatters.get(currency);
  if (cached) return cached;
  try {
    const formatter = new Intl.NumberFormat(NUMBER_LOCALE, { style: "currency", currency });
    moneyFormatters.set(currency, formatter);
    return formatter;
  } catch {
    return undefined; // not a currency code at all; see formatMoney
  }
}

/**
 * A price stored in minor units, in its own currency.
 *
 * The divisor is the currency's own, not a hardcoded hundred: the yen's minor unit is
 * the yen, so ¥1000 is stored as 1000 and dividing by a hundred would quote a bill at a
 * hundredth of its size. `Intl` carries the ISO 4217 exponent, so it is asked rather
 * than guessed. A code `Intl` will not take is a broken row, and the minor units are
 * shown raw beside it rather than converted by a guessed exponent.
 */
export function formatMoney(minorUnits: number, currency: string): string {
  const formatter = moneyFormatter(currency);
  if (!formatter) return `${formatCount(minorUnits)} ${currency}`;

  const exponent = formatter.resolvedOptions().maximumFractionDigits ?? 2;
  return formatter.format(minorUnits / 10 ** exponent);
}
