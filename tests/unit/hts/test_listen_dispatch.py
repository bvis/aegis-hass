"""The receive loop: which handler each incoming message goes to, and what gets acknowledged."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from custom_components.aegis_ajax.api.hts.client import HtsClient
from custom_components.aegis_ajax.api.hts.messages import FLAG_NO_ACK, HtsMessage, MsgType

_EVENT = 0x08
_HANDLERS = {
    MsgType.UPDATES: "_handle_update",
    MsgType.USER_REGISTRATION: "_handle_user_registration_response",
    MsgType.ACK: "_handle_answer",
    _EVENT: "_handle_event_message",
}


def _message(msg_type: int, *, flags: int = 0, seq: int = 1) -> HtsMessage:
    return HtsMessage(
        sender=0x12345678,
        receiver=0xC9D0E1F2,
        seq_num=seq,
        link=10,
        flags=flags,
        msg_type=msg_type,
        payload=b"",
    )


def _listening(*incoming: HtsMessage) -> HtsClient:
    """A connected client that receives ``incoming`` and then loses the connection."""
    client = HtsClient(
        login_token=b"\xde\xad\xbe\xef",
        user_hex_id="C9D0E1F2",
        device_id="device123",
        app_label="com.test.app",
    )
    client._connected = True
    client._receive_message = AsyncMock(  # type: ignore[method-assign]
        side_effect=[*incoming, ConnectionError("closed")]
    )
    client._send_ack = AsyncMock()  # type: ignore[method-assign]
    client._handle_update = AsyncMock()  # type: ignore[method-assign]
    for name in ("_handle_user_registration_response", "_handle_answer", "_handle_event_message"):
        setattr(client, name, MagicMock())
    return client


def _calls(client: HtsClient) -> dict[str, list[HtsMessage]]:
    return {
        name: [call.args[0] for call in getattr(client, name).call_args_list]
        for name in _HANDLERS.values()
    }


def _none_but(**handled: list[HtsMessage]) -> dict[str, list[HtsMessage]]:
    return {**{name: [] for name in _HANDLERS.values()}, **handled}


@pytest.mark.parametrize("msg_type", list(_HANDLERS))
async def test_each_message_type_goes_to_its_own_handler(msg_type: int) -> None:
    msg = _message(msg_type)
    client = _listening(msg)
    await client.listen()
    assert _calls(client) == _none_but(**{_HANDLERS[msg_type]: [msg]})


async def test_an_unknown_message_type_is_skipped_and_the_loop_goes_on() -> None:
    update = _message(MsgType.UPDATES, seq=2)
    client = _listening(_message(MsgType.PING), update)
    await client.listen()
    assert _calls(client) == _none_but(_handle_update=[update])


async def test_messages_are_acknowledged_unless_they_say_not_to() -> None:
    update = _message(MsgType.UPDATES, seq=1)
    silent = _message(MsgType.UPDATES, flags=FLAG_NO_ACK, seq=2)
    ack = _message(MsgType.ACK, seq=3)
    event = _message(_EVENT, seq=4)
    client = _listening(update, silent, ack, event)
    await client.listen()
    assert [call.args[0] for call in client._send_ack.await_args_list] == [update, event]
    # Acknowledged or not, every message is still handled.
    assert _calls(client) == _none_but(
        _handle_update=[update, silent], _handle_answer=[ack], _handle_event_message=[event]
    )


async def test_a_failed_acknowledgement_does_not_drop_the_message() -> None:
    update = _message(MsgType.UPDATES)
    client = _listening(update)
    client._send_ack.side_effect = OSError("write failed")
    await client.listen()
    assert _calls(client) == _none_but(_handle_update=[update])


async def test_listen_keeps_its_callbacks_and_closes_when_the_connection_ends() -> None:
    client = _listening()
    close = client.close
    client.close = AsyncMock(side_effect=close)  # type: ignore[method-assign]
    callbacks = [MagicMock() for _ in range(4)]
    await client.listen(*callbacks)
    assert [
        client._on_state_update,
        client._on_device_kv,
        client._on_chime_event,
        client._on_hub_event,
    ] == callbacks
    client.close.assert_awaited_once()
