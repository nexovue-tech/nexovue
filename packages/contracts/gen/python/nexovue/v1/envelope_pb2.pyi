from google.protobuf.internal import enum_type_wrapper as _enum_type_wrapper
from google.protobuf import descriptor as _descriptor
from google.protobuf import message as _message
from typing import ClassVar as _ClassVar, Optional as _Optional, Union as _Union

DESCRIPTOR: _descriptor.FileDescriptor

class Door(int, metaclass=_enum_type_wrapper.EnumTypeWrapper):
    __slots__ = ()
    DOOR_UNSPECIFIED: _ClassVar[Door]
    DOOR_OTLP: _ClassVar[Door]
    DOOR_SYNTHETIC: _ClassVar[Door]
    DOOR_POLLER: _ClassVar[Door]
    DOOR_RUM: _ClassVar[Door]
    DOOR_WEBHOOK: _ClassVar[Door]

class Signal(int, metaclass=_enum_type_wrapper.EnumTypeWrapper):
    __slots__ = ()
    SIGNAL_UNSPECIFIED: _ClassVar[Signal]
    SIGNAL_SPANS: _ClassVar[Signal]
    SIGNAL_LOGS: _ClassVar[Signal]
    SIGNAL_METRICS: _ClassVar[Signal]
DOOR_UNSPECIFIED: Door
DOOR_OTLP: Door
DOOR_SYNTHETIC: Door
DOOR_POLLER: Door
DOOR_RUM: Door
DOOR_WEBHOOK: Door
SIGNAL_UNSPECIFIED: Signal
SIGNAL_SPANS: Signal
SIGNAL_LOGS: Signal
SIGNAL_METRICS: Signal

class Envelope(_message.Message):
    __slots__ = ("tenant_id", "project_id", "door", "signal", "received_at_unix_nano", "ingest_id", "semconv_version", "payload")
    TENANT_ID_FIELD_NUMBER: _ClassVar[int]
    PROJECT_ID_FIELD_NUMBER: _ClassVar[int]
    DOOR_FIELD_NUMBER: _ClassVar[int]
    SIGNAL_FIELD_NUMBER: _ClassVar[int]
    RECEIVED_AT_UNIX_NANO_FIELD_NUMBER: _ClassVar[int]
    INGEST_ID_FIELD_NUMBER: _ClassVar[int]
    SEMCONV_VERSION_FIELD_NUMBER: _ClassVar[int]
    PAYLOAD_FIELD_NUMBER: _ClassVar[int]
    tenant_id: str
    project_id: str
    door: Door
    signal: Signal
    received_at_unix_nano: int
    ingest_id: str
    semconv_version: str
    payload: bytes
    def __init__(self, tenant_id: _Optional[str] = ..., project_id: _Optional[str] = ..., door: _Optional[_Union[Door, str]] = ..., signal: _Optional[_Union[Signal, str]] = ..., received_at_unix_nano: _Optional[int] = ..., ingest_id: _Optional[str] = ..., semconv_version: _Optional[str] = ..., payload: _Optional[bytes] = ...) -> None: ...
