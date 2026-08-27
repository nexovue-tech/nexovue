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
	return digits >= minCardDigits && digits <= maxCardDigits && luhnValid(run)
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
