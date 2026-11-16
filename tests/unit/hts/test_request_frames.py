"""The frames that ask a hub for its settings and status, decoded back from the wire."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from custom_components.aegis_ajax.api.hts.client import HtsClient, HtsConnectionError
from custom_components.aegis_ajax.api.hts.crypto import decrypt
from custom_components.aegis_ajax.api.hts.messages import (
    HtsMessage,
    MsgType,
    parse_message,
    tlv_encode,
)
from custom_components.aegis_ajax.api.hts.protocol import decode_frame

HUB = "002B1A51"
SETTINGS, STATUS = 3, 7


def _connected_client() -> tuple[HtsClient, MagicMock]:
    client = HtsClient(
        login_token=b"\xde\xad\xbe\xef",
        user_hex_id="C9D0E1F2",
        device_id="device123",
        app_label="com.test.app",
    )
    writer = MagicMock()
    writer.drain = AsyncMock()
    client._writer = writer
    return client, writer


def _sent(writer: MagicMock) -> list[HtsMessage]:
    return [
        parse_message(decrypt(decode_frame(call.args[0]))) for call in writer.write.call_args_list
    ]


def _assert_request(client: HtsClient, msg: HtsMessage, sub_key: int) -> None:
    assert msg.sender == client._sender_id
    assert msg.receiver == int(HUB, 16)
    assert (msg.link, msg.flags, msg.msg_type) == (10, 0, MsgType.UPDATES)
    # The body is padded to the cipher block; the request itself comes first.
    assert msg.payload.startswith(tlv_encode([bytes([sub_key]), b"\x01", b"\x01"]))


async def test_full_settings_request_frame() -> None:
    client, writer = _connected_client()
    await client._send_request_full_settings(HUB)
    (msg,) = _sent(writer)
    _assert_request(client, msg, SETTINGS)
    writer.drain.assert_awaited_once()


async def test_full_status_request_frame() -> None:
    client, writer = _connected_client()
    await client.request_full_status(HUB)
    (msg,) = _sent(writer)
    _assert_request(client, msg, STATUS)
    writer.drain.assert_awaited_once()


async def test_hub_data_asks_for_settings_then_status_with_rising_sequence() -> None:
    client, writer = _connected_client()
    first_seq = client._seq_num
    await client.request_hub_data(HUB)
    settings, status = _sent(writer)
    _assert_request(client, settings, SETTINGS)
    _assert_request(client, status, STATUS)
    assert (settings.seq_num, status.seq_num) == (first_seq, first_seq + 1)


async def test_requests_need_a_connection() -> None:
    client, _ = _connected_client()
    client._writer = None
    with pytest.raises(HtsConnectionError, match="Not connected"):
        await client.request_full_status(HUB)
