"""Tests for the repairs helper module."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

from custom_components.aegis_ajax.const import DOMAIN
from custom_components.aegis_ajax.repairs import (
    ISSUE_FCM_CREDENTIALS_INVALID,
    ISSUE_HTS_CHRONIC_FAILURE,
    ISSUE_HUB_OFFLINE_24H,
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
