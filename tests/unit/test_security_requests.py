"""What each arm/disarm command actually sends, and how it reads the answer.

These use the real request and response messages and replace only the gRPC
stub, so a command aimed at the wrong space, a flipped ``ignore_alarms`` or a
misread failure cannot pass.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from systems.ajax.api.mobile.v2.space.security import (
    arm_request_pb2,
    arm_to_night_mode_request_pb2,
    disarm_from_night_mode_request_pb2,
    disarm_request_pb2,
)
from systems.ajax.api.mobile.v2.space.security.group import (
    arm_group_request_pb2,
    disarm_group_request_pb2,
)

from custom_components.aegis_ajax.api.security import SecurityApi, SecurityError

if TYPE_CHECKING:
    from google.protobuf.message import Message

_STUB = (
    "systems.ajax.api.mobile.v2.space.security."
    "space_security_endpoints_pb2_grpc.SpaceSecurityServiceStub"
)
_METADATA = [("token", "abc")]

# command -> (stub method, request message, response message, takes a group, takes ignore_alarms)
_COMMANDS: dict[str, tuple[str, Any, Any, bool, bool]] = {
    "arm": ("arm", arm_request_pb2.ArmSpaceRequest, arm_request_pb2.ArmSpaceResponse, False, True),
    "disarm": (
        "disarm",
        disarm_request_pb2.DisarmSpaceRequest,
        disarm_request_pb2.DisarmSpaceResponse,
        False,
        False,
    ),
    "arm_night_mode": (
        "armToNightMode",
        arm_to_night_mode_request_pb2.ArmSpaceToNightModeRequest,
        arm_to_night_mode_request_pb2.ArmSpaceToNightModeResponse,
        False,
        True,
    ),
    "disarm_from_night_mode": (
        "disarmFromNightMode",
        disarm_from_night_mode_request_pb2.DisarmSpaceFromNightModeRequest,
        disarm_from_night_mode_request_pb2.DisarmSpaceFromNightModeResponse,
        False,
        False,
    ),
    "arm_group": (
        "armGroup",
        arm_group_request_pb2.ArmSpaceGroupRequest,
        arm_group_request_pb2.ArmSpaceGroupResponse,
        True,
        True,
    ),
    "disarm_group": (
        "disarmGroup",
        disarm_group_request_pb2.DisarmSpaceGroupRequest,
        disarm_group_request_pb2.DisarmSpaceGroupResponse,
        True,
        False,
    ),
}
_WITH_IGNORE = [name for name, spec in _COMMANDS.items() if spec[4]]


def _success(response_cls: type[Message]) -> Message:
    response = response_cls()
    response.success.SetInParent()
    return response


def _failure(response_cls: type[Message], error: str | None) -> Message:
    response = response_cls()
    response.failure.SetInParent()
    if error:
        getattr(response.failure, error).SetInParent()
    return response


class _Harness:
    """A SecurityApi whose stub answers with the given responses, in order."""

    def __init__(self, command: str, *responses: Message) -> None:
        self.command = command
        self.rpc_name, self.request_cls, self.response_cls, self.grouped, _ = _COMMANDS[command]
        client = MagicMock()
        self.channel = client._get_channel.return_value
        client._session.get_call_metadata.return_value = _METADATA
        self.api = SecurityApi(client)
        self.rpc = AsyncMock(side_effect=list(responses) or [_success(self.response_cls)])
        self.stub_cls = MagicMock()
        setattr(self.stub_cls.return_value, self.rpc_name, self.rpc)

    async def run(self, **kwargs: bool) -> None:
        args = ("space-1", "group-2") if self.grouped else ("space-1",)
        with patch(_STUB, self.stub_cls):
            await getattr(self.api, self.command)(*args, **kwargs)

    @property
    def request(self) -> Message:
        return self.rpc.await_args.args[0]


@pytest.mark.parametrize("command", list(_COMMANDS))
async def test_command_sends_its_request_for_that_space(command: str) -> None:
    harness = _Harness(command)
    await harness.run()
    harness.stub_cls.assert_called_once_with(harness.channel)
    harness.rpc.assert_awaited_once()
    assert harness.rpc.await_args.kwargs == {"metadata": _METADATA, "timeout": 15}
    request = harness.request
    assert type(request) is harness.request_cls
    assert request.space_locator.space_id == "space-1"
    if harness.grouped:
        assert request.group_id == "group-2"


@pytest.mark.parametrize("command", _WITH_IGNORE)
async def test_arming_does_not_ignore_alarms_unless_asked(command: str) -> None:
    harness = _Harness(command)
    await harness.run()
    assert harness.request.ignore_alarms is False


@pytest.mark.parametrize("command", _WITH_IGNORE)
async def test_arming_can_ignore_alarms(command: str) -> None:
    harness = _Harness(command)
    await harness.run(ignore_alarms=True)
    assert harness.request.ignore_alarms is True


@pytest.mark.parametrize("command", list(_COMMANDS))
async def test_a_rejected_command_raises_with_the_reason(command: str) -> None:
    harness = _Harness(command, _failure(_COMMANDS[command][2], "permission_denied"))
    with pytest.raises(SecurityError, match="^permission_denied$"):
        await harness.run()
    harness.rpc.assert_awaited_once()


@pytest.mark.parametrize("command", ["arm", "disarm", "arm_night_mode"])
async def test_already_in_the_requested_state_is_not_an_error(command: str) -> None:
    harness = _Harness(
        command, _failure(_COMMANDS[command][2], "already_in_the_requested_security_state")
    )
    await harness.run()
    harness.rpc.assert_awaited_once()


@pytest.mark.parametrize(
    ("command", "reason"),
    [
        ("disarm_from_night_mode", "disarm_rejected"),
        ("arm_group", "arm_group_rejected"),
        ("disarm_group", "disarm_group_rejected"),
    ],
)
async def test_a_failure_without_a_reason_still_names_the_command(
    command: str, reason: str
) -> None:
    harness = _Harness(command, _failure(_COMMANDS[command][2], None))
    with pytest.raises(SecurityError, match=f"^{reason}$"):
        await harness.run()


_DISARM_RESPONSE = disarm_request_pb2.DisarmSpaceResponse


@pytest.mark.parametrize("busy", ["hub_busy", "another_transition_is_in_progress"])
async def test_disarm_retries_while_the_hub_is_busy(busy: str) -> None:
    harness = _Harness(
        "disarm",
        _failure(_DISARM_RESPONSE, busy),
        _failure(_DISARM_RESPONSE, busy),
        _success(_DISARM_RESPONSE),
    )
    with patch("asyncio.sleep", AsyncMock()) as sleep:
        await harness.run()
    assert harness.rpc.await_count == 3
    assert [call.args for call in sleep.await_args_list] == [(2,), (2,)]
    assert all(call.args[0] is harness.request for call in harness.rpc.await_args_list)


async def test_disarm_gives_up_after_three_busy_answers() -> None:
    harness = _Harness("disarm", *[_failure(_DISARM_RESPONSE, "hub_busy") for _ in range(4)])
    with (
        patch("asyncio.sleep", AsyncMock()) as sleep,
        pytest.raises(SecurityError, match="^hub_busy$"),
    ):
        await harness.run()
    assert harness.rpc.await_count == 3
    assert sleep.await_count == 2


async def test_disarm_does_not_retry_other_failures() -> None:
    harness = _Harness(
        "disarm", _failure(_DISARM_RESPONSE, "permission_denied"), _success(_DISARM_RESPONSE)
    )
    with (
        patch("asyncio.sleep", AsyncMock()) as sleep,
        pytest.raises(SecurityError, match="^permission_denied$"),
    ):
        await harness.run()
    harness.rpc.assert_awaited_once()
    sleep.assert_not_awaited()
