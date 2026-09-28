from __future__ import annotations

import asyncio
import logging
from asyncio import CancelledError
from datetime import timedelta, datetime as dt, timezone
from typing import TypedDict

from aiohttp import ClientResponseError
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed
from homeassistant.helpers import entity_registry as er

from custom_components.mypyllant.const import (
    DOMAIN,
    OPTION_REFRESH_DELAY,
    DEFAULT_REFRESH_DELAY,
    QUOTA_PAUSE_INTERVAL,
    API_DOWN_PAUSE_INTERVAL,
    OPTION_FETCH_MPC,
    OPTION_FETCH_RTS,
    DEFAULT_FETCH_RTS,
    DEFAULT_FETCH_MPC,
    OPTION_FETCH_AMBISENSE_ROOMS,
    DEFAULT_FETCH_AMBISENSE_ROOMS,
    OPTION_FETCH_ENERGY_MANAGEMENT,
    DEFAULT_FETCH_ENERGY_MANAGEMENT,
    OPTION_FETCH_EEBUS,
    DEFAULT_FETCH_EEBUS,
    OPTION_FETCH_AMBISENSE_CAPABILITY,
    DEFAULT_FETCH_AMBISENSE_CAPABILITY,
    OPTION_FETCH_CONNECTION_STATUS,
    DEFAULT_FETCH_CONNECTION_STATUS,
    OPTION_FETCH_DTC,
    DEFAULT_FETCH_DTC,
)
from custom_components.mypyllant.utils import (
    is_quota_exceeded_exception,
    extract_quota_duration,
)
from myPyllant.api import AmbisenseNoFacilityError, MyPyllantAPI
from myPyllant.enums import DeviceDataBucketResolution
from myPyllant.models import System, DeviceData, Home

_LOGGER = logging.getLogger(__name__)


class MyPyllantCoordinator(DataUpdateCoordinator):
    api: MyPyllantAPI

    def __init__(
        self,
        hass: HomeAssistant,
        api: MyPyllantAPI,
        entry: ConfigEntry,
        update_interval: timedelta | None,
    ) -> None:
        self.api = api
        self.hass = hass
        self.entry = entry
        self._quota_hit_time_key = f"quota_time_{self.__class__.__name__.lower()}"
        self._quota_end_time_key = f"quota_end_time_{self.__class__.__name__.lower()}"
        self._quota_exc_info_key = f"quota_exc_info_{self.__class__.__name__.lower()}"

        super().__init__(
            hass,
            _LOGGER,
            name="myVAILLANT",
            update_interval=update_interval,
        )

    @property
    def hass_data(self):
        return self.hass.data[DOMAIN][self.entry.entry_id]

    @property
    def _quota_hit_time(self) -> dt | None:
        """
        Get the time when the quota was hit, separately for each subclass
        """
        if self._quota_hit_time_key not in self.hass_data:
            self.hass_data[self._quota_hit_time_key] = None
        return self.hass_data[self._quota_hit_time_key]

    @_quota_hit_time.setter
    def _quota_hit_time(self, value):
        self.hass_data[self._quota_hit_time_key] = value

    @_quota_hit_time.deleter
    def _quota_hit_time(self):
        del self.hass_data[self._quota_hit_time_key]

    @property
    def _quota_end_time(self) -> dt | None:
        """
        Get the time when the quota was hit, separately for each subclass
        """
        if self._quota_end_time_key not in self.hass_data:
            self.hass_data[self._quota_end_time_key] = None
        return self.hass_data[self._quota_end_time_key]

    @_quota_end_time.setter
    def _quota_end_time(self, value):
        self.hass_data[self._quota_end_time_key] = value

    @_quota_end_time.deleter
    def _quota_end_time(self):
        del self.hass_data[self._quota_end_time_key]

    @property
    def _quota_exc_info(self) -> BaseException | None:
        """
        Get the exception that happened when the quota was hit, separately for each subclass
        """
        if self._quota_exc_info_key not in self.hass_data:
            self.hass_data[self._quota_exc_info_key] = None
        return self.hass_data[self._quota_exc_info_key]

    @_quota_exc_info.setter
    def _quota_exc_info(self, value):
        self.hass_data[self._quota_exc_info_key] = value

    @_quota_exc_info.deleter
    def _quota_exc_info(self):
        del self.hass_data[self._quota_exc_info_key]

    def _clear_quota_state(self) -> None:
        """Clear all quota-related state to allow fresh retries."""
        self._quota_hit_time = None
        self._quota_end_time = None
        self._quota_exc_info = None

    async def _refresh_session(self):
        if (
            self.api.oauth_session_expires is None
            or self.api.oauth_session_expires
            < dt.now(timezone.utc) + timedelta(seconds=180)
        ):
            _LOGGER.debug("Refreshing token for %s", self.api.username)
            await self.api.refresh_token()
        else:
            delta = self.api.oauth_session_expires - (
                dt.now(timezone.utc) + timedelta(seconds=180)
            )
            _LOGGER.debug(
                "Waiting %ss until token refresh for %s",
                delta.seconds,
                self.api.username,
            )

    async def async_request_refresh_delayed(self, delay=None):
        """
        The API takes a long time to return updated values (i.e. after setting a new heating mode)
        This function waits for a few second and then refreshes
        """

        # API calls sometimes update the models, so we update the data before waiting for the refresh
        # to see immediate changes in the UI
        self.async_set_updated_data(self.data)
        if not delay:
            delay = self.entry.options.get(OPTION_REFRESH_DELAY, DEFAULT_REFRESH_DELAY)
        if delay:
            _LOGGER.debug("Waiting %ss before refreshing data", delay)
            await asyncio.sleep(delay)
        await self.async_request_refresh()

    def _raise_api_down(self, exc_info: CancelledError | TimeoutError) -> None:
        """
        Raises UpdateFailed if a TimeoutError or CancelledError occurred during updating

        Sets a quota time, so the API isn't queried as often while it is down
        """
        self._quota_hit_time = dt.now(timezone.utc)
        self._quota_exc_info = exc_info
        raise UpdateFailed(
            f"myVAILLANT API is down, skipping update of myVAILLANT {self.__class__.__name__} "
            f"for another {QUOTA_PAUSE_INTERVAL}s"
        ) from exc_info

    def _set_quota_and_raise(self, exc_info: ClientResponseError) -> None:
        """
        Check if the API raises a ClientResponseError with "Quota Exceeded" in the message
        Raises UpdateFailed if a quota error is detected
        """
        if is_quota_exceeded_exception(exc_info):
            duration = extract_quota_duration(exc_info)
            self._quota_hit_time = dt.now(timezone.utc)
            if duration:
                self._quota_end_time = dt.now(timezone.utc) + timedelta(
                    seconds=duration
                )
            self._quota_exc_info = exc_info
            self._raise_if_quota_hit()

    def _raise_if_quota_hit(self) -> None:
        """
        Check if we previously hit a quota, and if the quota was hit within a certain interval
        If yes, we keep raising UpdateFailed() until after the interval to avoid spamming the API
        """
        if not self._quota_hit_time:
            return

        time_elapsed = (dt.now(timezone.utc) - self._quota_hit_time).total_seconds()

        if is_quota_exceeded_exception(self._quota_exc_info):
            _LOGGER.debug(
                "Quota was hit %ss ago on %s by %s",
                int(time_elapsed),
                self._quota_hit_time,
                self.__class__,
                exc_info=self._quota_exc_info,
            )
            if self._quota_end_time:
                # If the API responded with an end time, we use that instead of the default QUOTA_PAUSE_INTERVAL
                if dt.now(timezone.utc) < self._quota_end_time:
                    remaining = int(
                        (self._quota_end_time - dt.now(timezone.utc)).total_seconds()
                    )
                    raise UpdateFailed(
                        f"{self._quota_exc_info.message} on {self._quota_exc_info.request_info.real_url}, "  # type: ignore
                        f"skipping update of myVAILLANT {self.__class__.__name__} for another"
                        f" {remaining}s"
                    ) from self._quota_exc_info
                else:
                    # Quota backoff period has expired, clear state and allow retry
                    _LOGGER.info(
                        "Quota backoff expired for %s, clearing quota state and resuming updates",
                        self.__class__.__name__,
                    )
                    self._clear_quota_state()
                    return
            elif time_elapsed < QUOTA_PAUSE_INTERVAL:
                # No end time provided, use default interval
                raise UpdateFailed(
                    f"{self._quota_exc_info.message} on {self._quota_exc_info.request_info.real_url}, "  # type: ignore
                    f"skipping update of myVAILLANT {self.__class__.__name__} for another"
                    f" {int(QUOTA_PAUSE_INTERVAL - time_elapsed)}s"
                ) from self._quota_exc_info
            else:
                # Default backoff period has expired, clear state and allow retry
                _LOGGER.info(
                    "Quota backoff expired for %s (no end time), clearing quota state and resuming updates",
                    self.__class__.__name__,
                )
                self._clear_quota_state()
                return
        else:
            _LOGGER.debug(
                "myVAILLANT API is down since %ss (%s)",
                int(time_elapsed),
                self._quota_hit_time,
                exc_info=self._quota_exc_info,
            )
            if time_elapsed < API_DOWN_PAUSE_INTERVAL:
                raise UpdateFailed(
                    f"myVAILLANT API is down, skipping update of myVAILLANT {self.__class__.__name__} for another"
                    f" {int(API_DOWN_PAUSE_INTERVAL - time_elapsed)}s"
                ) from self._quota_exc_info
            else:
                # API down backoff has expired, clear state and allow retry
                _LOGGER.info(
                    "API down backoff expired for %s, clearing state and resuming updates",
                    self.__class__.__name__,
                )
                self._clear_quota_state()
                return


class SystemUpdateFailure(TypedDict):
    system_id: str
    home_name: str
    error: str
    error_type: str
    failed_at: str
    status: int | None
    url: str | None


class SystemCoordinator(MyPyllantCoordinator):
    data: list[System]  # type: ignore

    def __init__(
        self,
        hass: HomeAssistant,
        api: MyPyllantAPI,
        entry: ConfigEntry,
        update_interval: timedelta | None,
    ) -> None:
        super().__init__(hass, api, entry, update_interval)
        # Keep this state per coordinator instance. Homes used to be a class
        # attribute, which can leak state between config entries.
        self.homes: list[Home] = []
        self.system_failures: dict[str, SystemUpdateFailure] = {}
        self.system_last_success: dict[str, str] = {}

    @staticmethod
    def _home_system_id(home: Home) -> str:
        return str(home.system_id)

    @staticmethod
    def _home_name(home: Home) -> str:
        return home.home_name or home.nomenclature or str(home.system_id)

    @staticmethod
    def _failure_details(home: Home, exc: BaseException) -> SystemUpdateFailure:
        request_info = getattr(exc, "request_info", None)
        real_url = getattr(request_info, "real_url", None)
        return {
            "system_id": str(home.system_id),
            "home_name": SystemCoordinator._home_name(home),
            "error": str(exc),
            "error_type": type(exc).__name__,
            "failed_at": dt.now(timezone.utc).isoformat(),
            "status": getattr(exc, "status", None),
            "url": str(real_url) if real_url is not None else None,
        }

    def is_system_available(self, system_id: str) -> bool:
        """Return whether the latest fetch for this system succeeded."""
        return system_id not in self.system_failures

    def get_system_diagnostics(self, system_id: str) -> dict:
        """Return per-system fetch diagnostics for diagnostic entities."""
        failure = self.system_failures.get(system_id)
        return {
            "system_id": system_id,
            "last_success": self.system_last_success.get(system_id),
            "last_error": failure["error"] if failure else None,
            "last_error_type": failure["error_type"] if failure else None,
            "last_error_at": failure["failed_at"] if failure else None,
            "last_http_status": failure["status"] if failure else None,
            "last_error_url": failure["url"] if failure else None,
        }

    async def _async_update_data(self) -> list[System]:  # type: ignore
        self._raise_if_quota_hit()
        include_connection_status = self.entry.options.get(
            OPTION_FETCH_CONNECTION_STATUS, DEFAULT_FETCH_CONNECTION_STATUS
        )
        include_diagnostic_trouble_codes = self.entry.options.get(
            OPTION_FETCH_DTC, DEFAULT_FETCH_DTC
        )
        include_rts = self.entry.options.get(OPTION_FETCH_RTS, DEFAULT_FETCH_RTS)
        include_mpc = self.entry.options.get(OPTION_FETCH_MPC, DEFAULT_FETCH_MPC)
        include_ambisense_rooms = self.entry.options.get(
            OPTION_FETCH_AMBISENSE_ROOMS, DEFAULT_FETCH_AMBISENSE_ROOMS
        )
        include_energy_management = self.entry.options.get(
            OPTION_FETCH_ENERGY_MANAGEMENT, DEFAULT_FETCH_ENERGY_MANAGEMENT
        )
        include_eebus = self.entry.options.get(OPTION_FETCH_EEBUS, DEFAULT_FETCH_EEBUS)
        include_ambisense_capability = self.entry.options.get(
            OPTION_FETCH_AMBISENSE_CAPABILITY, DEFAULT_FETCH_AMBISENSE_CAPABILITY
        )
        _LOGGER.debug("Starting async update data for SystemCoordinator")

        try:
            await self._refresh_session()
            if not self.homes:
                _LOGGER.debug("Fetching homes for systems fetch")
                self.homes = [
                    h
                    async for h in await self.hass.async_add_executor_job(
                        self.api.get_homes
                    )
                ]
            else:
                _LOGGER.debug("Using cached homes for systems fetch")
        except ClientResponseError as e:
            self._set_quota_and_raise(e)
            raise UpdateFailed(str(e)) from e
        except (CancelledError, TimeoutError) as e:
            self._raise_api_down(e)
            return []  # mypy

        previous_data = self.data or []
        previous_by_id = {system.id: system for system in previous_data}
        previous_failures = self.system_failures
        current_failures: dict[str, SystemUpdateFailure] = {}
        current_by_id: dict[str, System] = {}
        successful_order: list[str] = []
        successful_systems = 0
        first_error: BaseException | None = None

        # Fetch every home independently. A broken gateway must not abort the
        # generator before the remaining homes have been refreshed.
        for home in self.homes:
            system_id = self._home_system_id(home)
            try:
                systems = [
                    system
                    async for system in await self.hass.async_add_executor_job(
                        self.api.get_systems,
                        include_connection_status,
                        include_diagnostic_trouble_codes,
                        include_rts,
                        include_mpc,
                        include_ambisense_rooms,
                        include_energy_management,
                        include_eebus,
                        include_ambisense_capability,
                        [home],
                    )
                ]
                if not systems:
                    raise RuntimeError(
                        f"No system returned for home {self._home_name(home)}"
                    )
            except ClientResponseError as e:
                # Account-wide quota errors must keep their original global
                # backoff behaviour. Other HTTP errors are isolated per home.
                self._set_quota_and_raise(e)
                failure = self._failure_details(home, e)
                current_failures[system_id] = failure
                first_error = first_error or e
            except AmbisenseNoFacilityError as e:
                failure = self._failure_details(home, e)
                current_failures[system_id] = failure
                first_error = first_error or e
                _LOGGER.warning(
                    "Ambisense rooms are not available for %s; keeping the last "
                    "known system data. Consider disabling the Ambisense rooms option: %s",
                    self._home_name(home),
                    e,
                )
            except TimeoutError as e:
                failure = self._failure_details(home, e)
                current_failures[system_id] = failure
                first_error = first_error or e
            except ValueError as e:
                # myPyllant may raise ValueError for a home-specific unsupported
                # control identifier. Isolate that home just like an HTTP error.
                failure = self._failure_details(home, e)
                current_failures[system_id] = failure
                first_error = first_error or e
            except CancelledError:
                raise
            else:
                successful_systems += len(systems)
                now = dt.now(timezone.utc).isoformat()
                for system in systems:
                    current_by_id[system.id] = system
                    successful_order.append(system.id)
                    self.system_last_success[system.id] = now
                    if system.id in previous_failures:
                        _LOGGER.info(
                            "System %s (%s) recovered",
                            system.id,
                            self._home_name(home),
                        )
                continue

            if system_id not in previous_failures:
                _LOGGER.warning(
                    "System %s (%s) update failed; isolating this system: %s",
                    system_id,
                    self._home_name(home),
                    current_failures[system_id]["error"],
                )
            else:
                _LOGGER.debug(
                    "System %s (%s) is still unavailable: %s",
                    system_id,
                    self._home_name(home),
                    current_failures[system_id]["error"],
                )

            # Keep the previous object in exactly the same logical home slot so
            # existing entities keep their system/index mapping. Its entities
            # are marked unavailable by is_system_available().
            if system_id in previous_by_id:
                current_by_id[system_id] = previous_by_id[system_id]

        self.system_failures = current_failures

        # If every home failed, retain the original coordinator-wide failure
        # semantics. This prevents a complete API outage from looking healthy.
        if self.homes and successful_systems == 0:
            if isinstance(first_error, TimeoutError):
                self._raise_api_down(first_error)
            if first_error is not None:
                raise UpdateFailed(
                    f"All myVAILLANT systems failed to update: {first_error}"
                ) from first_error
            raise UpdateFailed("No myVAILLANT systems returned by the API")

        # Preserve existing list indexes. If a home was missing during initial
        # setup and later recovers, inserting it in the middle would make every
        # entity after it point at the wrong system. Existing systems therefore
        # keep their previous order; newly discovered/recovered systems are
        # appended until the integration is reloaded and entities are rebuilt.
        previous_order = [
            system.id for system in previous_data if system.id in current_by_id
        ]
        new_order = [
            system_id
            for system_id in successful_order
            if system_id not in previous_order
        ]
        data = [current_by_id[system_id] for system_id in previous_order + new_order]

        # Clear quota/API-down state when at least one home was refreshed. A
        # home-specific failure must not suppress refreshes for healthy homes.
        self._clear_quota_state()
        return data


class SystemWithDeviceData(TypedDict):
    home_name: str
    devices_data: list[list[DeviceData]]


class DailyDataCoordinator(MyPyllantCoordinator):
    data: dict[str, SystemWithDeviceData]

    def __init__(
        self,
        hass: HomeAssistant,
        api: MyPyllantAPI,
        entry: ConfigEntry,
        update_interval: timedelta | None,
    ) -> None:
        super().__init__(hass, api, entry, update_interval)
        self.system_failures: dict[str, SystemUpdateFailure] = {}
        self.system_last_success: dict[str, str] = {}

    def is_system_available(self, system_id: str) -> bool:
        """Return whether daily data for a system was refreshed successfully."""
        return system_id not in self.system_failures

    async def is_sensor_disabled(self, unique_id: str) -> bool:
        """
        Check if a sensor is disabled to be able to skip its API update
        """
        entity_registry = er.async_get(self.hass)
        entity_id = entity_registry.async_get_entity_id("sensor", DOMAIN, unique_id)
        if entity_id:
            entity_entry = entity_registry.async_get(entity_id)
            return entity_entry is not None and bool(entity_entry.disabled)
        return False

    async def _async_update_data(self) -> dict[str, SystemWithDeviceData]:
        self._raise_if_quota_hit()
        _LOGGER.debug("Starting async update data for DailyDataCoordinator")
        try:
            await self._refresh_session()
        except ClientResponseError as e:
            self._set_quota_and_raise(e)
            raise UpdateFailed(str(e)) from e
        except (CancelledError, TimeoutError) as e:
            self._raise_api_down(e)
            return {}  # mypy

        if (
            "system_coordinator" not in self.hass_data
            or not self.hass_data["system_coordinator"].data
        ):
            raise UpdateFailed("No systems available for daily data fetch")

        system_coordinator: SystemCoordinator = self.hass_data["system_coordinator"]
        previous_data = self.data or {}
        previous_failures = self.system_failures
        current_failures: dict[str, SystemUpdateFailure] = {}
        data: dict[str, SystemWithDeviceData] = {}

        for system in system_coordinator.data:
            system_id = system.id

            # If the normal system refresh already identified this gateway as
            # unavailable, do not make additional daily-data requests to it.
            if not system_coordinator.is_system_available(system_id):
                failure = system_coordinator.system_failures.get(system_id)
                if failure is not None:
                    current_failures[system_id] = failure.copy()
                else:
                    current_failures[system_id] = SystemCoordinator._failure_details(
                        system.home,
                        RuntimeError("System refresh is unavailable"),
                    )
                if system_id in previous_data:
                    data[system_id] = previous_data[system_id]
                continue

            today = dt.now(system.timezone).replace(
                microsecond=0, second=0, minute=0, hour=0
            )
            yesterday = (today - timedelta(days=1)).date()
            start = dt(
                yesterday.year,
                yesterday.month,
                yesterday.day,
                tzinfo=system.timezone,
            )
            end = today + timedelta(days=1)
            _LOGGER.debug(
                "Getting daily data for %s from %s to %s", system.id, start, end
            )

            system_data: SystemWithDeviceData = {
                "home_name": system.home.home_name or system.home.nomenclature,
                "devices_data": [],
            }

            try:
                if len(system.devices) == 0:
                    _LOGGER.debug("No devices in %s", system.id)
                    data[system_id] = system_data
                    self.system_last_success[system_id] = dt.now(
                        timezone.utc
                    ).isoformat()
                    continue

                for de_index, device in enumerate(system.devices):
                    for da_index, dd in enumerate(device.data):
                        sensor_id = f"{DOMAIN}_{device.system_id}_{device.device_uuid}_{da_index}_{de_index}"
                        if await self.is_sensor_disabled(sensor_id):
                            device.data[da_index].skip_data_update = True
                    device_data = self.api.get_data_by_device(
                        device, DeviceDataBucketResolution.HOUR, start, end
                    )
                    system_data["devices_data"].append(
                        [da async for da in device_data]
                    )
            except ClientResponseError as e:
                self._set_quota_and_raise(e)
                current_failures[system_id] = SystemCoordinator._failure_details(
                    system.home, e
                )
            except TimeoutError as e:
                current_failures[system_id] = SystemCoordinator._failure_details(
                    system.home, e
                )
            except CancelledError:
                raise
            else:
                data[system_id] = system_data
                self.system_last_success[system_id] = dt.now(timezone.utc).isoformat()
                if system_id in previous_failures:
                    _LOGGER.info("Daily data for system %s recovered", system_id)
                continue

            if system_id not in previous_failures:
                _LOGGER.warning(
                    "Daily data update failed for system %s; keeping previous data: %s",
                    system_id,
                    current_failures[system_id]["error"],
                )
            else:
                _LOGGER.debug(
                    "Daily data for system %s is still unavailable: %s",
                    system_id,
                    current_failures[system_id]["error"],
                )
            if system_id in previous_data:
                data[system_id] = previous_data[system_id]

        self.system_failures = current_failures
        self._clear_quota_state()
        return data
