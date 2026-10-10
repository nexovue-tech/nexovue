package consume

import "context"

// Narrow finds what a refused insert actually refused.
//
// An insert reports a refusal for its whole block, not for the row that caused
// it, so items are halved and each half written again, down to single items:
// every half the store accepts is written, and every single item it refuses is
// handed to park. One refused item among n costs about 2·log₂(n) inserts —
// paid only on a refusal — where parking the whole chunk cost every record in
// it, often other tenants' (docs/changes/refusals-narrowed-to-the-record).
//
// write must return a Permanent error for a refusal. Any other failure ends the
// narrowing and is returned, so the batch is re-consumed as it always was;
// halves already written are written again — the duplicate the at-least-once
// contract accepts, never a hole.
func Narrow[T any](
	ctx context.Context,
	items []T,
	refusal error,
	write func(context.Context, []T) error,
	park func(context.Context, T, error) error,
) error {
	if len(items) == 0 {
		return nil
	}
	if len(items) == 1 {
		return park(ctx, items[0], refusal)
	}
	middle := len(items) / 2
	for _, half := range [][]T{items[:middle], items[middle:]} {
		err := write(ctx, half)
		switch {
		case err == nil:
		case IsPermanent(err):
			if narrowErr := Narrow(ctx, half, err, write, park); narrowErr != nil {
				return narrowErr
			}
		default:
			return err
		}
	}
	return nil
}
