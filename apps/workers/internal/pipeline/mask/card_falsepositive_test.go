package mask

import "testing"

// The regression: masking is irreversible, and this rule is on by default over
// every attribute, so a false positive destroys real telemetry with no way back.
// Luhn alone passes one digit run in ten. A nanosecond timestamp passed it, a
// service name carrying one was stored as `chaos-[masked]`, and the metric that
// named that service then read as total data loss.
func TestOrdinaryLongNumbersAreNotMistakenForCards(t *testing.T) {
	notCards := []struct{ name, value string }{
		{"nanosecond timestamp that passes Luhn", "1787816027743000000"},
		{"nanosecond timestamp that does not", "1787833301615779000"},
		{"millisecond timestamp", "1787833301615"},
		{"order id", "9900112233445566"},
		{"snowflake id", "1234567890123456789"},
		{"phone number with separators", "1-555-0123-4567-890"},
		{"account number starting 7", "7012345678901234"},
		{"sixteen ones", "1111111111111111"},
	}
	for _, sample := range notCards {
		t.Run(sample.name, func(t *testing.T) {
			masked, changed := maskCardNumbers(sample.value, blankRun)
			if changed {
				t.Errorf("%q was masked to %q; it is not a card number, and masking is "+
					"irreversible", sample.value, masked)
			}
		})
	}
}

// The rule still has to do its job: a real card is destroyed, whatever network
// issued it and however it is written.
func TestRealCardNumbersAreStillMasked(t *testing.T) {
	cards := []struct{ network, value string }{
		{"Visa 16", "4111111111111111"},
		{"Visa 13", "4222222222222"},
		{"Mastercard 51-55", "5555555555554444"},
		{"Mastercard 2221-2720", "2223003122003222"},
		{"American Express 34", "343434343434343"},
		{"American Express 37", "378282246310005"},
		{"Discover 6011", "6011111111111117"},
		{"JCB", "3530111333300000"},
		{"Diners Club", "30569309025904"},
		{"written with spaces", "4111 1111 1111 1111"},
		{"written with dashes", "4111-1111-1111-1111"},
	}
	for _, card := range cards {
		t.Run(card.network, func(t *testing.T) {
			if _, changed := maskCardNumbers(card.value, blankRun); !changed {
				t.Errorf("a real %s number (%q) survived masking", card.network, card.value)
			}
		})
	}
}

func blankRun(string) (string, bool) { return Placeholder, true }
