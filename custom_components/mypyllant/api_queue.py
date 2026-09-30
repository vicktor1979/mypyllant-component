"""Serialize account refresh batches in this Home Assistant instance.

The queue covers setup/login, coordinator refreshes and reloads.  It does not
coordinate other HA instances or the official app.  No API call is made while
waiting.  An asyncio lock (rather than per-account timers) prevents overlap.
"""
from __future__ import annotations

import asyncio
import logging
import time
from collections import Counter
from contextlib import asynccontextmanager
from functools import wraps
from typing import TYPE_CHECKING

from .const import API_REFRESH_GAP_SECONDS, DOMAIN

if TYPE_CHECKING:
    from homeassistant.core import HomeAssistant

_LOGGER = logging.getLogger(__name__)
_DATA_KEY = f"{DOMAIN}_api_refresh_queue"


class ApiRefreshQueue:
    """FIFO, cancellation-safe, task-reentrant batch lock with a quiet gap."""

    def __init__(self, gap_seconds: float = API_REFRESH_GAP_SECONDS) -> None:
        self.gap_seconds = max(0.0, float(gap_seconds))
        self._lock = asyncio.Lock()
        self._owner: asyncio.Task | None = None
        self._owner_entry: str | None = None
        self._next_allowed = 0.0
        self._waiting: Counter[str] = Counter()

    def status(self, entry_id: str) -> dict[str, bool]:
        return {
            "waiting": self._waiting[entry_id] > 0,
            "active": self._owner_entry == entry_id,
        }

    @asynccontextmanager
    async def slot(self, entry_id: str, label: str):
        task = asyncio.current_task()
        if task is not None and task is self._owner:
            if self._owner_entry != entry_id:
                raise RuntimeError("A refresh batch cannot nest another account")
            # Setup calls the coordinator in the same task; do not deadlock.
            yield
            return

        self._waiting[entry_id] += 1
        waiting = True
        acquired = False
        started = False
        try:
            _LOGGER.debug("API refresh queued: %s", label)
            await self._lock.acquire()
            acquired = True
            remaining = self._next_allowed - time.monotonic()
            if remaining > 0:
                await asyncio.sleep(remaining)
            self._waiting[entry_id] -= 1
            waiting = False
            self._owner = task
            self._owner_entry = entry_id
            started = True
            _LOGGER.debug("API refresh started: %s", label)
            yield
        finally:
            if waiting:
                self._waiting[entry_id] -= 1
            if not self._waiting[entry_id]:
                self._waiting.pop(entry_id, None)
            if started:
                self._owner = None
                self._owner_entry = None
                self._next_allowed = time.monotonic() + self.gap_seconds
                _LOGGER.debug("API refresh finished: %s", label)
            if acquired:
                self._lock.release()


def get_api_refresh_queue(hass: HomeAssistant) -> ApiRefreshQueue:
    """Keep one queue across all entries, including entry reloads."""
    if _DATA_KEY not in hass.data:
        hass.data[_DATA_KEY] = ApiRefreshQueue()
    return hass.data[_DATA_KEY]


def serialized_refresh(method):
    """Check local guards before AND after waiting for an API refresh slot."""
    @wraps(method)
    async def wrapped(self, *args, **kwargs):
        # Disabled history and empty accounts never reserve an API slot.
        if self._skip_network_refresh():
            return {}
        await self._raise_if_persistent_quota_hit()
        self._raise_if_quota_hit()
        entry_data = self.hass_data
        queue = get_api_refresh_queue(self.hass)
        async with queue.slot(
            self.entry.entry_id,
            f"{self.entry.title}: {self.__class__.__name__}",
        ):
            # An entry could have been unloaded/reloaded while queued.
            if self.hass.data.get(DOMAIN, {}).get(self.entry.entry_id) is not entry_data:
                raise asyncio.CancelledError("Config entry changed while queued")
            if self._skip_network_refresh():
                return {}
            await self._raise_if_persistent_quota_hit()
            self._raise_if_quota_hit()
            return await method(self, *args, **kwargs)
    return wrapped
