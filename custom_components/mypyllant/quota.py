from __future__ import annotations

from datetime import datetime as dt, timedelta, timezone
from typing import Any, TypedDict
from urllib.parse import urlsplit
import re

from aiohttp import ClientResponseError
from homeassistant.core import HomeAssistant
from homeassistant.helpers.storage import Store

from .const import DOMAIN, QUOTA_PAUSE_INTERVAL
from .utils import extract_quota_duration, is_quota_exceeded_exception

_STORAGE_VERSION = 1
_STORAGE_KEY_PREFIX = f"{DOMAIN}.quota_backoff"


class StoredQuotaState(TypedDict):
    hit_at: str
    until: str
    message: str
    status: int | None
    url: str | None


class QuotaBackoffStore:
    """Persist account-level API quota backoff across reloads and restarts."""

    def __init__(
        self, hass: HomeAssistant, entry_id: str, *, scope: str = "account"
    ) -> None:
        if scope not in ("account", "energy"):
            raise ValueError("Unknown quota scope")
        key = (
            f"{_STORAGE_KEY_PREFIX}_{entry_id}"
            if scope == "account"
            else f"{_STORAGE_KEY_PREFIX}_energy_{entry_id}"
        )
        self._store: Store[dict[str, Any]] = Store(
            hass,
            _STORAGE_VERSION,
            key,
            private=True,
        )
        self._state: StoredQuotaState | None = None
        self._loaded = False

    @staticmethod
    def _parse_datetime(value: str | None) -> dt | None:
        if not value:
            return None
        try:
            parsed = dt.fromisoformat(value)
        except (TypeError, ValueError):
            return None
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return parsed.astimezone(timezone.utc)

    async def async_load(self) -> None:
        """Load persisted quota state once."""
        if self._loaded:
            return
        self._loaded = True
        data = await self._store.async_load()
        if not isinstance(data, dict):
            self._state = None
            return

        until = self._parse_datetime(data.get("until"))
        hit_at = self._parse_datetime(data.get("hit_at"))
        if until is None or hit_at is None:
            await self.async_clear()
            return

        self._state = {
            "hit_at": hit_at.isoformat(),
            "until": until.isoformat(),
            "message": str(data.get("message") or "Quota exceeded"),
            "status": (
                data.get("status")
                if isinstance(data.get("status"), int)
                else None
            ),
            "url": str(data.get("url")) if data.get("url") else None,
        }

        if not self.is_active:
            await self.async_clear()

    @property
    def is_active(self) -> bool:
        """Return whether a persisted backoff is currently active."""
        if self._state is None:
            return False
        until = self._parse_datetime(self._state["until"])
        return until is not None and dt.now(timezone.utc) < until

    @property
    def until(self) -> dt | None:
        if self._state is None:
            return None
        return self._parse_datetime(self._state["until"])

    @property
    def remaining_seconds(self) -> int:
        until = self.until
        if until is None:
            return 0
        return max(0, int((until - dt.now(timezone.utc)).total_seconds()))

    @property
    def state(self) -> StoredQuotaState | None:
        return self._state.copy() if self._state is not None else None

    def retry_message(self, context: str = "API access") -> str:
        """Return a human-readable message without making an API request."""
        remaining = self.remaining_seconds
        until = self.until
        until_text = until.isoformat() if until is not None else "unknown"
        return (
            f"myVAILLANT quota backoff is active for {context}; "
            f"no API request will be made for another {remaining}s "
            f"(until {until_text})"
        )

    async def async_set_from_exception(self, exc: ClientResponseError) -> None:
        """Persist quota state from a Vaillant quota response."""
        if not is_quota_exceeded_exception(exc):
            return

        now = dt.now(timezone.utc)
        duration = extract_quota_duration(exc)
        if duration is None:
            duration = QUOTA_PAUSE_INTERVAL
        request_info = getattr(exc, "request_info", None)
        real_url = getattr(request_info, "real_url", None)
        self._state = {
            "hit_at": now.isoformat(),
            "until": (now + timedelta(seconds=duration)).isoformat(),
            "message": exc.message or "Quota exceeded",
            "status": exc.status,
            "url": str(real_url) if real_url is not None else None,
        }
        self._loaded = True
        await self._store.async_save(dict(self._state))

    async def async_import_state(self, state: StoredQuotaState) -> None:
        """Copy a legacy deadline without shortening an existing energy pause."""
        until = self._parse_datetime(state.get("until"))
        if until is None:
            return
        if self.until is not None and self.until >= until:
            return
        self._state = state.copy()
        self._loaded = True
        await self._store.async_save(dict(self._state))

    async def async_clear(self) -> None:
        """Clear persistent quota state."""
        self._state = None
        self._loaded = True
        await self._store.async_remove()


def is_energy_history_url(url: str | None) -> bool:
    """Identify only the historical device buckets endpoint, not currentSystem."""
    if not url:
        return False
    try:
        path = urlsplit(str(url)).path
    except ValueError:
        return False
    return re.search(r"/emf/v[0-9]+/[^/]+/devices/[^/]+/buckets/?$", path) is not None


def is_energy_quota_exception(exc: ClientResponseError) -> bool:
    """Only a buckets 403 quota is scoped locally to energy history.

    A 429 or an unknown endpoint remains account-wide; do not assume that
    Vaillant's undocumented policy is always endpoint-specific.
    """
    request_info = getattr(exc, "request_info", None)
    return (
        exc.status == 403
        and is_quota_exceeded_exception(exc)
        and is_energy_history_url(getattr(request_info, "real_url", None))
    )


async def async_migrate_energy_quota(
    account: QuotaBackoffStore, energy: QuotaBackoffStore
) -> bool:
    """Reclassify old buckets-only 403 without deleting the server's deadline."""
    state = account.state
    if (
        state is None
        or state.get("status") != 403
        or not is_energy_history_url(state.get("url"))
    ):
        return False
    # Save first: failed storage writes must never erase a known quota.
    await energy.async_import_state(state)
    await account.async_clear()
    return True
