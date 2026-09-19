package mask

import (
	nexovuev1 "github.com/nexovue-tech/nexovue/packages/contracts/gen/go/nexovue/v1"
)

// MaskProfileSamples masks every value the project's rules cover across a profiles
// payload, and returns how many it replaced.
//
// Attributes only — the resource's and each sample's. Frames are not masked:
// function names, files and lines are the customer's code, not their customers'
// data, and a masked frame would be a flame graph nobody can read (F17, BR3).
// A sample's labels are where caller-controlled text lives: a profiler that tags
// samples with a request's user id is exactly what the key rules are for.
func (m *Masker) MaskProfileSamples(projectID string, samples *nexovuev1.ProfileSamples) int {
	rules := m.rules.MaskingRules(projectID)
	masked := 0
	for _, batch := range samples.GetBatches() {
		masked += maskAttributes(rules, batch.GetResourceAttributes())
		for _, sample := range batch.GetSamples() {
			masked += maskAttributes(rules, sample.GetAttributes())
		}
	}
	return masked
}
