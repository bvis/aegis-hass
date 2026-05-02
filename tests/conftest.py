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
