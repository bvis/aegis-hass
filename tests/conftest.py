"""Shared test fixtures."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

# Wire up the proto search path before any test module is collected, so that
# `from systems.ajax.api...` imports at module top level resolve in tests
# that don't import the integration first. Must come after stdlib/third-party
# imports to keep ruff's import-sorter happy, but before any test module
# collection can attempt a `systems.*` import — pytest imports conftest first.
from custom_components.aegis_ajax.api import _proto_path as _proto_path  # noqa: E402, F401


def _stub_camera_module() -> None:
    """Stand in for `homeassistant.components.camera` where it can't import.

    The real module drags in HA's stream component, which needs numpy, and
    the dev image has none. The stub carries only the names camera.py uses,
    shaped like HA's own (frozen dataclasses for the WebRTC messages).
    """
    import sys  # noqa: PLC0415
    from dataclasses import dataclass  # noqa: PLC0415
    from enum import IntFlag  # noqa: PLC0415
    from types import ModuleType  # noqa: PLC0415
    from typing import Any  # noqa: PLC0415

    try:
        import homeassistant.components.camera  # noqa: F401, PLC0415

        return
    except ImportError:
        pass

    camera_mod = ModuleType("homeassistant.components.camera")

    class Camera:
        def __init__(self) -> None:
            pass

        async def async_will_remove_from_hass(self) -> None:
            pass

    class CameraEntityFeature(IntFlag):
        ON_OFF = 1
        STREAM = 2

    @dataclass(frozen=True)
    class WebRTCAnswer:
        answer: str

    @dataclass(frozen=True)
    class WebRTCCandidate:
        candidate: Any

    @dataclass(frozen=True)
    class WebRTCError:
        code: str
        message: str

    webrtc_mod = ModuleType("homeassistant.components.camera.webrtc")
    for obj in (WebRTCAnswer, WebRTCCandidate, WebRTCError):
        setattr(webrtc_mod, obj.__name__, obj)
    camera_mod.Camera = Camera  # type: ignore[attr-defined]
    camera_mod.CameraEntityFeature = CameraEntityFeature  # type: ignore[attr-defined]
    camera_mod.webrtc = webrtc_mod  # type: ignore[attr-defined]
    sys.modules["homeassistant.components.camera"] = camera_mod
    sys.modules["homeassistant.components.camera.webrtc"] = webrtc_mod


_stub_camera_module()


@pytest.fixture
def mock_grpc_channel() -> MagicMock:
    """Create a mock gRPC channel."""
    channel = MagicMock()
    channel.close = AsyncMock()
    return channel


@pytest.fixture
def mock_session_token() -> bytes:
    """A fake session token (16 bytes)."""
    return bytes.fromhex("aabbccdd11223344aabbccdd11223344")


@pytest.fixture
def mock_user_hex_id() -> str:
    return "user123hex"
