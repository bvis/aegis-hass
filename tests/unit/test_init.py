"""Tests for the integration __init__.py setup."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest


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
                return_value=MagicMock(connect=AsyncMock(), session=MagicMock()),
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
