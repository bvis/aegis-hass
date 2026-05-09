"""Tests for HTS message builder/parser and TLV encoding."""

import pytest

from custom_components.aegis_ajax.api.hts.messages import (
    ACK_KEY_RECEIVED,
    AUTH_KEY_AUTHENTICATION_REQUEST,
    AUTH_KEY_AUTHENTICATION_RESPONSE,
    AUTH_KEY_CONNECT_CLIENT_NEW,
    AUTH_KEY_CONNECTED,
    FLAG_NO_ACK,
    HtsMessage,
    MsgType,
    build_message,
    parse_message,
    tlv_decode,
    tlv_encode,
    tlv_escape_param,
    tlv_unescape_param,
)

# ---------------------------------------------------------------------------
# TLV escape / unescape
# ---------------------------------------------------------------------------


class TestTlvEscape:
    def test_no_special_bytes(self) -> None:
        assert tlv_escape_param(b"\x01\x02\x03") == b"\x01\x02\x03"

    def test_escape_delimiter(self) -> None:
        # 0x05 -> 0x06 0x35
        assert tlv_escape_param(b"\x05") == b"\x06\x35"

    def test_escape_esc_byte(self) -> None:
        # 0x06 -> 0x06 0x36
        assert tlv_escape_param(b"\x06") == b"\x06\x36"

    def test_escape_mixed(self) -> None:
        data = bytes([0x01, 0x05, 0x06, 0x07])
        expected = bytes([0x01, 0x06, 0x35, 0x06, 0x36, 0x07])
        assert tlv_escape_param(data) == expected

    def test_escape_empty(self) -> None:
        assert tlv_escape_param(b"") == b""

    def test_roundtrip(self) -> None:
        for b in range(256):
            original = bytes([b])
            assert tlv_unescape_param(tlv_escape_param(original)) == original


class TestTlvUnescape:
    def test_no_escape(self) -> None:
        assert tlv_unescape_param(b"\x01\x02\x03") == b"\x01\x02\x03"

    def test_unescape_delimiter(self) -> None:
        assert tlv_unescape_param(b"\x06\x35") == b"\x05"

    def test_unescape_esc(self) -> None:
        assert tlv_unescape_param(b"\x06\x36") == b"\x06"

    def test_truncated_escape_preserved(self) -> None:
        # Lenient (#108): an orphan 0x06 at the end of a segment used to
        # raise and kill the HTS listen loop on the very next update.
        # Treat the byte as literal data so the message decodes and the
        # network sensors keep working.
        assert tlv_unescape_param(b"\x06") == b"\x06"

    def test_unknown_escape_preserved(self) -> None:
        # Lenient (#108): @uddinr's hub firmware emits 0x06 0x6A inside
        # a TLV segment which our escape table doesn't recognise. Strict
        # ValueError used to propagate and shut down the listen loop —
        # network sensors stayed unavailable forever. Preserve both
        # bytes literally so the rest of the message decodes; if it
        # turns out 0x6A is actually a third escape code we don't know
        # about, the worst case is a slightly wrong byte in one field
        # rather than the entire HTS surface being permanently dead.
        assert tlv_unescape_param(b"\x06\x6a") == b"\x06\x6a"
        assert tlv_unescape_param(b"\x06\x01") == b"\x06\x01"

    def test_unknown_escape_preserved_in_context(self) -> None:
        # Surrounding bytes pass through unchanged — only the unknown
        # escape pair is preserved as-is.
        assert tlv_unescape_param(b"AB\x06\x6aCD") == b"AB\x06\x6aCD"

    def test_known_escape_still_works_after_lenient_fallback(self) -> None:
        # Regression: don't accidentally make 0x06 0x35 / 0x06 0x36
        # also literal — those still mean what they always meant.
        assert tlv_unescape_param(b"\x06\x35\x06\x6a\x06\x36") == b"\x05\x06\x6a\x06"

    def test_empty(self) -> None:
        assert tlv_unescape_param(b"") == b""


# ---------------------------------------------------------------------------
# TLV encode / decode
# ---------------------------------------------------------------------------


class TestTlvEncode:
    def test_single_param(self) -> None:
        # \x05<param>\x05
        result = tlv_encode([b"\x01\x02"])
        assert result == b"\x05\x01\x02\x05"

    def test_two_params(self) -> None:
        result = tlv_encode([b"\x01", b"\x02"])
        assert result == b"\x05\x01\x05\x02\x05"

    def test_three_params(self) -> None:
        result = tlv_encode([b"A", b"B", b"C"])
        assert result == b"\x05A\x05B\x05C\x05"

    def test_empty_params_list(self) -> None:
        assert tlv_encode([]) == b""

    def test_escapes_delimiter_in_param(self) -> None:
        result = tlv_encode([b"\x05"])
        assert result == b"\x05\x06\x35\x05"

    def test_escapes_esc_in_param(self) -> None:
        result = tlv_encode([b"\x06"])
        assert result == b"\x05\x06\x36\x05"

    def test_empty_param(self) -> None:
        result = tlv_encode([b""])
        assert result == b"\x05\x05"

    def test_two_empty_params(self) -> None:
        result = tlv_encode([b"", b""])
        assert result == b"\x05\x05\x05"


class TestTlvDecode:
    def test_single_param(self) -> None:
        # Leading delimiter: \x05<param>
        assert tlv_decode(b"\x05\x01\x02") == [b"\x01\x02"]

    def test_two_params(self) -> None:
        assert tlv_decode(b"\x05\x01\x05\x02") == [b"\x01", b"\x02"]

    def test_trailing_delimiter(self) -> None:
        # Trailing delimiter doesn't add empty param
        assert tlv_decode(b"\x01\x02\x05") == [b"\x01\x02"]

    def test_three_params(self) -> None:
        assert tlv_decode(b"A\x05B\x05C\x05") == [b"A", b"B", b"C"]

    def test_empty_input(self) -> None:
        assert tlv_decode(b"") == []

    def test_unescape_delimiter_in_param(self) -> None:
        assert tlv_decode(b"\x06\x35\x05") == [b"\x05"]

    def test_unescape_esc_in_param(self) -> None:
        assert tlv_decode(b"\x06\x36\x05") == [b"\x06"]

    def test_empty_param(self) -> None:
        # Single delimiter with no content → empty list (empty params filtered)
        assert tlv_decode(b"\x05") == []

    def test_two_empty_params(self) -> None:
        # Two delimiters with no content → empty list
        assert tlv_decode(b"\x05\x05") == []

    def test_no_trailing_delimiter(self) -> None:
        # data without trailing delimiter: last segment still captured
        assert tlv_decode(b"\x01\x05\x02") == [b"\x01", b"\x02"]


class TestTlvRoundtrip:
    def test_roundtrip_simple(self) -> None:
        params = [b"hello", b"world"]
        assert tlv_decode(tlv_encode(params)) == params

    def test_roundtrip_with_special_bytes(self) -> None:
        params = [b"\x05\x06", b"\x00\xff"]
        assert tlv_decode(tlv_encode(params)) == params

    def test_roundtrip_binary_all_bytes(self) -> None:
        params = [bytes(range(256))]
        assert tlv_decode(tlv_encode(params)) == params

    def test_roundtrip_many_params(self) -> None:
        params = [bytes([i]) for i in range(10)]
        assert tlv_decode(tlv_encode(params)) == params


# ---------------------------------------------------------------------------
# MsgType enum
# ---------------------------------------------------------------------------


class TestMsgType:
    def test_known_values(self) -> None:
        assert MsgType.PING == 0x0D
        assert MsgType.USER_REGISTRATION == 0x11
        assert MsgType.AUTHENTICATION == 0x15
        assert MsgType.ACK == 0x16
        assert MsgType.UPDATES == 0x19
        assert MsgType.READ_PARAMETER == 0x06
        assert MsgType.WRITE_PARAMETER == 0x07
        assert MsgType.HUB_REGISTRATION == 0x09
        assert MsgType.ADM_CONTROL == 0x10
        assert MsgType.CONNECTION == 0x18
        assert MsgType.HUB_SERVICE == 0x1D


# ---------------------------------------------------------------------------
# Auth constants
# ---------------------------------------------------------------------------


class TestAuthConstants:
    def test_values(self) -> None:
        assert AUTH_KEY_CONNECT_CLIENT_NEW == 0x3F
        assert AUTH_KEY_AUTHENTICATION_REQUEST == 0x00
        assert AUTH_KEY_AUTHENTICATION_RESPONSE == 0x01
        assert AUTH_KEY_CONNECTED == 0x0F
        assert ACK_KEY_RECEIVED == 0x00
        assert FLAG_NO_ACK == 0x20


# ---------------------------------------------------------------------------
# HtsMessage properties
# ---------------------------------------------------------------------------


class TestHtsMessageProperties:
    def _make(self, flags: int) -> HtsMessage:
        return HtsMessage(
            sender=1,
            receiver=2,
            seq_num=0,
            link=0,
            flags=flags,
            msg_type=MsgType.PING,
        )

    def test_is_no_ack_true(self) -> None:
        assert self._make(FLAG_NO_ACK).is_no_ack is True

    def test_is_no_ack_false(self) -> None:
        assert self._make(0x00).is_no_ack is False

    def test_is_no_ack_with_other_flags(self) -> None:
        assert self._make(FLAG_NO_ACK | 0x01).is_no_ack is True

    def test_is_duplicate_true(self) -> None:
        assert self._make(0x40).is_duplicate is True

    def test_is_duplicate_false(self) -> None:
        assert self._make(0x00).is_duplicate is False

    def test_send_try_low_bits(self) -> None:
        assert self._make(0x1F).send_try == 0x1F

    def test_send_try_ignores_upper_bits(self) -> None:
        assert self._make(0xFF).send_try == 0x1F

    def test_send_try_zero(self) -> None:
        assert self._make(0x00).send_try == 0


# ---------------------------------------------------------------------------
# build_message
# ---------------------------------------------------------------------------


class TestBuildMessage:
    def test_header_length_no_payload(self) -> None:
        msg = HtsMessage(
            sender=0x00000001,
            receiver=0x00000002,
            seq_num=0,
            link=0,
            flags=0,
            msg_type=MsgType.PING,
        )
        data = build_message(msg)
        assert len(data) == 14

    def test_header_with_payload(self) -> None:
        payload = b"\xaa\xbb\xcc"
        msg = HtsMessage(
            sender=1,
            receiver=2,
            seq_num=1,
            link=0,
            flags=0,
            msg_type=MsgType.PING,
            payload=payload,
        )
        data = build_message(msg)
        assert len(data) == 14 + len(payload)
        assert data[14:] == payload

    def test_sender_receiver_big_endian(self) -> None:
        msg = HtsMessage(
            sender=0x01020304,
            receiver=0x05060708,
            seq_num=0,
            link=0,
            flags=0,
            msg_type=MsgType.PING,
        )
        data = build_message(msg)
        assert data[0:4] == b"\x01\x02\x03\x04"
        assert data[4:8] == b"\x05\x06\x07\x08"

    def test_seq_num_3_bytes(self) -> None:
        msg = HtsMessage(
            sender=0,
            receiver=0,
            seq_num=0xABCDEF,
            link=0,
            flags=0,
            msg_type=MsgType.PING,
        )
        data = build_message(msg)
        assert data[8:11] == b"\xab\xcd\xef"

    def test_link_flags_type_bytes(self) -> None:
        msg = HtsMessage(
            sender=0,
            receiver=0,
            seq_num=0,
            link=0x11,
            flags=0x22,
            msg_type=MsgType.ACK,
        )
        data = build_message(msg)
        assert data[11] == 0x11
        assert data[12] == 0x22
        assert data[13] == MsgType.ACK

    def test_raw_int_msg_type(self) -> None:
        msg = HtsMessage(
            sender=0,
            receiver=0,
            seq_num=0,
            link=0,
            flags=0,
            msg_type=0xFF,
        )
        data = build_message(msg)
        assert data[13] == 0xFF

    def test_seq_num_wraps_at_24bit(self) -> None:
        msg = HtsMessage(
            sender=0,
            receiver=0,
            seq_num=0x1ABCDEF,  # more than 24 bits
            link=0,
            flags=0,
            msg_type=MsgType.PING,
        )
        data = build_message(msg)
        # only lower 24 bits kept
        assert data[8:11] == b"\xab\xcd\xef"


# ---------------------------------------------------------------------------
# parse_message
# ---------------------------------------------------------------------------


class TestParseMessage:
    def _make_bytes(
        self,
        sender: int = 0,
        receiver: int = 0,
        seq_num: int = 0,
        link: int = 0,
        flags: int = 0,
        msg_type: int = MsgType.PING,
        payload: bytes = b"",
    ) -> bytes:
        msg = HtsMessage(
            sender=sender,
            receiver=receiver,
            seq_num=seq_num,
            link=link,
            flags=flags,
            msg_type=msg_type,
            payload=payload,
        )
        return build_message(msg)

    def test_too_short_raises(self) -> None:
        with pytest.raises(ValueError, match="too short"):
            parse_message(b"\x00" * 13)

    def test_exact_header_no_payload(self) -> None:
        data = self._make_bytes(sender=1, receiver=2)
        msg = parse_message(data)
        assert msg.sender == 1
        assert msg.receiver == 2
        assert msg.payload == b""

    def test_known_msg_type_parsed(self) -> None:
        data = self._make_bytes(msg_type=MsgType.AUTHENTICATION)
        msg = parse_message(data)
        assert msg.msg_type == MsgType.AUTHENTICATION
        assert isinstance(msg.msg_type, MsgType)

    def test_unknown_msg_type_kept_as_int(self) -> None:
        data = self._make_bytes(msg_type=0xFF)
        msg = parse_message(data)
        assert msg.msg_type == 0xFF
        assert not isinstance(msg.msg_type, MsgType)

    def test_payload_preserved(self) -> None:
        payload = b"\x01\x02\x03\x04\x05"
        data = self._make_bytes(payload=payload)
        msg = parse_message(data)
        assert msg.payload == payload

    def test_seq_num_roundtrip(self) -> None:
        data = self._make_bytes(seq_num=0xABCDEF)
        msg = parse_message(data)
        assert msg.seq_num == 0xABCDEF

    def test_all_fields_roundtrip(self) -> None:
        original = HtsMessage(
            sender=0xDEADBEEF,
            receiver=0xCAFEBABE,
            seq_num=0x123456,
            link=0x07,
            flags=FLAG_NO_ACK,
            msg_type=MsgType.UPDATES,
            payload=b"\xff\xfe",
        )
        parsed = parse_message(build_message(original))
        assert parsed.sender == original.sender
        assert parsed.receiver == original.receiver
        assert parsed.seq_num == original.seq_num
        assert parsed.link == original.link
        assert parsed.flags == original.flags
        assert parsed.msg_type == original.msg_type
        assert parsed.payload == original.payload

    def test_empty_exactly_14_bytes(self) -> None:
        data = b"\x00" * 14
        msg = parse_message(data)
        assert msg.sender == 0
        assert msg.receiver == 0
        assert msg.seq_num == 0
        assert msg.payload == b""

    def test_flags_preserved(self) -> None:
        data = self._make_bytes(flags=FLAG_NO_ACK | 0x40 | 0x03)
        msg = parse_message(data)
        assert msg.is_no_ack is True
        assert msg.is_duplicate is True
        assert msg.send_try == 0x03
