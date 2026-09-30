from __future__ import annotations

import logging
from typing import Any

from homeassistant.components.button import ButtonEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity import EntityCategory

from .const import DOMAIN
from .quota import QuotaBackoffStore

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(
    hass: HomeAssistant,
    config: ConfigEntry,
    async_add_entities,
) -> None:
    """Set up account-level myVAILLANT diagnostic buttons."""
    entry_data = hass.data.get(DOMAIN, {}).get(config.entry_id, {})
    quota_backoff: QuotaBackoffStore | None = entry_data.get("quota_backoff")

    async_add_entities(
        [VaillantApiManualRetryButton(config, quota_backoff)],
        update_before_add=False,
    )


class VaillantApiManualRetryButton(ButtonEntity):
    """Force one immediate myVAILLANT config-entry retry."""

    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_icon = "mdi:reload"
    _attr_should_poll = False

    def __init__(
        self,
        config: ConfigEntry,
        quota_backoff: QuotaBackoffStore | None,
    ) -> None:
        self.config = config
        self.quota_backoff = quota_backoff

    @property
    def unique_id(self) -> str:
        return f"{DOMAIN}_{self.config.entry_id}_api_manual_retry"

    @property
    def name(self) -> str:
        account = self.config.title.split("@", 1)[0]
        return f"{account} – Vaillant API kézi újrapróbálkozás"

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        return {
            "Fiók": self.config.title,
            "Művelet": (
                "Törli a helyi API-korlát miatti várakozást, majd egyszer "
                "azonnal újratölti ezt a myVAILLANT fiókot."
            ),
        }

    async def async_press(self) -> None:
        """Clear local quota backoff and request one immediate config reload."""
        entry_data = self.hass.data.get(DOMAIN, {}).get(self.config.entry_id, {})

        # Cancel a previously scheduled automatic post-quota reload.  The
        # upcoming config-entry unload would cancel it as well, but doing it
        # here prevents a race where both reloads are queued at once.
        cancel_reload = entry_data.get("quota_reload_cancel")
        if cancel_reload is not None:
            cancel_reload()
            entry_data["quota_reload_cancel"] = None

        if self.quota_backoff is not None:
            await self.quota_backoff.async_clear()

        _LOGGER.warning(
            "Manual myVAILLANT API retry requested for %s; local quota backoff "
            "was cleared and one immediate config-entry reload will be attempted",
            self.config.title,
        )

        # Schedule instead of awaiting the reload directly. The reload unloads
        # this button platform itself, so queueing it lets the button service
        # call finish cleanly before the entity disappears/reloads.
        self.hass.async_create_task(
            self.hass.config_entries.async_reload(self.config.entry_id)
        )
