package otlp_test

import (
	"bytes"
	"log/slog"
	"net/http"
	"net/http/httptest"
	"strings"
	"testing"

	"google.golang.org/genproto/googleapis/rpc/code"
	"google.golang.org/protobuf/encoding/protojson"
	"google.golang.org/protobuf/proto"

	nexovuev1 "github.com/nexovue-tech/nexovue/packages/contracts/gen/go/nexovue/v1"

	"github.com/nexovue-tech/nexovue/apps/receiver/internal/auth"
	"github.com/nexovue-tech/nexovue/apps/receiver/internal/otlp"
)

// The browser door as it is mounted, rather than as its parts test individually.
//
// Everything below exists because the two kinds of key were built to be
// unmixable at the table and then had to be wired to real paths without a
// conditional putting them back together. So these assert the wiring: which
// credential each path resolves, where each path will read one from, and that a
// key of the wrong kind is unknown rather than merely unauthorized.

// The browser door's three paths, spelled out rather than composed, so a change
// to the prefix has to be made here too and is seen.
const (
	browserTracesPath  = "/browser/v1/traces"
	browserLogsPath    = "/browser/v1/logs"
	browserMetricsPath = "/browser/v1/metrics"
)

const otherOrigin = "https://someone-elses.example"

// browserPost sends an export the way a page does: no key header unless asked,
// an Origin, and whatever query string the caller wants.
func browserPost(
	t *testing.T,
	d door,
	target string,
	origin string,
	headers map[string]string,
) *httptest.ResponseRecorder {
	t.Helper()
	body := encodedRequest(t, exportRequest(traceID(0x11)))
	request := httptest.NewRequest(http.MethodPost, target, bytes.NewReader(body))
	request.Header.Set("Content-Type", contentTypeProtobuf)
	if origin != "" {
		request.Header.Set("Origin", origin)
	}
	for name, value := range headers {
		request.Header.Set(name, value)
	}

	recorder := httptest.NewRecorder()
	d.handler.ServeHTTP(recorder, request)
	return recorder
}

// siteKeyHeader is what a page that can set headers sends.
var siteKeyHeader = map[string]string{auth.HeaderName: validSiteKey}

/* ------------------------------------------------- the browser door serves */

func TestABrowserExportFromAnAllowedOriginIsPublished(t *testing.T) {
	d := newDoor(t, doorOptions{})

	response := browserPost(t, d, browserTracesPath, pageOrigin, siteKeyHeader)

	if response.Code != http.StatusOK {
		t.Fatalf("status = %d, want %d (%s)", response.Code, http.StatusOK, response.Body)
	}
	if len(d.producer.records) != 1 {
		t.Fatalf("produced %d records, want 1", len(d.producer.records))
	}
	var envelope nexovuev1.Envelope
	if err := proto.Unmarshal(d.producer.records[0].Value, &envelope); err != nil {
		t.Fatalf("record is not an Envelope: %v", err)
	}
	// The public key's own project, taken from site_keys and from nowhere else.
	if envelope.GetTenantId() != orgID || envelope.GetProjectId() != projectID {
		t.Errorf("envelope tenancy = %s/%s, want %s/%s",
			envelope.GetTenantId(), envelope.GetProjectId(), orgID, projectID)
	}
	// A browser export is still an OTLP export: nothing downstream may learn
	// which door it came through.
	if envelope.GetDoor() != nexovuev1.Door_DOOR_OTLP {
		t.Errorf("door = %v, want DOOR_OTLP", envelope.GetDoor())
	}
}

// An empty export is a legal one for every signal, so it proves the route is
// mounted and authenticated without three payload shapes saying so.
func TestAllThreeSignalsAreMountedOnTheBrowserDoor(t *testing.T) {
	for _, path := range []string{browserTracesPath, browserLogsPath, browserMetricsPath} {
		t.Run(path, func(t *testing.T) {
			d := newDoor(t, doorOptions{})

			request := httptest.NewRequest(http.MethodPost, path, bytes.NewReader(nil))
			request.Header.Set("Content-Type", contentTypeProtobuf)
			request.Header.Set("Origin", pageOrigin)
			request.Header.Set(auth.HeaderName, validSiteKey)
			response := httptest.NewRecorder()
			d.handler.ServeHTTP(response, request)

			if response.Code != http.StatusOK {
				t.Errorf("status = %d, want %d (%s)", response.Code, http.StatusOK, response.Body)
			}
		})
	}
}

// sendBeacon cannot set a header, and it is the only thing that survives an
// unload — so this path is the whole reason the query parameter exists.
func TestASiteKeyInTheQueryStringAuthenticatesTheBrowserDoor(t *testing.T) {
	d := newDoor(t, doorOptions{})

	response := browserPost(t, d,
		browserTracesPath+"?"+auth.SiteKeyQueryParam+"="+validSiteKey, pageOrigin, nil)

	if response.Code != http.StatusOK {
		t.Fatalf("status = %d, want %d (%s)", response.Code, http.StatusOK, response.Body)
	}
	if len(d.producer.records) != 1 {
		t.Errorf("produced %d records, want 1", len(d.producer.records))
	}
}

func TestTheBrowserPathAnswersAPreflight(t *testing.T) {
	d := newDoor(t, doorOptions{})

	request := httptest.NewRequest(http.MethodOptions, browserTracesPath, nil)
	request.Header.Set("Origin", pageOrigin)
	request.Header.Set("Access-Control-Request-Method", http.MethodPost)
	request.Header.Set("Access-Control-Request-Headers", "content-type,"+auth.HeaderName)
	response := httptest.NewRecorder()
	d.handler.ServeHTTP(response, request)

	if response.Code != http.StatusNoContent {
		t.Fatalf("status = %d, want %d", response.Code, http.StatusNoContent)
	}
	if got := response.Header().Get("Access-Control-Allow-Origin"); got != pageOrigin {
		t.Errorf("Access-Control-Allow-Origin = %q, want %q", got, pageOrigin)
	}
	if got := response.Header().Get("Access-Control-Allow-Headers"); !strings.Contains(got, auth.HeaderName) {
		t.Errorf("Access-Control-Allow-Headers = %q, want it to name %q", got, auth.HeaderName)
	}
}

/* -------------------------------------------------- the browser door refuses */

// The refusal a customer onboarding a site actually hits, and the one that has
// to be readable: without the echoed origin the page sees a CORS failure and
// never the sentence naming what is wrong.
func TestAnUnlistedOriginIsRefusedInWordsThePageCanRead(t *testing.T) {
	d := newDoor(t, doorOptions{})

	response := browserPost(t, d, browserTracesPath, otherOrigin, siteKeyHeader)

	if response.Code != http.StatusForbidden {
		t.Fatalf("status = %d, want %d (%s)", response.Code, http.StatusForbidden, response.Body)
	}
	if got := response.Header().Get("Access-Control-Allow-Origin"); got != otherOrigin {
		t.Errorf("Access-Control-Allow-Origin = %q, want %q: a 403 without it is invisible", got, otherOrigin)
	}
	refusal := rpcStatus(t, response)
	if refusal.GetCode() != int32(code.Code_PERMISSION_DENIED) {
		t.Errorf("code = %d, want PERMISSION_DENIED", refusal.GetCode())
	}
	if !strings.Contains(refusal.GetMessage(), "not allowed from this origin") {
		t.Errorf("message = %q, want it to name the origin as the problem", refusal.GetMessage())
	}
	if len(d.producer.records) != 0 {
		t.Error("a refused export reached the broker")
	}
}

// A browser stamps Origin on every POST, so a public key without one did not
// come from a browser.
func TestAPublicKeyWithoutAnOriginIsRefused(t *testing.T) {
	d := newDoor(t, doorOptions{})

	response := browserPost(t, d, browserTracesPath, "", siteKeyHeader)

	if response.Code != http.StatusForbidden {
		t.Fatalf("status = %d, want %d (%s)", response.Code, http.StatusForbidden, response.Body)
	}
	if !strings.Contains(rpcStatus(t, response).GetMessage(), "without a browser Origin") {
		t.Errorf("message = %q, want it to name the missing Origin", rpcStatus(t, response).GetMessage())
	}
}

func TestAPublicKeyWithNoAllowedOriginsAcceptsNothing(t *testing.T) {
	// Empty means allow nothing, not allow everything: a key whose origins were
	// never configured must not be a key that works from anywhere.
	d := newDoor(t, doorOptions{siteOrigins: []string{}})

	response := browserPost(t, d, browserTracesPath, pageOrigin, siteKeyHeader)

	if response.Code != http.StatusForbidden {
		t.Errorf("status = %d, want %d (%s)", response.Code, http.StatusForbidden, response.Body)
	}
}

/* ------------------------------------------ neither key opens the other door */

// The wiring's whole claim. Each path resolves one table, so a key of the other
// kind is *unknown* there — 401, not a 403 that would imply it nearly worked.

func TestASecretKeyIsUnknownOnTheBrowserPath(t *testing.T) {
	d := newDoor(t, doorOptions{})

	response := browserPost(t, d, browserTracesPath, pageOrigin,
		map[string]string{auth.HeaderName: validKey})

	if response.Code != http.StatusUnauthorized {
		t.Fatalf("status = %d, want %d (%s)", response.Code, http.StatusUnauthorized, response.Body)
	}
	if len(d.producer.records) != 0 {
		t.Error("a secret key exported through the browser door")
	}
}

func TestAPublicKeyIsUnknownOnTheServerPath(t *testing.T) {
	d := newDoor(t, doorOptions{})

	response := postTo(t, d, otlp.TracesPath,
		encodedRequest(t, exportRequest(traceID(0x11))),
		map[string]string{auth.HeaderName: validSiteKey})

	if response.Code != http.StatusUnauthorized {
		t.Fatalf("status = %d, want %d (%s)", response.Code, http.StatusUnauthorized, response.Body)
	}
	if len(d.producer.records) != 0 {
		t.Error("a public key exported through the server door")
	}
}

// The asymmetry that makes a query-string credential safe at all: the door that
// reads one can only resolve keys that were never secret, and the door that
// resolves secret keys never reads a URL.
func TestASecretKeyInTheQueryStringOpensNeitherDoor(t *testing.T) {
	for name, path := range map[string]string{
		"browser path": browserTracesPath,
		"server path":  otlp.TracesPath,
	} {
		t.Run(name, func(t *testing.T) {
			d := newDoor(t, doorOptions{})

			response := browserPost(t, d,
				path+"?"+auth.SiteKeyQueryParam+"="+validKey, pageOrigin, nil)

			if response.Code != http.StatusUnauthorized {
				t.Errorf("status = %d, want %d (%s)",
					response.Code, http.StatusUnauthorized, response.Body)
			}
		})
	}
}

func TestASiteKeyInTheQueryStringDoesNotOpenTheServerDoor(t *testing.T) {
	d := newDoor(t, doorOptions{})

	response := browserPost(t, d,
		otlp.TracesPath+"?"+auth.SiteKeyQueryParam+"="+validSiteKey, pageOrigin, nil)

	if response.Code != http.StatusUnauthorized {
		t.Errorf("status = %d, want %d (%s)", response.Code, http.StatusUnauthorized, response.Body)
	}
}

/* --------------------------------------------------------------- accounting */

// One receiver serving two doors is only diagnosable if a line says which, and
// the path is the only field that can: both doors carry the same tenancy shape.
func TestABrowserExportIsLoggedUnderItsOwnPath(t *testing.T) {
	written := &bytes.Buffer{}
	traffic := otlp.NewTraffic()
	d := newDoor(t, doorOptions{
		log:     slog.New(slog.NewJSONHandler(written, &slog.HandlerOptions{Level: slog.LevelDebug})),
		traffic: traffic,
	})

	if response := browserPost(t, d, browserTracesPath, pageOrigin, siteKeyHeader); response.Code != http.StatusOK {
		t.Fatalf("status = %d, want %d (%s)", response.Code, http.StatusOK, response.Body)
	}

	var accepted map[string]any
	for _, line := range recordedLines(t, written) {
		if line["msg"] == "export accepted" {
			accepted = line
		}
	}
	if accepted == nil {
		t.Fatalf("no acceptance was logged (%s)", written)
	}
	if accepted["path"] != browserTracesPath {
		t.Errorf("path = %v, want %q", accepted["path"], browserTracesPath)
	}
	if accepted["origin"] != pageOrigin {
		t.Errorf("origin = %v, want %q", accepted["origin"], pageOrigin)
	}

	summary := summaryLine(t, runSummary(t, traffic))
	if summary["accepted_spans"] != float64(1) {
		t.Errorf("accepted_spans = %v, want 1", summary["accepted_spans"])
	}
}

/* ------------------------------------------------------- the unload beacon */

// navigator.sendBeacon is the only export that survives a page unload, and it
// can reach this door only as text/plain: it sets no headers, and it fixes the
// request's credentials mode to "include", so any content type outside the CORS
// safelist is preflighted and then failed by the browser because ADR 0087
// deliberately never answers Access-Control-Allow-Credentials. ADR 0089.

// beaconContentType is what a browser stamps on a text/plain beacon, charset
// and all, so the parse of the parameter is exercised rather than assumed.
const beaconContentType = "text/plain;charset=UTF-8"

// beacon sends exactly what navigator.sendBeacon produces: a POST with no
// header the page chose, the key in the query string, and a JSON body typed
// text/plain.
func beacon(t *testing.T, d door, path string, body []byte) *httptest.ResponseRecorder {
	t.Helper()
	request := httptest.NewRequest(http.MethodPost,
		path+"?"+auth.SiteKeyQueryParam+"="+validSiteKey, bytes.NewReader(body))
	request.Header.Set("Content-Type", beaconContentType)
	request.Header.Set("Origin", pageOrigin)

	recorder := httptest.NewRecorder()
	d.handler.ServeHTTP(recorder, request)
	return recorder
}

func jsonExportBody(t *testing.T) []byte {
	t.Helper()
	body, err := protojson.Marshal(exportRequest(traceID(0x55)))
	if err != nil {
		t.Fatalf("encoding JSON request: %v", err)
	}
	return body
}

func TestATextPlainBeaconIsAcceptedOnTheBrowserDoor(t *testing.T) {
	d := newDoor(t, doorOptions{})

	response := beacon(t, d, browserTracesPath, jsonExportBody(t))

	if response.Code != http.StatusOK {
		t.Fatalf("status = %d, want %d (%s)", response.Code, http.StatusOK, response.Body)
	}
	// text/plain is the CORS safelist's requirement, not a claim about the
	// encoding: the body was JSON and the answer says so.
	if got := response.Header().Get("Content-Type"); got != contentTypeJSON {
		t.Errorf("Content-Type = %q, want %q", got, contentTypeJSON)
	}
	if len(d.producer.records) != 1 {
		t.Fatalf("produced %d records, want 1", len(d.producer.records))
	}
	var envelope nexovuev1.Envelope
	if err := proto.Unmarshal(d.producer.records[0].Value, &envelope); err != nil {
		t.Fatalf("record is not an Envelope: %v", err)
	}
	if envelope.GetTenantId() != orgID || envelope.GetProjectId() != projectID {
		t.Errorf("envelope tenancy = %s/%s, want %s/%s",
			envelope.GetTenantId(), envelope.GetProjectId(), orgID, projectID)
	}
}

func TestATextPlainBeaconReachesEverySignalOfTheBrowserDoor(t *testing.T) {
	for _, path := range []string{browserTracesPath, browserLogsPath, browserMetricsPath} {
		t.Run(path, func(t *testing.T) {
			d := newDoor(t, doorOptions{})

			// An empty export is legal for every signal, so one body proves the
			// content type is read on all three without three payload shapes.
			if response := beacon(t, d, path, []byte(`{}`)); response.Code != http.StatusOK {
				t.Errorf("status = %d, want %d (%s)", response.Code, http.StatusOK, response.Body)
			}
		})
	}
}

// The affordance belongs to the browser door and to nothing else. A server
// exporter has no reason to send text/plain and no CORS problem to solve, so
// the server door's content types do not contain it and cannot be made to.
func TestATextPlainExportIsRefusedOnTheServerDoor(t *testing.T) {
	d := newDoor(t, doorOptions{})

	response := postTo(t, d, otlp.TracesPath, jsonExportBody(t),
		map[string]string{"Content-Type": beaconContentType})

	if response.Code != http.StatusUnsupportedMediaType {
		t.Fatalf("status = %d, want %d (%s)",
			response.Code, http.StatusUnsupportedMediaType, response.Body)
	}
	if len(d.producer.records) != 0 {
		t.Error("a text/plain export reached the broker through the server door")
	}
}

// Accepting the content type is not accepting anything: a text/plain body is
// still OTLP/JSON and is still parsed as such.
func TestATextPlainBeaconThatIsNotJSONIsRefused(t *testing.T) {
	for name, body := range map[string]string{
		"not JSON at all":  "hello, receiver",
		"JSON of no shape": `{"resourceSpans": "not a list"}`,
	} {
		t.Run(name, func(t *testing.T) {
			d := newDoor(t, doorOptions{})

			response := beacon(t, d, browserTracesPath, []byte(body))

			if response.Code != http.StatusBadRequest {
				t.Fatalf("status = %d, want %d (%s)",
					response.Code, http.StatusBadRequest, response.Body)
			}
			if len(d.producer.records) != 0 {
				t.Error("an unparseable beacon reached the broker")
			}
		})
	}
}
