"""Tests for pyscript config flow."""

import logging
from unittest import mock

import pytest
from aiohttp import ClientConnectionError

from homeassistant import data_entry_flow
from homeassistant.config_entries import SOURCE_USER
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryNotReady

from myPyllant.api import MyPyllantAPI
from myPyllant.http_client import AuthenticationFailed
from myPyllant.tests.generate_test_data import DATA_DIR
from myPyllant.tests.utils import load_test_data

from custom_components.mypyllant.const import DOMAIN
from custom_components.mypyllant import (
    SystemCoordinator,
    async_setup_entry,
    async_unload_entry,
)
from custom_components.mypyllant.config_flow import DATA_SCHEMA
from tests.utils import get_config_entry, test_user_input

_LOGGER = logging.getLogger(__name__)


async def test_flow_init(hass):
    """Test the initial flow."""
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": SOURCE_USER}
    )

    expected = {
        "data_schema": DATA_SCHEMA,
        "description_placeholders": None,
        "errors": {},
        "flow_id": mock.ANY,
        "handler": "mypyllant",
        "step_id": "user",
        "type": "form",
        "last_step": None,
        "preview": None,
    }
    assert expected == result


async def test_user_flow_minimum_fields(hass: HomeAssistant):
    """Test user config flow with minimum fields."""
    # test form shows
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": SOURCE_USER}
    )
    assert result["type"] == data_entry_flow.FlowResultType.FORM
    assert result["step_id"] == "user"

    with mock.patch(
        "custom_components.mypyllant.config_flow.validate_input",
        side_effect=AuthenticationFailed,
    ):
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"],
            user_input=test_user_input,
        )

    assert result["type"] == data_entry_flow.FlowResultType.FORM


async def test_async_setup(
    hass,
    mypyllant_aioresponses,
    mocked_api: MyPyllantAPI,
):
    test_data = load_test_data(DATA_DIR / "heatpump_heat_curve")
    with mypyllant_aioresponses(test_data) as _:
        config_entry = get_config_entry()
        config_entry.add_to_hass(hass)
        await hass.config_entries.async_setup(config_entry.entry_id)
        await hass.async_block_till_done()

        mock.patch("myPyllant.api.MyPyllantAPI", mocked_api)
        result = await async_setup_entry(hass, config_entry)
        assert result, "Component did not setup successfully"

        result = await async_unload_entry(hass, config_entry)
        assert result, "Component did not unload successfully"

    await mocked_api.aiohttp_session.close()


async def test_async_setup_retries_transient_login_error(hass: HomeAssistant):
    """Temporary login/network errors should be retried by Home Assistant."""
    config_entry = get_config_entry()
    api = mock.MagicMock()
    api.login = mock.AsyncMock(side_effect=ClientConnectionError("temporary"))
    api.aiohttp_session.close = mock.AsyncMock()

    with mock.patch(
        "custom_components.mypyllant.MyPyllantAPI", return_value=api
    ):
        with pytest.raises(ConfigEntryNotReady):
            await async_setup_entry(hass, config_entry)

    api.aiohttp_session.close.assert_awaited_once()


async def test_async_setup_retries_failed_first_system_refresh(
    hass: HomeAssistant,
):
    """A failed initial coordinator refresh must not leave partial entities."""
    config_entry = get_config_entry()
    api = mock.MagicMock()
    api.login = mock.AsyncMock()
    api.aiohttp_session.close = mock.AsyncMock()

    with (
        mock.patch(
            "custom_components.mypyllant.MyPyllantAPI", return_value=api
        ),
        mock.patch.object(
            SystemCoordinator,
            "async_config_entry_first_refresh",
            new=mock.AsyncMock(
                side_effect=ConfigEntryNotReady("temporary initial refresh failure")
            ),
        ),
    ):
        with pytest.raises(ConfigEntryNotReady):
            await async_setup_entry(hass, config_entry)

    api.aiohttp_session.close.assert_awaited_once()


async def test_async_setup_skips_api_while_persisted_quota_is_active(
    hass: HomeAssistant,
):
    """A reload/restart must not make API calls during persisted quota backoff."""
    config_entry = get_config_entry()
    quota_backoff = mock.MagicMock()
    quota_backoff.async_load = mock.AsyncMock()
    quota_backoff.is_active = True
    quota_backoff.retry_message.return_value = "quota backoff active"

    api_factory = mock.MagicMock()
    with (
        mock.patch(
            "custom_components.mypyllant.QuotaBackoffStore",
            return_value=quota_backoff,
        ),
        mock.patch(
            "custom_components.mypyllant.MyPyllantAPI",
            api_factory,
        ),
    ):
        with pytest.raises(ConfigEntryNotReady, match="quota backoff active"):
            await async_setup_entry(hass, config_entry)

    quota_backoff.async_load.assert_awaited_once()
    api_factory.assert_not_called()
