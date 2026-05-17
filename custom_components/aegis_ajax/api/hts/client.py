"""Async TCP+TLS client for the Ajax HTS binary protocol."""

from __future__ import annotations

import asyncio
import contextlib
import logging
import ssl
from typing import TYPE_CHECKING

from custom_components.aegis_ajax.api.hts.auth import (
    ConnectedResponse,
    build_connect_request,
    parse_connected_response,
    solve_challenge,
)
from custom_components.aegis_ajax.api.hts.crypto import decrypt, encrypt
from custom_components.aegis_ajax.api.hts.hub_state import (
    KEY_ACTIVE_CHANNELS,
    KEY_ETH_ENABLED,
    KEY_GPRS_ENABLED,
    KEY_HUB_POWERED,
    KEY_WIFI_ENABLED,
    HubNetworkState,
    parse_hub_params,
)
from custom_components.aegis_ajax.api.hts.messages import (
    ACK_KEY_RECEIVED,
    AUTH_KEY_AUTHENTICATION_REQUEST,
    AUTH_KEY_AUTHENTICATION_RESPONSE,
    HtsMessage,
    MsgType,
    build_message,
    parse_message,
    tlv_decode,
    tlv_encode,
)
from custom_components.aegis_ajax.api.hts.protocol import (
    ETX,
    STX,
    decode_frame,
    encode_frame,
    pad16,
)

if TYPE_CHECKING:
    from collections.abc import Callable

_LOGGER = logging.getLogger(__name__)

HTS_HOST = "hts.prod.ajax.systems"
HTS_PORT = 443
PING_INTERVAL = 30
READ_TIMEOUT = 40
# Bound the full 4-step auth handshake. Without this, a server that keeps the
# TCP connection alive but feeds bytes slowly can keep `_receive_message()`'s
# per-chunk reads under READ_TIMEOUT forever, so the coroutine never resolves.
AUTH_TIMEOUT = 20
# Tolerance for idle HTS connections in `listen()`: a healthy server can stay
# quiet beyond READ_TIMEOUT, so we only close the connection after this many
# back-to-back read timeouts with no inbound data (#76).
MAX_CONSECUTIVE_READ_TIMEOUTS = 3


class HtsConnectionError(Exception):
    """Raised when the TCP/TLS connection fails."""


class HtsAuthError(Exception):
    """Raised when the authentication handshake fails."""


class HtsClient:
    """Async TCP+TLS client for the Ajax HTS binary protocol."""

    _ssl_ctx: ssl.SSLContext | None = None

    def __init__(
        self,
        login_token: bytes,
        user_hex_id: str,
        device_id: str,
        app_label: str,
        host: str = HTS_HOST,
        port: int = HTS_PORT,
    ) -> None:
        self._login_token = login_token
        self._user_hex_id = user_hex_id
        self._device_id = device_id
        self._app_label = app_label
        self._host = host
        self._port = port

        self._reader: asyncio.StreamReader | None = None
        self._writer: asyncio.StreamWriter | None = None
        self._connected = False
        self._seq_num = 1

        self._sender_id: int = int(user_hex_id, 16) if user_hex_id else 0
        self._receiver_id: int = 0
        self._connection_token: bytes = b""
        from custom_components.aegis_ajax.api.hts.auth import HubInfo  # noqa: PLC0415

        self._hubs: list[HubInfo] = []

        self._ping_task: asyncio.Task[None] | None = None
        self._data_request_task: asyncio.Task[None] | None = None
        self._read_buf = bytearray()
        self._consecutive_read_timeouts = 0
        self._hub_states: dict[str, HubNetworkState] = {}
        self._on_state_update: Callable[[str, HubNetworkState], None] | None = None
        # Per-device kv callback wired by the coordinator for #123. The
        # client itself does not know which devices emit electrical
        # readings — it just forwards every non-hub kv block from a
        # STATUS/SETTINGS body and lets the coordinator filter by type.
        self._on_device_kv: Callable[[str, str, dict[int, bytes]], None] | None = None
        self._refresh_tasks: dict[str, asyncio.Task[None]] = {}

    # ------------------------------------------------------------------
    # Properties
    # ------------------------------------------------------------------

    @property
    def is_connected(self) -> bool:
        """True when the client is authenticated and connected."""
        return self._connected

    @property
    def hub_states(self) -> dict[str, HubNetworkState]:
        """Current hub network states, keyed by hub_id."""
        return self._hub_states

    # ------------------------------------------------------------------
    # Sequence number
    # ------------------------------------------------------------------

    def _next_seq(self) -> int:
        """Return the current sequence number and advance it, wrapping at 0xFFFFFF."""
        seq = self._seq_num
        self._seq_num = (self._seq_num + 1) & 0xFFFFFF
        return seq

    # ------------------------------------------------------------------
    # Connection
    # ------------------------------------------------------------------

    async def connect(self) -> ConnectedResponse:
        """Open a TCP+TLS connection and perform authentication.

        Returns:
            ConnectedResponse on success.

        Raises:
            HtsConnectionError: If the TCP/TLS connection cannot be established.
            HtsAuthError: If the auth handshake fails.
        """
        if HtsClient._ssl_ctx is None:
            HtsClient._ssl_ctx = ssl.create_default_context()
        ssl_ctx = HtsClient._ssl_ctx
        try:
            self._reader, self._writer = await asyncio.wait_for(
                asyncio.open_connection(self._host, self._port, ssl=ssl_ctx),
                timeout=10,
            )
        except (TimeoutError, OSError) as exc:
            raise HtsConnectionError(f"Cannot connect to {self._host}:{self._port}: {exc}") from exc

        try:
            return await asyncio.wait_for(self._authenticate(), timeout=AUTH_TIMEOUT)
        except TimeoutError as exc:
            await self.close()
            raise HtsConnectionError(f"HTS auth handshake timed out after {AUTH_TIMEOUT}s") from exc
        except Exception:
            await self.close()
            raise

    # ------------------------------------------------------------------
    # Authentication
    # ------------------------------------------------------------------

    async def _authenticate(self) -> ConnectedResponse:
        """Perform the 4-step HTS auth handshake.

        Returns:
            ConnectedResponse with session token and hub list.

        Raises:
            HtsAuthError: On any handshake error.
        """
        # Step 1: send USER_REGISTRATION with CONNECT_CLIENT_NEW payload
        payload = build_connect_request(
            login_token=self._login_token,
            device_id=self._device_id,
            app_label=self._app_label,
        )
        await self._send_message(MsgType.USER_REGISTRATION, payload)

        # Step 2: receive AUTHENTICATION msg with challenge (skip ACKs)
        auth_req = await self._receive_message()
        while auth_req.msg_type == MsgType.ACK:
            _LOGGER.debug("Skipping ACK during auth handshake")
            auth_req = await self._receive_message()
        if auth_req.msg_type != MsgType.AUTHENTICATION:
            raise HtsAuthError(f"Expected AUTHENTICATION msg, got 0x{int(auth_req.msg_type):02X}")

        params = tlv_decode(auth_req.payload)
        _LOGGER.debug(
            "Auth request: %d params, payload=%db",
            len(params),
            len(auth_req.payload),
        )

        # params[0] should be AUTH_KEY_AUTHENTICATION_REQUEST (0x00)
        # params[1] should be the 2-byte challenge
        if not params or params[0] != bytes([AUTH_KEY_AUTHENTICATION_REQUEST]):
            raise HtsAuthError(f"Unexpected auth request: {[p.hex() for p in params]}")
        if len(params) < 2 or len(params[1]) < 2:
            raise HtsAuthError(f"Challenge too short: {[p.hex() for p in params]}")

        challenge_a = params[1][0]
        challenge_b = params[1][1]

        # ACK the auth challenge (required before sending response)
        await self._send_ack(auth_req)

        # Step 3: send AUTHENTICATION with challenge response
        response_bytes = solve_challenge(challenge_a, challenge_b)
        _LOGGER.debug(
            "Challenge: a=0x%02X b=0x%02X → response=0x%s",
            challenge_a,
            challenge_b,
            response_bytes.hex(),
        )
        auth_resp_payload = tlv_encode(
            [bytes([AUTH_KEY_AUTHENTICATION_RESPONSE]), response_bytes]
        )  # tlv_encode adds trailing delimiter
        # Build the auth response message manually for exact control
        auth_resp_msg = HtsMessage(
            sender=self._sender_id,
            receiver=0,
            seq_num=self._next_seq(),
            link=0,
            flags=0,
            msg_type=MsgType.AUTHENTICATION,
            payload=auth_resp_payload,
        )
        raw = build_message(auth_resp_msg)
        padded = pad16(raw)
        encrypted = encrypt(padded)
        frame = encode_frame(encrypted)
        _LOGGER.debug(
            "Auth response: raw=%db padded=%db frame=%db", len(raw), len(padded), len(frame)
        )
        if self._writer is None:
            raise HtsConnectionError("Not connected")
        self._writer.write(frame)
        await self._writer.drain()

        # Step 4: receive USER_REGISTRATION (CONNECTED) response (skip ACKs)
        connected_msg = await self._receive_message()
        while connected_msg.msg_type == MsgType.ACK:
            _LOGGER.debug("Skipping ACK during auth handshake")
            connected_msg = await self._receive_message()

        # Adopt the server's seq range before ACKing
        self._seq_num = (connected_msg.seq_num + 2) & 0xFFFFFF
        _LOGGER.debug(
            "Adopting server seq range: connected seq=%d, our next seq=%d",
            connected_msg.seq_num,
            self._seq_num,
        )
        await self._send_ack(connected_msg)

        if connected_msg.msg_type != MsgType.USER_REGISTRATION:
            raise HtsAuthError(
                f"Expected USER_REGISTRATION (CONNECTED) msg, "
                f"got 0x{int(connected_msg.msg_type):02X}"
            )

        params2 = tlv_decode(connected_msg.payload)
        _LOGGER.debug(
            "Connected response: %d params, payload=%db", len(params2), len(connected_msg.payload)
        )

        try:
            connected = parse_connected_response(connected_msg.payload)
        except ValueError as exc:
            raise HtsAuthError(f"Failed to parse CONNECTED response: {exc}") from exc

        self._connection_token = connected.token
        self._hubs = connected.hubs
        self._connected = True

        _LOGGER.debug(
            "HTS authenticated: %d hub(s), token=%s...",
            len(connected.hubs),
            connected.token[:4].hex(),
        )
        return connected

    # ------------------------------------------------------------------
    # Send / receive
    # ------------------------------------------------------------------

    async def _send_message(self, msg_type: MsgType, payload: bytes) -> None:
        """Build, encrypt and send an HTS message."""
        msg = HtsMessage(
            sender=self._sender_id,
            receiver=self._receiver_id,
            seq_num=self._next_seq(),
            link=0,
            flags=0,
            msg_type=msg_type,
            payload=payload,
        )
        raw = build_message(msg)
        padded = pad16(raw)
        encrypted = encrypt(padded)
        frame = encode_frame(encrypted)
        _LOGGER.debug(
            "SEND: type=0x%02X seq=%d raw=%db padded=%db enc=%db frame=%db",
            int(msg_type),
            msg.seq_num,
            len(raw),
            len(padded),
            len(encrypted),
            len(frame),
        )
        if self._writer is None:
            raise HtsConnectionError("Not connected")
        self._writer.write(frame)
        await self._writer.drain()

    async def _send_response(
        self,
        original: HtsMessage,
        msg_type: MsgType,
        payload: bytes,
    ) -> None:
        """Send a response message, swapping sender/receiver from original."""
        msg = HtsMessage(
            sender=original.receiver,
            receiver=original.sender,
            seq_num=self._next_seq(),
            link=original.link,
            flags=0,
            msg_type=msg_type,
            payload=payload,
        )
        raw = build_message(msg)
        padded = pad16(raw)
        encrypted = encrypt(padded)
        frame = encode_frame(encrypted)
        _LOGGER.debug(
            "SEND response: type=0x%02X seq=%d frame=%db",
            int(msg_type),
            msg.seq_num,
            len(frame),
        )
        if self._writer is None:
            raise HtsConnectionError("Not connected")
        self._writer.write(frame)
        await self._writer.drain()

    async def _send_ack(self, original: HtsMessage) -> None:
        """Send an ACK for *original*."""
        ack_payload = tlv_encode(
            [bytes([ACK_KEY_RECEIVED]), original.seq_num.to_bytes(3, "big")]
        )  # tlv_encode includes trailing delimiter
        msg = HtsMessage(
            sender=self._sender_id,
            receiver=self._receiver_id,
            seq_num=self._next_seq(),
            link=original.link,
            flags=0,
            msg_type=MsgType.ACK,
            payload=ack_payload,
        )
        raw = build_message(msg)
        padded = pad16(raw)
        encrypted = encrypt(padded)
        frame = encode_frame(encrypted)
        if self._writer is None:
            raise HtsConnectionError("Not connected")
        self._writer.write(frame)
        await self._writer.drain()

    async def _receive_message(self) -> HtsMessage:
        """Read and decode the next message from the stream."""
        frame = await self._read_frame()
        body = decode_frame(frame)
        plaintext = decrypt(body)
        return parse_message(plaintext)

    async def _read_frame(self) -> bytes:
        """Read a complete STX...ETX frame using buffered chunk reads."""
        if self._reader is None:
            raise HtsConnectionError("Not connected")

        while True:
            # Try to extract a frame from the existing buffer
            stx_pos = self._read_buf.find(STX)
            if stx_pos != -1:
                etx_pos = self._read_buf.find(ETX, stx_pos + 1)
                if etx_pos != -1:
                    frame = bytes(self._read_buf[stx_pos : etx_pos + 1])
                    del self._read_buf[: etx_pos + 1]
                    return frame

            # Need more data — read a chunk
            chunk = await asyncio.wait_for(
                self._reader.read(4096),
                timeout=READ_TIMEOUT,
            )
            if not chunk:
                raise ConnectionError("Connection closed by remote")
            self._read_buf.extend(chunk)

    # ------------------------------------------------------------------
    # Listen loop
    # ------------------------------------------------------------------

    async def request_hub_data(self, hub_id: str) -> None:
        """Send REQUEST_FULL_SETTINGS *and* REQUEST_FULL_STATUS.

        Used at startup / after the unknown-update fallback path. The
        periodic refresh loop (#123) uses the lighter
        `_send_request_full_status` alone — SETTINGS is ~6 KB and only
        carries config that does not change at runtime, so requesting
        it every 60s would be pure waste.
        """
        await self._send_request_full_settings(hub_id)
        await self._send_request_full_status(hub_id)

    async def _send_request_full_settings(self, hub_id: str) -> None:
        """REQUEST_FULL_SETTINGS (sub-key=3) — heavy, ~6 KB response."""
        await self._send_request_payload(hub_id, sub_key=3, label="REQUEST_FULL_SETTINGS")

    async def _send_request_full_status(self, hub_id: str) -> None:
        """REQUEST_FULL_STATUS (sub-key=7) — lighter, ~2.7 KB response with live readings."""
        await self._send_request_payload(hub_id, sub_key=7, label="REQUEST_FULL_STATUS")

    async def _send_request_payload(self, hub_id: str, *, sub_key: int, label: str) -> None:
        """Generic 3-param REQUEST sender shared by SETTINGS and STATUS variants."""
        if self._writer is None:
            raise HtsConnectionError("Not connected")
        hub_id_int = int(hub_id, 16)
        payload = tlv_encode([bytes([sub_key]), bytes([1]), bytes([1])])
        msg = HtsMessage(
            sender=self._sender_id,
            receiver=hub_id_int,
            seq_num=self._next_seq(),
            link=10,
            flags=0,
            msg_type=MsgType.UPDATES,
            payload=payload,
        )
        raw = build_message(msg)
        padded = pad16(raw)
        encrypted = encrypt(padded)
        frame = encode_frame(encrypted)
        if self._writer is None:
            raise HtsConnectionError("Not connected")
        self._writer.write(frame)
        await self._writer.drain()
        _LOGGER.debug("Sent %s to %s", label, hub_id)

    async def listen(
        self,
        on_state_update: Callable[[str, HubNetworkState], None] | None = None,
        on_device_kv: Callable[[str, str, dict[int, bytes]], None] | None = None,
    ) -> None:
        """Main receive loop: ACK messages and dispatch UPDATES.

        Args:
            on_state_update: Optional callback invoked with (hub_id, state) whenever
                             a hub state changes.
            on_device_kv: Optional callback invoked with (hub_id, device_id_hex, kv)
                          once per non-hub device row contained in a STATUS_BODY or
                          SETTINGS_BODY message. `device_id_hex` is upper-case to
                          match `coordinator.devices` keys. The coordinator decides
                          which device types consume the kv (#123 electrical
                          readings live here).
        """
        self._on_state_update = on_state_update
        self._on_device_kv = on_device_kv
        self._ping_task = asyncio.create_task(self._ping_loop())

        # Request hub data immediately (connection is stable now)
        async def _request_data() -> None:
            await asyncio.sleep(0.1)
            for hub in self._hubs:
                try:
                    await self.request_hub_data(hub.hub_id)
                except Exception as e:
                    _LOGGER.warning("Failed to request hub data: %s", e)

        self._data_request_task = asyncio.create_task(_request_data())

        try:
            while self._connected:
                try:
                    msg = await self._receive_message()
                except TimeoutError:
                    self._consecutive_read_timeouts += 1
                    if self._consecutive_read_timeouts >= MAX_CONSECUTIVE_READ_TIMEOUTS:
                        _LOGGER.warning(
                            "HTS read timeout %d times in a row; closing connection",
                            self._consecutive_read_timeouts,
                        )
                        break
                    _LOGGER.debug(
                        "HTS read timeout %d/%d with no inbound data; keeping connection open",
                        self._consecutive_read_timeouts,
                        MAX_CONSECUTIVE_READ_TIMEOUTS,
                    )
                    continue
                except ConnectionError as exc:
                    _LOGGER.warning("HTS connection error in listen: %s", exc)
                    break
                self._consecutive_read_timeouts = 0

                if not msg.is_no_ack and msg.msg_type != MsgType.ACK:
                    try:
                        await self._send_ack(msg)
                        _LOGGER.debug("  ACK sent for seq=%d", msg.seq_num)
                    except Exception as e:
                        _LOGGER.warning("  ACK failed: %s", e)

                _LOGGER.debug(
                    "RECV: type=0x%02X seq=%d sender=%08X link=%d payload=%db",
                    int(msg.msg_type),
                    msg.seq_num,
                    msg.sender,
                    msg.link,
                    len(msg.payload),
                )
                if msg.msg_type == MsgType.UPDATES:
                    await self._handle_update(msg)
                elif msg.msg_type == MsgType.ACK:
                    pass  # expected
                else:
                    _LOGGER.debug("  payload hex: %s", msg.payload[:80].hex())
        finally:
            await self.close()

    # ------------------------------------------------------------------
    # Update handler
    # ------------------------------------------------------------------

    async def _handle_update(self, msg: HtsMessage) -> None:
        """Parse an UPDATES message and update hub state."""
        # Belt-and-suspenders for #108: even with the lenient
        # `tlv_unescape_param` (preserves unknown 0x06 0xNN pairs), a
        # future parser bug or a truly garbled payload should not kill
        # the listen loop and silently take down hub-network sensors
        # for hours. Drop the offending message, log payload hex for
        # post-mortem, and let the next update flow normally.
        try:
            params = tlv_decode(msg.payload)
        except Exception:
            _LOGGER.debug(
                "Failed to decode UPDATES payload (first 80 bytes: %s) — dropping message",
                msg.payload[:80].hex(),
                exc_info=True,
            )
            return
        if not params:
            return

        sub_key = params[0][0] if params[0] else 0
        hub_id = self._hub_id_from_message(msg)

        # SETTINGS_BODY (5) and STATUS_BODY (9) contain data for all devices.
        # Hub data is preceded by the hub_id (4 bytes) as a marker param.
        if sub_key in (5, 9):
            if not hub_id:
                return
            hub_id_bytes = bytes.fromhex(hub_id)
            kv = self._extract_device_kv(params, hub_id_bytes)
            # Walk every device row in the body once. Two consumers:
            #   1. #123 readings — emit each non-hub kv via on_device_kv
            #      so the coordinator can parse it as `DeviceReadings`
            #      (current_ma / power_consumed_wh for WallSwitch and
            #      the Socket family). The client itself stays
            #      device-type-agnostic.
            #   2. DEBUG probe — log the sub-keys per device when
            #      DEBUG logging is on for this module, so the post-
            #      mortem of an unfamiliar device family is one log
            #      sample away. Default-level installs pay nothing.
            non_hub: list[tuple[bytes, dict[int, bytes]]] = [
                (did, kvs)
                for did, kvs in self._extract_all_devices_kv(params)
                if did != hub_id_bytes
            ]
            if self._on_device_kv is not None:
                for did, kvs in non_hub:
                    if not kvs:
                        continue
                    try:
                        self._on_device_kv(hub_id, did.hex().upper(), kvs)
                    except Exception:  # noqa: BLE001
                        _LOGGER.exception(
                            "on_device_kv callback raised for hub %s device %s",
                            hub_id,
                            did.hex().upper(),
                        )
            if _LOGGER.isEnabledFor(logging.DEBUG):
                body_label = "SETTINGS_BODY" if sub_key == 5 else "STATUS_BODY"
                if non_hub:
                    summary = ", ".join(
                        f"{did.hex().upper()}=["
                        + ",".join(f"0x{k:02x}({len(kvs[k])}b)" for k in sorted(kvs))
                        + "]"
                        for did, kvs in non_hub
                    )
                    _LOGGER.debug(
                        "Hub %s: %s non-hub devices (#123 probe): %s",
                        hub_id,
                        body_label,
                        summary,
                    )
            if kv:
                _LOGGER.debug(
                    "Hub %s: parsed %d keys from %s",
                    hub_id,
                    len(kv),
                    "SETTINGS_BODY" if sub_key == 5 else "STATUS_BODY",
                )
                existing = self._hub_states.get(hub_id)
                new_state = parse_hub_params(kv, existing)
                self._hub_states[hub_id] = new_state
                if self._on_state_update:
                    self._on_state_update(hub_id, new_state)
            return

        if not hub_id:
            return

        kv = self._extract_direct_kv(params[1:])
        if kv and self._is_network_state_delta(kv):
            _LOGGER.debug(
                "Hub %s: parsed %d keys from delta sub-key %d",
                hub_id,
                len(kv),
                sub_key,
            )
            existing = self._hub_states.get(hub_id)
            new_state = parse_hub_params(kv, existing)
            self._hub_states[hub_id] = new_state
            if self._on_state_update:
                self._on_state_update(hub_id, new_state)
            return

        # Sub-keys 11 (STATUS_UPDATE) and 12 (SETTINGS_UPDATE) are the
        # hub's per-device push channels: the hub emits one of these
        # whenever any single device's status (11) or settings (12)
        # change. Payload shape is identical to one device's row inside
        # a body — `[sub_key, device_id_4b, k1, v1, k2, v2, ...]` — so
        # the same `_extract_all_devices_kv` walker pulls out the
        # device id + kv block. Routed through `on_device_kv` so the
        # coordinator's existing per-device handler (#123) consumes
        # both the boot-time STATUS_BODY snapshot and these live pushes
        # via one code path.
        #
        # Bandwidth note: longer hub-network variants of these sub-keys
        # (~50 bytes) were previously diverted to `_extract_direct_kv`
        # above. That path still fires before this one and handles
        # hub-network deltas. Anything reaching here is a per-device
        # delta; if `kvs` is empty (e.g. firmware-internal heartbeat)
        # the helper just skips. This subsumes the #111 `sub_key == 11
        # return` drop — the issue there was firing `_schedule_hub_
        # refresh` on every heartbeat (~8.6 KB round-trip), not the
        # silent drop itself. Now we read the delta in-place.
        if sub_key in (11, 12) and hub_id:
            non_hub = [
                (did, kvs)
                for did, kvs in self._extract_all_devices_kv(params)
                if did != bytes.fromhex(hub_id)
            ]
            if self._on_device_kv is not None:
                for did, kvs in non_hub:
                    if not kvs:
                        continue
                    try:
                        self._on_device_kv(hub_id, did.hex().upper(), kvs)
                    except Exception:  # noqa: BLE001
                        _LOGGER.exception(
                            "on_device_kv callback raised for hub %s device %s",
                            hub_id,
                            did.hex().upper(),
                        )
            if _LOGGER.isEnabledFor(logging.DEBUG) and non_hub:
                update_label = "STATUS_UPDATE" if sub_key == 11 else "SETTINGS_UPDATE"
                summary = ", ".join(
                    f"{did.hex().upper()}=["
                    + ",".join(f"0x{k:02x}({len(kvs[k])}b)" for k in sorted(kvs))
                    + "]"
                    for did, kvs in non_hub
                )
                _LOGGER.debug(
                    "Hub %s: %s push (#123): %s",
                    hub_id,
                    update_label,
                    summary,
                )
            return

        self._schedule_hub_refresh(hub_id, f"unknown update sub-key {sub_key}")

    def _hub_id_from_message(self, msg: HtsMessage) -> str | None:
        """Return the hub id when the message is clearly associated with one hub."""
        known_hubs = {hub.hub_id for hub in self._hubs}
        for endpoint in (msg.sender, msg.receiver):
            hub_id = f"{endpoint:08X}"
            if hub_id in known_hubs:
                return hub_id
        if len(self._hubs) == 1:
            return self._hubs[0].hub_id
        return None

    @staticmethod
    def _extract_direct_kv(params: list[bytes]) -> dict[int, bytes]:
        """Extract alternating 1-byte key/value pairs from a direct delta payload."""
        kv: dict[int, bytes] = {}
        i = 0
        while i + 1 < len(params):
            key_p = params[i]
            val_p = params[i + 1]
            if len(key_p) == 1:
                kv[key_p[0]] = val_p
            i += 2
        return kv

    @staticmethod
    def _is_network_state_delta(kv: dict[int, bytes]) -> bool:
        """Return True when the parsed delta contains HTS hub-network keys."""
        return any(
            key in kv
            for key in (
                KEY_ACTIVE_CHANNELS,
                KEY_ETH_ENABLED,
                KEY_WIFI_ENABLED,
                KEY_GPRS_ENABLED,
                KEY_HUB_POWERED,
            )
        )

    def _schedule_hub_refresh(self, hub_id: str, reason: str) -> None:
        """Refresh one hub state once when an unparsed hub update arrives."""
        existing = self._refresh_tasks.get(hub_id)
        if existing and not existing.done():
            return

        async def _refresh() -> None:
            try:
                _LOGGER.debug("Hub %s: requesting fresh HTS snapshot after %s", hub_id, reason)
                await self.request_hub_data(hub_id)
            except Exception:
                _LOGGER.debug("Hub %s: HTS snapshot refresh failed", hub_id, exc_info=True)
            finally:
                self._refresh_tasks.pop(hub_id, None)

        task = asyncio.create_task(_refresh())
        self._refresh_tasks[hub_id] = task

    @staticmethod
    def _extract_device_kv(
        params: list[bytes],
        device_id: bytes,
    ) -> dict[int, bytes]:
        """Extract key-value pairs for a specific device from a body dump.

        The body contains entries for multiple devices. Each device section
        starts with a 4-byte device ID param, followed by alternating
        key/value params until the next 4-byte device ID.
        """
        # Find the device_id marker
        start = None
        for i, p in enumerate(params):
            if p == device_id:
                start = i + 1
                break
        if start is None:
            return {}

        kv: dict[int, bytes] = {}
        i = start
        while i + 1 < len(params):
            key_p = params[i]
            val_p = params[i + 1]
            # Next device starts with a 4-byte ID (and it's not the first entry)
            if len(key_p) == 4 and i > start:
                break
            if len(key_p) == 1:
                kv[key_p[0]] = val_p
            # Skip 2-byte keys (extended keys we don't need yet)
            i += 2
        return kv

    @staticmethod
    def _extract_all_devices_kv(
        params: list[bytes],
    ) -> list[tuple[bytes, dict[int, bytes]]]:
        """Walk the whole body and emit per-device kv tuples.

        Generalises `_extract_device_kv`, which only returns the section
        belonging to one specific device id. The full body is a flat
        list shaped as

            [sub_key, marker_A, k1, v1, k2, v2, ..., marker_B, k1, v1, ...]

        where every 4-byte param is a device id marker and the 1-byte
        params between markers are sub-keys (with the next param as the
        value). 2-byte extended keys are skipped on purpose — same rule
        as `_extract_device_kv`. Orphan params before the first marker
        (the leading sub_key byte, malformed prefixes) are skipped too.

        Returns a list preserving the body's encounter order so callers
        can distinguish the hub's section (always first today) from
        per-device sections.
        """
        result: list[tuple[bytes, dict[int, bytes]]] = []
        current_id: bytes | None = None
        current_kv: dict[int, bytes] = {}
        i = 0
        while i < len(params):
            p = params[i]
            if len(p) == 4:
                if current_id is not None:
                    result.append((current_id, current_kv))
                current_id = p
                current_kv = {}
                i += 1
                continue
            if current_id is None:
                i += 1
                continue
            if i + 1 >= len(params):
                break
            val_p = params[i + 1]
            if len(p) == 1:
                current_kv[p[0]] = val_p
            i += 2
        if current_id is not None:
            result.append((current_id, current_kv))
        return result

    # ------------------------------------------------------------------
    # Ping
    # ------------------------------------------------------------------

    async def _ping_loop(self) -> None:
        """Send a PING every PING_INTERVAL seconds while connected."""
        while self._connected:
            await asyncio.sleep(PING_INTERVAL)
            if self._connected:
                try:
                    await self._send_message(MsgType.PING, b"")
                except Exception as exc:  # noqa: BLE001
                    _LOGGER.warning("HTS ping failed; closing connection: %s", exc)
                    self._connected = False
                    break

    # ------------------------------------------------------------------
    # Close
    # ------------------------------------------------------------------

    async def close(self) -> None:
        """Disconnect cleanly."""
        self._connected = False
        if self._data_request_task is not None:
            self._data_request_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._data_request_task
            self._data_request_task = None
        refresh_tasks = list(self._refresh_tasks.values())
        self._refresh_tasks.clear()
        for task in refresh_tasks:
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await task
        if self._ping_task is not None:
            self._ping_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._ping_task
            self._ping_task = None
        if self._writer is not None:
            with contextlib.suppress(Exception):
                self._writer.close()
                await self._writer.wait_closed()
            self._writer = None
        self._reader = None
        self._read_buf.clear()
