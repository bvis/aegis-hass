"""Hub events tied to the right hub, session termination requests, and opening the connection."""

from __future__ import annotations

import ssl
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from custom_components.aegis_ajax.api.hts.client import (
    HtsClient,
    HtsConnectionError,
    HtsTerminationOutcomeUnknownError,
)
from custom_components.aegis_ajax.api.hts.hub_events import HubEvent
from custom_components.aegis_ajax.api.hts.messages import HtsMessage

_CLIENT = "custom_components.aegis_ajax.api.hts.client"
_EVENT = HubEvent(hub_id="AAAA0001", code=0x74, hub_ts=100, expires_at=130, values={7: b"x"})


def _client(*hub_ids: str) -> HtsClient:
    client = HtsClient(
        login_token=b"\xde\xad\xbe\xef",
        user_hex_id="C9D0E1F2",
        device_id="device123",
        app_label="com.test.app",
        host="hts.example",
        port=4433,
    )
    client._hubs = [MagicMock(hub_id=hub_id) for hub_id in hub_ids]
    return client


def _from(sender: int) -> HtsMessage:
    return HtsMessage(
        sender=sender, receiver=0xC9D0E1F2, seq_num=1, link=10, flags=0, msg_type=0x08, payload=b""
    )


def _dispatch(client: HtsClient, msg: HtsMessage, event: HubEvent | None = _EVENT) -> MagicMock:
    client._on_hub_event = MagicMock()
    with patch(f"{_CLIENT}.parse_hub_event", return_value=event) as parse:
        client._dispatch_hub_event(msg, [b"params"])
    parse.assert_called_once_with([b"params"])
    return client._on_hub_event


def test_an_event_naming_a_known_hub_is_passed_on_as_is() -> None:
    callback = _dispatch(_client("AAAA0001", "BBBB0002"), _from(0xBBBB0002))
    callback.assert_called_once_with(_EVENT)


def test_an_event_naming_an_unknown_hub_is_tied_to_the_hub_that_sent_it() -> None:
    callback = _dispatch(_client("BBBB0002", "CCCC0003"), _from(0xCCCC0003))
    callback.assert_called_once_with(
        HubEvent(hub_id="CCCC0003", code=0x74, hub_ts=100, expires_at=130, values={7: b"x"})
    )


def test_an_event_that_fits_no_known_hub_is_dropped() -> None:
    callback = _dispatch(_client("BBBB0002", "CCCC0003"), _from(0x12345678))
    callback.assert_not_called()


def test_an_unparsable_event_is_dropped() -> None:
    _dispatch(_client("AAAA0001"), _from(0xAAAA0001), event=None).assert_not_called()


def test_no_event_is_parsed_without_a_listener_and_a_failing_one_is_contained() -> None:
    client = _client("AAAA0001")
    with patch(f"{_CLIENT}.parse_hub_event") as parse:
        client._dispatch_hub_event(_from(0xAAAA0001), [b"params"])
    parse.assert_not_called()

    client._on_hub_event = MagicMock(side_effect=RuntimeError("listener bug"))
    with patch(f"{_CLIENT}.parse_hub_event", return_value=_EVENT):
        client._dispatch_hub_event(_from(0xAAAA0001), [b"params"])
    client._on_hub_event.assert_called_once_with(_EVENT)


async def test_each_session_is_terminated_by_its_signed_eight_byte_id() -> None:
    client = _client()
    client._request_user_registration = AsyncMock(return_value=[])  # type: ignore[method-assign]
    assert await client.kill_client_sessions([1_000, -5]) == [1_000, -5]
    assert [call.kwargs for call in client._request_user_registration.await_args_list] == [
        {
            "request_key": 0x42,
            "response_key": 0x41,
            "params": [b"\x00\x00\x00\x00\x00\x00\x03\xe8"],
            "termination_session_id": 1_000,
        },
        {
            "request_key": 0x42,
            "response_key": 0x41,
            "params": [b"\xff\xff\xff\xff\xff\xff\xff\xfb"],
            "termination_session_id": -5,
        },
    ]


async def test_an_unconfirmed_termination_reports_which_one_and_what_was_done() -> None:
    client = _client()
    client._request_user_registration = AsyncMock(  # type: ignore[method-assign]
        side_effect=[[], HtsTerminationOutcomeUnknownError(2_000)]
    )
    with pytest.raises(HtsTerminationOutcomeUnknownError) as err:
        await client.kill_client_sessions([1_000, 2_000, 3_000])
    assert err.value.session_id == 2_000
    assert err.value.succeeded_session_ids == [1_000]
    # The third session is never attempted.
    assert [
        (call.kwargs["termination_session_id"], call.kwargs["params"])
        for call in client._request_user_registration.await_args_list
    ] == [
        (1_000, [b"\x00\x00\x00\x00\x00\x00\x03\xe8"]),
        (2_000, [b"\x00\x00\x00\x00\x00\x00\x07\xd0"]),
    ]


async def test_connect_opens_a_verified_tls_connection_to_the_configured_host() -> None:
    client = _client()
    reader, writer, connected = MagicMock(), MagicMock(), MagicMock()
    client._authenticate = AsyncMock(return_value=connected)  # type: ignore[method-assign]
    with patch(
        f"{_CLIENT}.asyncio.open_connection", AsyncMock(return_value=(reader, writer))
    ) as open_connection:
        assert await client.connect() is connected
    open_connection.assert_awaited_once()
    assert open_connection.await_args.args == ("hts.example", 4433)
    (context,) = open_connection.await_args.kwargs.values()
    assert list(open_connection.await_args.kwargs) == ["ssl"]
    assert isinstance(context, ssl.SSLContext)
    assert context.verify_mode is ssl.CERT_REQUIRED
    assert context.check_hostname is True
    assert (client._reader, client._writer) == (reader, writer)


async def test_connect_reports_a_refused_connection_and_closes_after_a_failed_login() -> None:
    client = _client()
    with (
        patch(f"{_CLIENT}.asyncio.open_connection", AsyncMock(side_effect=OSError("refused"))),
        pytest.raises(HtsConnectionError, match=r"Cannot connect to hts\.example:4433: refused"),
    ):
        await client.connect()

    client = _client()
    client._authenticate = AsyncMock(side_effect=RuntimeError("bad login"))  # type: ignore[method-assign]
    client.close = AsyncMock()  # type: ignore[method-assign]
    with (
        patch(
            f"{_CLIENT}.asyncio.open_connection",
            AsyncMock(return_value=(MagicMock(), MagicMock())),
        ),
        pytest.raises(RuntimeError, match="bad login"),
    ):
        await client.connect()
    client.close.assert_awaited_once()
