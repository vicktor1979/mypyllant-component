"""Tests for myVAILLANT account diagnostic buttons."""

from unittest import mock

from custom_components.mypyllant.button import VaillantApiManualRetryButton
from custom_components.mypyllant.const import DOMAIN
from tests.utils import get_config_entry


async def test_manual_api_retry_clears_backoff_and_reloads(hass):
    """Manual retry clears local quota state and reloads only its config entry."""
    config_entry = get_config_entry()
    quota_backoff = mock.MagicMock()
    quota_backoff.async_clear = mock.AsyncMock()
    cancel_reload = mock.MagicMock()

    hass.data.setdefault(DOMAIN, {})[config_entry.entry_id] = {
        "quota_backoff": quota_backoff,
        "quota_reload_cancel": cancel_reload,
    }

    button = VaillantApiManualRetryButton(config_entry, quota_backoff)
    button.hass = hass

    with mock.patch.object(
        hass.config_entries,
        "async_reload",
        new=mock.AsyncMock(return_value=True),
    ) as async_reload:
        await button.async_press()
        await hass.async_block_till_done()

    cancel_reload.assert_called_once()
    quota_backoff.async_clear.assert_awaited_once()
    async_reload.assert_awaited_once_with(config_entry.entry_id)
    assert (
        hass.data[DOMAIN][config_entry.entry_id]["quota_reload_cancel"]
        is None
    )
