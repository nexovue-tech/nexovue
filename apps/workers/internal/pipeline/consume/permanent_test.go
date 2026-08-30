package consume

import (
	"context"
	"errors"
	"io"
	"log/slog"
	"testing"
	"time"
)

func TestARetryableFailureIsRetried(t *testing.T) {
	loop := &Loop[struct{}]{log: quietLogger(), now: time.Now}
	attempts := 0

	err := loop.Retry(context.Background(), "insert", func(context.Context) error {
		attempts++
		if attempts < 3 {
			return errors.New("connection refused")
		}
		return nil
	})

	if err != nil || attempts != 3 {
		t.Fatalf("err = %v after %d attempts, want success on the third", err, attempts)
	}
}

// The budget exists for a store that is briefly away. A store that refused the
// data will refuse it again, and spending two minutes finding that out is what
// ended the pipeline and took every other signal down with it.
func TestARefusedInsertIsNotRetried(t *testing.T) {
	loop := &Loop[struct{}]{log: quietLogger(), now: time.Now}
	attempts := 0
	refusal := AsPermanent(errors.New("INCORRECT_DATA (117): Duplicate path"))

	err := loop.Retry(context.Background(), "insert", func(context.Context) error {
		attempts++
		return refusal
	})

	if attempts != 1 {
		t.Errorf("attempts = %d, want the answer taken at face value", attempts)
	}
	if !IsPermanent(err) {
		t.Errorf("err = %v, want the refusal reported as permanent", err)
	}
}

func TestAPermanentErrorSurvivesWrapping(t *testing.T) {
	wrapped := AsPermanent(errors.New("refused"))

	if !IsPermanent(errors.Join(errors.New("insert 28 logs"), wrapped)) {
		t.Error("a wrapped refusal must still read as permanent")
	}
	if IsPermanent(errors.New("connection refused")) {
		t.Error("an ordinary error must not read as permanent")
	}
}

// The retry path logs each attempt; these tests are about the count, not the log.
func quietLogger() *slog.Logger {
	return slog.New(slog.NewTextHandler(io.Discard, nil))
}
