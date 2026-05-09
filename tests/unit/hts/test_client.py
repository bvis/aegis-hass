"""Tests for HtsClient."""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from custom_components.aegis_ajax.api.hts.client import (
    AUTH_TIMEOUT,
    HTS_HOST,
    HTS_PORT,
    MAX_CONSECUTIVE_READ_TIMEOUTS,
    HtsClient,
    HtsConnectionError,
)
from custom_components.aegis_ajax.api.hts.hub_state import HubNetworkState
from custom_components.aegis_ajax.api.hts.messages import HtsMessage, MsgType, tlv_encode
from custom_components.aegis_ajax.api.hts.protocol import ETX, STX

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_client(**kwargs: object) -> HtsClient:
    defaults = {
        "login_token": b"\xde\xad\xbe\xef",
        "user_hex_id": "C9D0E1F2",
        "device_id": "device123",
        "app_label": "com.test.app",
    }
    defaults.update(kwargs)
    return HtsClient(**defaults)


# ---------------------------------------------------------------------------
# __init__ state
# ---------------------------------------------------------------------------


class TestInit:
    def test_default_host_and_port(self) -> None:
        client = _make_client()
        assert client._host == HTS_HOST
        assert client._port == HTS_PORT

    def test_custom_host_and_port(self) -> None:
        client = _make_client(host="localhost", port=9999)
        assert client._host == "localhost"
        assert client._port == 9999

    def test_reader_writer_none(self) -> None:
        client = _make_client()
        assert client._reader is None
        assert client._writer is None

    def test_not_connected(self) -> None:
        client = _make_client()
        assert client._connected is False
        assert client.is_connected is False

    def test_seq_num_zero(self) -> None:
        client = _make_client()
        assert client._seq_num == 1

    def test_sender_id_from_user_hex_id(self) -> None:
        client = _make_client(user_hex_id="C9D0E1F2")
        assert client._sender_id == 0xC9D0E1F2
        assert client._receiver_id == 0

    def test_connection_token_empty(self) -> None:
        client = _make_client()
        assert client._connection_token == b""

    def test_hubs_empty(self) -> None:
        client = _make_client()
        assert client._hubs == []

    def test_ping_task_none(self) -> None:
        client = _make_client()
        assert client._ping_task is None

    def test_hub_states_empty(self) -> None:
        client = _make_client()
        assert client._hub_states == {}
        assert client.hub_states == {}

    def test_on_state_update_none(self) -> None:
        client = _make_client()
        assert client._on_state_update is None

    def test_login_token_stored(self) -> None:
        client = _make_client(login_token=b"\x01\x02\x03")
        assert client._login_token == b"\x01\x02\x03"

    def test_device_id_stored(self) -> None:
        client = _make_client(device_id="mydevice")
        assert client._device_id == "mydevice"

    def test_app_label_stored(self) -> None:
        client = _make_client(app_label="com.my.app")
        assert client._app_label == "com.my.app"


# ---------------------------------------------------------------------------
# _next_seq
# ---------------------------------------------------------------------------


class TestNextSeq:
    def test_starts_at_zero(self) -> None:
        client = _make_client()
        assert client._next_seq() == 1

    def test_increments(self) -> None:
        client = _make_client()
        client._next_seq()  # 1
        assert client._next_seq() == 2

    def test_sequential_calls(self) -> None:
        client = _make_client()
        results = [client._next_seq() for _ in range(5)]
        assert results == [1, 2, 3, 4, 5]

    def test_wraps_at_0xffffff(self) -> None:
        client = _make_client()
        client._seq_num = 0xFFFFFF
        assert client._next_seq() == 0xFFFFFF
        assert client._seq_num == 0  # wrapped

    def test_wrap_next_is_zero(self) -> None:
        client = _make_client()
        client._seq_num = 0xFFFFFF
        client._next_seq()  # consumes 0xFFFFFF
        assert client._next_seq() == 0  # next is 0

    def test_no_value_exceeds_max(self) -> None:
        client = _make_client()
        client._seq_num = 0xFFFFFE
        for _ in range(4):
            seq = client._next_seq()
            assert 0 <= seq <= 0xFFFFFF


# ---------------------------------------------------------------------------
# _send_message
# ---------------------------------------------------------------------------


class TestSendMessage:
    """_send_message should produce a valid STX...ETX frame on the wire."""

    @pytest.mark.asyncio
    async def test_frame_starts_with_stx_ends_with_etx(self) -> None:
        client = _make_client()

        written_data = bytearray()

        mock_writer = MagicMock()
        mock_writer.write = lambda data: written_data.extend(data)
        mock_writer.drain = AsyncMock()
        client._writer = mock_writer

        from custom_components.aegis_ajax.api.hts.messages import MsgType

        await client._send_message(MsgType.PING, b"")

        assert len(written_data) > 0
        assert written_data[0] == STX
        assert written_data[-1] == ETX

    @pytest.mark.asyncio
    async def test_drain_called(self) -> None:
        client = _make_client()

        mock_writer = MagicMock()
        mock_writer.write = MagicMock()
        mock_writer.drain = AsyncMock()
        client._writer = mock_writer

        from custom_components.aegis_ajax.api.hts.messages import MsgType

        await client._send_message(MsgType.PING, b"")

        mock_writer.drain.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_seq_increments_on_send(self) -> None:
        client = _make_client()

        mock_writer = MagicMock()
        mock_writer.write = MagicMock()
        mock_writer.drain = AsyncMock()
        client._writer = mock_writer

        from custom_components.aegis_ajax.api.hts.messages import MsgType

        assert client._seq_num == 1
        await client._send_message(MsgType.PING, b"")
        assert client._seq_num == 2

    @pytest.mark.asyncio
    async def test_frame_is_bytes(self) -> None:
        client = _make_client()

        captured = []
        mock_writer = MagicMock()
        mock_writer.write = lambda data: captured.append(bytes(data))
        mock_writer.drain = AsyncMock()
        client._writer = mock_writer

        from custom_components.aegis_ajax.api.hts.messages import MsgType

        await client._send_message(MsgType.PING, b"")

        assert len(captured) == 1
        assert isinstance(captured[0], bytes)


class TestConnectAuthTimeout:
    """Issue #74: connect() must bound the auth handshake."""

    @pytest.mark.asyncio
    async def test_authenticate_hang_raises_connection_error(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        client = _make_client()

        # Pretend the TCP+TLS dial succeeded so we go straight into auth.
        async def _fake_open_connection(*_args: object, **_kwargs: object) -> tuple:
            return MagicMock(), MagicMock()

        monkeypatch.setattr(
            "custom_components.aegis_ajax.api.hts.client.asyncio.open_connection",
            _fake_open_connection,
        )

        # Make the handshake hang forever — exactly the symptom in #74.
        async def _hang() -> None:
            await asyncio.Event().wait()

        async def _fake_authenticate() -> object:
            await _hang()
            raise AssertionError("unreachable")

        monkeypatch.setattr(client, "_authenticate", _fake_authenticate)
        monkeypatch.setattr(client, "close", AsyncMock())

        # Override the timeout so the test doesn't actually wait 20s.
        monkeypatch.setattr("custom_components.aegis_ajax.api.hts.client.AUTH_TIMEOUT", 0.05)

        with pytest.raises(HtsConnectionError, match="auth handshake timed out"):
            await client.connect()

        client.close.assert_awaited()

    def test_auth_timeout_default(self) -> None:
        # Sanity check: default budget is bounded and reasonable.
        assert 1 <= AUTH_TIMEOUT <= 60


class TestHandleUpdate:
    @pytest.mark.asyncio
    async def test_status_body_updates_hub_state(self) -> None:
        client = _make_client()
        client._hubs = [MagicMock(hub_id="12345678")]
        client._on_state_update = MagicMock()
        msg = HtsMessage(
            sender=0x12345678,
            receiver=client._sender_id,
            seq_num=1,
            link=10,
            flags=0,
            msg_type=MsgType.UPDATES,
            payload=tlv_encode(
                [
                    b"\x09",
                    bytes.fromhex("12345678"),
                    b"\x48",
                    b"\x02",
                ]
            ),
        )

        await client._handle_update(msg)

        state = client.hub_states["12345678"]
        assert state.wifi_connected is True
        assert state.primary_connection == "wifi"
        client._on_state_update.assert_called_once_with("12345678", state)

    @pytest.mark.asyncio
    async def test_direct_delta_update_clears_ethernet_and_keeps_wifi(self) -> None:
        client = _make_client()
        client._hubs = [MagicMock(hub_id="12345678")]
        client._hub_states["12345678"] = HubNetworkState(
            ethernet_connected=True,
            wifi_connected=True,
        )
        client._on_state_update = MagicMock()
        msg = HtsMessage(
            sender=0x12345678,
            receiver=client._sender_id,
            seq_num=1,
            link=10,
            flags=0,
            msg_type=MsgType.UPDATES,
            payload=tlv_encode(
                [
                    b"\x0a",
                    b"\x48",
                    b"\x02",
                ]
            ),
        )

        await client._handle_update(msg)

        state = client.hub_states["12345678"]
        assert state.ethernet_connected is False
        assert state.wifi_connected is True
        assert state.primary_connection == "wifi"
        client._on_state_update.assert_called_once_with("12345678", state)

    @pytest.mark.asyncio
    async def test_malformed_payload_drops_message_without_raising(self) -> None:
        # Regression for #108: @uddinr's hub firmware emitted a payload
        # containing 0x06 0x6A inside a TLV segment which our escape
        # table didn't recognise. The lenient `tlv_unescape_param`
        # already preserves unknown pairs, but as belt-and-suspenders
        # `_handle_update` swallows any tlv_decode exception so a
        # future parser bug or a truly garbled payload does not bubble
        # out, kill the listen loop, and leave hub-network sensors
        # permanently unavailable.
        client = _make_client()
        client._hubs = [MagicMock(hub_id="12345678")]
        client._on_state_update = MagicMock()

        # Force a tlv_decode failure with a payload that will reach an
        # exception path even with the lenient unescape (mock the
        # decoder so we don't have to construct an artificial wire-
        # format that survives the lenient parser).
        with patch(
            "custom_components.aegis_ajax.api.hts.client.tlv_decode",
            side_effect=ValueError("synthetic decode failure"),
        ):
            msg = HtsMessage(
                sender=0x12345678,
                receiver=client._sender_id,
                seq_num=1,
                link=10,
                flags=0,
                msg_type=MsgType.UPDATES,
                payload=b"\x05\x06\x6a\x05",
            )
            # Must not raise — the listen loop depends on this
            await client._handle_update(msg)

        # No state update fired — message was correctly dropped
        client._on_state_update.assert_not_called()

    @pytest.mark.asyncio
    async def test_unknown_hub_update_requests_refresh_once(self) -> None:
        client = _make_client()
        client._hubs = [MagicMock(hub_id="12345678")]

        async def _refresh(_: str) -> None:
            await asyncio.sleep(0)

        client.request_hub_data = AsyncMock(side_effect=_refresh)
        msg = HtsMessage(
            sender=0x12345678,
            receiver=client._sender_id,
            seq_num=1,
            link=10,
            flags=0,
            msg_type=MsgType.UPDATES,
            payload=tlv_encode(
                [
                    b"\x22",
                    b"\x99",
                    b"\x01",
                ]
            ),
        )

        await asyncio.gather(client._handle_update(msg), client._handle_update(msg))
        await asyncio.sleep(0)

        client.request_hub_data.assert_awaited_once_with("12345678")

    @pytest.mark.asyncio
    async def test_listen_keeps_connection_open_on_idle_read_timeout(self) -> None:
        client = _make_client()
        client._connected = True
        ack = HtsMessage(
            sender=0x12345678,
            receiver=client._sender_id,
            seq_num=1,
            link=10,
            flags=0,
            msg_type=MsgType.ACK,
            payload=b"",
        )
        client._receive_message = AsyncMock(  # type: ignore[method-assign]
            side_effect=[TimeoutError, ack, ConnectionError("closed")]
        )

        await client.listen()

        assert client._receive_message.await_count == 3
        assert client._consecutive_read_timeouts == 0

    @pytest.mark.asyncio
    async def test_listen_closes_after_max_consecutive_idle_timeouts(self) -> None:
        client = _make_client()
        client._connected = True
        client._receive_message = AsyncMock(  # type: ignore[method-assign]
            side_effect=[TimeoutError] * MAX_CONSECUTIVE_READ_TIMEOUTS
        )

        await client.listen()

        assert client._receive_message.await_count == MAX_CONSECUTIVE_READ_TIMEOUTS
        assert client._consecutive_read_timeouts == MAX_CONSECUTIVE_READ_TIMEOUTS

    @pytest.mark.asyncio
    async def test_ping_loop_marks_disconnected_on_send_failure(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(
            "custom_components.aegis_ajax.api.hts.client.PING_INTERVAL",
            0,
        )
        client = _make_client()
        client._connected = True
        client._send_message = AsyncMock(side_effect=OSError("socket closed"))  # type: ignore[method-assign]

        await client._ping_loop()

        assert client._connected is False
        client._send_message.assert_awaited_once_with(MsgType.PING, b"")
