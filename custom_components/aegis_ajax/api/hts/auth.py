"""Authentication handshake for the Ajax HTS binary protocol.

Implements the challenge-response mechanism and CONNECT_CLIENT_NEW message
building/parsing for the Ajax HTS protocol.
"""

from __future__ import annotations

from dataclasses import dataclass

from custom_components.aegis_ajax.api.hts.messages import (
    AUTH_KEY_CONNECT_CLIENT_NEW,
    AUTH_KEY_CONNECTED,
    tlv_decode,
    tlv_encode,
)
from custom_components.aegis_ajax.const import CLIENT_DEVICE_MODEL, CLIENT_VERSION


@dataclass(frozen=True)
class HubInfo:
    """Information about a hub returned in the connected response.

    Attributes:
        hub_id:    Hub identifier as an uppercase hex string (e.g. "0A1B2C3D").
        is_master: True if this hub is the master hub.
    """

    hub_id: str
    is_master: bool


@dataclass(frozen=True)
class ConnectedResponse:
    """Parsed CONNECTED response from the Ajax server.

    Attributes:
        token: Session token bytes for subsequent authenticated requests.
        hubs:  List of hubs accessible to this account.
    """

    token: bytes
    hubs: list[HubInfo]


def solve_challenge(a: int, b: int) -> bytes:
    """Compute the challenge-response answer.

    Args:
        a: First challenge byte (integer).
        b: Second challenge byte (integer).

    Returns:
        2-byte response: [a ^ b, (b & a) << 2], each masked to 0xFF.
    """
    byte0 = (a ^ b) & 0xFF
    byte1 = ((b & a) << 2) & 0xFF
    return bytes([byte0, byte1])


def build_connect_request(
    login_token: bytes,
    device_id: str,
    app_label: str,
    client_os: str = "Android",
    client_version: str = CLIENT_VERSION,
    connection_type: int = 5,
    device_model: str = CLIENT_DEVICE_MODEL,
) -> bytes:
    """Build the TLV payload for a CONNECT_CLIENT_NEW message (msgType=0x11).

    Uses key=2 (login token) — the session token from gRPC login, as raw bytes.

    TLV params are alternating key-value pairs after the sub-key:
      0x3F (sub-key), 2 (login_token key), token_bytes,
      3, push_type, 4, push_token, 5, isPro,
      7, device_id, 8, app_label, 9, os_type,
      10, version, 11, connection_type, 13, model

    Args:
        login_token:      gRPC session token as raw bytes.
        device_id:        Device identifier string.
        app_label:        Application label string.
        client_os:        OS name string (default "Android").
        client_version:   App version string (default const.CLIENT_VERSION).
        connection_type:  Connection type integer (default 5).
        device_model:     Device model string (default const.CLIENT_DEVICE_MODEL).

    Returns:
        TLV-encoded payload bytes.
    """
    # Device ID includes app_label suffix (e.g. "uuid_Protegim_alarma")
    full_device_id = f"{device_id}_{app_label}"
    params: list[bytes] = [
        bytes([AUTH_KEY_CONNECT_CLIENT_NEW]),  # sub-key 0x3F
        bytes([2]),  # key: login token
        login_token,  # value: raw session token bytes
        # key=3 (push_token_type) and key=4 (push_token) omitted — no push token
        bytes([5]),  # key: isProLoginRequest
        bytes([0]),  # value: false
        bytes([7]),  # key: client device ID
        full_device_id.encode(),  # value: "uuid_AppLabel"
        bytes([8]),  # key: application label
        app_label.encode(),  # value
        bytes([9]),  # key: client OS type
        bytes([0]),  # value: 0
        bytes([10]),  # key: client version
        client_version.encode(),  # value
        bytes([11]),  # key: connection type
        bytes([connection_type]),  # value: 2 = WiFi
        bytes([12]),  # key: client OS
        (client_os + " 14").encode(),  # value: "Android 14"
        bytes([13]),  # key: client device model
        device_model.encode(),  # value
    ]
    return tlv_encode(params)


def parse_connected_response(payload: bytes) -> ConnectedResponse:
    """Parse the TLV payload of a CONNECTED response.

    Expected TLV layout:
      params[0] = bytes([AUTH_KEY_CONNECTED]) (0x0F)
      params[1] = token bytes
      params[2..n] = hub entries, each 5 bytes:
                       [0:4] hub_id as big-endian uint32
                       [4]   is_master flag (non-zero = True)

    Args:
        payload: Raw TLV-encoded payload bytes from the server.

    Returns:
        A ConnectedResponse with token and hub list.

    Raises:
        ValueError: If the payload is malformed or the first param is unexpected.
    """
    params = tlv_decode(payload)

    if len(params) < 2:
        raise ValueError(
            f"CONNECTED response too short: expected at least 2 params, got {len(params)}"
        )

    if params[0] != bytes([AUTH_KEY_CONNECTED]):
        raise ValueError(
            f"Unexpected first param: expected 0x{AUTH_KEY_CONNECTED:02X}, got {params[0].hex()}"
        )

    token = params[1]

    # Hub entries: hub_id (4 bytes) + is_master (1 byte) as separate params
    # Stop parsing when we encounter params that don't look like hub data
    hubs: list[HubInfo] = []
    i = 2
    while i < len(params):
        chunk = params[i]
        if len(chunk) == 4:
            hub_id = chunk.hex().upper()
            is_master = False
            if i + 1 < len(params) and len(params[i + 1]) == 1:
                is_master = params[i + 1][0] != 0
                i += 1
            hubs.append(HubInfo(hub_id=hub_id, is_master=is_master))
            i += 1
        else:
            break  # not hub data, stop parsing

    return ConnectedResponse(token=token, hubs=hubs)
