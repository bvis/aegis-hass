"""Tests for the gRPC client core."""

from __future__ import annotations

import asyncio
import time
from unittest.mock import AsyncMock, MagicMock, patch

import grpc
import pytest

from custom_components.aegis_ajax.api.client import (
    _KEEPALIVE_START_MS,
    AjaxGrpcClient,
)
from custom_components.aegis_ajax.api.session import AjaxSession
from custom_components.aegis_ajax.const import GRPC_HOST, GRPC_PORT


class TestClientInit:
    def test_default_host_port(self) -> None:
        client = AjaxGrpcClient.__new__(AjaxGrpcClient)
        client._host = GRPC_HOST
        client._port = GRPC_PORT
        assert client._host == "mobile-gw.prod.ajax.systems"
        assert client._port == 443

    def test_session_created(self) -> None:
        client = AjaxGrpcClient(email="test@example.com", password="secret")
        assert isinstance(client._session, AjaxSession)
        assert client._session._email == "test@example.com"

    def test_session_property(self) -> None:
        client = AjaxGrpcClient(email="a@b.com", password="p")
        assert client.session is client._session

    def test_is_connected_false_initially(self) -> None:
        client = AjaxGrpcClient(email="a@b.com", password="p")
        assert client.is_connected is False

    def test_is_connected_true_with_channel_and_auth(self) -> None:
        client = AjaxGrpcClient.__new__(AjaxGrpcClient)
        client._channel = MagicMock()
        client._session = MagicMock()
        client._session.is_authenticated = True
        assert client.is_connected is True

    def test_get_channel_raises_when_not_connected(self) -> None:
        client = AjaxGrpcClient.__new__(AjaxGrpcClient)
        client._channel = None
        with pytest.raises(ConnectionError, match="not connected"):
            client._get_channel()

    def test_get_channel_returns_channel(self) -> None:
        client = AjaxGrpcClient.__new__(AjaxGrpcClient)
        mock_channel = MagicMock()
        client._channel = mock_channel
        assert client._get_channel() is mock_channel


class TestClientConnect:
    @pytest.mark.asyncio
    async def test_connect_creates_channel(self) -> None:
        client = AjaxGrpcClient.__new__(AjaxGrpcClient)
        client._host = GRPC_HOST
        client._port = GRPC_PORT
        client._channel = None
        client._keepalive_time_ms = _KEEPALIVE_START_MS

        mock_channel = MagicMock()
        with (
            patch("grpc.ssl_channel_credentials"),
            patch("grpc.aio.secure_channel", return_value=mock_channel) as mock_secure,
        ):
            await client.connect()
            mock_secure.assert_called_once()
            assert client._channel is mock_channel

    @pytest.mark.asyncio
    async def test_connect_sets_keepalive_options(self) -> None:
        """The long-lived device stream needs HTTP/2 keepalive so a half-open
        connection surfaces as UNAVAILABLE (which the reconnect path recovers)
        instead of hanging silently until an HA restart (#236)."""
        client = AjaxGrpcClient.__new__(AjaxGrpcClient)
        client._host = GRPC_HOST
        client._port = GRPC_PORT
        client._channel = None
        client._keepalive_time_ms = _KEEPALIVE_START_MS

        with (
            patch("grpc.ssl_channel_credentials"),
            patch("grpc.aio.secure_channel", return_value=MagicMock()) as mock_secure,
        ):
            await client.connect()

        options = dict(mock_secure.call_args.kwargs["options"])
        assert options["grpc.keepalive_time_ms"] > 0
        assert options["grpc.keepalive_timeout_ms"] > 0
        # Must ping even when the open stream is idle (push-on-change can be
        # quiet for hours) — otherwise gRPC stops pinging after 2 dataless pings.
        assert options["grpc.http2.max_pings_without_data"] == 0
        assert options["grpc.keepalive_permit_without_calls"] == 1

    def test_reduce_keepalive_halves_then_floors(self) -> None:
        """Keepalive self-tunes down by halving and stops at the 60s floor."""
        from custom_components.aegis_ajax.api.client import _KEEPALIVE_FLOOR_MS

        client = AjaxGrpcClient.__new__(AjaxGrpcClient)
        client._keepalive_time_ms = _KEEPALIVE_START_MS

        steps = []
        while client.reduce_keepalive():
            steps.append(client.keepalive_time_ms)

        assert steps == [120000, 60000]
        assert client.keepalive_time_ms == _KEEPALIVE_FLOOR_MS
        # Already at the floor → no further reduction.
        assert client.reduce_keepalive() is False
        assert client.keepalive_time_ms == _KEEPALIVE_FLOOR_MS

    @pytest.mark.asyncio
    async def test_open_channel_uses_current_keepalive(self) -> None:
        """A reduced interval is applied to the next channel that is opened."""
        client = AjaxGrpcClient.__new__(AjaxGrpcClient)
        client._host = GRPC_HOST
        client._port = GRPC_PORT
        client._keepalive_time_ms = 60000

        with (
            patch("grpc.ssl_channel_credentials"),
            patch("grpc.aio.secure_channel", return_value=MagicMock()) as mock_secure,
        ):
            client._open_channel()

        options = dict(mock_secure.call_args.kwargs["options"])
        assert options["grpc.keepalive_time_ms"] == 60000

    @pytest.mark.asyncio
    async def test_reconnect_channel_sets_keepalive_options(self) -> None:
        """A recreated channel (post-failure) must carry the same keepalive."""
        client = AjaxGrpcClient.__new__(AjaxGrpcClient)
        client._host = GRPC_HOST
        client._port = GRPC_PORT
        client._channel = None
        client._reconnect_lock = asyncio.Lock()
        client._keepalive_time_ms = _KEEPALIVE_START_MS

        with (
            patch("grpc.ssl_channel_credentials"),
            patch("grpc.aio.secure_channel", return_value=MagicMock()) as mock_secure,
        ):
            await client.reconnect_channel()

        options = dict(mock_secure.call_args.kwargs["options"])
        assert options["grpc.keepalive_time_ms"] > 0
        assert options["grpc.http2.max_pings_without_data"] == 0

    @pytest.mark.asyncio
    async def test_close_clears_channel(self) -> None:
        client = AjaxGrpcClient.__new__(AjaxGrpcClient)
        mock_channel = AsyncMock()
        client._channel = mock_channel
        client._refresh_task = None
        client._session = MagicMock()

        await client.close()
        mock_channel.close.assert_called_once()
        assert client._channel is None

    @pytest.mark.asyncio
    async def test_close_cancels_refresh_task(self) -> None:
        client = AjaxGrpcClient.__new__(AjaxGrpcClient)
        mock_channel = AsyncMock()
        client._channel = mock_channel
        client._session = MagicMock()

        mock_task = MagicMock()
        mock_task.done.return_value = False
        client._refresh_task = mock_task

        await client.close()
        mock_task.cancel.assert_called_once()

    @pytest.mark.asyncio
    async def test_close_when_no_channel(self) -> None:
        client = AjaxGrpcClient.__new__(AjaxGrpcClient)
        client._channel = None
        client._refresh_task = None
        client._session = MagicMock()
        # Should not raise
        await client.close()


class TestReconnectChannel:
    """Channel recreation after a wedged transport (issue #236).

    A long-lived stream that hits UNAVAILABLE must be able to swap the
    shared channel for a fresh one without re-login, so reconnection
    doesn't land back on the same half-open channel forever.
    """

    @pytest.mark.asyncio
    async def test_reconnect_opens_fresh_channel_and_closes_old(self) -> None:
        client = AjaxGrpcClient.__new__(AjaxGrpcClient)
        client._host = GRPC_HOST
        client._port = GRPC_PORT
        client._reconnect_lock = asyncio.Lock()
        client._keepalive_time_ms = _KEEPALIVE_START_MS
        old_channel = AsyncMock()
        client._channel = old_channel

        new_channel = MagicMock()
        with (
            patch("grpc.ssl_channel_credentials"),
            patch("grpc.aio.secure_channel", return_value=new_channel) as mock_secure,
        ):
            await client.reconnect_channel()

        old_channel.close.assert_awaited_once()
        assert client._channel is new_channel
        mock_secure.assert_called_once()

    @pytest.mark.asyncio
    async def test_reconnect_with_no_existing_channel(self) -> None:
        client = AjaxGrpcClient.__new__(AjaxGrpcClient)
        client._host = GRPC_HOST
        client._port = GRPC_PORT
        client._reconnect_lock = asyncio.Lock()
        client._keepalive_time_ms = _KEEPALIVE_START_MS
        client._channel = None

        new_channel = MagicMock()
        with (
            patch("grpc.ssl_channel_credentials"),
            patch("grpc.aio.secure_channel", return_value=new_channel),
        ):
            await client.reconnect_channel()

        assert client._channel is new_channel

    @pytest.mark.asyncio
    async def test_reconnect_preserves_session(self) -> None:
        client = AjaxGrpcClient.__new__(AjaxGrpcClient)
        client._host = GRPC_HOST
        client._port = GRPC_PORT
        client._reconnect_lock = asyncio.Lock()
        client._keepalive_time_ms = _KEEPALIVE_START_MS
        client._channel = AsyncMock()
        session = MagicMock()
        client._session = session

        with (
            patch("grpc.ssl_channel_credentials"),
            patch("grpc.aio.secure_channel", return_value=MagicMock()),
        ):
            await client.reconnect_channel()

        session.clear_session.assert_not_called()


class TestRateLimit:
    @pytest.mark.asyncio
    async def test_rate_limit_passes_under_limit(self) -> None:
        client = AjaxGrpcClient.__new__(AjaxGrpcClient)
        client._rate_limit_timestamps = []
        # Should not block when under limit
        await client._check_rate_limit()
        assert len(client._rate_limit_timestamps) == 1

    @pytest.mark.asyncio
    async def test_rate_limit_cleans_old_timestamps(self) -> None:
        client = AjaxGrpcClient.__new__(AjaxGrpcClient)
        # Add old timestamps
        old_time = time.monotonic() - 200
        client._rate_limit_timestamps = [old_time] * 5
        await client._check_rate_limit()
        # Old timestamps should be removed
        assert len(client._rate_limit_timestamps) == 1


class TestClientRetry:
    @pytest.mark.asyncio
    async def test_retry_on_transient_error(self) -> None:
        client = AjaxGrpcClient.__new__(AjaxGrpcClient)
        client._session = AjaxSession()

        call_count = 0

        async def flaky_call() -> str:
            nonlocal call_count
            call_count += 1
            if call_count < 3:
                raise ConnectionError("UNAVAILABLE")
            return "success"

        result = await client._retry(flaky_call, max_retries=3, base_delay=0.01)
        assert result == "success"
        assert call_count == 3

    @pytest.mark.asyncio
    async def test_retry_exhausted(self) -> None:
        client = AjaxGrpcClient.__new__(AjaxGrpcClient)
        client._session = AjaxSession()

        async def always_fails() -> str:
            raise ConnectionError("UNAVAILABLE")

        with pytest.raises(ConnectionError):
            await client._retry(always_fails, max_retries=2, base_delay=0.01)

    @pytest.mark.asyncio
    async def test_retry_reraises_non_transient_grpc_error(self) -> None:
        import grpc

        client = AjaxGrpcClient.__new__(AjaxGrpcClient)
        client._session = AjaxSession()

        mock_error = MagicMock(spec=grpc.aio.AioRpcError)
        mock_error.code.return_value = grpc.StatusCode.NOT_FOUND

        async def fails_with_non_transient() -> str:
            raise mock_error

        with pytest.raises(TypeError):
            await client._retry(fails_with_non_transient, max_retries=3, base_delay=0.01)


class TestCallUnary:
    @pytest.mark.asyncio
    async def test_call_unary(self) -> None:
        client = AjaxGrpcClient.__new__(AjaxGrpcClient)
        client._rate_limit_timestamps = []
        mock_channel = MagicMock()
        client._channel = mock_channel
        mock_session = MagicMock()
        mock_session.get_call_metadata.return_value = [("token", "abc")]
        client._session = mock_session

        mock_response = MagicMock()
        mock_method = AsyncMock(return_value=mock_response)
        mock_channel.unary_unary.return_value = mock_method

        mock_request = MagicMock()
        mock_response_type = MagicMock()

        result = await client.call_unary("/some/method", mock_request, mock_response_type)
        assert result is mock_response
        mock_channel.unary_unary.assert_called_once()

    @pytest.mark.asyncio
    async def test_call_unary_refetches_channel_between_retries(self) -> None:
        """After the channel is recreated mid-flight (#236), a retried unary
        call must pick up the new channel instead of reusing the dead one."""
        client = AjaxGrpcClient.__new__(AjaxGrpcClient)
        client._rate_limit_timestamps = []
        mock_session = MagicMock()
        mock_session.get_call_metadata.return_value = []
        client._session = mock_session

        channel_a = MagicMock()
        channel_b = MagicMock()
        client._channel = channel_a

        transient = grpc.aio.AioRpcError(
            grpc.StatusCode.UNAVAILABLE,
            grpc.aio.Metadata(),
            grpc.aio.Metadata(),
            details="",
        )

        async def method_a(*_args: object, **_kwargs: object) -> None:
            # Simulate the stream task swapping the channel out from under us.
            client._channel = channel_b
            raise transient

        channel_a.unary_unary.return_value = AsyncMock(side_effect=method_a)
        mock_response = MagicMock()
        channel_b.unary_unary.return_value = AsyncMock(return_value=mock_response)

        with patch("asyncio.sleep", new=AsyncMock()):
            result = await client.call_unary("/m", MagicMock(), MagicMock())

        assert result is mock_response
        channel_b.unary_unary.assert_called_once()

    @pytest.mark.asyncio
    async def test_call_server_stream(self) -> None:
        client = AjaxGrpcClient.__new__(AjaxGrpcClient)
        client._rate_limit_timestamps = []
        mock_channel = MagicMock()
        client._channel = mock_channel
        mock_session = MagicMock()
        mock_session.get_call_metadata.return_value = []
        client._session = mock_session

        mock_stream = MagicMock()
        mock_method = MagicMock(return_value=mock_stream)
        mock_channel.unary_stream.return_value = mock_method

        mock_request = MagicMock()
        mock_response_type = MagicMock()

        result = await client.call_server_stream("/some/method", mock_request, mock_response_type)
        assert result is mock_stream
