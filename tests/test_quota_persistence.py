from aiohttp import RequestInfo
from aiohttp.client_exceptions import ClientResponseError

from custom_components.mypyllant.quota import QuotaBackoffStore


async def test_quota_backoff_persists_across_store_instances(hass):
    """Persisted quota must survive integration reloads/restarts."""
    exc = ClientResponseError(
        request_info=RequestInfo(
            url="https://api.vaillant-group.com/service-connected-control/end-user-app-api/v1/homes",  # type: ignore
            method="GET",
            headers=None,  # type: ignore
        ),
        history=None,  # type: ignore
        status=403,
        message="Out of call volume quota",
        headers={"Retry-After": "1800"},  # type: ignore
    )

    first = QuotaBackoffStore(hass, "quota-test-entry")
    await first.async_set_from_exception(exc)
    assert first.is_active
    assert first.remaining_seconds > 0

    # Simulate a newly-created integration instance after a reload/restart.
    second = QuotaBackoffStore(hass, "quota-test-entry")
    await second.async_load()
    assert second.is_active
    assert second.remaining_seconds > 0
    assert second.state is not None
    assert second.state["status"] == 403

    await second.async_clear()
