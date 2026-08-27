package mask

import "strings"

// Payment card numbers are 13 to 19 digits. Shorter runs are order ids and
// phone numbers; longer runs are not card numbers at all.
const (
	minCardDigits = 13
	maxCardDigits = 19
)

// maskCardNumbers replaces every digit run in text that looks like a payment
// card number and passes a Luhn check. Separators inside a run — spaces and
// dashes, the two ways a human writes a card number — are part of the run.
//
// Luhn is what makes this safe to have on by default: a random 16-digit id has
// one chance in ten of passing it, and a real card number always does. The one
// in ten is why replace may decline a run: the project allowlisted it.
func maskCardNumbers(text string, replace func(run string) (string, bool)) (string, bool) {
	if !hasEnoughDigits(text) {
		return text, false
	}

	// The builder is created only once a run actually matches, so a value with
	// a long id in it and no card number allocates nothing.
	var builder strings.Builder
	index, copied := 0, 0
	for index < len(text) {
		if !isDigit(text[index]) {
			index++
			continue
		}
		end, digits := scanCardRun(text, index)
		if isCardNumber(text[index:end], digits) {
			if replacement, ok := replace(text[index:end]); ok {
				if builder.Len() == 0 {
					builder.Grow(len(text))
				}
				builder.WriteString(text[copied:index])
				builder.WriteString(replacement)
				copied = end
			}
		}
		index = end
	}

	if copied == 0 {
		return text, false
	}
	builder.WriteString(text[copied:])
	return builder.String(), true
}

func isCardNumber(run string, digits int) bool {
	return digits >= minCardDigits && digits <= maxCardDigits &&
		issuedByAKnownNetwork(run, digits) && luhnValid(run)
}

// issuedByAKnownNetwork reports whether a run begins the way a real payment card
// does, at a length that issuer actually uses.
//
// Luhn alone is not a card test. One digit run in ten passes it by chance, and
// this rule is on by default over every attribute, so that tenth run is
// destroyed irreversibly. It is not hypothetical: a nanosecond timestamp
// (1787816027743000000) passes Luhn, and a service name carrying one was masked
// down to `chaos-[masked]` — the count that named it then read as total data
// loss.
//
// Checking the issuer identification number costs nothing and removes almost all
// of that. A timestamp starts 17; no network issues 17. Ranges from the card
// networks' published IIN assignments; this deliberately answers "could this be
// a card" and not "whose card is it", which genuinely needs a maintained
// database and would be the wrong dependency for a masking rule.
func issuedByAKnownNetwork(run string, digits int) bool {
	prefix := digitsOf(run, 4)
	switch {
	case prefix[0] == '4': // Visa
		return digits == 13 || digits == 16 || digits == 19
	case between(prefix, 2, 51, 55), between(prefix, 4, 2221, 2720): // Mastercard
		return digits == 16
	case between(prefix, 2, 34, 34), between(prefix, 2, 37, 37): // American Express
		return digits == 15
	case between(prefix, 4, 6011, 6011), between(prefix, 2, 65, 65),
		between(prefix, 3, 644, 649): // Discover
		return digits == 16 || digits == 19
	case between(prefix, 4, 3528, 3589): // JCB
		return digits >= 16 && digits <= 19
	case between(prefix, 3, 300, 305), between(prefix, 2, 36, 36),
		between(prefix, 2, 38, 39): // Diners Club
		return digits == 14
	case between(prefix, 2, 62, 62): // UnionPay
		return digits >= 16 && digits <= 19
	default:
		return false
	}
}

// digitsOf returns the first n digits of a run, ignoring separators, padded so
// a short run cannot index out of range.
func digitsOf(run string, n int) string {
	var out []byte
	for i := 0; i < len(run) && len(out) < n; i++ {
		if isDigit(run[i]) {
			out = append(out, run[i])
		}
	}
	for len(out) < n {
		out = append(out, '0')
	}
	return string(out)
}

// between reports whether the first width digits of prefix fall in [low, high].
func between(prefix string, width, low, high int) bool {
	value := 0
	for i := 0; i < width; i++ {
		value = value*10 + int(prefix[i]-'0')
	}
	return value >= low && value <= high
}

// scanCardRun returns the end of the digit run starting at start, and how many
// digits it holds. A space or dash continues the run only when a digit follows.
func scanCardRun(text string, start int) (end, digits int) {
	end = start
	for end < len(text) {
		switch {
		case isDigit(text[end]):
			digits++
			end++
		case isSeparator(text[end]) && end+1 < len(text) && isDigit(text[end+1]):
			end++
		default:
			return end, digits
		}
	}
	return end, digits
}

// hasEnoughDigits is the cheap gate that keeps the scanner off the hot path for
// the overwhelming majority of attribute values, which hold no long digit run.
func hasEnoughDigits(text string) bool {
	if len(text) < minCardDigits {
		return false
	}
	run := 0
	for index := 0; index < len(text); index++ {
		switch {
		case isDigit(text[index]):
			run++
			if run >= minCardDigits {
				return true
			}
		case isSeparator(text[index]):
			// Keeps the run alive across the separators a human types.
		default:
			run = 0
		}
	}
	return false
}

// luhnValid runs the Luhn checksum over the digits of run, ignoring separators.
func luhnValid(run string) bool {
	sum, position := 0, 0
	for index := len(run) - 1; index >= 0; index-- {
		if !isDigit(run[index]) {
			continue
		}
		digit := int(run[index] - '0')
		if position%2 == 1 {
			digit *= 2
			if digit > 9 {
				digit -= 9
			}
		}
		sum += digit
		position++
	}
	return position > 0 && sum%10 == 0
}

func isDigit(c byte) bool { return c >= '0' && c <= '9' }

func isSeparator(c byte) bool { return c == ' ' || c == '-' }
