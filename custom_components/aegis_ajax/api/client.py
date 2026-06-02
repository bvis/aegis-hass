"""Core gRPC client for Ajax Systems API."""

from __future__ import annotations

import asyncio
import logging
import random
import time
from typing import TYPE_CHECKING, Any

import grpc

from custom_components.aegis_ajax.api.session import (
    AjaxSession,
    AuthenticationError,
    TwoFactorRequiredError,
)
from custom_components.aegis_ajax.const import (
    APPLICATION_LABEL,
    GRPC_HOST,
    GRPC_PORT,
    GRPC_TIMEOUT,
    MAX_RETRIES,
    RATE_LIMIT_REQUESTS,
    RATE_LIMIT_WINDOW,
)

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable

# Proto imports use C extensions (protobuf). Import at module level so HA's
# async event loop never triggers a blocking-import violation at runtime.
# _proto_path is loaded by api/__init__.py before this module.
# isort: off
from v3.mobilegwsvc.commonmodels.type import user_role_pb2  # noqa: E402
from v3.mobilegwsvc.service.login_by_password import (  # noqa: E402
    endpoint_pb2_grpc as login_password_grpc,
    request_pb2 as login_password_req,
)
from v3.mobilegwsvc.service.login_by_totp import (  # noqa: E402
    endpoint_pb2_grpc as login_totp_grpc,
    request_pb2 as login_totp_req,
)
# isort: on

_LOGGER = logging.getLogger(__name__)

_TRANSIENT_CODES = {
    grpc.StatusCode.UNAVAILABLE,
    grpc.StatusCode.DEADLINE_EXCEEDED,
    grpc.StatusCode.INTERNAL,
}


class AjaxGrpcClient:
    """High-level gRPC client for the Ajax mobile gateway."""

    def __init__(
        self,
        email: str,
        password: str | None = None,
        device_id: str | None = None,
        app_label: str = APPLICATION_LABEL,
        host: str = GRPC_HOST,
        port: int = GRPC_PORT,
        password_hash: str | None = None,
    ) -> None:
        self._host = host
        self._port = port
        self._session = AjaxSession(device_id=device_id, app_label=app_label)
        if password_hash is not None:
            self._session.set_credentials_hashed(email, password_hash)
        elif password is not None:
            self._session.set_credentials(email, password)
        else:
            raise ValueError("Either password or password_hash must be provided")
        self._channel: grpc.aio.Channel | None = None
        self._rate_limit_timestamps: list[float] = []
        self._refresh_task: asyncio.Task[None] | None = None
        # Serializes channel recreation (#236) so a stream reconnect and a
        # poll failing on the same wedged channel don't churn it twice.
        self._reconnect_lock = asyncio.Lock()

    @property
    def session(self) -> AjaxSession:
        return self._session

    @property
    def is_connected(self) -> bool:
        return self._channel is not None and self._session.is_authenticated

    def _open_channel(self) -> grpc.aio.Channel:
        target = f"{self._host}:{self._port}"
        credentials = grpc.ssl_channel_credentials()
        return grpc.aio.secure_channel(target, credentials)

    async def connect(self) -> None:
        self._channel = self._open_channel()
        _LOGGER.debug("gRPC channel opened to %s:%s", self._host, self._port)

    async def reconnect_channel(self) -> None:
        """Tear down the current gRPC channel and open a fresh one.

        Used by long-lived consumers (the device stream) when the channel
        wedges after a transport-level failure (UNAVAILABLE / peer reset)
        and reconnecting onto it never recovers — the symptom behind #236,
        where a single stream error left the integration silent for hours
        until a full HA restart recreated the channel.

        The session token is preserved, so no re-login is needed: callers
        fetch the channel via `_get_channel()` at call time, so they
        transparently pick up the new one on their next call. The lock
        keeps a stream reconnect and a concurrent poll failure from
        recreating the channel twice.
        """
        async with self._reconnect_lock:
            old = self._channel
            if old is not None:
                try:
                    await old.close()
                except Exception:  # noqa: BLE001
                    _LOGGER.debug("Error closing stale gRPC channel", exc_info=True)
            self._channel = self._open_channel()
            _LOGGER.debug("gRPC channel recreated after transport failure")

    async def close(self) -> None:
        if self._refresh_task and not self._refresh_task.done():
            self._refresh_task.cancel()
            self._refresh_task = None
        if self._channel:
            await self._channel.close()
            self._channel = None
        self._session.clear_session()
        _LOGGER.debug("gRPC channel closed")

    async def logout(self) -> None:
        """Invalidate the current Ajax session server-side via LogoutService.

        Called only when the user permanently removes the integration —
        not on every reload. Reload paths must keep the session alive so
        the next restart can reuse the token instead of opening another
        active session in the Ajax account.
        """
        if self._channel is None or not self._session.is_authenticated:
            return
        from v3.mobilegwsvc.service.logout import (  # noqa: PLC0415
            endpoint_pb2_grpc,
            request_pb2,
        )

        try:
            stub = endpoint_pb2_grpc.LogoutServiceStub(self._channel)
            request = request_pb2.LogoutRequest()
            metadata = self._session.get_call_metadata()
            await stub.execute(request, metadata=metadata, timeout=10)
            _LOGGER.debug("Ajax session logged out (server-side invalidated)")
        except Exception:  # noqa: BLE001
            _LOGGER.debug("Logout call failed (best-effort)", exc_info=True)
        finally:
            self._session.clear_session()

    def _get_channel(self) -> grpc.aio.Channel:
        if self._channel is None:
            raise ConnectionError("gRPC channel not connected. Call connect() first.")
        return self._channel

    async def _check_rate_limit(self) -> None:
        now = time.monotonic()
        self._rate_limit_timestamps = [
            t for t in self._rate_limit_timestamps if now - t < RATE_LIMIT_WINDOW
        ]
        if len(self._rate_limit_timestamps) >= RATE_LIMIT_REQUESTS:
            wait = RATE_LIMIT_WINDOW - (now - self._rate_limit_timestamps[0])
            _LOGGER.warning("Rate limit reached, waiting %.1fs", wait)
            await asyncio.sleep(wait)
        self._rate_limit_timestamps.append(time.monotonic())

    async def _retry(
        self,
        coro_fn: Callable[[], Awaitable[Any]],
        max_retries: int = MAX_RETRIES,
        base_delay: float = 1.0,
    ) -> Any:  # noqa: ANN401
        last_error: Exception | None = None
        for attempt in range(max_retries):
            try:
                return await coro_fn()
            except grpc.aio.AioRpcError as e:
                if e.code() not in _TRANSIENT_CODES:
                    raise
                last_error = e
            except (ConnectionError, OSError) as e:
                last_error = e

            if attempt < max_retries - 1:
                delay = base_delay * (2**attempt) * (0.8 + 0.4 * random.random())
                _LOGGER.debug(
                    "Retry %d/%d after %.1fs: %s", attempt + 1, max_retries, delay, last_error
                )
                await asyncio.sleep(delay)

        raise last_error  # type: ignore[misc]

    async def call_unary(
        self,
        method_path: str,
        request: Any,  # noqa: ANN401
        response_type: Any,  # noqa: ANN401
        timeout: float = GRPC_TIMEOUT,
    ) -> Any:  # noqa: ANN401
        await self._check_rate_limit()
        metadata = self._session.get_call_metadata()

        async def _do_call() -> Any:  # noqa: ANN401
            # Re-fetch the channel on every attempt: if a stream reconnect
            # recreated it (#236) between retries, the retried call must use
            # the fresh channel rather than the dead one captured up front.
            channel = self._get_channel()
            method = channel.unary_unary(
                method_path,
                request_serializer=request.SerializeToString,
                response_deserializer=response_type.FromString,
            )
            return await method(request, metadata=metadata, timeout=timeout)

        return await self._retry(_do_call)

    async def login(self) -> None:
        """Authenticate with Ajax servers via gRPC."""
        channel = self._get_channel()
        stub = login_password_grpc.LoginByPasswordServiceStub(channel)
        params = self._session.get_login_params()

        request = login_password_req.LoginByPasswordRequest(
            email=params["email"],
            password_sha256_hash=params["password_sha256_hash"],
            user_role=user_role_pb2.USER_ROLE_USER,
        )

        metadata = self._session.get_device_info_metadata()

        response = await stub.execute(request, metadata=metadata, timeout=GRPC_TIMEOUT)

        if response.HasField("success"):
            token_hex = response.success.session_token.hex()
            user_hex_id = response.success.lite_account.user_hex_id
            self._session.set_session(token_hex, user_hex_id)
            _LOGGER.debug("Logged in as %s", user_hex_id)
        elif response.HasField("failure"):
            error_type = response.failure.WhichOneof("error")
            if error_type == "two_fa_required":
                raise TwoFactorRequiredError(response.failure.two_fa_required.request_id)
            elif error_type == "invalid_credentials":
                raise AuthenticationError("Invalid email or password")
            elif error_type == "account_locked":
                raise AuthenticationError("Account is locked")
            elif error_type == "account_not_confirmed":
                raise AuthenticationError("Account not confirmed")
            else:
                raise AuthenticationError(f"Login failed: {error_type}")

    async def login_totp(self, email: str, request_id: str, totp_code: str) -> None:
        """Complete 2FA authentication by submitting the TOTP code."""
        channel = self._get_channel()
        stub = login_totp_grpc.LoginByTotpServiceStub(channel)

        request = login_totp_req.LoginByTotpRequest(
            email=email,
            user_role=user_role_pb2.USER_ROLE_USER,
            totp=totp_code,
            request_id=request_id,
        )

        metadata = self._session.get_device_info_metadata()
        response = await stub.execute(request, metadata=metadata, timeout=GRPC_TIMEOUT)

        if response.HasField("success"):
            token_hex = response.success.session_token.hex()
            user_hex_id = response.success.lite_account.user_hex_id
            self._session.set_session(token_hex, user_hex_id)
            _LOGGER.debug("2FA login successful as %s", user_hex_id)
        elif response.HasField("failure"):
            error_type = response.failure.WhichOneof("error")
            if error_type == "invalid_totp":
                raise AuthenticationError("Invalid TOTP code")
            elif error_type == "account_locked":
                raise AuthenticationError("Account is locked")
            else:
                raise AuthenticationError(f"2FA login failed: {error_type}")

    async def call_server_stream(
        self,
        method_path: str,
        request: Any,  # noqa: ANN401
        response_type: Any,  # noqa: ANN401
        timeout: float | None = None,
    ) -> Any:  # noqa: ANN401
        await self._check_rate_limit()
        channel = self._get_channel()
        metadata = self._session.get_call_metadata()

        method = channel.unary_stream(
            method_path,
            request_serializer=request.SerializeToString,
            response_deserializer=response_type.FromString,
        )
        return method(request, metadata=metadata, timeout=timeout)
