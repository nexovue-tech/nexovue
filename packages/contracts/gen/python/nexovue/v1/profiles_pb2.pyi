from opentelemetry.proto.common.v1 import common_pb2 as _common_pb2
from google.protobuf.internal import containers as _containers
from google.protobuf import descriptor as _descriptor
from google.protobuf import message as _message
from collections.abc import Iterable as _Iterable, Mapping as _Mapping
from typing import ClassVar as _ClassVar, Optional as _Optional, Union as _Union

DESCRIPTOR: _descriptor.FileDescriptor

class ProfileSamples(_message.Message):
    __slots__ = ("batches",)
    BATCHES_FIELD_NUMBER: _ClassVar[int]
    batches: _containers.RepeatedCompositeFieldContainer[ProfileBatch]
    def __init__(self, batches: _Optional[_Iterable[_Union[ProfileBatch, _Mapping]]] = ...) -> None: ...

class ProfileBatch(_message.Message):
    __slots__ = ("resource_attributes", "sample_type", "sample_unit", "period", "period_type", "period_unit", "samples")
    RESOURCE_ATTRIBUTES_FIELD_NUMBER: _ClassVar[int]
    SAMPLE_TYPE_FIELD_NUMBER: _ClassVar[int]
    SAMPLE_UNIT_FIELD_NUMBER: _ClassVar[int]
    PERIOD_FIELD_NUMBER: _ClassVar[int]
    PERIOD_TYPE_FIELD_NUMBER: _ClassVar[int]
    PERIOD_UNIT_FIELD_NUMBER: _ClassVar[int]
    SAMPLES_FIELD_NUMBER: _ClassVar[int]
    resource_attributes: _containers.RepeatedCompositeFieldContainer[_common_pb2.KeyValue]
    sample_type: str
    sample_unit: str
    period: int
    period_type: str
    period_unit: str
    samples: _containers.RepeatedCompositeFieldContainer[Sample]
    def __init__(self, resource_attributes: _Optional[_Iterable[_Union[_common_pb2.KeyValue, _Mapping]]] = ..., sample_type: _Optional[str] = ..., sample_unit: _Optional[str] = ..., period: _Optional[int] = ..., period_type: _Optional[str] = ..., period_unit: _Optional[str] = ..., samples: _Optional[_Iterable[_Union[Sample, _Mapping]]] = ...) -> None: ...

class Sample(_message.Message):
    __slots__ = ("frames", "value", "time_unix_nano", "trace_id", "span_id", "attributes")
    FRAMES_FIELD_NUMBER: _ClassVar[int]
    VALUE_FIELD_NUMBER: _ClassVar[int]
    TIME_UNIX_NANO_FIELD_NUMBER: _ClassVar[int]
    TRACE_ID_FIELD_NUMBER: _ClassVar[int]
    SPAN_ID_FIELD_NUMBER: _ClassVar[int]
    ATTRIBUTES_FIELD_NUMBER: _ClassVar[int]
    frames: _containers.RepeatedCompositeFieldContainer[Frame]
    value: int
    time_unix_nano: int
    trace_id: bytes
    span_id: bytes
    attributes: _containers.RepeatedCompositeFieldContainer[_common_pb2.KeyValue]
    def __init__(self, frames: _Optional[_Iterable[_Union[Frame, _Mapping]]] = ..., value: _Optional[int] = ..., time_unix_nano: _Optional[int] = ..., trace_id: _Optional[bytes] = ..., span_id: _Optional[bytes] = ..., attributes: _Optional[_Iterable[_Union[_common_pb2.KeyValue, _Mapping]]] = ...) -> None: ...

class Frame(_message.Message):
    __slots__ = ("function", "file", "line", "binary", "address")
    FUNCTION_FIELD_NUMBER: _ClassVar[int]
    FILE_FIELD_NUMBER: _ClassVar[int]
    LINE_FIELD_NUMBER: _ClassVar[int]
    BINARY_FIELD_NUMBER: _ClassVar[int]
    ADDRESS_FIELD_NUMBER: _ClassVar[int]
    function: str
    file: str
    line: int
    binary: str
    address: int
    def __init__(self, function: _Optional[str] = ..., file: _Optional[str] = ..., line: _Optional[int] = ..., binary: _Optional[str] = ..., address: _Optional[int] = ...) -> None: ...
