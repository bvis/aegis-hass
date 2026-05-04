"""Tests for the repairs helper module."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

from custom_components.aegis_ajax.const import DOMAIN
from custom_components.aegis_ajax.repairs import (
    ISSUE_FCM_CREDENTIALS_INVALID,
    ISSUE_GRPCIO_VERSION_MISMATCH,
    ISSUE_HTS_CHRONIC_FAILURE,
    ISSUE_HUB_OFFLINE_24H,
    MIN_GRPCIO_VERSION,
    _parse_version,
    async_check_grpcio_version,
    async_clear_fcm_credentials_invalid,
    async_clear_hts_chronic_failure,
    async_clear_hub_offline,
    async_register_fcm_credentials_invalid,
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
