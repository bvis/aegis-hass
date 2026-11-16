"""The hub login handshake on the wire, the session list, and how a hub's state is merged."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

from custom_components.aegis_ajax.api.hts.auth import build_connect_request, solve_challenge
from custom_components.aegis_ajax.api.hts.client import (
    _CLIENT_SESSION_RECORD_SEPARATOR as SEPARATOR,
)
from custom_components.aegis_ajax.api.hts.client import ClientSession, HtsClient
from custom_components.aegis_ajax.api.hts.crypto import decrypt
from custom_components.aegis_ajax.api.hts.hub_state import HubNetworkState
from custom_components.aegis_ajax.api.hts.messages import (
    AUTH_KEY_AUTHENTICATION_REQUEST,
    AUTH_KEY_AUTHENTICATION_RESPONSE,
    HtsMessage,
    MsgType,
    parse_message,
    tlv_encode,
)
from custom_components.aegis_ajax.api.hts.protocol import decode_frame

_CLIENT = "custom_components.aegis_ajax.api.hts.client"


def _client() -> HtsClient:
    return HtsClient(
        login_token=b"\xde\xad\xbe\xef",
        user_hex_id="C9D0E1F2",
        device_id="device123",
        app_label="com.test.app",
    )


def _incoming(msg_type: MsgType, payload: bytes, seq: int) -> HtsMessage:
    return HtsMessage(
        sender=1, receiver=0, seq_num=seq, link=0, flags=0, msg_type=msg_type, payload=payload
    )


async def test_login_handshake_messages() -> None:
    client = _client()
    writer = MagicMock()
    writer.drain = AsyncMock()
    client._writer = writer
    client._send_message = AsyncMock()  # type: ignore[method-assign]
    client._send_ack = AsyncMock()  # type: ignore[method-assign]
    challenge = _incoming(
        MsgType.AUTHENTICATION,
        tlv_encode([bytes([AUTH_KEY_AUTHENTICATION_REQUEST]), bytes([0x12, 0x34])]),
        seq=10,
    )
    connected = _incoming(MsgType.USER_REGISTRATION, tlv_encode([b"\x00"]), seq=20)
    client._receive_message = AsyncMock(side_effect=[challenge, connected])  # type: ignore[method-assign]
    first_seq = client._seq_num
    session = MagicMock(token=b"\xaa\xbb\xcc\xdd", hubs=[MagicMock(hub_id="AAAA0001")])

    with patch(f"{_CLIENT}.parse_connected_response", return_value=session) as parse:
        assert await client._authenticate() is session

    # 1. The connect request carries this client's login token, device and app.
    client._send_message.assert_awaited_once_with(
        MsgType.USER_REGISTRATION,
        build_connect_request(
            login_token=b"\xde\xad\xbe\xef", device_id="device123", app_label="com.test.app"
        ),
    )
    # 2. Both server messages are acknowledged, in order.
    assert [call.args for call in client._send_ack.await_args_list] == [(challenge,), (connected,)]
    # 3. The challenge answer goes out as an AUTHENTICATION message to the server.
    (frame,) = (call.args[0] for call in writer.write.call_args_list)
    answer = parse_message(decrypt(decode_frame(frame)))
    assert (answer.sender, answer.receiver) == (client._sender_id, 0)
    assert (answer.link, answer.flags, answer.msg_type) == (0, 0, MsgType.AUTHENTICATION)
    assert answer.seq_num == first_seq
    assert answer.payload.startswith(
        tlv_encode([bytes([AUTH_KEY_AUTHENTICATION_RESPONSE]), solve_challenge(0x12, 0x34)])
    )
    # 4. The session answer is parsed and its hubs kept.
    parse.assert_called_once_with(connected.payload)
    assert client._hubs is session.hubs
    assert client._connection_token == b"\xaa\xbb\xcc\xdd"


def _ts(value: int) -> bytes:
    return value.to_bytes(8, "big", signed=True)


def test_client_sessions_are_read_field_by_field() -> None:
    params = [
        b"\x41",
        *(b"\x01", _ts(1_000)),
        *(b"\x03", b"Pixel 9"),
        *(b"\x04", b"Android 15"),
        *(b"\x05", _ts(2_000)),
        *(b"\x06", _ts(1_500)),
        *(b"\x07", b"\x01"),
        *(b"\x09", b"3.30"),
        *(b"\x0a", b"Ajax"),
        SEPARATOR,
        # Same identity, but never active: not the current session.
        *(b"\x01", _ts(-7)),
        *(b"\x06", _ts(0)),
        *(b"\x07", b"\x01"),
        SEPARATOR,
        # Someone else's session, with a timestamp of the wrong size.
        *(b"\x03", b"iPhone"),
        *(b"\x05", b"\x00\x00\x00\x01"),
        *(b"\x06", _ts(900)),
    ]
    assert HtsClient._parse_client_sessions(params) == [
        ClientSession(
            session_id=1_000,
            device_model="Pixel 9",
            operating_system="Android 15",
            application="Ajax",
            version="3.30",
            created_at=1_000,
            expires_at=2_000,
            last_active_at=1_500,
            is_current=True,
            is_self_identity=True,
        ),
        ClientSession(
            session_id=-7,
            device_model="",
            operating_system="",
            application="",
            version="",
            created_at=-7,
            expires_at=None,
            last_active_at=0,
            is_current=False,
            is_self_identity=True,
        ),
        ClientSession(
            session_id=None,
            device_model="iPhone",
            operating_system="",
            application="",
            version="",
            created_at=None,
            expires_at=None,
            last_active_at=900,
            is_current=False,
            is_self_identity=False,
        ),
    ]


async def test_a_status_body_is_merged_into_what_was_known_about_that_hub() -> None:
    client = _client()
    client._hubs = [MagicMock(hub_id="12345678"), MagicMock(hub_id="87654321")]
    known = HubNetworkState(externally_powered=True)
    other = HubNetworkState(externally_powered=False)
    client._hub_states = {"12345678": known, "87654321": other}
    client._on_state_update = MagicMock()
    merged = HubNetworkState(externally_powered=True, wifi_connected=True)
    body = HtsMessage(
        sender=0x12345678,
        receiver=client._sender_id,
        seq_num=1,
        link=10,
        flags=0,
        msg_type=MsgType.UPDATES,
        payload=tlv_encode([b"\x09", bytes.fromhex("12345678"), b"\x48", b"\x02"]),
    )
    with patch(f"{_CLIENT}.parse_hub_params", return_value=merged) as parse:
        await client._handle_update(body)
    parse.assert_called_once_with({0x48: b"\x02"}, known)
    assert client.hub_states == {"12345678": merged, "87654321": other}
    client._on_state_update.assert_called_once_with("12345678", merged)
