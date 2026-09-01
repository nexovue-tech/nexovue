package otlp_test

import (
	"bytes"
	"log/slog"
	"net/http"
	"net/http/httptest"
	"strings"
	"testing"

	"github.com/nexovue-tech/nexovue/apps/receiver/internal/auth"
	"github.com/nexovue-tech/nexovue/apps/receiver/internal/otlp"
)

const pageOrigin = "https://shop.example.com"

// options sends the preflight a browser sends before an export.
func options(t *testing.T, d door, origin string) *httptest.ResponseRecorder {
	t.Helper()
	request := httptest.NewRequest(http.MethodOptions, otlp.TracesPath, nil)
	if origin != "" {
		request.Header.Set("Origin", origin)
		request.Header.Set("Access-Control-Request-Method", http.MethodPost)
		request.Header.Set("Access-Control-Request-Headers", "content-type,"+auth.HeaderName)
	}

	recorder := httptest.NewRecorder()
	d.handler.ServeHTTP(recorder, request)
	return recorder
}

func TestPreflightIsAnsweredWithoutACredential(t *testing.T) {
	// The load-bearing one. A preflight carries the key's header *name* and
	// never its value, so refusing here would turn away every browser before
	// the request that can actually be judged is ever sent.
	d := newDoor(t, doorOptions{})

	response := options(t, d, pageOrigin)

	if response.Code != http.StatusNoContent {
		t.Fatalf("status = %d, want %d (%s)", response.Code, http.StatusNoContent, response.Body)
	}
	header := response.Header()
	if got := header.Get("Access-Control-Allow-Origin"); got != pageOrigin {
		t.Errorf("Access-Control-Allow-Origin = %q, want the origin echoed", got)
	}
	if got := header.Get("Access-Control-Allow-Methods"); got != http.MethodPost {
		t.Errorf("Access-Control-Allow-Methods = %q, want POST", got)
	}
	if got := header.Get("Access-Control-Allow-Headers"); !strings.Contains(got, auth.HeaderName) {
		t.Errorf("Access-Control-Allow-Headers = %q, want the ingest key header", got)
	}
	if got := header.Get("Access-Control-Allow-Headers"); !strings.Contains(got, "content-type") {
		t.Errorf("Access-Control-Allow-Headers = %q, want content-type", got)
	}
	if got := header.Get("Access-Control-Max-Age"); got == "" {
		t.Error("no Access-Control-Max-Age; every export would be preflighted twice")
	}
	if got := header.Get("Vary"); got != "Origin" {
		t.Errorf("Vary = %q, want Origin", got)
	}
}

func TestPreflightNeverAllowsCredentials(t *testing.T) {
	// sendBeacon with a JSON body sets credentials mode include. Allowing them
	// would carry a visitor's cookies to us on every export.
	d := newDoor(t, doorOptions{})

	if got := options(t, d, pageOrigin).Header().Get("Access-Control-Allow-Credentials"); got != "" {
		t.Errorf("Access-Control-Allow-Credentials = %q, want it absent", got)
	}
	response := post(t, d, encodedRequest(t, exportRequest(traceID(0x11))),
		map[string]string{"Origin": pageOrigin})
	if got := response.Header().Get("Access-Control-Allow-Credentials"); got != "" {
		t.Errorf("Access-Control-Allow-Credentials = %q on the export, want it absent", got)
	}
}

func TestAnOptionsWithNoOriginIsNotACORSAnswer(t *testing.T) {
	d := newDoor(t, doorOptions{})

	response := options(t, d, "")

	if response.Code != http.StatusNoContent {
		t.Fatalf("status = %d, want %d", response.Code, http.StatusNoContent)
	}
	if got := response.Header().Get("Access-Control-Allow-Origin"); got != "" {
		t.Errorf("Access-Control-Allow-Origin = %q, want it absent when nobody asked", got)
	}
	if got := response.Header().Get("Allow"); !strings.Contains(got, http.MethodPost) {
		t.Errorf("Allow = %q, want POST named", got)
	}
}

func TestASecretKeyUsedFromAPageIsRefusedReadably(t *testing.T) {
	// Two things at once, and both matter: the export is refused 403, and the
	// refusal carries the CORS headers that let the page read it. Without them
	// the browser reports an opaque CORS failure and the customer has nothing.
	d := newDoor(t, doorOptions{})

	response := post(t, d, encodedRequest(t, exportRequest(traceID(0x11))),
		map[string]string{"Origin": pageOrigin})

	if response.Code != http.StatusForbidden {
		t.Fatalf("status = %d, want %d (%s)", response.Code, http.StatusForbidden, response.Body)
	}
	if got := response.Header().Get("Access-Control-Allow-Origin"); got != pageOrigin {
		t.Errorf("Access-Control-Allow-Origin = %q, want the origin echoed on the refusal", got)
	}
	if len(d.producer.records) != 0 {
		t.Errorf("%d records reached the broker, want none", len(d.producer.records))
	}
	if body := response.Body.String(); !strings.Contains(body, "browser") {
		t.Errorf("body = %q, want it to name the problem", body)
	}
}

func TestAnUnauthenticatedBrowserExportIsAlsoReadable(t *testing.T) {
	// The onboarding case: a key typed wrong. A 401 the page cannot read is a
	// support ticket; a 401 it can read is a fix.
	d := newDoor(t, doorOptions{})

	response := post(t, d, encodedRequest(t, exportRequest(traceID(0x11))),
		map[string]string{"Origin": pageOrigin, auth.HeaderName: "nvk_live_000000000000000000000w"})

	if response.Code != http.StatusUnauthorized {
		t.Fatalf("status = %d, want %d (%s)", response.Code, http.StatusUnauthorized, response.Body)
	}
	if got := response.Header().Get("Access-Control-Allow-Origin"); got != pageOrigin {
		t.Errorf("Access-Control-Allow-Origin = %q, want the origin echoed on the refusal", got)
	}
}

func TestRetryAfterIsExposedToPageScript(t *testing.T) {
	// A browser SDK that cannot read Retry-After backs off on its own schedule
	// into a door already saying stop. The header is exposed on every reply to
	// an origin-bearing request, so the refusal that carries it can be read.
	d := newDoor(t, doorOptions{})

	response := post(t, d, encodedRequest(t, exportRequest(traceID(0x11))),
		map[string]string{"Origin": pageOrigin})

	if got := response.Header().Get("Access-Control-Expose-Headers"); !strings.Contains(got, "Retry-After") {
		t.Errorf("Access-Control-Expose-Headers = %q, want Retry-After", got)
	}
}

func TestAServerExportIsUnchangedByAnyOfThis(t *testing.T) {
	// Every exporter in production today is this request.
	d := newDoor(t, doorOptions{})

	response := post(t, d, encodedRequest(t, exportRequest(traceID(0x11))), nil)

	if response.Code != http.StatusOK {
		t.Fatalf("status = %d, want %d (%s)", response.Code, http.StatusOK, response.Body)
	}
	if got := response.Header().Get("Access-Control-Allow-Origin"); got != "" {
		t.Errorf("Access-Control-Allow-Origin = %q, want it absent when no origin was sent", got)
	}
	if got := response.Header().Get("Vary"); got != "Origin" {
		t.Errorf("Vary = %q, want Origin on every reply so no cache mixes them up", got)
	}
	if len(d.producer.records) != 1 {
		t.Errorf("%d records reached the broker, want 1", len(d.producer.records))
	}
}

func TestARefusedOriginIsCountedAndNamedInTheLog(t *testing.T) {
	// The diagnosis path. peer_address is the TLS terminator's and user_agent
	// is some visitor's browser; the origin is the only field that names the
	// customer's misconfigured site.
	written := &bytes.Buffer{}
	traffic := otlp.NewTraffic()
	d := newDoor(t, doorOptions{
		log:     slog.New(slog.NewJSONHandler(written, &slog.HandlerOptions{Level: slog.LevelDebug})),
		traffic: traffic,
	})

	post(t, d, encodedRequest(t, exportRequest(traceID(0x11))),
		map[string]string{"Origin": pageOrigin})

	var refusal map[string]any
	for _, line := range recordedLines(t, written) {
		if line["msg"] == "export refused" {
			refusal = line
		}
	}
	if refusal == nil {
		t.Fatalf("no refusal was logged (%s)", written)
	}
	if refusal["origin"] != pageOrigin {
		t.Errorf("origin = %v, want %q", refusal["origin"], pageOrigin)
	}
	if refusal["reason"] != "forbidden_origin" {
		t.Errorf("reason = %v, want forbidden_origin", refusal["reason"])
	}

	summary := summaryLine(t, runSummary(t, traffic))
	if summary["refused_forbidden_origin"] != float64(1) {
		t.Errorf("refused_forbidden_origin = %v, want 1", summary["refused_forbidden_origin"])
	}
}
