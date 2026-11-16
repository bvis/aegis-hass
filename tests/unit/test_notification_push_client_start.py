"""Starting the push client: what it is built with, and what a failed start leaves behind."""

from __future__ import annotations

from typing import TYPE_CHECKING
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from custom_components.aegis_ajax import notification as module
from custom_components.aegis_ajax.notification import AjaxNotificationListener

if TYPE_CHECKING:
    from collections.abc import Iterator


@pytest.fixture(autouse=True)
def _ha_sessions() -> Iterator[None]:
    with (
        patch("homeassistant.helpers.aiohttp_client.async_create_clientsession"),
        patch("homeassistant.helpers.aiohttp_client.async_get_clientsession"),
    ):
        yield


def _listener(entry_id: str = "entry-1") -> AjaxNotificationListener:
    hass = MagicMock()
    hass.async_add_executor_job = AsyncMock()
    listener = AjaxNotificationListener(
        hass=hass,
        coordinator=MagicMock(),
        fcm_project_id="mws-mobile-client---2",
        fcm_app_id="1:991608156148:android:" + "a" * 40,
        fcm_api_key="AIza" + "x" * 35,
        fcm_sender_id="991608156148",
        entry_id=entry_id,
    )
    listener._fcm_config = MagicMock(name="fcm_config")
    listener._credentials = {"fcm": "creds"}
    listener._last_persistent_id = "left-over"
    return listener


class _Started:
    """Patches around one start attempt; keeps the mocks to look at afterwards."""

    def __init__(self, client: MagicMock) -> None:
        self.client = client
        self._patches = (
            patch("firebase_messaging.FcmPushClient", return_value=client),
            patch("firebase_messaging.FcmPushClientConfig"),
            patch.object(module, "install_fcm_decrypt_guard"),
            patch.object(module, "attach_fcm_log_guard"),
            patch.object(module, "async_register_fcm_credentials_invalid"),
            patch.object(module.time, "monotonic", return_value=1234.5),
        )

    def __enter__(self) -> _Started:
        (self.client_cls, self.config_cls, self.decrypt_guard, self.log_guard, self.repair, _) = (
            p.start() for p in self._patches
        )
        return self

    def __exit__(self, *_exc: object) -> None:
        for p in reversed(self._patches):
            p.stop()


def _sync_client() -> MagicMock:
    client = MagicMock()
    client.start = MagicMock()
    return client


async def test_push_client_is_built_for_this_listener_and_started_off_the_loop() -> None:
    listener = _listener()
    with _Started(_sync_client()) as run:
        assert await listener._async_start_push_client() is True
    run.config_cls.assert_called_once_with(abort_on_sequential_error_count=3)
    run.client_cls.assert_called_once_with(
        callback=listener._on_notification,
        fcm_config=listener._fcm_config,
        credentials={"fcm": "creds"},
        config=run.config_cls.return_value,
    )
    run.log_guard.assert_called_once_with()
    run.decrypt_guard.assert_called_once_with(run.client)
    listener._hass.async_add_executor_job.assert_awaited_once_with(run.client.start)
    run.client.start.assert_not_called()
    assert listener._push_client is run.client
    assert listener._fcm_client_started_at == 1234.5
    assert listener._last_persistent_id is None
    run.repair.assert_not_called()


async def test_a_push_client_with_an_async_start_is_awaited_directly() -> None:
    listener = _listener()
    client = MagicMock()
    client.start = AsyncMock()
    with _Started(client):
        assert await listener._async_start_push_client() is True
    client.start.assert_awaited_once_with()
    listener._hass.async_add_executor_job.assert_not_awaited()


@pytest.mark.parametrize(
    ("entry_id", "register_repair", "repair_raised"),
    [("entry-1", True, True), ("entry-1", False, False), ("", True, False)],
)
async def test_a_failed_start_leaves_no_client_and_raises_the_repair_only_on_first_setup(
    entry_id: str, register_repair: bool, repair_raised: bool
) -> None:
    listener = _listener(entry_id)
    listener._hass.async_add_executor_job.side_effect = OSError("network down")
    with _Started(_sync_client()) as run:
        started = await listener._async_start_push_client(
            register_repair_on_failure=register_repair
        )
    assert started is False
    assert listener._push_client is None
    if repair_raised:
        run.repair.assert_called_once_with(listener._hass, entry_id="entry-1")
    else:
        run.repair.assert_not_called()


async def test_the_repair_is_raised_by_default_when_the_first_start_fails() -> None:
    listener = _listener()
    listener._hass.async_add_executor_job.side_effect = OSError("network down")
    with _Started(_sync_client()) as run:
        assert await listener._async_start_push_client() is False
    run.repair.assert_called_once_with(listener._hass, entry_id="entry-1")
