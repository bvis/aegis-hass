"""HTS binary protocol message builder and parser, including TLV encoding.

The HTS protocol uses a 14-byte header followed by a TLV-encoded payload.
TLV parameters are delimited by 0x05, with 0x05 and 0x06 bytes escaped inside
parameter values.

Header layout (all big-endian):
  sender    4 bytes
  receiver  4 bytes
  seq_num   3 bytes
  link      1 byte
  flags     1 byte
  msg_type  1 byte
  (total: 14 bytes)
"""

from __future__ import annotations

import logging
import struct
from dataclasses import dataclass, field
from enum import IntEnum

_LOGGER = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# TLV helpers
# ---------------------------------------------------------------------------

_DELIM = 0x05
_ESC = 0x06
_ESC_DELIM = 0x35  # escaped representation of 0x05 after ESC byte
_ESC_ESC = 0x36  # escaped representation of 0x06 after ESC byte


def tlv_escape_param(param: bytes) -> bytes:
    """Escape a single TLV parameter value.

    Replaces:
      0x05 -> 0x06 0x35
      0x06 -> 0x06 0x36
    """
    out = bytearray()
    for b in param:
        if b == _ESC:
            out.append(_ESC)
            out.append(_ESC_ESC)
        elif b == _DELIM:
            out.append(_ESC)
            out.append(_ESC_DELIM)
        else:
            out.append(b)
    return bytes(out)


def tlv_unescape_param(data: bytes) -> bytes:
    """Unescape a single TLV parameter value.

    Reverses the two known escape sequences:
      0x06 0x35 -> 0x05
      0x06 0x36 -> 0x06

    Lenient on unknown sequences (#108): a `0x06 <other>` pair is
    preserved as two literal bytes rather than raising. The strict
    behaviour used to bubble a `ValueError` up through `tlv_decode`,
    out of `_handle_update`, into `_run_hts_lifecycle`'s broad except,
    which terminated the listen loop and left hub-network sensors
    permanently `unavailable` for affected installs (@uddinr captured
    `0x06 0x6A` from real firmware traffic). If the unknown pair turns
    out to be a third escape code we don't yet know about, the worst
    case is a slightly wrong byte in one field rather than the whole
    HTS surface being dead. An orphan `0x06` at the end of the segment
    is treated the same way (preserved as a literal 0x06).
    """
    out = bytearray()
    i = 0
    while i < len(data):
        b = data[i]
        if b == _ESC:
            if i + 1 >= len(data):
                _LOGGER.debug("Orphan 0x06 at end of TLV param; preserving literal")
                out.append(_ESC)
                break
            nxt = data[i + 1]
            if nxt == _ESC_DELIM:
                out.append(_DELIM)
                i += 2
                continue
            if nxt == _ESC_ESC:
                out.append(_ESC)
                i += 2
                continue
            _LOGGER.debug("Unknown escape 0x06 0x%02X; preserving literal", nxt)
            out.append(_ESC)
            out.append(nxt)
            i += 2
            continue
        out.append(b)
        i += 1
    return bytes(out)


def tlv_encode(params: list[bytes]) -> bytes:
    """Encode a list of byte parameters into TLV wire format.

    Format: \\x05<p1>\\x05<p2>...\\x05<pN>\\x05
    Each parameter preceded by 0x05, plus trailing 0x05.
    Matches Java AYc.wrapParameters exactly.
    """
    if not params:
        return b""
    out = bytearray()
    for p in params:
        out.append(_DELIM)
        out.extend(tlv_escape_param(p))
    out.append(_DELIM)  # trailing delimiter (required)
    return bytes(out)


def tlv_decode(data: bytes) -> list[bytes]:
    """Decode TLV wire format into a list of byte parameters.

    Splits on unescaped 0x05 delimiters and unescapes each segment.
    A trailing empty segment (from a trailing delimiter) is dropped.
    """
    if not data:
        return []

    segments: list[bytes] = []
    current = bytearray()
    i = 0
    while i < len(data):
        b = data[i]
        if b == _ESC:
            # consume escape + next byte as a unit
            current.append(b)
            i += 1
            if i < len(data):
                current.append(data[i])
        elif b == _DELIM:
            segments.append(bytes(current))
            current = bytearray()
        else:
            current.append(b)
        i += 1

    # Any remaining non-empty bytes after the last delimiter
    if current:
        segments.append(bytes(current))

    # Unescape each segment, drop empty params from leading/trailing delimiters
    return [tlv_unescape_param(s) for s in segments if s]


# ---------------------------------------------------------------------------
# Message types
# ---------------------------------------------------------------------------


class MsgType(IntEnum):
    READ_PARAMETER = 0x06
    WRITE_PARAMETER = 0x07
    HUB_REGISTRATION = 0x09
    ADM_CONTROL = 0x10
    USER_REGISTRATION = 0x11
    AUTHENTICATION = 0x15
    ACK = 0x16
    CONNECTION = 0x18
    UPDATES = 0x19
    PING = 0x0D
    HUB_SERVICE = 0x1D


# ---------------------------------------------------------------------------
# Auth / protocol constants
# ---------------------------------------------------------------------------

AUTH_KEY_CONNECT_CLIENT_NEW = 0x3F
AUTH_KEY_AUTHENTICATION_REQUEST = 0x00
AUTH_KEY_AUTHENTICATION_RESPONSE = 0x01
AUTH_KEY_CONNECTED = 0x0F
ACK_KEY_RECEIVED = 0x00

FLAG_NO_ACK = 0x20

# ---------------------------------------------------------------------------
# HtsMessage dataclass
# ---------------------------------------------------------------------------

_HEADER_FMT = ">IIBBBbB"  # placeholder - built manually for 3-byte seq_num
_HEADER_SIZE = 14


@dataclass
class HtsMessage:
    """A single HTS protocol message.

    Attributes:
        sender:    4-byte sender address (big-endian uint32).
        receiver:  4-byte receiver address (big-endian uint32).
        seq_num:   3-byte sequence number (big-endian, 0..0xFFFFFF).
        link:      1-byte link field.
        flags:     1-byte flags field.
        msg_type:  Message type byte (MsgType or raw int).
        payload:   Raw payload bytes (already TLV-encoded or empty).
    """

    sender: int
    receiver: int
    seq_num: int
    link: int
    flags: int
    msg_type: MsgType | int
    payload: bytes = field(default=b"")

    # ------------------------------------------------------------------
    # Derived properties
    # ------------------------------------------------------------------

    @property
    def is_no_ack(self) -> bool:
        """True when the NO_ACK flag is set."""
        return bool(self.flags & FLAG_NO_ACK)

    @property
    def is_duplicate(self) -> bool:
        """True when the duplicate/retry flag (0x40) is set."""
        return bool(self.flags & 0x40)

    @property
    def send_try(self) -> int:
        """Low 5 bits of flags represent the send attempt counter."""
        return self.flags & 0x1F


# ---------------------------------------------------------------------------
# Build / parse
# ---------------------------------------------------------------------------


def build_message(msg: HtsMessage) -> bytes:
    """Serialize an HtsMessage to bytes.

    Header (14 bytes, big-endian):
      [0:4]  sender   uint32
      [4:8]  receiver uint32
      [8:11] seq_num  uint24
      [11]   link     uint8
      [12]   flags    uint8
      [13]   msg_type uint8

    Followed by the raw payload bytes.
    """
    header = struct.pack(
        ">II",
        msg.sender & 0xFFFFFFFF,
        msg.receiver & 0xFFFFFFFF,
    )
    # 3-byte big-endian sequence number
    seq = msg.seq_num & 0xFFFFFF
    header += seq.to_bytes(3, "big")
    header += struct.pack(
        ">BBB",
        msg.link & 0xFF,
        msg.flags & 0xFF,
        int(msg.msg_type) & 0xFF,
    )
    return header + msg.payload


def parse_message(data: bytes) -> HtsMessage:
    """Parse bytes into an HtsMessage.

    Raises:
        ValueError: If data is shorter than the 14-byte header.
    """
    if len(data) < _HEADER_SIZE:
        raise ValueError(
            f"Message too short: expected at least {_HEADER_SIZE} bytes, got {len(data)}"
        )

    sender, receiver = struct.unpack_from(">II", data, 0)
    seq_num = int.from_bytes(data[8:11], "big")
    link, flags, raw_type = struct.unpack_from(">BBB", data, 11)

    try:
        msg_type: MsgType | int = MsgType(raw_type)
    except ValueError:
        msg_type = raw_type

    payload = data[_HEADER_SIZE:]

    return HtsMessage(
        sender=sender,
        receiver=receiver,
        seq_num=seq_num,
        link=link,
        flags=flags,
        msg_type=msg_type,
        payload=payload,
    )
