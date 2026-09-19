package profiles

import (
	"errors"
	"fmt"

	"google.golang.org/protobuf/encoding/protowire"
	"google.golang.org/protobuf/proto"

	nexovuev1 "github.com/nexovue-tech/nexovue/packages/contracts/gen/go/nexovue/v1"
)

// ErrSampleTooLarge is one sample whose stack alone is past the record limit: it
// cannot be split, and no retry makes it smaller, so it is refused.
var ErrSampleTooLarge = errors.New("one profile sample is larger than a record can hold")

// The field numbers Split counts overhead for, from profiles.proto.
const (
	batchesField protowire.Number = 1
	samplesField protowire.Number = 7
)

// Split divides converted samples into parts that each serialize to at most maxBytes,
// so every part fits one Kafka record.
//
// Converting writes each sample's whole stack out, so the result can be many times
// the request that carried it — past the record limit, which a broker refuses and
// the sender's Collector would retry for ever. Batches are packed whole where they
// fit; a batch that does not is cut between its samples, each piece keeping the
// batch's resource and type, so every piece reads alone.
func Split(converted *nexovuev1.ProfileSamples, maxBytes int) ([]*nexovuev1.ProfileSamples, error) {
	if proto.Size(converted) <= maxBytes {
		return []*nexovuev1.ProfileSamples{converted}, nil
	}

	var parts []*nexovuev1.ProfileSamples
	current, size := &nexovuev1.ProfileSamples{}, 0
	for _, batch := range converted.GetBatches() {
		pieces, err := splitBatch(batch, maxBytes)
		if err != nil {
			return nil, err
		}
		for _, piece := range pieces {
			pieceSize := elementSize(batchesField, proto.Size(piece))
			if size+pieceSize > maxBytes && len(current.GetBatches()) > 0 {
				parts = append(parts, current)
				current, size = &nexovuev1.ProfileSamples{}, 0
			}
			current.Batches = append(current.Batches, piece)
			size += pieceSize
		}
	}
	if len(current.GetBatches()) > 0 {
		parts = append(parts, current)
	}
	return parts, nil
}

// splitBatch cuts one batch between its samples into pieces that each fit a part
// of their own.
func splitBatch(batch *nexovuev1.ProfileBatch, maxBytes int) ([]*nexovuev1.ProfileBatch, error) {
	if elementSize(batchesField, proto.Size(batch)) <= maxBytes {
		return []*nexovuev1.ProfileBatch{batch}, nil
	}
	header := proto.CloneOf(batch)
	header.Samples = nil
	headerSize := proto.Size(header)

	// Sizes are kept raw and the piece's own length prefix recounted on every
	// sample: the prefix is a varint, and it grows as the piece does.
	var pieces []*nexovuev1.ProfileBatch
	piece, raw := proto.CloneOf(header), headerSize
	for _, sample := range batch.GetSamples() {
		sampleSize := elementSize(samplesField, proto.Size(sample))
		if elementSize(batchesField, headerSize+sampleSize) > maxBytes {
			return nil, fmt.Errorf("%w: %d bytes against a limit of %d", ErrSampleTooLarge, sampleSize, maxBytes)
		}
		if elementSize(batchesField, raw+sampleSize) > maxBytes {
			pieces = append(pieces, piece)
			piece, raw = proto.CloneOf(header), headerSize
		}
		piece.Samples = append(piece.Samples, sample)
		raw += sampleSize
	}
	if len(piece.GetSamples()) > 0 {
		pieces = append(pieces, piece)
	}
	return pieces, nil
}

// elementSize is what one embedded message of the given size costs inside its
// parent: the tag, the length prefix, and the message.
func elementSize(field protowire.Number, size int) int {
	return protowire.SizeTag(field) + protowire.SizeBytes(size)
}
