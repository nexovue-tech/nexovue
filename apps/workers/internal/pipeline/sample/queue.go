package sample

// queue is a first-in-first-out list of trace ids. Traces close in the order
// they opened, so a queue is all the ordering the buffer needs — a heap would
// buy nothing and cost a comparison per span.
//
// Popped slots are compacted rather than shifted on every pop, so a steady
// stream costs amortised constant time per entry.
type queue[T any] struct {
	items []T
	head  int
}

// compactAfter is how many popped slots accumulate before the queue reclaims
// them, chosen so compaction is rare and the wasted slots stay bounded.
const compactAfter = 1024

func (q *queue[T]) push(item T) { q.items = append(q.items, item) }

func (q *queue[T]) peek() (T, bool) {
	if q.head >= len(q.items) {
		var zero T
		return zero, false
	}
	return q.items[q.head], true
}

func (q *queue[T]) pop() (T, bool) {
	item, ok := q.peek()
	if !ok {
		return item, false
	}
	var zero T
	q.items[q.head] = zero // release the reference; the slot outlives the pop
	q.head++
	if q.head >= compactAfter {
		q.items = append(q.items[:0], q.items[q.head:]...)
		q.head = 0
	}
	return item, true
}

func (q *queue[T]) reset() {
	q.items = q.items[:0]
	q.head = 0
}
