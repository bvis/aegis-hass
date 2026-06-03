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
    HtsAuthError,
    HtsClient,
    HtsConnectionError,
)
from custom_components.aegis_ajax.api.hts.hub_state import HubNetworkState
from custom_components.aegis_ajax.api.hts.messages import (
    AUTH_KEY_AUTHENTICATION_REQUEST,
    HtsMessage,
    MsgType,
    tlv_encode,
)
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
    async def test_per_device_delta_with_state_byte_routes_to_device_path(self) -> None:
        """#179 follow-up: STATUS_UPDATE deltas for an individual device
        must reach `_on_device_kv`, not the hub-network-state parser.

        The bug: `_extract_direct_kv(params[1:])` was being run on every
        non-body delta to test for the hub-network shape. For a per-device
        payload `[sub_key, device_id_4b, k1, v1, k2, v2, …]`, the 4-byte
        device_id at `params[1]` doesn't qualify as a key (length != 1),
        so the helper happily pairs up later bytes — frequently producing
        a kv dict whose keys contain `0x03` (`KEY_HUB_POWERED`) because
        a value byte downstream happens to be `0x03` (the operational
        state byte common to many Ajax devices). `_is_network_state_delta`
        then fires the wrong path and every per-device live reading is
        silently dropped, leaving `device_readings` to only ever be
        seeded by the initial STATUS_BODY snapshot. The fix routes by
        payload shape (a 4-byte `params[1]` means per-device) before
        running the heuristic.
        """
        client = _make_client()
        client._hubs = [MagicMock(hub_id="12345678")]
        client._on_state_update = MagicMock()
        captured: list[tuple[str, str, dict[int, bytes]]] = []
        client._on_device_kv = lambda hub_id, did, kv: captured.append((hub_id, did, kv))

        # Mimics a real Outlet STATUS_UPDATE: 4-byte device_id, then
        # multiple (k1=byte, v1=byte) pairs where v1 happens to be 0x03 —
        # the byte that triggered the misroute on SaetanSaDiablo's hub.
        msg = HtsMessage(
            sender=0x12345678,
            receiver=client._sender_id,
            seq_num=1,
            link=10,
            flags=0,
            msg_type=MsgType.UPDATES,
            payload=tlv_encode(
                [
                    b"\x0b",  # sub_key 11 = STATUS_UPDATE
                    bytes.fromhex("30537E4C"),
                    b"\x02",
                    b"\x03",  # value byte = 0x03 = KEY_HUB_POWERED
                    b"\x05",
                    b"\x07",
                ]
            ),
        )

        await client._handle_update(msg)

        # Per-device path must fire with the real kv from the payload.
        assert captured == [("12345678", "30537E4C", {0x02: b"\x03", 0x05: b"\x07"})]
        # Network-state parser must NOT have been called.
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

    @pytest.mark.asyncio
    async def test_listen_skips_malformed_frame_and_keeps_listening(self) -> None:
        """A ValueError from frame decode/decrypt/parse must not propagate out
        of listen() and tear down the connection — skip the bad frame and keep
        reading (audit fix)."""
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
            side_effect=[ValueError("bad CRC"), ack, ConnectionError("closed")]
        )

        # Must NOT raise — the ValueError is swallowed and the loop continues.
        await client.listen()

        # All three reads happened: bad frame skipped, ack handled, then close.
        assert client._receive_message.await_count == 3


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
    async def test_non_hub_device_subkeys_log_includes_hex_values(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        """#179: the per-device DEBUG probe must include the raw hex VALUE of
        each sub-key, not just its size. Without values, mapping an unknown
        device family (Outlet Type E etc.) requires another round-trip with
        the user; with values, a single capture under a known load pins
        every reading to its sub-key."""
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
                    bytes.fromhex("30537E4C"),
                    b"\x37",
                    b"\x00\x11\x22\x33",  # candidate energy/power 4-byte
                    b"\x73",
                    b"\xde\xad\xbe\xef\x01\x02\x03\x04\x05\x06\x07\x08\x09\x0a\x0b\x0c",
                ]
            ),
        )

        await client._handle_update(msg)

        probe_lines = [r for r in caplog.records if "#123 probe" in r.getMessage()]
        assert probe_lines, "expected one #123 probe DEBUG line"
        text = probe_lines[0].getMessage()
        # Hex value of 0x37 must surface so we can correlate it with a
        # known load on the Outlet (#179).
        assert "0x37=00112233" in text
        # Long blob value must surface too — that's where energy
        # accumulators live on the Outlet.
        assert "0x73=deadbeef0102030405060708090a0b0c" in text

    @pytest.mark.asyncio
    async def test_text_looking_values_are_redacted(self, caplog: pytest.LogCaptureFixture) -> None:
        """#179 follow-up: PII (device names, user emails, phone numbers)
        live in SETTINGS sub-keys as raw text bytes. When a user shares a
        DEBUG log publicly to help debug an unmapped device family, those
        values must NOT surface as easily-decodable hex. Replace anything
        that looks like ASCII text with a length-preserving placeholder
        so the byte shape stays visible but the content does not."""
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
                    b"\x05",  # sub-key 5 = SETTINGS_BODY
                    bytes.fromhex("12345678"),
                    b"\x48",
                    b"\x02",
                    bytes.fromhex("AABBCCDD"),
                    b"\x09",
                    b"FIN HAB",  # 7-byte device name = text → redact
                    b"\x02",
                    b"alice@example.com",  # email = text → redact
                    b"\x37",
                    b"\x00\x11\x22\x33",  # numeric, 0x00 NUL not printable → keep hex
                    b"\x73",
                    b"\x00" * 16,  # all-zero counter → keep hex
                ]
            ),
        )

        await client._handle_update(msg)

        probe_lines = [r for r in caplog.records if "#123 probe" in r.getMessage()]
        assert probe_lines, "expected one #123 probe DEBUG line"
        text = probe_lines[0].getMessage()
        # Text values redacted, length preserved.
        assert "0x09=<text:7b>" in text
        assert "0x02=<text:17b>" in text
        # And the raw text bytes are NOT in the log under any encoding.
        assert "FIN HAB" not in text
        assert "alice" not in text
        assert b"FIN HAB".hex() not in text
        assert b"alice@example.com".hex() not in text
        # Numeric / blob values still visible (these are the #179 readings).
        assert "0x37=00112233" in text
        assert "0x73=00000000000000000000000000000000" in text

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


class TestStatusRefreshLoop:
    @pytest.mark.asyncio
    async def test_status_refresh_loop_calls_send_request_full_status_per_hub(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(
            "custom_components.aegis_ajax.api.hts.client.STATUS_REFRESH_INTERVAL",
            0,
        )
        from custom_components.aegis_ajax.api.hts.auth import HubInfo

        client = _make_client()
        client._hubs = [
            HubInfo(hub_id="001C940E", is_master=True),
            HubInfo(hub_id="00B1A532", is_master=False),
        ]
        client._connected = True

        async def fake_send(hub_id: str) -> None:
            # Disconnect after the first cycle so the loop exits.
            client._connected = False

        client._send_request_full_status = AsyncMock(side_effect=fake_send)  # type: ignore[method-assign]

        await client._status_refresh_loop()

        assert client._send_request_full_status.await_count == 2
        hub_ids_called = {call.args[0] for call in client._send_request_full_status.await_args_list}
        assert hub_ids_called == {"001C940E", "00B1A532"}

    @pytest.mark.asyncio
    async def test_status_refresh_loop_keeps_going_on_per_hub_failure(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # One hub raising on send must not break the loop for the other hub
        # nor terminate the periodic refresh — next cycle should still fire.
        monkeypatch.setattr(
            "custom_components.aegis_ajax.api.hts.client.STATUS_REFRESH_INTERVAL",
            0,
        )
        from custom_components.aegis_ajax.api.hts.auth import HubInfo

        client = _make_client()
        client._hubs = [
            HubInfo(hub_id="BROKEN01", is_master=True),
            HubInfo(hub_id="WORKING1", is_master=False),
        ]
        client._connected = True
        call_count = 0

        async def fake_send(hub_id: str) -> None:
            nonlocal call_count
            call_count += 1
            if hub_id == "BROKEN01":
                raise OSError("hub unreachable")
            if call_count >= 2:
                client._connected = False  # exit after first full cycle

        client._send_request_full_status = AsyncMock(side_effect=fake_send)  # type: ignore[method-assign]

        await client._status_refresh_loop()

        # Both hubs called in cycle 1 — broken one didn't abort the cycle.
        assert client._send_request_full_status.await_count == 2


# ---------------------------------------------------------------------------
# _authenticate handshake (was excluded from coverage — see #bypass review)
# ---------------------------------------------------------------------------


def _msg(msg_type: MsgType, payload: bytes, seq: int = 0) -> HtsMessage:
    return HtsMessage(
        sender=1,
        receiver=0,
        seq_num=seq,
        link=0,
        flags=0,
        msg_type=msg_type,
        payload=payload,
    )


def _challenge_msg(a: int = 0x12, b: int = 0x34) -> HtsMessage:
    payload = tlv_encode([bytes([AUTH_KEY_AUTHENTICATION_REQUEST]), bytes([a, b])])
    return _msg(MsgType.AUTHENTICATION, payload, seq=10)


def _connected_msg(seq: int = 20) -> HtsMessage:
    return _msg(MsgType.USER_REGISTRATION, tlv_encode([b"\x00"]), seq=seq)


def _ack_msg() -> HtsMessage:
    return _msg(MsgType.ACK, b"", seq=5)


def _auth_client() -> HtsClient:
    """A client wired so `_authenticate` exercises real crypto on the response
    write path while the message exchange is scripted via `_receive_message`."""
    client = _make_client()
    writer = MagicMock()
    writer.drain = AsyncMock()
    client._writer = writer
    client._send_message = AsyncMock()  # type: ignore[method-assign]
    client._send_ack = AsyncMock()  # type: ignore[method-assign]
    return client


class TestAuthenticate:
    @pytest.mark.asyncio
    async def test_happy_path_sets_connected_state(self) -> None:
        client = _auth_client()
        # Leading ACKs before each real reply exercise both skip loops.
        client._receive_message = AsyncMock(  # type: ignore[method-assign]
            side_effect=[_ack_msg(), _challenge_msg(), _ack_msg(), _connected_msg()]
        )
        fake = MagicMock()
        fake.token = b"\xaa\xbb\xcc\xdd"
        fake.hubs = []
        with patch(
            "custom_components.aegis_ajax.api.hts.client.parse_connected_response",
            return_value=fake,
        ):
            result = await client._authenticate()

        assert result is fake
        assert client._connected is True
        assert client._connection_token == b"\xaa\xbb\xcc\xdd"
        client._send_message.assert_awaited_once()  # USER_REGISTRATION sent
        client._writer.drain.assert_awaited_once()  # challenge response flushed

    @pytest.mark.asyncio
    async def test_adopts_server_seq_range(self) -> None:
        client = _auth_client()
        client._receive_message = AsyncMock(  # type: ignore[method-assign]
            side_effect=[_challenge_msg(), _connected_msg(seq=100)]
        )
        fake = MagicMock(token=b"\x00\x01\x02\x03", hubs=[])
        with patch(
            "custom_components.aegis_ajax.api.hts.client.parse_connected_response",
            return_value=fake,
        ):
            await client._authenticate()
        assert client._seq_num == (100 + 2) & 0xFFFFFF

    @pytest.mark.asyncio
    async def test_non_authentication_reply_raises(self) -> None:
        client = _auth_client()
        client._receive_message = AsyncMock(  # type: ignore[method-assign]
            side_effect=[_connected_msg()]  # USER_REGISTRATION, not AUTHENTICATION
        )
        with pytest.raises(HtsAuthError, match="Expected AUTHENTICATION"):
            await client._authenticate()

    @pytest.mark.asyncio
    async def test_unexpected_auth_key_raises(self) -> None:
        client = _auth_client()
        bad = _msg(MsgType.AUTHENTICATION, tlv_encode([bytes([0x09]), bytes([0x12, 0x34])]))
        client._receive_message = AsyncMock(side_effect=[bad])  # type: ignore[method-assign]
        with pytest.raises(HtsAuthError, match="Unexpected auth request"):
            await client._authenticate()

    @pytest.mark.asyncio
    async def test_short_challenge_raises(self) -> None:
        client = _auth_client()
        short = _msg(
            MsgType.AUTHENTICATION,
            tlv_encode([bytes([AUTH_KEY_AUTHENTICATION_REQUEST]), bytes([0x12])]),
        )
        client._receive_message = AsyncMock(side_effect=[short])  # type: ignore[method-assign]
        with pytest.raises(HtsAuthError, match="Challenge too short"):
            await client._authenticate()

    @pytest.mark.asyncio
    async def test_writer_gone_before_response_raises(self) -> None:
        client = _auth_client()
        client._writer = None
        client._receive_message = AsyncMock(side_effect=[_challenge_msg()])  # type: ignore[method-assign]
        with pytest.raises(HtsConnectionError, match="Not connected"):
            await client._authenticate()

    @pytest.mark.asyncio
    async def test_connected_parse_failure_raises(self) -> None:
        client = _auth_client()
        client._receive_message = AsyncMock(  # type: ignore[method-assign]
            side_effect=[_challenge_msg(), _connected_msg()]
        )
        with (
            patch(
                "custom_components.aegis_ajax.api.hts.client.parse_connected_response",
                side_effect=ValueError("garbage"),
            ),
            pytest.raises(HtsAuthError, match="Failed to parse CONNECTED"),
        ):
            await client._authenticate()

    @pytest.mark.asyncio
    async def test_connected_wrong_type_raises(self) -> None:
        client = _auth_client()
        # Second reply is AUTHENTICATION again (not USER_REGISTRATION).
        client._receive_message = AsyncMock(  # type: ignore[method-assign]
            side_effect=[_challenge_msg(), _challenge_msg()]
        )
        with pytest.raises(HtsAuthError, match="Expected USER_REGISTRATION"):
            await client._authenticate()


# ---------------------------------------------------------------------------
# _read_frame buffer extraction + connect() (mockable asyncio I/O)
# ---------------------------------------------------------------------------


class TestReadFrame:
    @pytest.mark.asyncio
    async def test_extracts_frame_across_chunks(self) -> None:
        client = _make_client()
        reader = MagicMock()
        # Frame split across two reads; trailing bytes stay buffered.
        reader.read = AsyncMock(
            side_effect=[bytes([STX]) + b"AB", b"CD" + bytes([ETX]) + b"leftover"]
        )
        client._reader = reader
        frame = await client._read_frame()
        assert frame == bytes([STX]) + b"ABCD" + bytes([ETX])
        assert bytes(client._read_buf) == b"leftover"

    @pytest.mark.asyncio
    async def test_no_reader_raises(self) -> None:
        client = _make_client()
        client._reader = None
        with pytest.raises(HtsConnectionError, match="Not connected"):
            await client._read_frame()

    @pytest.mark.asyncio
    async def test_remote_close_raises_connection_error(self) -> None:
        client = _make_client()
        reader = MagicMock()
        reader.read = AsyncMock(return_value=b"")  # EOF
        client._reader = reader
        with pytest.raises(ConnectionError, match="closed by remote"):
            await client._read_frame()

    @pytest.mark.asyncio
    async def test_caps_buffer_on_runaway_stream(self) -> None:
        """Bytes keep arriving but no STX…ETX ever completes — the buffer must
        be capped and a reconnect forced, not grown without bound (audit fix)."""
        from custom_components.aegis_ajax.api.hts.client import MAX_FRAME_BUFFER_BYTES

        client = _make_client()
        reader = MagicMock()
        reader.read = AsyncMock(return_value=b"\x00" * 4096)  # never STX/ETX
        client._reader = reader
        with pytest.raises(ConnectionError, match="exceeded"):
            await client._read_frame()
        # Buffer was cleared on the way out, not left to leak.
        assert len(client._read_buf) == 0
        assert MAX_FRAME_BUFFER_BYTES > 0


class TestRedactPayloadHex:
    """`_redact_payload_hex` masks printable-ASCII runs inside a binary dump."""

    def test_masks_embedded_text_run(self) -> None:
        from custom_components.aegis_ajax.api.hts.client import _redact_payload_hex

        out = _redact_payload_hex(b"\x00\x01Deurbel\x02")
        assert "Deurbel" not in out
        assert "<text:7b>" in out
        assert out.startswith("0001")

    def test_pure_binary_stays_hex(self) -> None:
        from custom_components.aegis_ajax.api.hts.client import _redact_payload_hex

        assert _redact_payload_hex(b"\x00\x01\x02") == "000102"

    def test_short_printable_run_not_masked(self) -> None:
        from custom_components.aegis_ajax.api.hts.client import _redact_payload_hex

        out = _redact_payload_hex(b"\x00AB\x00")
        assert "<text:" not in out


class TestConnect:
    @pytest.mark.asyncio
    async def test_connect_failure_wrapped(self) -> None:
        client = _make_client()
        with (
            patch(
                "custom_components.aegis_ajax.api.hts.client.asyncio.open_connection",
                side_effect=OSError("refused"),
            ),
            pytest.raises(HtsConnectionError, match="Cannot connect"),
        ):
            await client.connect()

    @pytest.mark.asyncio
    async def test_connect_runs_authenticate_on_success(self) -> None:
        client = _make_client()
        fake = MagicMock(token=b"\x00\x01\x02\x03", hubs=[])
        with (
            patch(
                "custom_components.aegis_ajax.api.hts.client.asyncio.open_connection",
                return_value=(MagicMock(), MagicMock()),
            ),
            patch.object(client, "_authenticate", AsyncMock(return_value=fake)),
        ):
            result = await client.connect()
        assert result is fake


class TestChimeEvent:
    """`type=0x08` hub Chime-toggle event recognition + callback (#239)."""

    @staticmethod
    def _chime_payload(state_byte: bytes = b"\x39") -> bytes:
        # Mirrors BadFlo's #239 capture: 0x02, 0x22, 0x33 80 41 a4, <state>, …
        return tlv_encode([b"\x02", b"\x22", b"\x33\x80\x41\xa4", state_byte, b"\x00\x00", b"\x00"])

    def test_is_chime_event_true_for_signature(self) -> None:
        from custom_components.aegis_ajax.api.hts.messages import tlv_decode

        params = tlv_decode(self._chime_payload())
        assert HtsClient._is_chime_event(params) is True

    def test_is_chime_event_false_for_other_event(self) -> None:
        from custom_components.aegis_ajax.api.hts.messages import tlv_decode

        params = tlv_decode(tlv_encode([b"\x02", b"\x99", b"\x00"]))
        assert HtsClient._is_chime_event(params) is False

    def test_event_message_fires_callback_with_candidate_byte(self) -> None:
        client = _make_client()
        client._hubs = [MagicMock(hub_id="12345678")]
        cb = MagicMock()
        client._on_chime_event = cb
        msg = HtsMessage(
            sender=0x12345678,
            receiver=client._sender_id,
            seq_num=1,
            link=10,
            flags=0,
            msg_type=0x08,
            payload=self._chime_payload(b"\x39"),
        )

        client._handle_event_message(msg)

        cb.assert_called_once()
        hub_id, payload_hex, candidate = cb.call_args[0]
        assert hub_id == "12345678"
        assert candidate == 0x39
        assert isinstance(payload_hex, str)

    def test_non_chime_event_does_not_fire_callback(self) -> None:
        client = _make_client()
        client._hubs = [MagicMock(hub_id="12345678")]
        cb = MagicMock()
        client._on_chime_event = cb
        msg = HtsMessage(
            sender=0x12345678,
            receiver=client._sender_id,
            seq_num=1,
            link=10,
            flags=0,
            msg_type=0x08,
            payload=tlv_encode([b"\x07", b"\x01"]),
        )

        client._handle_event_message(msg)

        cb.assert_not_called()

    def test_no_callback_set_is_safe(self) -> None:
        client = _make_client()
        client._hubs = [MagicMock(hub_id="12345678")]
        client._on_chime_event = None
        msg = HtsMessage(
            sender=0x12345678,
            receiver=client._sender_id,
            seq_num=1,
            link=10,
            flags=0,
            msg_type=0x08,
            payload=self._chime_payload(),
        )
        # Must not raise.
        client._handle_event_message(msg)
