"""Tests for the repairs helper module."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from custom_components.aegis_ajax.const import DOMAIN
from custom_components.aegis_ajax.repairs import (
    ISSUE_FCM_CREDENTIALS_INVALID,
    ISSUE_FCM_NOT_CONFIGURED,
    ISSUE_GRPCIO_VERSION_MISMATCH,
    ISSUE_HTS_CHRONIC_FAILURE,
    ISSUE_HUB_OFFLINE_24H,
    MIN_GRPCIO_VERSION,
    FcmCredentialsRepairFlow,
    _parse_version,
    async_check_grpcio_version,
    async_clear_fcm_credentials_invalid,
    async_clear_fcm_not_configured,
    async_clear_hts_chronic_failure,
    async_clear_hub_offline,
    async_create_fix_flow,
    async_register_fcm_credentials_invalid,
    async_register_fcm_not_configured,
    async_register_hts_chronic_failure,
    async_register_hub_offline,
)


class TestHubOfflineRepair:
    def test_register_calls_create_with_per_space_id(self) -> None:
        hass = MagicMock()
        with patch("custom_components.aegis_ajax.repairs.ir.async_create_issue") as create:
            async_register_hub_offline(hass, space_id="s1", hub_name="Home", hours_offline=30)

        create.assert_called_once()
        args, kwargs = create.call_args
        assert args[0] is hass
        assert args[1] == DOMAIN
        # issue_id is namespaced per space so multi-space installs don't collide
        assert args[2] == f"{ISSUE_HUB_OFFLINE_24H}:s1"
        assert kwargs["translation_key"] == ISSUE_HUB_OFFLINE_24H
        assert kwargs["translation_placeholders"]["hub_name"] == "Home"
        assert kwargs["translation_placeholders"]["hours_offline"] == "30"
        assert kwargs["is_fixable"] is False

    def test_clear_calls_delete_with_same_id(self) -> None:
        hass = MagicMock()
        with patch("custom_components.aegis_ajax.repairs.ir.async_delete_issue") as delete:
            async_clear_hub_offline(hass, space_id="s1")

        delete.assert_called_once_with(hass, DOMAIN, f"{ISSUE_HUB_OFFLINE_24H}:s1")


class TestHtsChronicFailureRepair:
    def test_register_calls_create(self) -> None:
        hass = MagicMock()
        with patch("custom_components.aegis_ajax.repairs.ir.async_create_issue") as create:
            async_register_hts_chronic_failure(hass, space_id="s1", minutes_failing=45)

        create.assert_called_once()
        args, kwargs = create.call_args
        assert args[2] == f"{ISSUE_HTS_CHRONIC_FAILURE}:s1"
        assert kwargs["translation_placeholders"]["minutes_failing"] == "45"

    def test_clear_calls_delete(self) -> None:
        hass = MagicMock()
        with patch("custom_components.aegis_ajax.repairs.ir.async_delete_issue") as delete:
            async_clear_hts_chronic_failure(hass, space_id="s1")

        delete.assert_called_once_with(hass, DOMAIN, f"{ISSUE_HTS_CHRONIC_FAILURE}:s1")


class TestFcmCredentialsRepair:
    def test_register_per_entry(self) -> None:
        hass = MagicMock()
        with patch("custom_components.aegis_ajax.repairs.ir.async_create_issue") as create:
            async_register_fcm_credentials_invalid(hass, entry_id="entry-abc")

        create.assert_called_once()
        args, _ = create.call_args
        assert args[2] == f"{ISSUE_FCM_CREDENTIALS_INVALID}:entry-abc"

    def test_clear_per_entry(self) -> None:
        hass = MagicMock()
        with patch("custom_components.aegis_ajax.repairs.ir.async_delete_issue") as delete:
            async_clear_fcm_credentials_invalid(hass, entry_id="entry-abc")

        delete.assert_called_once_with(hass, DOMAIN, f"{ISSUE_FCM_CREDENTIALS_INVALID}:entry-abc")

    def test_register_marks_fixable_and_carries_entry_id_in_data(self) -> None:
        """is_fixable=True and the entry_id is in `data` so the fix flow can find it."""
        hass = MagicMock()
        with patch("custom_components.aegis_ajax.repairs.ir.async_create_issue") as create:
            async_register_fcm_credentials_invalid(hass, entry_id="entry-abc")

        kwargs = create.call_args.kwargs
        assert kwargs["is_fixable"] is True
        assert kwargs["data"] == {"entry_id": "entry-abc"}


class TestFcmNotConfiguredRepair:
    def test_register_per_entry(self) -> None:
        hass = MagicMock()
        with patch("custom_components.aegis_ajax.repairs.ir.async_create_issue") as create:
            async_register_fcm_not_configured(hass, entry_id="entry-abc")

        create.assert_called_once()
        args, kwargs = create.call_args
        assert args[2] == f"{ISSUE_FCM_NOT_CONFIGURED}:entry-abc"
        # `not configured` is a WARNING — feature missing, not an error. `invalid`
        # stays ERROR because the user actively configured wrong values.
        from homeassistant.helpers import issue_registry as ir  # noqa: PLC0415

        assert kwargs["severity"] == ir.IssueSeverity.WARNING
        assert kwargs["is_fixable"] is True
        assert kwargs["data"] == {"entry_id": "entry-abc"}

    def test_clear_per_entry(self) -> None:
        hass = MagicMock()
        with patch("custom_components.aegis_ajax.repairs.ir.async_delete_issue") as delete:
            async_clear_fcm_not_configured(hass, entry_id="entry-abc")

        delete.assert_called_once_with(hass, DOMAIN, f"{ISSUE_FCM_NOT_CONFIGURED}:entry-abc")


class TestParseVersion:
    def test_clean_semver(self) -> None:
        assert _parse_version("1.60.0") == (1, 60, 0)

    def test_two_components(self) -> None:
        assert _parse_version("1.60") == (1, 60)

    def test_pep440_pre_release_suffix_stripped(self) -> None:
        # `1.60.0rc1` should compare as if it were `1.60.0`. Stripping per
        # component preserves comparability with clean releases.
        assert _parse_version("1.60.0rc1") == (1, 60, 0)

    def test_post_release_suffix_stripped(self) -> None:
        # The `post1` segment has no leading digit so the parser stops there;
        # `1.60.0.post1` compares the same as `1.60.0`, which is the right
        # ordering for repair purposes (post-release ≥ release).
        assert _parse_version("1.60.0.post1") == (1, 60, 0)

    def test_garbage_returns_zero_so_repair_fires(self) -> None:
        # Empty / non-numeric strings must look "definitely too old" so the
        # caller raises the repair instead of silently swallowing the error.
        assert _parse_version("") == (0,)
        assert _parse_version("foo") == (0,)


class TestAsyncCheckGrpcioVersion:
    def test_current_version_clears_repair(self) -> None:
        """The dev container ships >= the floor, so the call clears the issue."""
        hass = MagicMock()
        with patch("custom_components.aegis_ajax.repairs.ir.async_delete_issue") as delete:
            async_check_grpcio_version(hass)

        delete.assert_called_once_with(hass, DOMAIN, ISSUE_GRPCIO_VERSION_MISMATCH)

    def test_too_old_grpc_raises_repair(self) -> None:
        """A grpc.__version__ below MIN_GRPCIO_VERSION must surface the repair."""
        hass = MagicMock()
        fake_grpc = MagicMock()
        fake_grpc.__version__ = "1.50.0"
        with (
            patch.dict("sys.modules", {"grpc": fake_grpc}),
            patch("custom_components.aegis_ajax.repairs.ir.async_create_issue") as create,
        ):
            async_check_grpcio_version(hass)

        create.assert_called_once()
        kwargs = create.call_args.kwargs
        assert kwargs["translation_key"] == ISSUE_GRPCIO_VERSION_MISMATCH
        assert kwargs["translation_placeholders"]["current"] == "1.50.0"
        assert kwargs["translation_placeholders"]["required"] == MIN_GRPCIO_VERSION
        assert kwargs["is_fixable"] is False

    def test_pre_release_above_floor_clears(self) -> None:
        """`1.60.0rc1` must not trigger the repair just because of the suffix."""
        hass = MagicMock()
        fake_grpc = MagicMock()
        fake_grpc.__version__ = "1.60.0rc1"
        with (
            patch.dict("sys.modules", {"grpc": fake_grpc}),
            patch("custom_components.aegis_ajax.repairs.ir.async_delete_issue") as delete,
            patch("custom_components.aegis_ajax.repairs.ir.async_create_issue") as create,
        ):
            async_check_grpcio_version(hass)

        delete.assert_called_once()
        create.assert_not_called()

    def test_missing_grpc_module_does_not_raise_repair(self) -> None:
        """If grpc itself can't be imported, leave HA core to surface that."""
        hass = MagicMock()

        # Simulate ImportError by clearing grpc from sys.modules so the
        # `import grpc` inside the function raises (no replacement entry).
        import builtins

        original_import = builtins.__import__

        def _fail_grpc(name: str, *args: object, **kwargs: object) -> object:
            if name == "grpc":
                raise ImportError("simulated")
            return original_import(name, *args, **kwargs)

        with (
            patch.object(builtins, "__import__", side_effect=_fail_grpc),
            patch("custom_components.aegis_ajax.repairs.ir.async_create_issue") as create,
            patch("custom_components.aegis_ajax.repairs.ir.async_delete_issue") as delete,
        ):
            async_check_grpcio_version(hass)

        create.assert_not_called()
        delete.assert_not_called()


class TestFcmCredentialsRepairFlow:
    @staticmethod
    def _make_hass(entry: MagicMock | None) -> MagicMock:
        hass = MagicMock()
        hass.config_entries.async_get_entry.return_value = entry
        hass.config_entries.async_reload = AsyncMock()
        return hass

    @pytest.mark.asyncio
    async def test_init_no_input_shows_form_with_existing_creds_prefilled(self) -> None:
        entry = MagicMock()
        entry.data = {
            "fcm_project_id": "old-proj",
            "fcm_app_id": "old-app",
            "fcm_api_key": "old-key",
            "fcm_sender_id": "old-sender",
        }
        entry.options = {}
        flow = FcmCredentialsRepairFlow("entry-abc")
        flow.hass = self._make_hass(entry)
        flow.async_show_form = MagicMock(return_value={"type": "form"})
        flow.add_suggested_values_to_schema = MagicMock(return_value="schema")

        await flow.async_step_init(None)

        flow.async_show_form.assert_called_once()
        assert flow.async_show_form.call_args.kwargs["step_id"] == "init"
        # Pre-filled with the currently-stored (broken) creds so the user
        # can edit only what's wrong rather than re-typing everything.
        suggested = flow.add_suggested_values_to_schema.call_args[0][1]
        assert suggested["fcm_project_id"] == "old-proj"
        assert suggested["fcm_api_key"] == "old-key"

    @pytest.mark.asyncio
    async def test_init_with_input_writes_entry_and_reloads(self) -> None:
        entry = MagicMock()
        entry.data = {"email": "u@x", "fcm_project_id": "old"}
        flow = FcmCredentialsRepairFlow("entry-abc")
        flow.hass = self._make_hass(entry)
        flow.hass.config_entries.async_update_entry = MagicMock()
        flow.async_create_entry = MagicMock(return_value={"type": "create_entry"})

        new_creds = {
            "fcm_project_id": "new-proj",
            "fcm_app_id": "new-app",
            "fcm_api_key": "new-key",
            "fcm_sender_id": "new-sender",
        }
        await flow.async_step_init(new_creds)

        # Existing entry data is preserved; only the four FCM fields rotate
        flow.hass.config_entries.async_update_entry.assert_called_once_with(
            entry, data={"email": "u@x", **new_creds}
        )
        flow.hass.config_entries.async_reload.assert_awaited_once_with("entry-abc")
        flow.async_create_entry.assert_called_once_with(data={})

    @pytest.mark.asyncio
    async def test_init_aborts_if_entry_vanished(self) -> None:
        flow = FcmCredentialsRepairFlow("entry-gone")
        flow.hass = self._make_hass(None)
        flow.async_abort = MagicMock(return_value={"type": "abort"})

        await flow.async_step_init({"fcm_project_id": "x"})

        flow.async_abort.assert_called_once_with(reason="entry_missing")
        flow.hass.config_entries.async_reload.assert_not_awaited()


class TestAsyncCreateFixFlow:
    @pytest.mark.asyncio
    async def test_returns_fcm_flow_for_fcm_issue(self) -> None:
        hass = MagicMock()
        flow = await async_create_fix_flow(
            hass, f"{ISSUE_FCM_CREDENTIALS_INVALID}:entry-abc", {"entry_id": "entry-abc"}
        )
        assert isinstance(flow, FcmCredentialsRepairFlow)
        assert flow._entry_id == "entry-abc"

    @pytest.mark.asyncio
    async def test_falls_back_to_data_entry_id_when_data_missing(self) -> None:
        """If `data` was lost, recover the entry id from the namespaced issue id."""
        hass = MagicMock()
        flow = await async_create_fix_flow(hass, f"{ISSUE_FCM_CREDENTIALS_INVALID}:entry-xyz", None)
        assert isinstance(flow, FcmCredentialsRepairFlow)
        assert flow._entry_id == "entry-xyz"

    @pytest.mark.asyncio
    async def test_returns_fcm_flow_for_not_configured_issue(self) -> None:
        """`fcm_not_configured` uses the same form as `fcm_credentials_invalid`."""
        hass = MagicMock()
        flow = await async_create_fix_flow(
            hass, f"{ISSUE_FCM_NOT_CONFIGURED}:entry-abc", {"entry_id": "entry-abc"}
        )
        assert isinstance(flow, FcmCredentialsRepairFlow)
        assert flow._entry_id == "entry-abc"

    @pytest.mark.asyncio
    async def test_falls_back_to_confirm_flow_for_unknown_issue(self) -> None:
        from homeassistant.components.repairs import ConfirmRepairFlow

        hass = MagicMock()
        flow = await async_create_fix_flow(hass, "some_other_issue", None)
        assert isinstance(flow, ConfirmRepairFlow)
