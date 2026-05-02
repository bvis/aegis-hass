"""Tests for security API (arm/disarm)."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from custom_components.aegis_ajax.api.security import SecurityApi, SecurityError

_GRPC_MOD = "systems.ajax.api.mobile.v2.space.security.space_security_endpoints_pb2_grpc"


class TestSecurityApiInit:
    def test_init(self) -> None:
        client = MagicMock()
        api = SecurityApi(client)
        assert api._client is client


class TestProtoPath:
    def test_proto_path_in_sys_path(self) -> None:
        """Proto path is added to sys.path by api._proto_path module."""
        import sys
        from pathlib import Path

        base = Path(__file__).parent.parent.parent
        expected = str(base / "custom_components" / "aegis_ajax" / "proto")
        assert expected in sys.path


def _make_security_api() -> tuple[SecurityApi, MagicMock, MagicMock]:
    """Return (api, mock_channel, mock_stub)."""
    mock_client = MagicMock()
    mock_channel = MagicMock()
    mock_client._get_channel.return_value = mock_channel
    mock_client._session.get_call_metadata.return_value = [("token", "abc")]
    api = SecurityApi(mock_client)
    return api, mock_channel, MagicMock()


class TestArm:
    @pytest.mark.asyncio
    async def test_arm_calls_stub(self) -> None:
        api, mock_channel, _ = _make_security_api()

        mock_stub_instance = MagicMock()
        mock_response = MagicMock()
        mock_response.HasField.return_value = False
        mock_stub_instance.arm = AsyncMock(return_value=mock_response)

        mock_stub_class = MagicMock(return_value=mock_stub_instance)
        mock_arm_request_pb2 = MagicMock()
        mock_grpc_module = MagicMock(SpaceSecurityServiceStub=mock_stub_class)
        mock_locator_pb2 = MagicMock()

        with patch.dict(
            "sys.modules",
            {
                "systems.ajax.api.mobile.v2.space.security.arm_request_pb2": mock_arm_request_pb2,
                _GRPC_MOD: mock_grpc_module,
                "systems.ajax.api.mobile.v2.common.space.space_locator_pb2": mock_locator_pb2,
                "systems.ajax.api.mobile.v2.space.security": MagicMock(
                    arm_request_pb2=mock_arm_request_pb2,
                    space_security_endpoints_pb2_grpc=mock_grpc_module,
                ),
                "systems.ajax.api.mobile.v2.common.space": MagicMock(
                    space_locator_pb2=mock_locator_pb2,
                ),
            },
        ):
            await api.arm("space-1")

        mock_stub_instance.arm.assert_called_once()

    @pytest.mark.asyncio
    async def test_arm_raises_on_failure(self) -> None:
        api, mock_channel, _ = _make_security_api()

        mock_stub_instance = MagicMock()
        mock_response = MagicMock()
        mock_response.HasField.return_value = True
        mock_stub_instance.arm = AsyncMock(return_value=mock_response)

        mock_stub_class = MagicMock(return_value=mock_stub_instance)
        mock_arm_request_pb2 = MagicMock()
        mock_grpc_module = MagicMock(SpaceSecurityServiceStub=mock_stub_class)
        mock_locator_pb2 = MagicMock()

        with (
            patch.dict(
                "sys.modules",
                {
                    "systems.ajax.api.mobile.v2.space.security.arm_request_pb2": (
                        mock_arm_request_pb2
                    ),
                    _GRPC_MOD: mock_grpc_module,
                    "systems.ajax.api.mobile.v2.common.space.space_locator_pb2": mock_locator_pb2,
                    "systems.ajax.api.mobile.v2.space.security": MagicMock(
                        arm_request_pb2=mock_arm_request_pb2,
                        space_security_endpoints_pb2_grpc=mock_grpc_module,
                    ),
                    "systems.ajax.api.mobile.v2.common.space": MagicMock(
                        space_locator_pb2=mock_locator_pb2,
                    ),
                },
            ),
            pytest.raises(SecurityError),
        ):
            await api.arm("space-1")


class TestDisarm:
    @pytest.mark.asyncio
    async def test_disarm_calls_stub(self) -> None:
        api, mock_channel, _ = _make_security_api()

        mock_stub_instance = MagicMock()
        mock_response = MagicMock()
        mock_response.HasField.return_value = False
        mock_stub_instance.disarm = AsyncMock(return_value=mock_response)

        mock_stub_class = MagicMock(return_value=mock_stub_instance)
        mock_disarm_request_pb2 = MagicMock()
        mock_grpc_module = MagicMock(SpaceSecurityServiceStub=mock_stub_class)
        mock_locator_pb2 = MagicMock()

        with patch.dict(
            "sys.modules",
            {
                "systems.ajax.api.mobile.v2.space.security.disarm_request_pb2": (
                    mock_disarm_request_pb2
                ),
                _GRPC_MOD: mock_grpc_module,
                "systems.ajax.api.mobile.v2.common.space.space_locator_pb2": mock_locator_pb2,
                "systems.ajax.api.mobile.v2.space.security": MagicMock(
                    disarm_request_pb2=mock_disarm_request_pb2,
                    space_security_endpoints_pb2_grpc=mock_grpc_module,
                ),
                "systems.ajax.api.mobile.v2.common.space": MagicMock(
                    space_locator_pb2=mock_locator_pb2,
                ),
            },
        ):
            await api.disarm("space-1")

        mock_stub_instance.disarm.assert_called_once()

    @pytest.mark.asyncio
    async def test_disarm_raises_on_failure(self) -> None:
        api, mock_channel, _ = _make_security_api()

        mock_stub_instance = MagicMock()
        mock_response = MagicMock()
        mock_response.HasField.return_value = True
        mock_stub_instance.disarm = AsyncMock(return_value=mock_response)

        mock_stub_class = MagicMock(return_value=mock_stub_instance)
        mock_disarm_request_pb2 = MagicMock()
        mock_grpc_module = MagicMock(SpaceSecurityServiceStub=mock_stub_class)
        mock_locator_pb2 = MagicMock()

        with (
            patch.dict(
                "sys.modules",
                {
                    "systems.ajax.api.mobile.v2.space.security.disarm_request_pb2": (
                        mock_disarm_request_pb2
                    ),
                    _GRPC_MOD: mock_grpc_module,
                    "systems.ajax.api.mobile.v2.common.space.space_locator_pb2": mock_locator_pb2,
                    "systems.ajax.api.mobile.v2.space.security": MagicMock(
                        disarm_request_pb2=mock_disarm_request_pb2,
                        space_security_endpoints_pb2_grpc=mock_grpc_module,
                    ),
                    "systems.ajax.api.mobile.v2.common.space": MagicMock(
                        space_locator_pb2=mock_locator_pb2,
                    ),
                },
            ),
            pytest.raises(SecurityError),
        ):
            await api.disarm("space-1")


class TestArmNightMode:
    @pytest.mark.asyncio
    async def test_arm_night_mode_calls_stub(self) -> None:
        api, mock_channel, _ = _make_security_api()

        mock_stub_instance = MagicMock()
        mock_response = MagicMock()
        mock_response.HasField.return_value = False
        mock_stub_instance.armToNightMode = AsyncMock(return_value=mock_response)

        mock_stub_class = MagicMock(return_value=mock_stub_instance)
        mock_request_pb2 = MagicMock()
        mock_grpc_module = MagicMock(SpaceSecurityServiceStub=mock_stub_class)
        mock_locator_pb2 = MagicMock()

        with patch.dict(
            "sys.modules",
            {
                "systems.ajax.api.mobile.v2.space.security.arm_to_night_mode_request_pb2": (
                    mock_request_pb2
                ),
                _GRPC_MOD: mock_grpc_module,
                "systems.ajax.api.mobile.v2.common.space.space_locator_pb2": mock_locator_pb2,
                "systems.ajax.api.mobile.v2.space.security": MagicMock(
                    arm_to_night_mode_request_pb2=mock_request_pb2,
                    space_security_endpoints_pb2_grpc=mock_grpc_module,
                ),
                "systems.ajax.api.mobile.v2.common.space": MagicMock(
                    space_locator_pb2=mock_locator_pb2,
                ),
            },
        ):
            await api.arm_night_mode("space-1")

        mock_stub_instance.armToNightMode.assert_called_once()

    @pytest.mark.asyncio
    async def test_arm_night_mode_raises_on_failure(self) -> None:
        api, mock_channel, _ = _make_security_api()

        mock_stub_instance = MagicMock()
        mock_response = MagicMock()
        mock_response.HasField.return_value = True
        mock_stub_instance.armToNightMode = AsyncMock(return_value=mock_response)

        mock_stub_class = MagicMock(return_value=mock_stub_instance)
        mock_request_pb2 = MagicMock()
        mock_grpc_module = MagicMock(SpaceSecurityServiceStub=mock_stub_class)
        mock_locator_pb2 = MagicMock()

        with (
            patch.dict(
                "sys.modules",
                {
                    "systems.ajax.api.mobile.v2.space.security.arm_to_night_mode_request_pb2": (
                        mock_request_pb2
                    ),
                    _GRPC_MOD: mock_grpc_module,
                    "systems.ajax.api.mobile.v2.common.space.space_locator_pb2": mock_locator_pb2,
                    "systems.ajax.api.mobile.v2.space.security": MagicMock(
                        arm_to_night_mode_request_pb2=mock_request_pb2,
                        space_security_endpoints_pb2_grpc=mock_grpc_module,
                    ),
                    "systems.ajax.api.mobile.v2.common.space": MagicMock(
                        space_locator_pb2=mock_locator_pb2,
                    ),
                },
            ),
            pytest.raises(SecurityError),
        ):
            await api.arm_night_mode("space-1")


class TestDisarmFromNightMode:
    @pytest.mark.asyncio
    async def test_disarm_from_night_mode_calls_stub(self) -> None:
        api, mock_channel, _ = _make_security_api()

        mock_stub_instance = MagicMock()
        mock_response = MagicMock()
        mock_response.HasField.return_value = False
        mock_stub_instance.disarmFromNightMode = AsyncMock(return_value=mock_response)

        mock_stub_class = MagicMock(return_value=mock_stub_instance)
        mock_request_pb2 = MagicMock()
        mock_grpc_module = MagicMock(SpaceSecurityServiceStub=mock_stub_class)
        mock_locator_pb2 = MagicMock()

        with patch.dict(
            "sys.modules",
            {
                "systems.ajax.api.mobile.v2.space.security.disarm_from_night_mode_request_pb2": (
                    mock_request_pb2
                ),
                _GRPC_MOD: mock_grpc_module,
                "systems.ajax.api.mobile.v2.common.space.space_locator_pb2": mock_locator_pb2,
                "systems.ajax.api.mobile.v2.space.security": MagicMock(
                    disarm_from_night_mode_request_pb2=mock_request_pb2,
                    space_security_endpoints_pb2_grpc=mock_grpc_module,
                ),
                "systems.ajax.api.mobile.v2.common.space": MagicMock(
                    space_locator_pb2=mock_locator_pb2,
                ),
            },
        ):
            await api.disarm_from_night_mode("space-1")

        mock_stub_instance.disarmFromNightMode.assert_called_once()

    @pytest.mark.asyncio
    async def test_disarm_from_night_mode_raises_on_failure(self) -> None:
        api, mock_channel, _ = _make_security_api()

        mock_stub_instance = MagicMock()
        mock_response = MagicMock()
        mock_response.HasField.return_value = True
        mock_stub_instance.disarmFromNightMode = AsyncMock(return_value=mock_response)

        mock_stub_class = MagicMock(return_value=mock_stub_instance)
        mock_request_pb2 = MagicMock()
        mock_grpc_module = MagicMock(SpaceSecurityServiceStub=mock_stub_class)
        mock_locator_pb2 = MagicMock()

        with (
            patch.dict(
                "sys.modules",
                {
                    "systems.ajax.api.mobile.v2.space.security"
                    ".disarm_from_night_mode_request_pb2": mock_request_pb2,
                    _GRPC_MOD: mock_grpc_module,
                    "systems.ajax.api.mobile.v2.common.space.space_locator_pb2": mock_locator_pb2,
                    "systems.ajax.api.mobile.v2.space.security": MagicMock(
                        disarm_from_night_mode_request_pb2=mock_request_pb2,
                        space_security_endpoints_pb2_grpc=mock_grpc_module,
                    ),
                    "systems.ajax.api.mobile.v2.common.space": MagicMock(
                        space_locator_pb2=mock_locator_pb2,
                    ),
                },
            ),
            pytest.raises(SecurityError),
        ):
            await api.disarm_from_night_mode("space-1")


class TestArmGroup:
    @pytest.mark.asyncio
    async def test_arm_group_calls_stub(self) -> None:
        api, mock_channel, _ = _make_security_api()

        mock_stub_instance = MagicMock()
        mock_response = MagicMock()
        mock_response.HasField.return_value = False
        mock_stub_instance.armGroup = AsyncMock(return_value=mock_response)

        mock_stub_class = MagicMock(return_value=mock_stub_instance)
        mock_arm_group_request_pb2 = MagicMock()
        mock_grpc_module = MagicMock(SpaceSecurityServiceStub=mock_stub_class)
        mock_locator_pb2 = MagicMock()

        with patch.dict(
            "sys.modules",
            {
                "systems.ajax.api.mobile.v2.space.security.group.arm_group_request_pb2": (
                    mock_arm_group_request_pb2
                ),
                _GRPC_MOD: mock_grpc_module,
                "systems.ajax.api.mobile.v2.common.space.space_locator_pb2": mock_locator_pb2,
                "systems.ajax.api.mobile.v2.space.security.group": MagicMock(
                    arm_group_request_pb2=mock_arm_group_request_pb2,
                ),
                "systems.ajax.api.mobile.v2.space.security": MagicMock(
                    space_security_endpoints_pb2_grpc=mock_grpc_module,
                ),
                "systems.ajax.api.mobile.v2.common.space": MagicMock(
                    space_locator_pb2=mock_locator_pb2,
                ),
            },
        ):
            await api.arm_group("space-1", "group-1")

        mock_stub_instance.armGroup.assert_called_once()

    @pytest.mark.asyncio
    async def test_arm_group_raises_on_failure(self) -> None:
        api, mock_channel, _ = _make_security_api()

        mock_stub_instance = MagicMock()
        mock_response = MagicMock()
        mock_response.HasField.return_value = True
        mock_stub_instance.armGroup = AsyncMock(return_value=mock_response)

        mock_stub_class = MagicMock(return_value=mock_stub_instance)
        mock_arm_group_request_pb2 = MagicMock()
        mock_grpc_module = MagicMock(SpaceSecurityServiceStub=mock_stub_class)
        mock_locator_pb2 = MagicMock()

        with (
            patch.dict(
                "sys.modules",
                {
                    "systems.ajax.api.mobile.v2.space.security.group.arm_group_request_pb2": (
                        mock_arm_group_request_pb2
                    ),
                    _GRPC_MOD: mock_grpc_module,
                    "systems.ajax.api.mobile.v2.common.space.space_locator_pb2": mock_locator_pb2,
                    "systems.ajax.api.mobile.v2.space.security.group": MagicMock(
                        arm_group_request_pb2=mock_arm_group_request_pb2,
                    ),
                    "systems.ajax.api.mobile.v2.space.security": MagicMock(
                        space_security_endpoints_pb2_grpc=mock_grpc_module,
                    ),
                    "systems.ajax.api.mobile.v2.common.space": MagicMock(
                        space_locator_pb2=mock_locator_pb2,
                    ),
                },
            ),
            pytest.raises(SecurityError),
        ):
            await api.arm_group("space-1", "group-1")


class TestDisarmGroup:
    @pytest.mark.asyncio
    async def test_disarm_group_calls_stub(self) -> None:
        api, mock_channel, _ = _make_security_api()

        mock_stub_instance = MagicMock()
        mock_response = MagicMock()
        mock_response.HasField.return_value = False
        mock_stub_instance.disarmGroup = AsyncMock(return_value=mock_response)

        mock_stub_class = MagicMock(return_value=mock_stub_instance)
        mock_disarm_group_request_pb2 = MagicMock()
        mock_grpc_module = MagicMock(SpaceSecurityServiceStub=mock_stub_class)
        mock_locator_pb2 = MagicMock()

        with patch.dict(
            "sys.modules",
            {
                "systems.ajax.api.mobile.v2.space.security.group.disarm_group_request_pb2": (
                    mock_disarm_group_request_pb2
                ),
                _GRPC_MOD: mock_grpc_module,
                "systems.ajax.api.mobile.v2.common.space.space_locator_pb2": mock_locator_pb2,
                "systems.ajax.api.mobile.v2.space.security.group": MagicMock(
                    disarm_group_request_pb2=mock_disarm_group_request_pb2,
                ),
                "systems.ajax.api.mobile.v2.space.security": MagicMock(
                    space_security_endpoints_pb2_grpc=mock_grpc_module,
                ),
                "systems.ajax.api.mobile.v2.common.space": MagicMock(
                    space_locator_pb2=mock_locator_pb2,
                ),
            },
        ):
            await api.disarm_group("space-1", "group-1")

        mock_stub_instance.disarmGroup.assert_called_once()

    @pytest.mark.asyncio
    async def test_disarm_group_raises_on_failure(self) -> None:
        api, mock_channel, _ = _make_security_api()

        mock_stub_instance = MagicMock()
        mock_response = MagicMock()
        mock_response.HasField.return_value = True
        mock_stub_instance.disarmGroup = AsyncMock(return_value=mock_response)

        mock_stub_class = MagicMock(return_value=mock_stub_instance)
        mock_disarm_group_request_pb2 = MagicMock()
        mock_grpc_module = MagicMock(SpaceSecurityServiceStub=mock_stub_class)
        mock_locator_pb2 = MagicMock()

        with (
            patch.dict(
                "sys.modules",
                {
                    "systems.ajax.api.mobile.v2.space.security.group.disarm_group_request_pb2": (
                        mock_disarm_group_request_pb2
                    ),
                    _GRPC_MOD: mock_grpc_module,
                    "systems.ajax.api.mobile.v2.common.space.space_locator_pb2": mock_locator_pb2,
                    "systems.ajax.api.mobile.v2.space.security.group": MagicMock(
                        disarm_group_request_pb2=mock_disarm_group_request_pb2,
                    ),
                    "systems.ajax.api.mobile.v2.space.security": MagicMock(
                        space_security_endpoints_pb2_grpc=mock_grpc_module,
                    ),
                    "systems.ajax.api.mobile.v2.common.space": MagicMock(
                        space_locator_pb2=mock_locator_pb2,
                    ),
                },
            ),
            pytest.raises(SecurityError),
        ):
            await api.disarm_group("space-1", "group-1")


class TestArmingCommands:
    def test_arm_is_callable(self) -> None:
        api = SecurityApi.__new__(SecurityApi)
        api._client = MagicMock()
        assert callable(api.arm)

    def test_disarm_is_callable(self) -> None:
        api = SecurityApi.__new__(SecurityApi)
        api._client = MagicMock()
        assert callable(api.disarm)

    def test_arm_night_mode_is_callable(self) -> None:
        api = SecurityApi.__new__(SecurityApi)
        api._client = MagicMock()
        assert callable(api.arm_night_mode)

    def test_arm_group_is_callable(self) -> None:
        api = SecurityApi.__new__(SecurityApi)
        api._client = MagicMock()
        assert callable(api.arm_group)

    def test_disarm_group_is_callable(self) -> None:
        api = SecurityApi.__new__(SecurityApi)
        api._client = MagicMock()
        assert callable(api.disarm_group)


class TestGroupProtoIntegration:
    """Real-proto regression tests for arm_group / disarm_group.

    The class-level tests above mock the entire `*_request_pb2` module with
    MagicMock, so a typo in the proto class name (e.g. ArmGroupRequest vs
    ArmSpaceGroupRequest) silently passes. These tests use the real proto
    module so any drift between security.py and the generated descriptors
    surfaces immediately.
    """

    def test_arm_group_request_class_name(self) -> None:
        from systems.ajax.api.mobile.v2.space.security.group import (
            arm_group_request_pb2,
        )

        assert hasattr(arm_group_request_pb2, "ArmSpaceGroupRequest")
        assert not hasattr(arm_group_request_pb2, "ArmGroupRequest")

    def test_disarm_group_request_class_name(self) -> None:
        from systems.ajax.api.mobile.v2.space.security.group import (
            disarm_group_request_pb2,
        )

        assert hasattr(disarm_group_request_pb2, "DisarmSpaceGroupRequest")
        assert not hasattr(disarm_group_request_pb2, "DisarmGroupRequest")

    @pytest.mark.asyncio
    async def test_arm_group_builds_real_proto(self) -> None:
        """Exercise security.arm_group with the real proto, mocking only the stub."""
        from systems.ajax.api.mobile.v2.space.security.group import (
            arm_group_request_pb2,
        )

        api, _, _ = _make_security_api()

        captured: dict[str, object] = {}
        mock_response = MagicMock()
        mock_response.HasField.return_value = False

        async def fake_arm_group(request, metadata=None, timeout=None):  # noqa: ANN001, ANN202, ARG001
            captured["request"] = request
            return mock_response

        mock_stub_instance = MagicMock()
        mock_stub_instance.armGroup = fake_arm_group
        mock_grpc_module = MagicMock(SpaceSecurityServiceStub=lambda _ch: mock_stub_instance)

        with patch.dict("sys.modules", {_GRPC_MOD: mock_grpc_module}):
            await api.arm_group("space-1", "group-2", ignore_alarms=True)

        request = captured["request"]
        assert isinstance(request, arm_group_request_pb2.ArmSpaceGroupRequest)
        assert request.group_id == "group-2"
        assert request.ignore_alarms is True
        assert request.space_locator.space_id == "space-1"

    @pytest.mark.asyncio
    async def test_disarm_group_builds_real_proto(self) -> None:
        from systems.ajax.api.mobile.v2.space.security.group import (
            disarm_group_request_pb2,
        )

        api, _, _ = _make_security_api()

        captured: dict[str, object] = {}
        mock_response = MagicMock()
        mock_response.HasField.return_value = False

        async def fake_disarm_group(request, metadata=None, timeout=None):  # noqa: ANN001, ANN202, ARG001
            captured["request"] = request
            return mock_response

        mock_stub_instance = MagicMock()
        mock_stub_instance.disarmGroup = fake_disarm_group
        mock_grpc_module = MagicMock(SpaceSecurityServiceStub=lambda _ch: mock_stub_instance)

        with patch.dict("sys.modules", {_GRPC_MOD: mock_grpc_module}):
            await api.disarm_group("space-1", "group-2")

        request = captured["request"]
        assert isinstance(request, disarm_group_request_pb2.DisarmSpaceGroupRequest)
        assert request.group_id == "group-2"
        assert request.space_locator.space_id == "space-1"
