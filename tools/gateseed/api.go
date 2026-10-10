package main

import (
	"bytes"
	"context"
	"encoding/json"
	"fmt"
	"io"
	"net/http"
	"net/http/cookiejar"
	"net/url"
)

// GateCheckName is the synthetic check the gate creates for itself, once.
const GateCheckName = "Gate: API health"

// gateCheckInterval is how often it is probed. The scheduler's own floor.
const gateCheckInterval = 60

// EnsureGateCheck signs in as the development stack's seeded owner and makes sure
// the gate's own synthetic check exists, returning its id.
//
// **A target the stack always runs.** The synthetics suite asserts on real
// prober output, and the only checks the development stack had probed the
// OpenTelemetry demo's storefront, so the suite failed whenever the demo was
// down — for two days after it came back, before the suite read a clean hour
// (docs/changes/tests-do-not-need-the-demo). This one probes the API's own
// liveness endpoint.
//
// Idempotent, by name, like tools/otel-demo/doors.py: every gate run must not
// mint another check.
func EnsureGateCheck(ctx context.Context, cfg Config) (string, error) {
	jar, err := cookiejar.New(nil)
	if err != nil {
		return "", err
	}
	client := &http.Client{Timeout: cfg.RequestTimeout, Jar: jar}

	if _, err := call(ctx, client, cfg, http.MethodPost, "/api/v1/auth/login",
		map[string]string{"email": cfg.DevEmail, "password": cfg.DevPassword}); err != nil {
		return "", fmt.Errorf("signing in as the seeded owner: %w", err)
	}

	var projects struct {
		Projects []struct {
			ID string `json:"id"`
		} `json:"projects"`
	}
	if err := decode(ctx, client, cfg, "/api/v1/projects", &projects); err != nil {
		return "", err
	}
	if len(projects.Projects) == 0 {
		return "", fmt.Errorf("the seeded owner has no project to create the gate's check in")
	}
	project := projects.Projects[0].ID

	var checks struct {
		Checks []struct {
			ID   string `json:"id"`
			Name string `json:"name"`
		} `json:"checks"`
	}
	if err := decode(ctx, client, cfg, "/api/v1/checks?project_id="+url.QueryEscape(project), &checks); err != nil {
		return "", err
	}
	for _, check := range checks.Checks {
		if check.Name == GateCheckName {
			return check.ID, nil
		}
	}

	created, err := call(ctx, client, cfg, http.MethodPost, "/api/v1/checks", map[string]any{
		"project_id":       project,
		"name":             GateCheckName,
		"interval_seconds": gateCheckInterval,
		"config": map[string]any{
			"type": "api",
			"request": map[string]any{
				"method": "GET",
				"url":    cfg.ProbeTarget,
				"assertions": []any{map[string]any{
					"source": "status_code", "comparison": "less_than", "target": "400",
				}},
			},
		},
	})
	if err != nil {
		return "", fmt.Errorf("creating the gate's synthetic check: %w", err)
	}
	var check struct {
		ID string `json:"id"`
	}
	if err := json.Unmarshal(created, &check); err != nil || check.ID == "" {
		return "", fmt.Errorf("the API created the gate's check but answered %q", string(created))
	}
	return check.ID, nil
}

func decode(ctx context.Context, client *http.Client, cfg Config, path string, into any) error {
	body, err := call(ctx, client, cfg, http.MethodGet, path, nil)
	if err != nil {
		return err
	}
	if err := json.Unmarshal(body, into); err != nil {
		return fmt.Errorf("reading %s: %w", path, err)
	}
	return nil
}

func call(ctx context.Context, client *http.Client, cfg Config, method, path string, payload any) ([]byte, error) {
	var body io.Reader
	if payload != nil {
		encoded, err := json.Marshal(payload)
		if err != nil {
			return nil, err
		}
		body = bytes.NewReader(encoded)
	}
	request, err := http.NewRequestWithContext(ctx, method, cfg.API+path, body)
	if err != nil {
		return nil, err
	}
	if payload != nil {
		request.Header.Set("Content-Type", "application/json")
	}
	response, err := client.Do(request)
	if err != nil {
		return nil, fmt.Errorf("%s %s: %w", method, path, err)
	}
	defer func() { _ = response.Body.Close() }()
	answer, err := io.ReadAll(io.LimitReader(response.Body, 1<<20))
	if err != nil {
		return nil, err
	}
	if response.StatusCode >= http.StatusBadRequest {
		return nil, fmt.Errorf("%s %s answered %d: %.300s", method, path, response.StatusCode, answer)
	}
	return answer, nil
}
