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
    async def test_subkey_11_with_anchor_keys_updates_state_without_refresh(self) -> None:
        # Regression for #111: short deltas on sub-key 11 carry no anchor
        # keys and used to fire a full-snapshot refresh on every heartbeat.
        # The long-form variant (50 byte payload with anchor keys) must
        # still parse and update hub state — that's the path real
        # network changes flow through.
        client = _make_client()
        client._hubs = [MagicMock(hub_id="12345678")]
        client._hub_states["12345678"] = HubNetworkState(ethernet_connected=True)
        client._on_state_update = MagicMock()
        client.request_hub_data = AsyncMock()
        msg = HtsMessage(
            sender=0x12345678,
            receiver=client._sender_id,
            seq_num=1,
            link=10,
            flags=0,
            msg_type=MsgType.UPDATES,
            payload=tlv_encode(
                [
                    b"\x0b",  # sub-key 11
                    b"\x48",  # KEY_ACTIVE_CHANNELS — anchor key, marks as network delta
                    b"\x02",  # wifi bit set
                ]
            ),
        )

        await client._handle_update(msg)

        state = client.hub_states["12345678"]
        assert state.wifi_connected is True
        client._on_state_update.assert_called_once_with("12345678", state)
        client.request_hub_data.assert_not_called()

    @pytest.mark.asyncio
    async def test_subkey_11_without_anchor_keys_drops_silently(self) -> None:
        # Regression for #111: Hansontech190 and b0arkz reported sub-key
        # 11 messages every few seconds with a 34-byte payload that
        # contained no anchor keys. The handler used to fall through to
        # `_schedule_hub_refresh` and trigger a full settings+status
        # round-trip per heartbeat. Now the handler drops these without
        # firing a refresh — the same parser would not learn anything
        # new from the snapshot, so the round-trip is pure stress on the
        # Ajax cloud and HA's event loop.
        client = _make_client()
        client._hubs = [MagicMock(hub_id="12345678")]
        client._on_state_update = MagicMock()
        client.request_hub_data = AsyncMock()
        msg = HtsMessage(
            sender=0x12345678,
            receiver=client._sender_id,
            seq_num=1,
            link=10,
            flags=0,
            msg_type=MsgType.UPDATES,
            payload=tlv_encode(
                [
                    b"\x0b",  # sub-key 11
                    b"\x99",  # unknown key
                    b"\x01",  # opaque value
                ]
            ),
        )

        await client._handle_update(msg)
        await asyncio.sleep(0)

        client.request_hub_data.assert_not_called()
        client._on_state_update.assert_not_called()

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


class TestExtractAllDevicesKv:
    """Per-device kv extraction for the #123 probe.

    Generalises `_extract_device_kv` (which returns kv for one specific
    device) to walk the entire body and emit one (device_id, kv) tuple
    per device marker. DEBUG-only consumer today — wired in
    `_handle_update` to surface what sub-keys non-hub devices carry,
    ahead of mapping them to sensors.
    """

    def test_empty_params_yields_empty_list(self) -> None:
        assert HtsClient._extract_all_devices_kv([]) == []

    def test_sub_key_without_markers_yields_empty_list(self) -> None:
        # `params[0]` is the sub-key byte; if there is no 4-byte device
        # marker afterwards, nothing should be reported.
        assert HtsClient._extract_all_devices_kv([b"\x09"]) == []

    def test_single_device_returns_its_kvs(self) -> None:
        hub_id = bytes.fromhex("12345678")
        result = HtsClient._extract_all_devices_kv(
            [
                b"\x09",  # sub_key (STATUS_BODY)
                hub_id,
                b"\x48",
                b"\x02",
                b"\x37",
                b"\xab\xcd",
            ]
        )
        assert result == [(hub_id, {0x48: b"\x02", 0x37: b"\xab\xcd"})]

    def test_two_devices_separated_correctly(self) -> None:
        hub_id = bytes.fromhex("12345678")
        wallswitch_id = bytes.fromhex("311B058D")
        result = HtsClient._extract_all_devices_kv(
            [
                b"\x09",
                hub_id,
                b"\x48",
                b"\x02",
                wallswitch_id,
                b"\x42",
                b"\x00\x28",  # current_ma = 40
                b"\x43",
                b"\x00\x09",  # power_wth = 9
            ]
        )
        assert result == [
            (hub_id, {0x48: b"\x02"}),
            (wallswitch_id, {0x42: b"\x00\x28", 0x43: b"\x00\x09"}),
        ]

    def test_two_byte_keys_are_skipped(self) -> None:
        # Same rule as `_extract_device_kv`: 1-byte keys only, 2-byte
        # extended keys are intentionally not surfaced.
        hub_id = bytes.fromhex("12345678")
        result = HtsClient._extract_all_devices_kv(
            [
                b"\x09",
                hub_id,
                b"\xff\xee",  # 2-byte extended key
                b"\x01",
                b"\x37",
                b"\xab",
            ]
        )
        assert result == [(hub_id, {0x37: b"\xab"})]

    def test_orphan_params_before_first_marker_are_skipped(self) -> None:
        # Garbage / unexpected leading params (no preceding device id)
        # should not crash and should not associate with any device.
        hub_id = bytes.fromhex("12345678")
        result = HtsClient._extract_all_devices_kv(
            [
                b"\x09",
                b"\x99",  # orphan single byte before any marker
                hub_id,
                b"\x48",
                b"\x02",
            ]
        )
        assert result == [(hub_id, {0x48: b"\x02"})]


class TestHandleUpdateNonHubProbe:
    """DEBUG probe for non-hub device sub-keys in STATUS / SETTINGS bodies (#123)."""

    @pytest.mark.asyncio
    async def test_non_hub_device_subkeys_are_logged(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        import logging

        caplog.set_level(logging.DEBUG, logger="custom_components.aegis_ajax.api.hts.client")
        client = _make_client()
        client._hubs = [MagicMock(hub_id="12345678")]
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
                    bytes.fromhex("311B058D"),
                    b"\x42",
                    b"\x00\x28",
                    b"\x43",
                    b"\x00\x09",
                ]
            ),
        )

        await client._handle_update(msg)

        probe_lines = [r for r in caplog.records if "#123 probe" in r.getMessage()]
        assert probe_lines, "expected one #123 probe DEBUG line"
        text = probe_lines[0].getMessage()
        assert "311B058D" in text
        assert "0x42" in text
        assert "0x43" in text

    @pytest.mark.asyncio
    async def test_on_device_kv_fires_once_per_non_hub_device(self) -> None:
        client = _make_client()
        client._hubs = [MagicMock(hub_id="12345678")]
        captured: list[tuple[str, str, dict[int, bytes]]] = []
        client._on_device_kv = lambda hub_id, did, kv: captured.append((hub_id, did, kv))
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
                    bytes.fromhex("311B058D"),
                    b"\x42",
                    b"\x00\x28",
                    b"\x43",
                    b"\x00\x09",
                    bytes.fromhex("AABBCCDD"),
                    b"\x05",
                    b"\x01",
                ]
            ),
        )

        await client._handle_update(msg)

        assert captured == [
            ("12345678", "311B058D", {0x42: b"\x00\x28", 0x43: b"\x00\x09"}),
            ("12345678", "AABBCCDD", {0x05: b"\x01"}),
        ]

    @pytest.mark.asyncio
    async def test_on_device_kv_skips_empty_device_rows(self) -> None:
        client = _make_client()
        client._hubs = [MagicMock(hub_id="12345678")]
        captured: list[tuple[str, str, dict[int, bytes]]] = []
        client._on_device_kv = lambda hub_id, did, kv: captured.append((hub_id, did, kv))
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
                    # Lone device marker without any kv pairs — must not
                    # emit (otherwise the coordinator wakes up for nothing).
                    bytes.fromhex("AABBCCDD"),
                ]
            ),
        )

        await client._handle_update(msg)

        assert captured == []

    @pytest.mark.asyncio
    async def test_on_device_kv_callback_exception_doesnt_break_loop(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        import logging as _logging

        caplog.set_level(_logging.ERROR, logger="custom_components.aegis_ajax.api.hts.client")
        client = _make_client()
        client._hubs = [MagicMock(hub_id="12345678")]

        def _raises(hub_id: str, did: str, kv: dict[int, bytes]) -> None:
            raise RuntimeError("synthetic")

        client._on_device_kv = _raises
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
                    bytes.fromhex("311B058D"),
                    b"\x42",
                    b"\x00\x28",
                ]
            ),
        )

        # Must not raise out of _handle_update — the listen loop has to
        # survive a buggy coordinator callback (#108 / parser hardening
        # mindset). The error must be logged with exc_info though.
        await client._handle_update(msg)
        errors = [r for r in caplog.records if r.levelname == "ERROR"]
        assert any("on_device_kv callback raised" in r.getMessage() for r in errors)

    @pytest.mark.asyncio
    async def test_probe_silent_when_only_hub_row_present(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        import logging

        caplog.set_level(logging.DEBUG, logger="custom_components.aegis_ajax.api.hts.client")
        client = _make_client()
        client._hubs = [MagicMock(hub_id="12345678")]
        msg = HtsMessage(
            sender=0x12345678,
            receiver=client._sender_id,
            seq_num=1,
            link=10,
            flags=0,
            msg_type=MsgType.UPDATES,
            payload=tlv_encode([b"\x09", bytes.fromhex("12345678"), b"\x48", b"\x02"]),
        )

        await client._handle_update(msg)

        probe_lines = [r for r in caplog.records if "#123 probe" in r.getMessage()]
        assert probe_lines == []


class TestStatusUpdatePush:
    """STATUS_UPDATE (sub-key 11) / SETTINGS_UPDATE (12) push deltas.

    These are emitted by the hub on its own cadence — no client-side
    subscribe is needed (the audit on PRO 2.47 showed both the single
    and bulk subscribe msg-types are deprecated and uncalled). The
    handler must route the per-device kv payload through the same
    `on_device_kv` callback used for the boot-time STATUS_BODY snapshot,
    so the coordinator side stays a single code path.
    """

    @pytest.mark.asyncio
    async def test_status_update_routes_through_on_device_kv(self) -> None:
        client = _make_client()
        client._hubs = [MagicMock(hub_id="12345678")]
        captured: list[tuple[str, str, dict[int, bytes]]] = []
        client._on_device_kv = lambda hub_id, did, kv: captured.append((hub_id, did, kv))
        msg = HtsMessage(
            sender=0x12345678,
            receiver=client._sender_id,
            seq_num=1,
            link=10,
            flags=0,
            msg_type=MsgType.UPDATES,
            payload=tlv_encode(
                [
                    b"\x0b",  # STATUS_UPDATE
                    bytes.fromhex("311B058D"),
                    b"\x42",
                    b"\x00\x28",
                    b"\x43",
                    b"\x00\x09",
                ]
            ),
        )

        await client._handle_update(msg)

        assert captured == [
            ("12345678", "311B058D", {0x42: b"\x00\x28", 0x43: b"\x00\x09"}),
        ]

    @pytest.mark.asyncio
    async def test_settings_update_routes_through_on_device_kv(self) -> None:
        # Same routing for sub-key 0x0c. We don't currently consume
        # settings-only sub-keys but the coordinator's electrical-type
        # filter is the single gate; emitting them keeps the surface
        # area uniform.
        client = _make_client()
        client._hubs = [MagicMock(hub_id="12345678")]
        captured: list[tuple[str, str, dict[int, bytes]]] = []
        client._on_device_kv = lambda hub_id, did, kv: captured.append((hub_id, did, kv))
        msg = HtsMessage(
            sender=0x12345678,
            receiver=client._sender_id,
            seq_num=1,
            link=10,
            flags=0,
            msg_type=MsgType.UPDATES,
            payload=tlv_encode(
                [
                    b"\x0c",  # SETTINGS_UPDATE
                    bytes.fromhex("311B058D"),
                    b"\x40",
                    b"\x01",
                ]
            ),
        )

        await client._handle_update(msg)

        assert captured == [("12345678", "311B058D", {0x40: b"\x01"})]

    @pytest.mark.asyncio
    async def test_status_update_with_hub_marker_is_ignored(self) -> None:
        # Same shape but the device id IS the hub — hub-level deltas
        # are owned by the `_extract_direct_kv` / `parse_hub_params`
        # path above; do not double-route them through on_device_kv.
        client = _make_client()
        client._hubs = [MagicMock(hub_id="12345678")]
        captured: list[tuple[str, str, dict[int, bytes]]] = []
        client._on_device_kv = lambda hub_id, did, kv: captured.append((hub_id, did, kv))
        msg = HtsMessage(
            sender=0x12345678,
            receiver=client._sender_id,
            seq_num=1,
            link=10,
            flags=0,
            msg_type=MsgType.UPDATES,
            payload=tlv_encode(
                [
                    b"\x0b",
                    bytes.fromhex("12345678"),
                    b"\x05",
                    b"\x01",
                ]
            ),
        )

        await client._handle_update(msg)

        assert captured == []

    @pytest.mark.asyncio
    async def test_status_update_no_longer_schedules_full_refresh(self) -> None:
        # Pre-#111 behaviour was a full snapshot round-trip on every
        # sub-key 11 (every few seconds — the original WallSwitch
        # bandwidth bug). Confirm the new path consumes the delta
        # without firing `_schedule_hub_refresh`.
        client = _make_client()
        client._hubs = [MagicMock(hub_id="12345678")]
        client._schedule_hub_refresh = MagicMock()  # type: ignore[method-assign]
        msg = HtsMessage(
            sender=0x12345678,
            receiver=client._sender_id,
            seq_num=1,
            link=10,
            flags=0,
            msg_type=MsgType.UPDATES,
            payload=tlv_encode(
                [
                    b"\x0b",
                    bytes.fromhex("311B058D"),
                    b"\x42",
                    b"\x00\x28",
                ]
            ),
        )

        await client._handle_update(msg)

        client._schedule_hub_refresh.assert_not_called()


class TestPingLoop:
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
