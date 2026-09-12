# Project setup, credentials, and onboarding

## Status

**Verified current-state baseline.** Related catalog features: F23 and F24; their full acceptance
criteria are not yet specified.

## Implemented behavior

- A signed-in organization can list, create, rename, configure, and close projects.
- Project closure withdraws its credentials. Projects hold sampling and masking configuration.
- Server API keys can be listed, created, and revoked. Browser site keys are a separate credential
  kind with allowed origins.
- The onboarding screen guides a new user to send telemetry and detects first trace data through
  the ordinary trace path.

## Code evidence

`apps/api/src/nexovue_api/projects/router.py`, `apikeys/router.py`, `signup/service.py`, and
`apps/web/src/features/projects/ProjectsScreen.tsx`, `settings/ApiKeysScreen.tsx`, and
`onboarding/OnboardingScreen.tsx` are the customer-facing sources. Browser-key origin behavior is
tested in `apps/receiver/internal/otlp/browserdoor_test.go`.
