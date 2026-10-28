"""Tests for HTS authentication handshake."""

import pytest

from custom_components.aegis_ajax.api.hts.auth import (
    ConnectedResponse,
    build_connect_request,
    parse_connected_response,
    solve_challenge,
)
from custom_components.aegis_ajax.api.hts.messages import (
    AUTH_KEY_CONNECTED,
    tlv_encode,
)


class TestSolveChallenge:
    def test_basic(self) -> None:
        # a=0x0A, b=0x06 -> xor=0x0C, (0x06&0x0A)<<2 = 0x02<<2 = 0x08
        result = solve_challenge(0x0A, 0x06)
        assert result == bytes([0x0A ^ 0x06, ((0x06 & 0x0A) << 2) & 0xFF])

    def test_zero(self) -> None:
        result = solve_challenge(0, 0)
        assert result == bytes([0x00, 0x00])

    def test_all_ones(self) -> None:
        # a=0xFF, b=0xFF -> xor=0x00, (0xFF&0xFF)<<2 = 0xFF<<2 = 0x3FC -> masked 0xFC
        result = solve_challenge(0xFF, 0xFF)
        assert result == bytes([0x00, 0xFC])

    def test_returns_two_bytes(self) -> None:
        result = solve_challenge(0x12, 0x34)
        assert len(result) == 2

    def test_each_byte_masked_to_ff(self) -> None:
        # Verify no byte exceeds 0xFF
        for a in range(0, 256, 17):
            for b in range(0, 256, 13):
                result = solve_challenge(a, b)
                assert all(0 <= x <= 0xFF for x in result)


class TestBuildConnectRequest:
    def test_contains_login_token(self) -> None:
        token = b"\xde\xad\xbe\xef"
        payload = build_connect_request(
            login_token=token,
            device_id="dev1",
            app_label="com.example.app",
        )
        assert token in payload

    def test_contains_app_label(self) -> None:
        payload = build_connect_request(
            login_token=b"\x00" * 16,
            device_id="dev1",
            app_label="com.ajax.security",
        )
        assert b"com.ajax.security" in payload

    def test_contains_device_id(self) -> None:
        payload = build_connect_request(
            login_token=b"\x00" * 4,
            device_id="mydevice",
            app_label="test",
        )
        assert b"mydevice" in payload

    def test_contains_client_version(self) -> None:
        payload = build_connect_request(
            login_token=b"\x00",
            device_id="d",
            app_label="app",
            client_version="3.46",
        )
        assert b"3.46" in payload

    def test_contains_device_model(self) -> None:
        payload = build_connect_request(
            login_token=b"\x00",
            device_id="d",
            app_label="app",
            device_model="SM-S921B",
        )
        assert b"SM-S921B" in payload

    def test_returns_bytes(self) -> None:
        payload = build_connect_request(
            login_token=b"\x00" * 4,
            device_id="d",
            app_label="test",
        )
        assert isinstance(payload, bytes)


class TestParseConnectedResponse:
    def _make_payload(self, token: bytes, hubs: list[tuple[int, bool]]) -> bytes:
        hub_params: list[bytes] = []
        for hub_id_int, is_master in hubs:
            hub_params.append(hub_id_int.to_bytes(4, "big"))
            hub_params.append(bytes([1 if is_master else 0]))
        params = [bytes([AUTH_KEY_CONNECTED]), token] + hub_params
        return tlv_encode(params)

    def test_extracts_token(self) -> None:
        token = b"\xaa\xbb\xcc\xdd"
        payload = self._make_payload(token, [])
        response = parse_connected_response(payload)
        assert response.token == token

    def test_extracts_single_hub(self) -> None:
        token = b"\x01\x02"
        payload = self._make_payload(token, [(0x0A1B2C3D, True)])
        response = parse_connected_response(payload)
        assert len(response.hubs) == 1
        assert response.hubs[0].hub_id == "0A1B2C3D"
        assert response.hubs[0].is_master is True

    def test_extracts_multiple_hubs(self) -> None:
        token = b"\xff"
        payload = self._make_payload(token, [(0x00000001, True), (0x00000002, False)])
        response = parse_connected_response(payload)
        assert len(response.hubs) == 2
        assert response.hubs[0].hub_id == "00000001"
        assert response.hubs[0].is_master is True
        assert response.hubs[1].hub_id == "00000002"
        assert response.hubs[1].is_master is False

    def test_no_hubs(self) -> None:
        token = b"\x99"
        payload = self._make_payload(token, [])
        response = parse_connected_response(payload)
        assert response.token == token
        assert response.hubs == []

    def test_returns_frozen_dataclass(self) -> None:
        payload = self._make_payload(b"\x01", [])
        response = parse_connected_response(payload)
        assert isinstance(response, ConnectedResponse)
        with pytest.raises((AttributeError, TypeError)):
            response.token = b"\x02"  # type: ignore[misc]

    def test_wrong_first_param_raises(self) -> None:
        params = [bytes([0x00]), b"\x01\x02"]  # wrong key
        payload = tlv_encode(params)
        with pytest.raises(ValueError, match="Unexpected first param"):
            parse_connected_response(payload)

    def test_too_short_raises(self) -> None:
        params = [bytes([AUTH_KEY_CONNECTED])]  # only one param
        payload = tlv_encode(params)
        with pytest.raises(ValueError, match="too short"):
            parse_connected_response(payload)

    def test_trailing_param_without_flag_is_not_a_hub(self) -> None:
        payload = tlv_encode(
            [
                bytes([AUTH_KEY_CONNECTED]),
                b"\x01" * 16,
                (0x0A1B2C3D).to_bytes(4, "big"),
                b"\x01",
                bytes.fromhex("4A0A6372"),
            ]
        )
        response = parse_connected_response(payload)
        assert [hub.hub_id for hub in response.hubs] == ["0A1B2C3D"]

    def test_hub_id_uppercase_hex(self) -> None:
        payload = self._make_payload(b"\x01", [(0xDEADBEEF, False)])
        response = parse_connected_response(payload)
        assert response.hubs[0].hub_id == "DEADBEEF"
