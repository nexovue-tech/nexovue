// Package mask removes sensitive values from decoded telemetry before anything
// downstream sees it.
//
// It is the first stage after decode on purpose: masking there means an
// unmasked value cannot reach a derived counter, a sampled write or the store.
// Masking is irreversible, so a rule is only on by default when its match can
// never be legitimately needed for debugging.
package mask

import (
	"encoding/json"
	"errors"
	"fmt"
	"strings"
)

// Placeholder replaces every masked value. It is deliberately not the logger's
// "[redacted]": that one describes a log line we chose not to print, this one
// describes stored data that no longer contains the original.
const Placeholder = "[masked]"

// Default rule families, named so a project can switch one off without
// switching off the rest.
const (
	// RuleCredentialKeys masks any attribute whose key names a credential.
	RuleCredentialKeys = "credential_keys" //nolint:gosec // a rule name, not a credential
	// RuleAuthorizationHeaders masks captured Authorization headers.
	RuleAuthorizationHeaders = "authorization_headers"
	// RuleCookies masks captured Cookie and Set-Cookie headers.
	RuleCookies = "cookies"
	// RuleCardNumbers masks digit runs inside a value that pass a Luhn check.
	RuleCardNumbers = "card_numbers"
)

// DefaultRuleNames lists every rule that is on without configuration.
var DefaultRuleNames = []string{
	RuleCredentialKeys,
	RuleAuthorizationHeaders,
	RuleCookies,
	RuleCardNumbers,
}

// credentialKeyMarkers are the key substrings the credential rule matches.
// api_key is spelled three ways in the wild and all three mean the same thing.
var credentialKeyMarkers = []string{
	"password",
	"secret",
	"token",
	"api_key",
	"apikey",
	"api-key",
}

// headerNames matched by the header rules. A captured header arrives as an
// attribute whose last dotted segment is the header name, which is what these
// compare against — the prefix differs between semconv generations.
var (
	authorizationHeaderNames = []string{"authorization", "proxy-authorization"}
	cookieHeaderNames        = []string{"cookie", "set-cookie"}
)

// action is what a matched value becomes: blanked to the placeholder, or
// replaced by a keyed pseudonym. Hashing exists because equal values stay equal
// under it, so "the same user across these requests" is still answerable
// afterwards — without saying who that user is.
type action uint8

const (
	actionBlank action = iota
	actionHash
)

// family is one default rule family: whether it is on, and what a match becomes.
type family struct {
	on     bool
	action action
}

// keyMarker is a key substring and what a value under a matching key becomes.
type keyMarker struct {
	text   string
	action action
}

// Rules is one project's masking configuration: the default families it keeps
// plus whatever extra keys it asked for. It is a value, built once per refresh
// and read concurrently without a lock.
type Rules struct {
	credentialKeys      family
	authorizationHeader family
	cookies             family
	cardNumbers         family

	// maskKeys are matched whole, maskKeyMarkers as substrings; both are
	// lower-cased at build time so matching never allocates, and both carry
	// what a match becomes.
	maskKeys       map[string]action
	maskKeyMarkers []keyMarker

	// allowValues are values no rule may mask, lower-cased at build time.
	allowValues map[string]struct{}

	// hashKey is the secret pseudonyms are derived with. Rules that hash
	// cannot be built without it.
	hashKey HashKey
}

// Defaults is the ruleset a project with no configuration gets: every
// high-confidence family on and blanking, nothing else.
func Defaults() Rules {
	blanking := family{on: true, action: actionBlank}
	return Rules{
		credentialKeys:      blanking,
		authorizationHeader: blanking,
		cookies:             blanking,
		cardNumbers:         blanking,
	}
}

// Option adjusts how a masking_config document is turned into rules.
type Option func(*Rules)

// WithHashKey installs the secret hashing rules derive their pseudonyms with.
// Without it a document that asks for hashing is refused rather than quietly
// blanked, because an operator who configured hashing is relying on being able
// to correlate what it produces.
func WithHashKey(key HashKey) Option {
	return func(rules *Rules) { rules.hashKey = key }
}

// projectConfig is the JSON shape of projects.masking_config.
//
// It carries no regular expressions on purpose: a pattern loaded from the
// database and run against every attribute of every span is both a performance
// cliff and a denial-of-service vector wearing a configuration field's costume.
type projectConfig struct {
	// Disable names default families to switch off for this project.
	Disable []string `json:"disable"`
	// Hash names default families whose matches become a pseudonym instead of
	// the placeholder.
	Hash []string `json:"hash"`
	// MaskKeys are attribute keys masked in full, matched case-insensitively.
	MaskKeys []string `json:"mask_keys"`
	// MaskKeyContains are substrings; any key containing one is masked.
	MaskKeyContains []string `json:"mask_key_contains"`
	// HashKeys and HashKeyContains match the same way as the two above, but
	// leave a pseudonym behind instead of the placeholder.
	HashKeys        []string `json:"hash_keys"`
	HashKeyContains []string `json:"hash_key_contains"`
	// AllowValues are values that survive every rule, matched whole and
	// case-insensitively. Whole values rather than patterns for the same reason
	// the rest of this document carries none.
	AllowValues []string `json:"allow_values"`
}

// RulesFromJSON builds a project's rules from its masking_config document.
// An empty or absent document yields the defaults; an unknown family name is an
// error rather than a silently ignored line, because the operator who typed it
// believes data is being masked.
func RulesFromJSON(document []byte, options ...Option) (Rules, error) {
	rules := Defaults()
	for _, option := range options {
		option(&rules)
	}
	if len(document) == 0 {
		return rules, nil
	}

	var parsed projectConfig
	if err := json.Unmarshal(document, &parsed); err != nil {
		return Rules{}, fmt.Errorf("masking_config: %w", err)
	}

	for _, name := range parsed.Disable {
		if err := rules.setFamily(strings.TrimSpace(name), func(f *family) { f.on = false }); err != nil {
			return Rules{}, err
		}
	}
	for _, name := range parsed.Hash {
		if err := rules.setFamily(strings.TrimSpace(name), func(f *family) { f.action = actionHash }); err != nil {
			return Rules{}, err
		}
	}
	rules.maskKeys = keyActions(parsed.MaskKeys, parsed.HashKeys)
	rules.maskKeyMarkers = keyActionMarkers(parsed.MaskKeyContains, parsed.HashKeyContains)
	rules.allowValues = lowercaseSet(parsed.AllowValues)

	if rules.hashesAnything() && !rules.hashKey.configured() {
		return Rules{}, errors.New(
			"masking_config: asks for hashing, but this worker has no masking hash key")
	}
	return rules, nil
}

// setFamily applies change to the named default family.
func (r *Rules) setFamily(name string, change func(*family)) error {
	switch name {
	case RuleCredentialKeys:
		change(&r.credentialKeys)
	case RuleAuthorizationHeaders:
		change(&r.authorizationHeader)
	case RuleCookies:
		change(&r.cookies)
	case RuleCardNumbers:
		change(&r.cardNumbers)
	default:
		return fmt.Errorf("masking_config: unknown rule %q; valid names are %s",
			name, strings.Join(DefaultRuleNames, ", "))
	}
	return nil
}

// hashesAnything reports whether any rule in force produces a pseudonym, which
// is what decides whether this project's rules need a hash key at all.
func (r Rules) hashesAnything() bool {
	for _, f := range r.families() {
		if f.on && f.action == actionHash {
			return true
		}
	}
	for _, act := range r.maskKeys {
		if act == actionHash {
			return true
		}
	}
	for _, marker := range r.maskKeyMarkers {
		if marker.action == actionHash {
			return true
		}
	}
	return false
}

func (r Rules) families() []family {
	return []family{r.credentialKeys, r.authorizationHeader, r.cookies, r.cardNumbers}
}

// masksKey reports whether an attribute under this key must be replaced whole,
// and what it is replaced with.
func (r Rules) masksKey(key string) (action, bool) {
	lowered := strings.ToLower(key)
	if r.credentialKeys.on && containsAny(lowered, credentialKeyMarkers) {
		return r.credentialKeys.action, true
	}
	if r.authorizationHeader.on && headerNameIn(lowered, authorizationHeaderNames) {
		return r.authorizationHeader.action, true
	}
	if r.cookies.on && headerNameIn(lowered, cookieHeaderNames) {
		return r.cookies.action, true
	}
	if act, ok := r.maskKeys[lowered]; ok {
		return act, true
	}
	return markerAction(lowered, r.maskKeyMarkers)
}

// masksValues reports whether string values need scanning at all, which lets
// the hot path skip every value when no value rule is on.
func (r Rules) masksValues() bool { return r.cardNumbers.on }

// allows reports whether a value is one the project named as safe. The
// allowlist beats every rule, which is the redaction processor's precedence and
// the only remedy an operator has for a false positive short of switching a
// whole rule off.
func (r Rules) allows(value string) bool {
	if len(r.allowValues) == 0 || value == "" {
		return false
	}
	_, ok := r.allowValues[strings.ToLower(value)]
	return ok
}

// maskValue returns the value with every masked run replaced, and whether
// anything changed.
func (r Rules) maskValue(value string) (string, bool) {
	if !r.cardNumbers.on || r.allows(value) {
		return value, false
	}
	return maskCardNumbers(value, r.replaceCardRun)
}

// replaceCardRun is what a matched card run becomes, reporting false for a run
// the project allowlisted — the internal id that happens to pass Luhn.
func (r Rules) replaceCardRun(run string) (string, bool) {
	if r.allows(run) {
		return run, false
	}
	return r.replace(r.cardNumbers.action, run), true
}

// replace returns what a matched value becomes. A value with no text to hash
// falls back to the placeholder: hashing every such value to one pseudonym
// would correlate things that have nothing to do with each other.
func (r Rules) replace(act action, original string) string {
	if act == actionHash && original != "" {
		return r.hashKey.pseudonym(original)
	}
	return Placeholder
}

// markerAction returns what a key containing one of the markers becomes.
func markerAction(lowered string, markers []keyMarker) (action, bool) {
	for _, marker := range markers {
		if marker.text != "" && strings.Contains(lowered, marker.text) {
			return marker.action, true
		}
	}
	return actionBlank, false
}

// headerNameIn matches an attribute key against header names. Captured headers
// are keyed http.request.header.<name> (or the legacy http.request.header_<name>
// some SDKs emit), so the comparison is on the tail, not the whole key.
func headerNameIn(loweredKey string, names []string) bool {
	tail := loweredKey
	if cut := strings.LastIndexAny(loweredKey, "._"); cut >= 0 {
		tail = loweredKey[cut+1:]
	}
	for _, name := range names {
		if tail == name || strings.HasSuffix(loweredKey, "."+name) {
			return true
		}
	}
	return false
}

func containsAny(lowered string, markers []string) bool {
	for _, marker := range markers {
		if marker != "" && strings.Contains(lowered, marker) {
			return true
		}
	}
	return false
}

func lowercaseSet(values []string) map[string]struct{} {
	if len(values) == 0 {
		return nil
	}
	set := make(map[string]struct{}, len(values))
	for _, value := range values {
		trimmed := strings.ToLower(strings.TrimSpace(value))
		if trimmed != "" {
			set[trimmed] = struct{}{}
		}
	}
	return set
}

// keyActions indexes whole keys by what a match becomes. Hashed keys are added
// last, so a key named in both lists is hashed rather than blanked.
func keyActions(blanked, hashed []string) map[string]action {
	if len(blanked)+len(hashed) == 0 {
		return nil
	}
	actions := make(map[string]action, len(blanked)+len(hashed))
	for _, value := range lowercaseList(blanked) {
		actions[value] = actionBlank
	}
	for _, value := range lowercaseList(hashed) {
		actions[value] = actionHash
	}
	return actions
}

// keyActionMarkers pairs each key substring with what a match becomes, blanked
// substrings first so an operator reads them in the order they wrote them.
func keyActionMarkers(blanked, hashed []string) []keyMarker {
	markers := make([]keyMarker, 0, len(blanked)+len(hashed))
	for _, value := range lowercaseList(blanked) {
		markers = append(markers, keyMarker{text: value, action: actionBlank})
	}
	for _, value := range lowercaseList(hashed) {
		markers = append(markers, keyMarker{text: value, action: actionHash})
	}
	if len(markers) == 0 {
		return nil
	}
	return markers
}

func lowercaseList(values []string) []string {
	var list []string
	for _, value := range values {
		trimmed := strings.ToLower(strings.TrimSpace(value))
		if trimmed != "" {
			list = append(list, trimmed)
		}
	}
	return list
}
