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

// Rules is one project's masking configuration: the default families it keeps
// plus whatever extra keys it asked for. It is a value, built once per refresh
// and read concurrently without a lock.
type Rules struct {
	credentialKeys      bool
	authorizationHeader bool
	cookies             bool
	cardNumbers         bool

	// maskKeys are matched whole, maskKeyContains as substrings; both are
	// lower-cased at build time so matching never allocates.
	maskKeys        map[string]struct{}
	maskKeyContains []string
}

// Defaults is the ruleset a project with no configuration gets: every
// high-confidence family on, nothing else.
func Defaults() Rules {
	return Rules{
		credentialKeys:      true,
		authorizationHeader: true,
		cookies:             true,
		cardNumbers:         true,
	}
}

// projectConfig is the JSON shape of projects.masking_config.
//
// It carries no regular expressions on purpose: a pattern loaded from the
// database and run against every attribute of every span is both a performance
// cliff and a denial-of-service vector wearing a configuration field's costume.
type projectConfig struct {
	// Disable names default families to switch off for this project.
	Disable []string `json:"disable"`
	// MaskKeys are attribute keys masked in full, matched case-insensitively.
	MaskKeys []string `json:"mask_keys"`
	// MaskKeyContains are substrings; any key containing one is masked.
	MaskKeyContains []string `json:"mask_key_contains"`
}

// RulesFromJSON builds a project's rules from its masking_config document.
// An empty or absent document yields the defaults; an unknown family name is an
// error rather than a silently ignored line, because the operator who typed it
// believes data is being masked.
func RulesFromJSON(document []byte) (Rules, error) {
	rules := Defaults()
	if len(document) == 0 {
		return rules, nil
	}

	var parsed projectConfig
	if err := json.Unmarshal(document, &parsed); err != nil {
		return Rules{}, fmt.Errorf("masking_config: %w", err)
	}

	for _, name := range parsed.Disable {
		if err := rules.disable(strings.TrimSpace(name)); err != nil {
			return Rules{}, err
		}
	}
	rules.maskKeys = lowercaseSet(parsed.MaskKeys)
	rules.maskKeyContains = lowercaseList(parsed.MaskKeyContains)
	return rules, nil
}

func (r *Rules) disable(name string) error {
	switch name {
	case RuleCredentialKeys:
		r.credentialKeys = false
	case RuleAuthorizationHeaders:
		r.authorizationHeader = false
	case RuleCookies:
		r.cookies = false
	case RuleCardNumbers:
		r.cardNumbers = false
	default:
		return fmt.Errorf("masking_config: unknown rule %q; valid names are %s",
			name, strings.Join(DefaultRuleNames, ", "))
	}
	return nil
}

// masksKey reports whether an attribute under this key must be replaced whole.
func (r Rules) masksKey(key string) bool {
	lowered := strings.ToLower(key)
	if r.credentialKeys && containsAny(lowered, credentialKeyMarkers) {
		return true
	}
	if r.authorizationHeader && headerNameIn(lowered, authorizationHeaderNames) {
		return true
	}
	if r.cookies && headerNameIn(lowered, cookieHeaderNames) {
		return true
	}
	if _, ok := r.maskKeys[lowered]; ok {
		return true
	}
	return containsAny(lowered, r.maskKeyContains)
}

// masksValues reports whether string values need scanning at all, which lets
// the hot path skip every value when no value rule is on.
func (r Rules) masksValues() bool { return r.cardNumbers }

// maskValue returns the value with every masked run replaced, and whether
// anything changed.
func (r Rules) maskValue(value string) (string, bool) {
	if !r.cardNumbers {
		return value, false
	}
	return maskCardNumbers(value)
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
