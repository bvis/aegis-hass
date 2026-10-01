"""Tests for the integration __init__.py setup."""

from __future__ import annotations

from typing import TYPE_CHECKING
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

if TYPE_CHECKING:
    from collections.abc import Iterator


@pytest.fixture(autouse=True)
def _device_registry_for_bare_hass() -> Iterator[None]:
    """Give `async_setup_entry` a device registry to pre-register hubs into (#444).

    These tests drive setup with a bare `MagicMock` hass whose `data` is an
    empty dict. Since HA 2026.8 `device_registry.async_get` raises
    `RuntimeError("Device registry not set up")` in that situation instead of
    lazily creating one, so without this every setup test breaks on a current
    core (aavdberg saw 12 failures on #447/#449). A test that needs a specific
    registry patches `dr.async_get` itself; the inner patch wins.
    """
    with patch("custom_components.aegis_ajax.dr.async_get", return_value=MagicMock()):
        yield


class TestAsyncSetupEntry:
    @pytest.mark.asyncio
    async def test_setup_entry_schedules_fcm_as_background_task(self) -> None:
        # Regression for #112 — FCM startup must not block setup. The
        # registration round-trip (Firebase + Ajax push token) takes
        # several seconds; awaiting it here used to push HA past the
        # "integration taking too long" boot threshold. Now scheduled
        # via `entry.async_create_background_task` so setup returns
        # immediately and FCM connects in the background.
        from custom_components.aegis_ajax import async_setup_entry

        hass = MagicMock()
        hass.data = {}
        hass.config_entries.async_forward_entry_setups = AsyncMock(return_value=True)
        entry = MagicMock()
        entry.entry_id = "entry-bg"
        entry.data = {"email": "x@y", "password_hash": "h", "spaces": ["s1"]}
        entry.options = {}

        mock_client = MagicMock()
        mock_client.connect = AsyncMock()
        mock_client.session = MagicMock()
        mock_coordinator = MagicMock()
        mock_coordinator.async_config_entry_first_refresh = AsyncMock()
        mock_coordinator.async_start_push_notifications = MagicMock()  # not awaited directly

        with (
            patch("custom_components.aegis_ajax.AjaxGrpcClient", return_value=mock_client),
            patch(
                "custom_components.aegis_ajax.AjaxCobrandedCoordinator",
                return_value=mock_coordinator,
            ),
        ):
            await async_setup_entry(hass, entry)

        # FCM startup should be scheduled, not awaited synchronously.
        mock_coordinator.async_start_push_notifications.assert_called_once()
        entry.async_create_background_task.assert_called_once()
        kwargs = entry.async_create_background_task.call_args.kwargs
        assert kwargs.get("name", "").startswith("aegis_ajax_fcm_start_")

    @pytest.mark.asyncio
    async def test_setup_defaults_app_label_to_the_ajax_application_label(self) -> None:
        # Every config-flow path falls back to APPLICATION_LABEL, but setup and
        # the FCM/logout paths used to fall back to "". Matching the flow is
        # right on its own merits, and `""` is the one label value known to
        # fail: it is rejected at *login* with `bad_request` in
        # `LoginByPasswordResponse.failure`.
        #
        # Deliberately not claimed: that Ajax binds the token to the label. A
        # probe against the real backend while scoping #99 found the label is
        # opaque and unvalidated — a nonsense label logs in and its token works
        # against `list_spaces` — and the UNAUTHENTICATED loop this was first
        # blamed for turned out to be `close()` clearing the session before the
        # flow persisted it. So the fix stands, the mechanism does not.
        from custom_components.aegis_ajax import async_setup_entry
        from custom_components.aegis_ajax.const import APPLICATION_LABEL

        hass = MagicMock()
        hass.data = {}
        hass.config_entries.async_forward_entry_setups = AsyncMock(return_value=True)
        entry = MagicMock()
        entry.entry_id = "entry-label"
        entry.data = {"email": "x@y", "password_hash": "h", "spaces": ["s1"]}
        entry.options = {}

        mock_client = MagicMock()
        mock_client.connect = AsyncMock()
        mock_client.session = MagicMock()
        mock_coordinator = MagicMock()
        mock_coordinator.async_config_entry_first_refresh = AsyncMock()

        with (
            patch(
                "custom_components.aegis_ajax.AjaxGrpcClient", return_value=mock_client
            ) as client_cls,
            patch(
                "custom_components.aegis_ajax.AjaxCobrandedCoordinator",
                return_value=mock_coordinator,
            ),
        ):
            await async_setup_entry(hass, entry)

        assert client_cls.call_args.kwargs["app_label"] == APPLICATION_LABEL

    @pytest.mark.asyncio
    async def test_setup_entry_creates_coordinator(self) -> None:
        from custom_components.aegis_ajax import async_setup_entry

        hass = MagicMock()
        hass.data = {}
        hass.config_entries.async_forward_entry_setups = AsyncMock(return_value=True)

        entry = MagicMock()
        entry.entry_id = "entry-1"
        entry.data = {
            "email": "test@example.com",
            "password_hash": "abc123hash",
            "spaces": ["s1"],
        }
        entry.options = {"poll_interval": 30}

        mock_client = MagicMock()
        mock_client.connect = AsyncMock()
        mock_client.session = MagicMock()

        mock_coordinator = MagicMock()
        mock_coordinator.async_config_entry_first_refresh = AsyncMock()
        mock_coordinator.async_start_push_notifications = AsyncMock()

        with (
            patch(
                "custom_components.aegis_ajax.AjaxGrpcClient", return_value=mock_client
            ) as mock_cls,
            patch(
                "custom_components.aegis_ajax.AjaxCobrandedCoordinator",
                return_value=mock_coordinator,
            ),
        ):
            result = await async_setup_entry(hass, entry)

        assert result is True
        assert entry.runtime_data is mock_coordinator
        # Verify client was created with password_hash, not password
        mock_cls.assert_called_once()
        call_kwargs = mock_cls.call_args[1]
        assert call_kwargs.get("password_hash") == "abc123hash"
        assert "password" not in call_kwargs or call_kwargs.get("password") is None

    @pytest.mark.asyncio
    async def test_setup_entry_passes_delay_panel_states_option(self) -> None:
        # #454: the coordinator learns the opt-in at construction; the options
        # listener reloads the entry, so a toggle always reaches it.
        from custom_components.aegis_ajax import async_setup_entry
        from custom_components.aegis_ajax.const import CONF_DELAY_PANEL_STATES

        hass = MagicMock()
        hass.data = {}
        hass.config_entries.async_forward_entry_setups = AsyncMock(return_value=True)
        entry = MagicMock()
        entry.entry_id = "entry-1"
        entry.data = {"email": "t@example.com", "password_hash": "h", "spaces": ["s1"]}
        entry.options = {CONF_DELAY_PANEL_STATES: True}
        mock_client = MagicMock()
        mock_client.connect = AsyncMock()
        mock_coordinator = MagicMock()
        mock_coordinator.async_config_entry_first_refresh = AsyncMock()
        mock_coordinator.async_start_push_notifications = AsyncMock()

        with (
            patch("custom_components.aegis_ajax.AjaxGrpcClient", return_value=mock_client),
            patch(
                "custom_components.aegis_ajax.AjaxCobrandedCoordinator",
                return_value=mock_coordinator,
            ) as coord_cls,
        ):
            await async_setup_entry(hass, entry)

        assert coord_cls.call_args.kwargs["delay_panel_states"] is True

    @pytest.mark.asyncio
    async def test_setup_registers_hub_devices_before_forwarding_platforms(self) -> None:
        # #444: children link to their hub with `via_device_id`, which HA
        # rejects unless the hub is already a registered device. Platforms
        # add entities in no particular order, so the hub is registered here,
        # before any platform runs. Only hubs are pre-registered; every other
        # device is created by its own entities as before.
        from custom_components.aegis_ajax import async_setup_entry
        from custom_components.aegis_ajax.api.models import Device
        from custom_components.aegis_ajax.const import DeviceState

        def _device(device_id: str, device_type: str) -> Device:
            return Device(
                id=device_id,
                hub_id="HUB7",
                name=device_id,
                device_type=device_type,
                room_id=None,
                group_id=None,
                state=DeviceState.ONLINE,
                malfunctions=0,
                bypassed=False,
                statuses={},
                battery=None,
            )

        order: list[str] = []
        hass = MagicMock()
        hass.data = {}

        async def _forward(*_args: object, **_kwargs: object) -> bool:
            order.append("forward")
            return True

        hass.config_entries.async_forward_entry_setups = _forward
        entry = MagicMock()
        entry.entry_id = "entry-hub"
        entry.data = {"email": "x@y", "password_hash": "h", "spaces": ["s1"]}
        entry.options = {}

        mock_client = MagicMock()
        mock_client.connect = AsyncMock()
        mock_client.session = MagicMock()
        mock_coordinator = MagicMock()
        mock_coordinator.async_config_entry_first_refresh = AsyncMock()
        mock_coordinator.async_start_push_notifications = MagicMock()
        mock_coordinator.devices = {
            "HUB7": _device("HUB7", "hub_two_4g"),
            "DOOR1": _device("DOOR1", "door_protect"),
        }
        mock_coordinator.rooms = {}
        registry = MagicMock()
        registry.async_get_or_create.side_effect = lambda **_kw: order.append("register")

        with (
            patch("custom_components.aegis_ajax.AjaxGrpcClient", return_value=mock_client),
            patch(
                "custom_components.aegis_ajax.AjaxCobrandedCoordinator",
                return_value=mock_coordinator,
            ),
            patch("custom_components.aegis_ajax.dr.async_get", return_value=registry),
        ):
            await async_setup_entry(hass, entry)

        assert order == ["register", "forward"]
        registry.async_get_or_create.assert_called_once()
        kwargs = registry.async_get_or_create.call_args.kwargs
        assert kwargs["config_entry_id"] == "entry-hub"
        assert kwargs["identifiers"] == {("aegis_ajax", "HUB7")}
        assert kwargs["name"] == "HUB7"
        assert "via_device" not in kwargs and "via_device_id" not in kwargs

    @pytest.mark.asyncio
    async def test_setup_entry_closes_client_when_first_refresh_fails(self) -> None:
        """A failed first refresh must close the gRPC channel before propagating.

        HA retries setup after ConfigEntryNotReady, creating a fresh client each
        time. Leaving the previous channel open leaks one channel per retry.
        """
        from homeassistant.exceptions import ConfigEntryNotReady

        from custom_components.aegis_ajax import async_setup_entry

        hass = MagicMock()
        hass.data = {}
        hass.config_entries.async_forward_entry_setups = AsyncMock(return_value=True)

        entry = MagicMock()
        entry.entry_id = "entry-1"
        entry.data = {"email": "x@y", "password_hash": "h", "spaces": ["s1"]}
        entry.options = {}

        mock_client = MagicMock()
        mock_client.connect = AsyncMock()
        mock_client.close = AsyncMock()
        mock_client.session = MagicMock()

        mock_coordinator = MagicMock()
        mock_coordinator.async_config_entry_first_refresh = AsyncMock(
            side_effect=ConfigEntryNotReady("server unreachable")
        )

        with (
            patch("custom_components.aegis_ajax.AjaxGrpcClient", return_value=mock_client),
            patch(
                "custom_components.aegis_ajax.AjaxCobrandedCoordinator",
                return_value=mock_coordinator,
            ),
            pytest.raises(ConfigEntryNotReady),
        ):
            await async_setup_entry(hass, entry)

        mock_client.close.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_setup_entry_with_legacy_password(self) -> None:
        """Test backward compatibility: legacy entries with plaintext password still work."""
        from custom_components.aegis_ajax import async_setup_entry

        hass = MagicMock()
        hass.data = {}
        hass.config_entries.async_forward_entry_setups = AsyncMock(return_value=True)

        entry = MagicMock()
        entry.entry_id = "entry-legacy"
        entry.data = {
            "email": "test@example.com",
            "password": "secret",
            "spaces": ["s1"],
        }
        entry.options = {}

        mock_client = MagicMock()
        mock_client.connect = AsyncMock()
        mock_client.session = MagicMock()

        mock_coordinator = MagicMock()
        mock_coordinator.async_config_entry_first_refresh = AsyncMock()
        mock_coordinator.async_start_push_notifications = AsyncMock()

        with (
            patch(
                "custom_components.aegis_ajax.AjaxGrpcClient", return_value=mock_client
            ) as mock_cls,
            patch(
                "custom_components.aegis_ajax.AjaxCobrandedCoordinator",
                return_value=mock_coordinator,
            ),
        ):
            result = await async_setup_entry(hass, entry)

        assert result is True
        # Verify client was created with plaintext password (legacy path)
        mock_cls.assert_called_once()
        call_kwargs = mock_cls.call_args[1]
        assert call_kwargs.get("password") == "secret"

    @pytest.mark.asyncio
    async def test_setup_entry_does_not_restore_session_token(self) -> None:
        """Ensure session token is no longer read from config entry data."""
        from custom_components.aegis_ajax import async_setup_entry

        hass = MagicMock()
        hass.data = {}
        hass.config_entries.async_forward_entry_setups = AsyncMock(return_value=True)

        entry = MagicMock()
        entry.entry_id = "entry-2"
        entry.data = {
            "email": "test@example.com",
            "password_hash": "abc123hash",
            "spaces": ["s1"],
        }
        entry.options = {}

        mock_client = MagicMock()
        mock_client.connect = AsyncMock()
        mock_client.session = MagicMock()

        mock_coordinator = MagicMock()
        mock_coordinator.async_config_entry_first_refresh = AsyncMock()
        mock_coordinator.async_start_push_notifications = AsyncMock()

        with (
            patch("custom_components.aegis_ajax.AjaxGrpcClient", return_value=mock_client),
            patch(
                "custom_components.aegis_ajax.AjaxCobrandedCoordinator",
                return_value=mock_coordinator,
            ),
        ):
            result = await async_setup_entry(hass, entry)

        assert result is True
        # Session token should NOT be restored — authentication happens fresh via coordinator
        mock_client.session.set_session.assert_not_called()


class TestProtoDescriptorCollisionGuard:
    """#151 — surface a remediation hint when protobuf descriptor pool collides."""

    def test_logs_remediation_for_duplicate_file_name(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        import logging as _logging

        from custom_components.aegis_ajax import _log_proto_descriptor_collision

        exc = TypeError(
            "Couldn't build proto file into descriptor pool: "
            "duplicate file name systems/ajax/api/ecosystem/v2/hubsvc/"
            "commonmodels/object_type.proto"
        )
        with caplog.at_level(_logging.ERROR, logger="custom_components.aegis_ajax"):
            _log_proto_descriptor_collision(exc)

        assert any("backup copy" in r.message for r in caplog.records), (
            "remediation hint should mention the backup-folder scenario"
        )
        assert any("Ajax-related custom integration" in r.message for r in caplog.records), (
            "remediation hint should mention the cross-integration scenario"
        )
        assert any("custom_components" in r.message for r in caplog.records)

    def test_no_log_for_unrelated_typeerror(self, caplog: pytest.LogCaptureFixture) -> None:
        import logging as _logging

        from custom_components.aegis_ajax import _log_proto_descriptor_collision

        with caplog.at_level(_logging.ERROR, logger="custom_components.aegis_ajax"):
            _log_proto_descriptor_collision(TypeError("something else broke"))

        assert not caplog.records, "guard should only fire for descriptor-pool collisions"


class TestOptionsUpdateListener:
    @pytest.mark.asyncio
    async def test_options_change_triggers_reload(self) -> None:
        from custom_components.aegis_ajax import _async_options_update_listener

        hass = MagicMock()
        hass.config_entries.async_reload = AsyncMock()

        entry = MagicMock()
        entry.entry_id = "entry-1"

        await _async_options_update_listener(hass, entry)

        hass.config_entries.async_reload.assert_awaited_once_with("entry-1")

    @pytest.mark.asyncio
    async def test_setup_registers_update_listener(self) -> None:
        from custom_components.aegis_ajax import async_setup_entry

        hass = MagicMock()
        hass.data = {}
        hass.config_entries.async_forward_entry_setups = AsyncMock(return_value=True)

        entry = MagicMock()
        entry.entry_id = "entry-1"
        entry.data = {
            "email": "test@example.com",
            "password_hash": "abc123hash",
            "spaces": ["s1"],
        }
        entry.options = {}

        mock_client = MagicMock()
        mock_client.connect = AsyncMock()
        mock_client.session = MagicMock()

        mock_coordinator = MagicMock()
        mock_coordinator.async_config_entry_first_refresh = AsyncMock()
        mock_coordinator.async_start_push_notifications = AsyncMock()

        with (
            patch("custom_components.aegis_ajax.AjaxGrpcClient", return_value=mock_client),
            patch(
                "custom_components.aegis_ajax.AjaxCobrandedCoordinator",
                return_value=mock_coordinator,
            ),
        ):
            await async_setup_entry(hass, entry)

        entry.add_update_listener.assert_called_once()


class TestAutoLabeling:
    @pytest.mark.asyncio
    async def test_apply_labels_creates_labels_and_assigns(self) -> None:
        from custom_components.aegis_ajax import _async_apply_labels
        from custom_components.aegis_ajax.const import LABELS

        hass = MagicMock()
        entry = MagicMock()
        entry.entry_id = "entry-1"

        # Mock label registry
        mock_label_reg = MagicMock()
        mock_label_reg.async_get_label.return_value = None  # labels don't exist yet

        # Mock entity registry with a door sensor
        mock_entity = MagicMock()
        mock_entity.entity_id = "binary_sensor.porta_door"
        mock_entity.original_device_class = "door"
        mock_entity.labels = set()

        mock_entity_reg = MagicMock()
        mock_entries_fn = MagicMock(return_value=[mock_entity])

        with (
            patch("homeassistant.helpers.label_registry.async_get", return_value=mock_label_reg),
            patch("homeassistant.helpers.entity_registry.async_get", return_value=mock_entity_reg),
            patch(
                "homeassistant.helpers.entity_registry.async_entries_for_config_entry",
                mock_entries_fn,
            ),
        ):
            await _async_apply_labels(hass, entry)

        # Labels should be created
        assert mock_label_reg.async_create.call_count == len(LABELS)

        # Entity should get aegis_door label
        mock_entity_reg.async_update_entity.assert_called_once()
        call_kwargs = mock_entity_reg.async_update_entity.call_args
        assert "aegis_door" in call_kwargs[1]["labels"]

    @pytest.mark.asyncio
    async def test_apply_labels_preserves_existing_labels(self) -> None:
        from custom_components.aegis_ajax import _async_apply_labels

        hass = MagicMock()
        entry = MagicMock()
        entry.entry_id = "entry-1"

        mock_label_reg = MagicMock()
        mock_label_reg.async_get_label.return_value = MagicMock()  # labels exist

        mock_entity = MagicMock()
        mock_entity.entity_id = "binary_sensor.porta_tamper"
        mock_entity.original_device_class = "tamper"
        mock_entity.labels = {"user_custom_label"}

        mock_entity_reg = MagicMock()

        with (
            patch("homeassistant.helpers.label_registry.async_get", return_value=mock_label_reg),
            patch("homeassistant.helpers.entity_registry.async_get", return_value=mock_entity_reg),
            patch(
                "homeassistant.helpers.entity_registry.async_entries_for_config_entry",
                return_value=[mock_entity],
            ),
        ):
            await _async_apply_labels(hass, entry)

        # Should preserve user label and add aegis_tamper
        call_kwargs = mock_entity_reg.async_update_entity.call_args[1]
        assert "user_custom_label" in call_kwargs["labels"]
        assert "aegis_tamper" in call_kwargs["labels"]

    @pytest.mark.asyncio
    async def test_apply_labels_skips_when_already_labeled(self) -> None:
        from custom_components.aegis_ajax import _async_apply_labels

        hass = MagicMock()
        entry = MagicMock()
        entry.entry_id = "entry-1"

        mock_label_reg = MagicMock()
        mock_label_reg.async_get_label.return_value = MagicMock()

        mock_entity = MagicMock()
        mock_entity.entity_id = "binary_sensor.porta_door"
        mock_entity.original_device_class = "door"
        mock_entity.labels = {"aegis_door"}  # already labeled

        mock_entity_reg = MagicMock()

        with (
            patch("homeassistant.helpers.label_registry.async_get", return_value=mock_label_reg),
            patch("homeassistant.helpers.entity_registry.async_get", return_value=mock_entity_reg),
            patch(
                "homeassistant.helpers.entity_registry.async_entries_for_config_entry",
                return_value=[mock_entity],
            ),
        ):
            await _async_apply_labels(hass, entry)

        # Should not update since label already present
        mock_entity_reg.async_update_entity.assert_not_called()

    @pytest.mark.asyncio
    async def test_apply_labels_hub_entities_by_pattern(self) -> None:
        from custom_components.aegis_ajax import _async_apply_labels

        hass = MagicMock()
        entry = MagicMock()
        entry.entry_id = "entry-1"

        mock_label_reg = MagicMock()
        mock_label_reg.async_get_label.return_value = MagicMock()

        mock_entity = MagicMock()
        mock_entity.entity_id = "sensor.alarma_ajax_ip_ethernet"
        mock_entity.original_device_class = None
        mock_entity.labels = set()

        mock_entity_reg = MagicMock()

        with (
            patch("homeassistant.helpers.label_registry.async_get", return_value=mock_label_reg),
            patch("homeassistant.helpers.entity_registry.async_get", return_value=mock_entity_reg),
            patch(
                "homeassistant.helpers.entity_registry.async_entries_for_config_entry",
                return_value=[mock_entity],
            ),
        ):
            await _async_apply_labels(hass, entry)

        call_kwargs = mock_entity_reg.async_update_entity.call_args[1]
        assert "aegis_hub" in call_kwargs["labels"]


class TestAutoCreateLabelsOption:
    """Verify the `auto_create_labels` OptionsFlow toggle gates label creation."""

    def _make_entry(self, options: dict) -> MagicMock:
        entry = MagicMock()
        entry.entry_id = "entry-1"
        entry.data = {
            "email": "test@example.com",
            "password_hash": "abc123hash",
            "spaces": ["s1"],
        }
        entry.options = options
        return entry

    async def _run_setup(self, entry: MagicMock) -> MagicMock:
        from custom_components.aegis_ajax import async_setup_entry

        hass = MagicMock()
        hass.data = {}
        hass.config_entries.async_forward_entry_setups = AsyncMock(return_value=True)

        mock_client = MagicMock()
        mock_client.connect = AsyncMock()
        mock_client.session = MagicMock()

        mock_coordinator = MagicMock()
        mock_coordinator.async_config_entry_first_refresh = AsyncMock()
        mock_coordinator.async_start_push_notifications = AsyncMock()

        apply_labels_mock = AsyncMock()
        with (
            patch("custom_components.aegis_ajax.AjaxGrpcClient", return_value=mock_client),
            patch(
                "custom_components.aegis_ajax.AjaxCobrandedCoordinator",
                return_value=mock_coordinator,
            ),
            patch("custom_components.aegis_ajax._async_apply_labels", apply_labels_mock),
        ):
            await async_setup_entry(hass, entry)
        return apply_labels_mock

    @pytest.mark.asyncio
    async def test_auto_create_labels_default_calls_apply(self) -> None:
        entry = self._make_entry(options={})
        apply_mock = await self._run_setup(entry)
        apply_mock.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_auto_create_labels_explicit_true_calls_apply(self) -> None:
        entry = self._make_entry(options={"auto_create_labels": True})
        apply_mock = await self._run_setup(entry)
        apply_mock.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_auto_create_labels_false_skips_apply(self) -> None:
        entry = self._make_entry(options={"auto_create_labels": False})
        apply_mock = await self._run_setup(entry)
        apply_mock.assert_not_awaited()


class TestPressPanicButtonHandler:
    """Verify the safety guards on _async_handle_press_panic_button."""

    def _make_call(self, data: dict) -> MagicMock:
        call = MagicMock()
        call.data = data
        call.context.user_id = None
        return call

    @pytest.mark.asyncio
    async def test_missing_confirm_raises(self) -> None:
        from homeassistant.exceptions import ServiceValidationError

        from custom_components.aegis_ajax import _async_handle_press_panic_button

        hass = MagicMock()
        with pytest.raises(ServiceValidationError, match="confirm"):
            await _async_handle_press_panic_button(hass, self._make_call({}))

    @pytest.mark.asyncio
    async def test_confirm_false_raises(self) -> None:
        from homeassistant.exceptions import ServiceValidationError

        from custom_components.aegis_ajax import _async_handle_press_panic_button

        hass = MagicMock()
        with pytest.raises(ServiceValidationError, match="confirm"):
            await _async_handle_press_panic_button(hass, self._make_call({"confirm": False}))

    @pytest.mark.asyncio
    async def test_confirm_true_no_target_raises(self) -> None:
        from homeassistant.exceptions import ServiceValidationError

        from custom_components.aegis_ajax import _async_handle_press_panic_button

        with patch(
            "custom_components.aegis_ajax._resolve_target_space_ids",
            return_value=[],
        ):
            hass = MagicMock()
            with pytest.raises(ServiceValidationError, match="no Aegis alarm panel"):
                await _async_handle_press_panic_button(hass, self._make_call({"confirm": True}))

    @pytest.mark.asyncio
    async def test_confirm_true_invokes_api(self) -> None:
        from custom_components.aegis_ajax import _async_handle_press_panic_button

        coordinator = MagicMock()
        coordinator.spaces_api.press_panic_button = AsyncMock()

        with patch(
            "custom_components.aegis_ajax._resolve_target_space_ids",
            return_value=[(coordinator, "space-1")],
        ):
            hass = MagicMock()
            await _async_handle_press_panic_button(
                hass,
                self._make_call({"confirm": True, "latitude": 1.0, "longitude": 2.0}),
            )

        coordinator.spaces_api.press_panic_button.assert_awaited_once_with(
            "space-1", latitude=1.0, longitude=2.0
        )


class TestAsyncUnloadEntry:
    @pytest.mark.asyncio
    async def test_unload_entry_success(self) -> None:
        from custom_components.aegis_ajax import async_unload_entry

        mock_coordinator = MagicMock()
        mock_coordinator.async_shutdown = AsyncMock()

        hass = MagicMock()
        hass.config_entries.async_unload_platforms = AsyncMock(return_value=True)

        entry = MagicMock()
        entry.entry_id = "entry-1"
        entry.runtime_data = mock_coordinator

        result = await async_unload_entry(hass, entry)

        assert result is True
        mock_coordinator.async_shutdown.assert_called_once()

    @pytest.mark.asyncio
    async def test_unload_entry_failure_does_not_clean_up(self) -> None:
        from custom_components.aegis_ajax import async_unload_entry

        mock_coordinator = MagicMock()
        mock_coordinator.async_shutdown = AsyncMock()

        hass = MagicMock()
        hass.config_entries.async_unload_platforms = AsyncMock(return_value=False)

        entry = MagicMock()
        entry.entry_id = "entry-1"
        entry.runtime_data = mock_coordinator

        result = await async_unload_entry(hass, entry)

        assert result is False
        mock_coordinator.async_shutdown.assert_not_called()


class TestSessionPersistence:
    """Verify the session-token write-back path between coordinator and entry.data."""

    @pytest.mark.asyncio
    async def test_setup_passes_persist_callback_to_coordinator(self) -> None:
        from custom_components.aegis_ajax import async_setup_entry

        hass = MagicMock()
        hass.data = {}
        hass.config_entries.async_forward_entry_setups = AsyncMock(return_value=True)

        entry = MagicMock()
        entry.entry_id = "entry-1"
        entry.data = {
            "email": "user@example.com",
            "password_hash": "hash",
            "spaces": ["s1"],
            "session_token": "tok-old",
            "user_hex_id": "hex-1",
        }
        entry.options = {}

        mock_client = MagicMock()
        mock_client.connect = AsyncMock()
        mock_client.session = MagicMock()
        mock_client.session.device_id = "dev-1"

        captured: dict[str, object] = {}

        def _record(*args: object, **kwargs: object) -> MagicMock:
            captured["kwargs"] = kwargs
            cm = MagicMock()
            cm.async_config_entry_first_refresh = AsyncMock()
            cm.async_start_push_notifications = AsyncMock()
            return cm

        with (
            patch("custom_components.aegis_ajax.AjaxGrpcClient", return_value=mock_client),
            patch(
                "custom_components.aegis_ajax.AjaxCobrandedCoordinator",
                side_effect=_record,
            ),
        ):
            await async_setup_entry(hass, entry)

        # Coordinator received an on_session_persist callback
        assert "on_session_persist" in captured["kwargs"]
        callback = captured["kwargs"]["on_session_persist"]
        assert callable(callback)

        # Calling the callback writes the new credentials back to the entry
        callback("tok-new", "hex-1")
        hass.config_entries.async_update_entry.assert_called_once()
        new_data = hass.config_entries.async_update_entry.call_args.kwargs["data"]
        assert new_data["session_token"] == "tok-new"
        assert new_data["user_hex_id"] == "hex-1"
        # Ajax binds the token to the device id, so the pair has to be stored
        # together or the next restart presents a mismatched combination.
        assert new_data["device_id"] == "dev-1"

    @pytest.mark.asyncio
    async def test_persist_callback_skips_when_unchanged(self) -> None:
        from custom_components.aegis_ajax import async_setup_entry

        hass = MagicMock()
        hass.data = {}
        hass.config_entries.async_forward_entry_setups = AsyncMock(return_value=True)

        entry = MagicMock()
        entry.entry_id = "entry-1"
        entry.data = {
            "email": "user@example.com",
            "password_hash": "hash",
            "spaces": ["s1"],
            "session_token": "tok-current",
            "user_hex_id": "hex-1",
            "device_id": "dev-1",
        }
        entry.options = {}

        captured: dict[str, object] = {}

        def _record(*args: object, **kwargs: object) -> MagicMock:
            captured["kwargs"] = kwargs
            cm = MagicMock()
            cm.async_config_entry_first_refresh = AsyncMock()
            cm.async_start_push_notifications = AsyncMock()
            return cm

        with (
            patch(
                "custom_components.aegis_ajax.AjaxGrpcClient",
                return_value=MagicMock(connect=AsyncMock(), session=MagicMock(device_id="dev-1")),
            ),
            patch(
                "custom_components.aegis_ajax.AjaxCobrandedCoordinator",
                side_effect=_record,
            ),
        ):
            await async_setup_entry(hass, entry)

        callback = captured["kwargs"]["on_session_persist"]
        # Same token as already stored — must not write
        callback("tok-current", "hex-1")
        hass.config_entries.async_update_entry.assert_not_called()

    @pytest.mark.asyncio
    async def test_persist_callback_writes_device_id_for_entries_created_without_one(
        self,
    ) -> None:
        """Entries predating the stored device id must gain one on the next login.

        Without a stored id, `async_setup_entry` generates a fresh uuid every
        setup. The token persisted under it is then presented under a different
        id on the next restart, Ajax rejects it, and the forced re-login demands
        2FA — a loop the user cannot escape.
        """
        from custom_components.aegis_ajax import async_setup_entry

        hass = MagicMock()
        hass.data = {}
        hass.config_entries.async_forward_entry_setups = AsyncMock(return_value=True)

        entry = MagicMock()
        entry.entry_id = "entry-1"
        entry.data = {
            "email": "user@example.com",
            "password_hash": "hash",
            "spaces": ["s1"],
        }
        entry.options = {}

        captured: dict[str, object] = {}

        def _record(*args: object, **kwargs: object) -> MagicMock:
            captured["kwargs"] = kwargs
            cm = MagicMock()
            cm.async_config_entry_first_refresh = AsyncMock()
            cm.async_start_push_notifications = AsyncMock()
            return cm

        with (
            patch(
                "custom_components.aegis_ajax.AjaxGrpcClient",
                return_value=MagicMock(
                    connect=AsyncMock(), session=MagicMock(device_id="dev-generated")
                ),
            ),
            patch(
                "custom_components.aegis_ajax.AjaxCobrandedCoordinator",
                side_effect=_record,
            ),
        ):
            await async_setup_entry(hass, entry)

        captured["kwargs"]["on_session_persist"]("tok-new", "hex-1")

        new_data = hass.config_entries.async_update_entry.call_args.kwargs["data"]
        assert new_data["device_id"] == "dev-generated"


class TestAsyncRemoveEntry:
    """Verify the LogoutService call path on permanent removal."""

    @pytest.mark.asyncio
    async def test_remove_entry_with_session_calls_logout(self) -> None:
        from custom_components.aegis_ajax import async_remove_entry

        hass = MagicMock()
        entry = MagicMock()
        entry.data = {
            "email": "user@example.com",
            "password_hash": "hash",
            "session_token": "tok",
            "user_hex_id": "hex",
        }

        mock_client = MagicMock()
        mock_client.connect = AsyncMock()
        mock_client.logout = AsyncMock()
        mock_client.close = AsyncMock()
        mock_client.session = MagicMock()

        with patch("custom_components.aegis_ajax.AjaxGrpcClient", return_value=mock_client):
            await async_remove_entry(hass, entry)

        mock_client.connect.assert_awaited_once()
        mock_client.logout.assert_awaited_once()
        mock_client.close.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_remove_entry_without_session_skips_logout(self) -> None:
        from custom_components.aegis_ajax import async_remove_entry

        hass = MagicMock()
        entry = MagicMock()
        entry.data = {"email": "user@example.com", "password_hash": "hash"}

        mock_client = MagicMock()
        mock_client.logout = AsyncMock()

        with patch("custom_components.aegis_ajax.AjaxGrpcClient", return_value=mock_client):
            await async_remove_entry(hass, entry)

        mock_client.logout.assert_not_called()


class TestSetPhotoOnDemandModeHandler:
    """Verify guards + dispatch of _async_handle_set_photo_on_demand_mode."""

    def _make_call(self, data: dict) -> MagicMock:
        call = MagicMock()
        call.data = data
        call.context.user_id = None
        return call

    @pytest.mark.asyncio
    async def test_missing_both_channels_raises(self) -> None:
        from homeassistant.exceptions import ServiceValidationError

        from custom_components.aegis_ajax import _async_handle_set_photo_on_demand_mode

        hass = MagicMock()
        with pytest.raises(ServiceValidationError, match="`user`.*`scenario`"):
            await _async_handle_set_photo_on_demand_mode(hass, self._make_call({}))

    @pytest.mark.asyncio
    async def test_no_target_raises(self) -> None:
        from homeassistant.exceptions import ServiceValidationError

        from custom_components.aegis_ajax import _async_handle_set_photo_on_demand_mode

        with patch(
            "custom_components.aegis_ajax._resolve_target_space_ids",
            return_value=[],
        ):
            hass = MagicMock()
            with pytest.raises(ServiceValidationError, match="no Aegis alarm panel"):
                await _async_handle_set_photo_on_demand_mode(hass, self._make_call({"user": True}))

    @pytest.mark.asyncio
    async def test_dispatches_to_devices_api(self) -> None:
        from custom_components.aegis_ajax import _async_handle_set_photo_on_demand_mode

        coordinator = MagicMock()
        coordinator.devices_api.set_photo_on_demand_mode = AsyncMock()
        coordinator.spaces = {"space-1": MagicMock(hub_id="HUB-A")}

        with patch(
            "custom_components.aegis_ajax._resolve_target_space_ids",
            return_value=[(coordinator, "space-1")],
        ):
            hass = MagicMock()
            await _async_handle_set_photo_on_demand_mode(
                hass, self._make_call({"user": True, "scenario": False})
            )

        coordinator.devices_api.set_photo_on_demand_mode.assert_awaited_once_with(
            "HUB-A", user_enabled=True, scenario_enabled=False
        )

    @pytest.mark.asyncio
    async def test_skips_targets_without_hub_id(self) -> None:
        from custom_components.aegis_ajax import _async_handle_set_photo_on_demand_mode

        coordinator = MagicMock()
        coordinator.devices_api.set_photo_on_demand_mode = AsyncMock()
        coordinator.spaces = {"space-1": MagicMock(hub_id="")}

        with patch(
            "custom_components.aegis_ajax._resolve_target_space_ids",
            return_value=[(coordinator, "space-1")],
        ):
            hass = MagicMock()
            await _async_handle_set_photo_on_demand_mode(hass, self._make_call({"user": True}))

        coordinator.devices_api.set_photo_on_demand_mode.assert_not_called()


class TestAsyncRemoveConfigEntryDevice:
    """#422 — the HA-standard manual-delete hook. HA only offers "Delete"
    on a device page when the integration defines it."""

    def _device_entry(self, *identifiers: tuple[str, str]) -> MagicMock:
        device_entry = MagicMock()
        device_entry.identifiers = set(identifiers)
        return device_entry

    def _entry(self) -> MagicMock:
        coordinator = MagicMock()
        coordinator.devices = {"d1": MagicMock(), "hub-1": MagicMock()}
        coordinator.keyfobs = {"kf1": MagicMock()}
        entry = MagicMock()
        entry.runtime_data = coordinator
        return entry

    @pytest.mark.asyncio
    async def test_devices_the_hub_still_reports_stay(self) -> None:
        from custom_components.aegis_ajax import async_remove_config_entry_device
        from custom_components.aegis_ajax.const import DOMAIN

        entry = self._entry()
        hass = MagicMock()

        assert not await async_remove_config_entry_device(
            hass, entry, self._device_entry((DOMAIN, "d1"))
        )
        # The hub device is always tracked, so it is protected by the same gate.
        assert not await async_remove_config_entry_device(
            hass, entry, self._device_entry((DOMAIN, "hub-1"))
        )
        # Runtime-discovered keyfobs live outside coordinator.devices but are
        # just as alive.
        assert not await async_remove_config_entry_device(
            hass, entry, self._device_entry((DOMAIN, "kf1"))
        )

    @pytest.mark.asyncio
    async def test_devices_the_hub_no_longer_reports_may_go(self) -> None:
        from custom_components.aegis_ajax import async_remove_config_entry_device
        from custom_components.aegis_ajax.const import DOMAIN

        entry = self._entry()
        hass = MagicMock()

        assert await async_remove_config_entry_device(
            hass, entry, self._device_entry((DOMAIN, "ghost"))
        )

    @pytest.mark.asyncio
    async def test_unloaded_entry_allows_removal(self) -> None:
        """With no coordinator running nothing can vouch for the device;
        deleting is allowed — a wrong delete self-heals on next setup."""
        from custom_components.aegis_ajax import async_remove_config_entry_device
        from custom_components.aegis_ajax.const import DOMAIN

        entry = MagicMock(spec=[])  # no runtime_data attribute at all
        hass = MagicMock()

        assert await async_remove_config_entry_device(
            hass, entry, self._device_entry((DOMAIN, "d1"))
        )
