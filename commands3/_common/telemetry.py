"""Scheduler telemetry: port of SchedulerProto / CommandProto / MechanismProto.

Dependency-free proto3 encoder for ``protobuf_commands.proto`` (same schema as Java, package
``wpi.proto``), plus the serialized ``FileDescriptorProto`` so dashboards (AdvantageScope,
Glass...) can decode it, and a NetworkTables publishing helper.
"""

from __future__ import annotations

import struct
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .command import Command
    from .scheduler import SchedulerBase

PROTO_FILE = "protobuf_commands.proto"
PROTO_PACKAGE = "wpi.proto"
SCHEDULER_TYPE = f"{PROTO_PACKAGE}.ProtobufScheduler"

# --------------------------------------------------------------------- wire format -----


def _varint(n: int) -> bytes:
    if n < 0:
        n += 1 << 64
    out = bytearray()
    while True:
        b = n & 0x7F
        n >>= 7
        if n:
            out.append(b | 0x80)
        else:
            out.append(b)
            return bytes(out)


def _key(field: int, wire: int) -> bytes:
    return _varint((field << 3) | wire)


def _f_varint(field: int, value: int) -> bytes:
    return _key(field, 0) + _varint(value)


def _f_double(field: int, value: float) -> bytes:
    return _key(field, 1) + struct.pack("<d", value)


def _f_bytes(field: int, value: bytes) -> bytes:
    return _key(field, 2) + _varint(len(value)) + value


def _f_str(field: int, value: str) -> bytes:
    return _f_bytes(field, value.encode())


# ------------------------------------------------------------------------ messages -----


def pack_mechanism(mechanism) -> bytes:
    name = mechanism.get_name()
    return _f_str(1, name) if name else b""


def pack_command(scheduler: "SchedulerBase", command: "Command") -> bytes:
    out = bytearray()
    rid = scheduler.run_id(command)
    if rid:
        out += _f_varint(1, rid)
    parent = scheduler.get_parent_of(command)
    if parent is not None:
        out += _f_varint(2, scheduler.run_id(parent))  # optional: always present when set
    if command.name():
        out += _f_str(3, command.name())
    if command.priority():
        out += _f_varint(4, command.priority())
    for m in command.requirements():
        out += _f_bytes(5, pack_mechanism(m))
    if scheduler.is_running(command):
        out += _f_double(6, scheduler.last_command_runtime_ms(command))
        out += _f_double(7, scheduler.total_runtime_ms(command))
    reqs = command.requirements()
    if len(reqs) == 1:
        (req,) = tuple(reqs)
        if scheduler.get_default_command_for(req) is command:
            out += _f_varint(8, 1)
    return bytes(out)


def pack_scheduler(scheduler: "SchedulerBase") -> bytes:
    out = bytearray()
    for c in scheduler.get_queued_commands():
        out += _f_bytes(1, pack_command(scheduler, c))
    for c in scheduler.get_running_commands():
        out += _f_bytes(2, pack_command(scheduler, c))
    if scheduler.last_runtime_ms() != 0.0:
        out += _f_double(3, scheduler.last_runtime_ms())
    return bytes(out)


# ---------------------------------------------------------------------- descriptor -----
# google/protobuf/descriptor.proto field numbers / enums
_LABEL_OPTIONAL, _LABEL_REPEATED = 1, 3
_T_DOUBLE, _T_INT32, _T_BOOL, _T_STRING, _T_MESSAGE, _T_UINT32 = 1, 5, 8, 9, 11, 13


def _field(name: str, number: int, type_: int, *, repeated: bool = False,
           type_name: str | None = None, oneof_index: int | None = None,
           proto3_optional: bool = False, json_name: str | None = None) -> bytes:
    out = _f_str(1, name) + _f_varint(3, number)
    out += _f_varint(4, _LABEL_REPEATED if repeated else _LABEL_OPTIONAL)
    out += _f_varint(5, type_)
    if type_name:
        out += _f_str(6, type_name)
    if oneof_index is not None:
        out += _f_varint(9, oneof_index)
    out += _f_str(10, json_name or _camel(name))
    if proto3_optional:
        out += _f_varint(17, 1)
    return out


def _camel(name: str) -> str:
    head, *rest = name.split("_")
    return head + "".join(p.title() for p in rest)


def _message(name: str, fields: list[bytes], oneofs: "tuple[str, ...] | list[str]" = ()) -> bytes:
    out = _f_str(1, name)
    for f in fields:
        out += _f_bytes(2, f)
    for o in oneofs:
        out += _f_bytes(8, _f_str(1, o))
    return out


def file_descriptor() -> bytes:
    """Serialized FileDescriptorProto for protobuf_commands.proto."""
    mech = _message("ProtobufMechanism", [_field("name", 1, _T_STRING)])
    optional = ["parent_id", "last_time_ms", "total_time_ms", "is_default_command"]
    cmd = _message(
        "ProtobufCommand",
        [
            _field("id", 1, _T_UINT32),
            _field("parent_id", 2, _T_UINT32, oneof_index=0, proto3_optional=True),
            _field("name", 3, _T_STRING),
            _field("priority", 4, _T_INT32),
            _field("requirements", 5, _T_MESSAGE, repeated=True,
                   type_name=f".{PROTO_PACKAGE}.ProtobufMechanism"),
            _field("last_time_ms", 6, _T_DOUBLE, oneof_index=1, proto3_optional=True),
            _field("total_time_ms", 7, _T_DOUBLE, oneof_index=2, proto3_optional=True),
            _field("is_default_command", 8, _T_BOOL, oneof_index=3, proto3_optional=True),
        ],
        [f"_{n}" for n in optional],
    )
    sched = _message(
        "ProtobufScheduler",
        [
            _field("queued_commands", 1, _T_MESSAGE, repeated=True,
                   type_name=f".{PROTO_PACKAGE}.ProtobufCommand"),
            _field("running_commands", 2, _T_MESSAGE, repeated=True,
                   type_name=f".{PROTO_PACKAGE}.ProtobufCommand"),
            _field("last_time_ms", 3, _T_DOUBLE),
        ],
    )
    out = _f_str(1, PROTO_FILE) + _f_str(2, PROTO_PACKAGE)
    for m in (mech, cmd, sched):
        out += _f_bytes(4, m)
    out += _f_bytes(8, _f_str(1, "org.wpilib.command3.proto"))  # FileOptions.java_package
    out += _f_str(12, "proto3")
    return out


# ----------------------------------------------------------------------- snapshots -----


def command_snapshot(scheduler: "SchedulerBase", command: "Command") -> dict:
    parent = scheduler.get_parent_of(command)
    d = {
        "id": scheduler.run_id(command),
        "name": command.name(),
        "priority": command.priority(),
        "requirements": [m.get_name() for m in command.requirements()],
    }
    if parent is not None:
        d["parent_id"] = scheduler.run_id(parent)
    if scheduler.is_running(command):
        d["last_time_ms"] = scheduler.last_command_runtime_ms(command)
        d["total_time_ms"] = scheduler.total_runtime_ms(command)
    reqs = command.requirements()
    if len(reqs) == 1 and scheduler.get_default_command_for(next(iter(reqs))) is command:
        d["is_default_command"] = True
    return d


def scheduler_snapshot(scheduler: "SchedulerBase") -> dict:
    return {
        "queued_commands": [command_snapshot(scheduler, c) for c in scheduler.get_queued_commands()],
        "running_commands": [command_snapshot(scheduler, c) for c in scheduler.get_running_commands()],
        "last_time_ms": scheduler.last_runtime_ms(),
    }


class SchedulerPublisher:
    """Publishes a scheduler's protobuf telemetry to NetworkTables every ``update()``."""

    def __init__(self, scheduler: "SchedulerBase", topic: str = "/Commands/Scheduler",
                 instance=None):
        import ntcore

        inst = instance if instance is not None else ntcore.NetworkTableInstance.get_default()
        self._scheduler = scheduler
        type_string = f"proto:{SCHEDULER_TYPE}"
        if not inst.has_schema(f"proto:{PROTO_FILE}"):
            inst.add_schema(f"proto:{PROTO_FILE}", "proto:FileDescriptorProto", file_descriptor())
        self._pub = inst.get_raw_topic(topic).publish(type_string)

    def update(self) -> None:
        self._pub.set(pack_scheduler(self._scheduler))
