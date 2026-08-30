package insertdedup

import "testing"

// A retry re-derives the same rows and must produce the same token, or the
// store cannot tell the retry from new data and the rollup sums it in twice.
func TestTheSameRowsProduceTheSameToken(t *testing.T) {
	build := func() string {
		digest := NewDigest()
		digest.AddRow("tenant", "project", uint64(5), []float64{1, 2, 3})
		digest.AddRow("tenant", "project", uint64(6), []float64{4})
		return digest.Token()
	}
	if first, second := build(), build(); first != second {
		t.Errorf("token = %q then %q, want them equal", first, second)
	}
}

// And a different drain must not collide with it, or a legitimate insert is
// silently dropped as a duplicate.
func TestDifferentContentProducesADifferentToken(t *testing.T) {
	base := NewDigest()
	base.AddRow("tenant", "project", uint64(5))

	changed := NewDigest()
	changed.AddRow("tenant", "project", uint64(6))

	reordered := NewDigest()
	reordered.AddRow("project", "tenant", uint64(5))

	extra := NewDigest()
	extra.AddRow("tenant", "project", uint64(5))
	extra.AddRow("tenant", "project", uint64(5))

	tokens := map[string]string{
		"base":      base.Token(),
		"changed":   changed.Token(),
		"reordered": reordered.Token(),
		"extra":     extra.Token(),
	}
	seen := make(map[string]string, len(tokens))
	for name, token := range tokens {
		if other, clash := seen[token]; clash {
			t.Errorf("%s and %s share the token %q", name, other, token)
		}
		seen[token] = name
	}
}

// A field boundary that hashes away would let two different drains share a
// token: "ab" then "c" is not "a" then "bc".
func TestFieldBoundariesAreNotHashedAway(t *testing.T) {
	first := NewDigest()
	first.AddRow("ab", "c")

	second := NewDigest()
	second.AddRow("a", "bc")

	if first.Token() == second.Token() {
		t.Errorf("both hashed to %q; the field separator is not doing its job", first.Token())
	}
}

// Two rows in one insert must not hash the same as their contents run together
// into one row.
func TestRowBoundariesAreNotHashedAway(t *testing.T) {
	split := NewDigest()
	split.AddRow("a")
	split.AddRow("b")

	joined := NewDigest()
	joined.AddRow("a", "b")

	if split.Token() == joined.Token() {
		t.Errorf("both hashed to %q; the row separator is not doing its job", split.Token())
	}
}
