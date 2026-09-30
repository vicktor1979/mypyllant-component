from __future__ import annotations

import logging

from aiohttp import ClientError, ClientResponseError
from datetime import datetime as dt, timedelta
import voluptuous as vol
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import Platform
from homeassistant.core import (
    HomeAssistant,
    SupportsResponse,
    ServiceCall,
    ServiceResponse,
    callback,
)
from homeassistant.exceptions import ConfigEntryAuthFailed, ConfigEntryNotReady, HomeAssistantError
from homeassistant.helpers import selector
from homeassistant.helpers.event import async_call_later

from myPyllant import export, report

from myPyllant.api import MyPyllantAPI
from myPyllant.const import DEFAULT_BRAND
from myPyllant.enums import DeviceDataBucketResolution
from myPyllant.http_client import (
    AuthenticationFailed,
    RealmInvalid,
    LoginEndpointInvalid,
)
from myPyllant.tests import generate_test_data
from .const import (
    DEFAULT_FETCH_ENERGY_HISTORY,
    OPTION_FETCH_ENERGY_HISTORY,
    DEFAULT_COUNTRY,
    DEFAULT_UPDATE_INTERVAL,
    DOMAIN,
    OPTION_BRAND,
    OPTION_COUNTRY,
    OPTION_UPDATE_INTERVAL,
    SERVICE_GENERATE_TEST_DATA,
    SERVICE_EXPORT,
    SERVICE_REPORT,
    OPTION_UPDATE_INTERVAL_DAILY,
    DEFAULT_UPDATE_INTERVAL_DAILY,
)
from .coordinator import SystemCoordinator, DailyDataCoordinator
from .quota import QuotaBackoffStore, async_migrate_energy_quota
from .utils import is_quota_exceeded_exception

_LOGGER = logging.getLogger(__name__)

PLATFORMS: list[Platform] = [
    Platform.BINARY_SENSOR,
    Platform.BUTTON,
    Platform.CALENDAR,
    Platform.CLIMATE,
    Platform.DATETIME,
    Platform.NUMBER,
    Platform.SENSOR,
    Platform.SWITCH,
    Platform.WATER_HEATER,
]


DIAGNOSTIC_PLATFORMS: list[Platform] = [Platform.SENSOR, Platform.BUTTON]


def _quota_reload_stagger(entry_id: str) -> int:
    """Return a stable 0-14s stagger to avoid a post-quota request burst."""
    return sum(entry_id.encode("utf-8")) % 15


async def _async_setup_quota_diagnostics(
    hass: HomeAssistant,
    entry: ConfigEntry,
    quota_backoff: QuotaBackoffStore,
) -> bool:
    """Load local-only diagnostics while Vaillant API backoff is active."""
    entry_data = hass.data[DOMAIN][entry.entry_id]
    entry_data["diagnostic_only"] = True
    entry_data["loaded_platforms"] = DIAGNOSTIC_PLATFORMS

    # Wait until after the server-provided quota window and stagger multiple
    # accounts deterministically. This avoids all config entries hitting the
    # Vaillant API at the same second when a shared quota window expires.
    delay = max(1, quota_backoff.remaining_seconds) + 2 + _quota_reload_stagger(
        entry.entry_id
    )

    @callback
    def _reload_after_quota(_now) -> None:
        current = hass.data.get(DOMAIN, {}).get(entry.entry_id)
        if current is None:
            return
        current["quota_reload_cancel"] = None
        hass.async_create_task(hass.config_entries.async_reload(entry.entry_id))

    entry_data["quota_reload_cancel"] = async_call_later(
        hass, delay, _reload_after_quota
    )

    await hass.config_entries.async_forward_entry_setups(
        entry, DIAGNOSTIC_PLATFORMS
    )

    until = quota_backoff.until
    _LOGGER.info(
        "myVAILLANT account %s is API-limited; loaded local diagnostics only. "
        "No Vaillant API requests will be made during backoff. Automatic reload "
        "scheduled in %ss%s",
        entry.title,
        delay,
        f" (quota until {until.isoformat()})" if until is not None else "",
    )
    return True

_DEVICE_DATA_BUCKET_RESOLUTION_OPTIONS = [
    selector.SelectOptionDict(value=v.value, label=v.value.title())
    for v in DeviceDataBucketResolution
]


async def async_migrate_entry(hass: HomeAssistant, config_entry: ConfigEntry):
    """Migrate old entry."""
    _LOGGER.debug("Migrating from version %s", config_entry.version)

    if config_entry.version == 1:
        """
        from homeassistant.helpers.entity_registry import async_migrate_entries, RegistryEntry
        from homeassistant.helpers.device_registry import async_entries_for_config_entry
        from homeassistant.core import callback

        devices = async_entries_for_config_entry(
            hass.data["device_registry"], config_entry.entry_id
        )

        @callback
        def update_unique_id(entity_entry: RegistryEntry):
            return {"new_unique_id": entity_entry.unique_id} # change entity_entry.unique_id

        await async_migrate_entries(hass, config_entry.entry_id, update_unique_id)
        config_entry.version = 2 # set to new version
        """

    _LOGGER.debug("Migration to version %s successful", config_entry.version)
    return True


async def _async_energy_option_updated(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Apply the new history toggle automatically, also in diagnostics mode."""
    entry_data = hass.data.get(DOMAIN, {}).get(entry.entry_id)
    if entry_data is None:
        return
    enabled = entry.options.get(OPTION_FETCH_ENERGY_HISTORY, DEFAULT_FETCH_ENERGY_HISTORY)
    if enabled != entry_data.get("energy_history_enabled"):
        await hass.config_entries.async_reload(entry.entry_id)


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    username: str = entry.data.get("username")  # type: ignore
    password: str = entry.data.get("password")  # type: ignore
    update_interval = entry.options.get(OPTION_UPDATE_INTERVAL, DEFAULT_UPDATE_INTERVAL)
    update_interval_daily = entry.options.get(
        OPTION_UPDATE_INTERVAL_DAILY, DEFAULT_UPDATE_INTERVAL_DAILY
    )
    country = entry.options.get(
        OPTION_COUNTRY, entry.data.get(OPTION_COUNTRY, DEFAULT_COUNTRY)
    )
    brand = entry.options.get(OPTION_BRAND, entry.data.get(OPTION_BRAND, DEFAULT_BRAND))

    hass.data.setdefault(DOMAIN, {})
    energy_enabled = entry.options.get(
        OPTION_FETCH_ENERGY_HISTORY, DEFAULT_FETCH_ENERGY_HISTORY
    )
    hass.data[DOMAIN][entry.entry_id] = {"energy_history_enabled": energy_enabled}
    entry.async_on_unload(entry.add_update_listener(_async_energy_option_updated))

    # Quota state is persisted per config entry, so a reload or Home Assistant
    # restart cannot accidentally bypass Vaillant's backoff window and burn
    # additional requests while the account is still rate limited.
    quota_backoff = QuotaBackoffStore(hass, entry.entry_id)
    await quota_backoff.async_load()
    hass.data[DOMAIN][entry.entry_id]["quota_backoff"] = quota_backoff
    energy_backoff = QuotaBackoffStore(hass, entry.entry_id, scope="energy")
    await energy_backoff.async_load()
    hass.data[DOMAIN][entry.entry_id]["energy_quota_backoff"] = energy_backoff
    if await async_migrate_energy_quota(quota_backoff, energy_backoff):
        _LOGGER.info(
            "Moved a historical buckets quota to energy-only backoff for %s; "
            "the server deadline is preserved, live data may be requested",
            entry.title,
        )
    if quota_backoff.is_active:
        return await _async_setup_quota_diagnostics(hass, entry, quota_backoff)

    _LOGGER.debug("Creating API and logging in with %s in realm %s", username, country)
    api = MyPyllantAPI(
        username=username, password=password, brand=brand, country=country
    )
    try:
        await api.login()
    except (AuthenticationFailed, LoginEndpointInvalid, RealmInvalid) as e:
        await api.aiohttp_session.close()
        raise ConfigEntryAuthFailed from e
    except ClientResponseError as e:
        if is_quota_exceeded_exception(e):
            await quota_backoff.async_set_from_exception(e)
            await api.aiohttp_session.close()
            return await _async_setup_quota_diagnostics(
                hass, entry, quota_backoff
            )
        await api.aiohttp_session.close()
        raise ConfigEntryNotReady(
            f"Temporary myVAILLANT HTTP error during login: {e}"
        ) from e
    except (ClientError, TimeoutError, OSError) as e:
        # Treat temporary login/network failures as a setup retry instead of a
        # permanent failed setup.  This is especially important when several
        # myVAILLANT config entries start at the same time after a HA restart.
        await api.aiohttp_session.close()
        raise ConfigEntryNotReady(
            f"Temporary myVAILLANT login/network error: {e}"
        ) from e
    except BaseException:
        await api.aiohttp_session.close()
        raise

    system_coordinator = SystemCoordinator(
        hass, api, entry, timedelta(seconds=update_interval)
    )
    _LOGGER.debug("Refreshing SystemCoordinator")
    try:
        # async_refresh() only records a failed first refresh and then setup
        # continues, which can leave entity-registry entries as "not
        # provided" until the user manually reloads the config entry.  The
        # config-entry first-refresh helper raises ConfigEntryNotReady on a
        # complete initial fetch failure, so Home Assistant retries setup
        # automatically once the Vaillant API/gateway recovers.
        await system_coordinator.async_config_entry_first_refresh()
    except ConfigEntryNotReady:
        await api.aiohttp_session.close()
        if quota_backoff.is_active:
            return await _async_setup_quota_diagnostics(
                hass, entry, quota_backoff
            )
        raise
    except BaseException:
        await api.aiohttp_session.close()
        raise
    hass.data[DOMAIN][entry.entry_id]["system_coordinator"] = system_coordinator

    # Keep empty accounts diagnostic-only. History is opt-in: when disabled,
    # do not even construct the daily coordinator, including on entry reload.
    # The live coordinator retains its own configured polling interval.
    if system_coordinator.empty_account:
        _LOGGER.info(
            "myVAILLANT account %s contains no homes; disabling polling until "
            "the config entry is reloaded",
            username,
        )
        system_coordinator.update_interval = None
        loaded_platforms = DIAGNOSTIC_PLATFORMS
    elif not energy_enabled:
        _LOGGER.info(
            "Energy history disabled for %s: no startup, reload or periodic buckets requests",
            entry.title,
        )
        loaded_platforms = PLATFORMS
    else:
        daily_data_coordinator = DailyDataCoordinator(
            hass,
            api,
            entry,
            timedelta(seconds=update_interval_daily) if update_interval_daily else None,
        )
        hass.data[DOMAIN][entry.entry_id][
            "daily_data_coordinator"
        ] = daily_data_coordinator
        _LOGGER.debug("Refreshing DailyDataCoordinator")
        try:
            await daily_data_coordinator.async_refresh()
        except BaseException:
            await api.aiohttp_session.close()
            raise
        loaded_platforms = PLATFORMS

    hass.data[DOMAIN][entry.entry_id]["loaded_platforms"] = loaded_platforms
    await hass.config_entries.async_forward_entry_setups(entry, loaded_platforms)
    if quota_backoff.is_active:
        # A token/account-wide error during the initial history refresh needs
        # a timer too; platform setup had not yet been completed when it failed.
        system_coordinator._schedule_runtime_quota_reload()

    def _check_energy_history_access() -> None:
        # Explicit report/test-data services must not bypass the opt-out or
        # repeatedly query historical endpoints during a known energy quota.
        if not entry.options.get(OPTION_FETCH_ENERGY_HISTORY, DEFAULT_FETCH_ENERGY_HISTORY):
            raise HomeAssistantError("Az energiaelőzmények lekérése ki van kapcsolva.")
        if energy_backoff.is_active:
            raise HomeAssistantError(energy_backoff.retry_message("energy export"))
        if quota_backoff.is_active:
            raise HomeAssistantError(quota_backoff.retry_message("energy export"))

    async def handle_export(call: ServiceCall) -> ServiceResponse:
        if call.data.get("data", False):
            _check_energy_history_access()
        _LOGGER.debug("Exporting data with params %s", call.data)
        return {
            "export": await export.main(
                user=username,
                password=password,
                brand=brand,
                country=country,
                data=call.data.get("data", False),
                resolution=call.data.get("resolution", DeviceDataBucketResolution.DAY),
                start=call.data.get("start"),
                end=call.data.get("end"),
            )
        }

    async def handle_generate_test_data(call: ServiceCall) -> ServiceResponse:
        _check_energy_history_access()
        return await generate_test_data.main(
            user=username,
            password=password,
            brand=brand,
            country=country,
            write_results=False,
        )

    async def handle_report(call: ServiceCall) -> ServiceResponse:
        _check_energy_history_access()
        return {
            f.file_name: f.file_content
            for f in await report.main(
                user=username,
                password=password,
                brand=brand,
                country=country,
                year=int(call.data.get("year", dt.now().year)),
                write_results=False,
            )
        }

    hass.services.async_register(
        DOMAIN,
        SERVICE_EXPORT,
        handle_export,
        schema=vol.Schema(
            {
                vol.Optional("data"): bool,
                vol.Optional("resolution"): selector.SelectSelector(
                    selector.SelectSelectorConfig(
                        options=_DEVICE_DATA_BUCKET_RESOLUTION_OPTIONS,
                        mode=selector.SelectSelectorMode.LIST,
                    ),
                ),
                vol.Optional("start"): vol.Coerce(dt.fromisoformat),
                vol.Optional("end"): vol.Coerce(dt.fromisoformat),
            }
        ),
        supports_response=SupportsResponse.ONLY,
    )
    hass.services.async_register(
        DOMAIN,
        SERVICE_GENERATE_TEST_DATA,
        handle_generate_test_data,
        supports_response=SupportsResponse.ONLY,
    )
    hass.services.async_register(
        DOMAIN,
        SERVICE_REPORT,
        handle_report,
        schema=vol.Schema(
            {
                vol.Required("year", default=dt.now().year): vol.Coerce(int),
            }
        ),
        supports_response=SupportsResponse.ONLY,
    )
    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload a config entry."""
    entry_data = hass.data.get(DOMAIN, {}).get(entry.entry_id, {})

    cancel_reload = entry_data.get("quota_reload_cancel")
    if cancel_reload is not None:
        cancel_reload()
        entry_data["quota_reload_cancel"] = None

    cancel_energy_retry = entry_data.pop("energy_quota_retry_cancel", None)
    if cancel_energy_retry is not None:
        cancel_energy_retry()

    loaded_platforms = entry_data.get("loaded_platforms", PLATFORMS)
    unload_ok = await hass.config_entries.async_unload_platforms(
        entry, loaded_platforms
    )
    if unload_ok:
        # Diagnostic-only quota mode has no API object. In normal mode both
        # coordinators share the same API/session, so close each unique API once.
        seen_api_ids: set[int] = set()
        for key in ("system_coordinator", "daily_data_coordinator"):
            coordinator = entry_data.get(key)
            if coordinator is not None:
                await coordinator.async_shutdown()
            api = getattr(coordinator, "api", None)
            if api is None or id(api) in seen_api_ids:
                continue
            seen_api_ids.add(id(api))
            await api.aiohttp_session.close()

        hass.data[DOMAIN].pop(entry.entry_id, None)

    return unload_ok
