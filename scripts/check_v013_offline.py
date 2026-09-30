"""Offline regression tests for v0.9.19.13.

Run in a separate Python process: python scripts/check_v013_offline.py
Uses real asyncio and the actual integration modules with small HA/myPyllant
interface stubs. No network, credentials, or installed Home Assistant required.
These tests do NOT replace Home Assistant integration/CI or real-device tests.
"""
from __future__ import annotations

import ast
import asyncio
import copy
import importlib.util
import json
import logging
from pathlib import Path
import sys
import time
import types
import unittest
from datetime import datetime, timedelta, timezone
from enum import StrEnum
from unittest.mock import AsyncMock, MagicMock, patch

from aiohttp import ClientResponseError, RequestInfo
from multidict import CIMultiDictProxy, CIMultiDict
from yarl import URL

ROOT = Path(__file__).resolve().parents[1]
COMPONENT = ROOT / 'custom_components' / 'mypyllant'
PREFIX = 'custom_components.mypyllant'


def module(name, **attrs):
    obj = types.ModuleType(name)
    obj.__path__ = []
    obj.__dict__.update(attrs)
    sys.modules[name] = obj
    if '.' in name:
        parent, child = name.rsplit('.', 1)
        if parent in sys.modules:
            setattr(sys.modules[parent], child, obj)
    return obj


class HAError(Exception):
    pass


class ConfigEntryNotReady(HAError):
    pass


class FakeEntity:
    hass = None
    def __init__(self, *args, **kwargs):
        pass
    def __class_getitem__(cls, key):
        return cls
    async def async_added_to_hass(self):
        pass
    def async_on_remove(self, callback):
        pass
    def async_write_ha_state(self):
        pass


class FakeCoordinator:
    def __init__(self, hass, logger, name=None, update_interval=None, **kwargs):
        self.hass = hass
        self.update_interval = update_interval
        self.data = None
        self.last_update_success = True
        self.last_exception = None
        self._listeners = []
    async def async_refresh(self):
        try:
            self.data = await self._async_update_data()
            self.last_update_success = True
            self.last_exception = None
        except HAError as exc:
            self.last_update_success = False
            self.last_exception = exc
        for listener in self._listeners[:]:
            listener()
    async def async_config_entry_first_refresh(self):
        await self.async_refresh()
        if not self.last_update_success:
            raise ConfigEntryNotReady() from self.last_exception
    async def async_request_refresh(self):
        await self.async_refresh()
    def async_set_updated_data(self, value):
        self.data = value
        self.last_update_success = True
    def async_add_listener(self, callback):
        self._listeners.append(callback)
        return lambda: self._listeners.remove(callback)
    async def async_shutdown(self):
        pass


class FakeStore:
    def __class_getitem__(cls, arg):
        return cls
    def __init__(self, hass, version, key, **kwargs):
        self.hass, self.key = hass, key
    async def async_load(self):
        return copy.deepcopy(self.hass.storage.get(self.key))
    async def async_save(self, value):
        self.hass.storage[self.key] = copy.deepcopy(value)
    async def async_remove(self):
        self.hass.storage.pop(self.key, None)


class DummySelector:
    SelectSelectorMode = types.SimpleNamespace(LIST='list', DROPDOWN='dropdown')
    def __getattr__(self, key):
        return lambda *a, **kw: kw


class ZoneOperatingType(StrEnum):
    HEATING = 'HEATING'
    COOLING = 'COOLING'


class Resolution(StrEnum):
    DAY = 'DAY'
    HOUR = 'HOUR'


class Platform(StrEnum):
    BINARY_SENSOR = 'binary_sensor'
    BUTTON = 'button'
    CALENDAR = 'calendar'
    CLIMATE = 'climate'
    DATETIME = 'datetime'
    NUMBER = 'number'
    SENSOR = 'sensor'
    SWITCH = 'switch'
    WATER_HEATER = 'water_heater'


module('voluptuous', Schema=lambda x: x, Optional=lambda x, **kw: x,
       Required=lambda x, **kw: x, Coerce=lambda x: x)
module('homeassistant')
module('homeassistant.core', HomeAssistant=object, callback=lambda fn: fn,
       SupportsResponse=types.SimpleNamespace(ONLY='only'),
       ServiceCall=object, ServiceResponse=dict)
module('homeassistant.const', Platform=Platform)
module('homeassistant.config_entries', ConfigEntry=object)
module('homeassistant.exceptions', ConfigEntryNotReady=ConfigEntryNotReady,
       ConfigEntryAuthFailed=HAError, HomeAssistantError=HAError)
module('homeassistant.helpers', selector=DummySelector())
module('homeassistant.helpers.entity', EntityCategory=types.SimpleNamespace(DIAGNOSTIC='diagnostic'))
module('homeassistant.helpers.device_registry', DeviceInfo=dict)
module('homeassistant.helpers.update_coordinator', DataUpdateCoordinator=FakeCoordinator,
       UpdateFailed=HAError, CoordinatorEntity=FakeEntity)
module('homeassistant.helpers.storage', Store=FakeStore)
module('homeassistant.helpers.event')
module('homeassistant.helpers.entity_registry', async_get=lambda hass: types.SimpleNamespace(
    async_get_entity_id=lambda *a: None, async_get=lambda *a: None))
module('myPyllant')
module('myPyllant.enums', ZoneOperatingType=ZoneOperatingType, DeviceDataBucketResolution=Resolution)
module('myPyllant.models', System=object, Home=object, DeviceData=object)
module('myPyllant.const', DEFAULT_BRAND='vaillant', DEFAULT_HOLIDAY_DURATION=365)
module('myPyllant.http_client', AuthenticationFailed=type('AuthenticationFailed',(Exception,),{}),
       RealmInvalid=type('RealmInvalid',(Exception,),{}),
       LoginEndpointInvalid=type('LoginEndpointInvalid',(Exception,),{}))
module('myPyllant.api', MyPyllantAPI=object,
       AmbisenseNoFacilityError=type('AmbisenseNoFacilityError',(Exception,),{}))
for name in ('export', 'report'):
    module('myPyllant.'+name, main=AsyncMock())
module('myPyllant.tests')
module('myPyllant.tests.generate_test_data', main=AsyncMock())
module('custom_components')
package = module(PREFIX)
package.__path__ = [str(COMPONENT)]


def schedule(hass, delay, callback):
    timer = {'delay': delay, 'callback': callback, 'cancelled': False}
    hass.timers.append(timer)
    def cancel():
        timer['cancelled'] = True
    return cancel


sys.modules['homeassistant.helpers.event'].async_call_later = schedule


def load(name):
    fullname=PREFIX+'.'+name
    spec=importlib.util.spec_from_file_location(fullname, COMPONENT/(name+'.py'))
    obj=importlib.util.module_from_spec(spec)
    sys.modules[fullname]=obj
    spec.loader.exec_module(obj)
    return obj


CONST = load('const')
UTILS = load('utils')
QUOTA = load('quota')
QUEUE = load('api_queue')
COORD = load('coordinator')
# Execute __init__ with the imported child modules available.
exec(compile((COMPONENT/'__init__.py').read_text(), str(COMPONENT/'__init__.py'), 'exec'), package.__dict__)
INIT = package

# Compile the actual diagnostic entity class, without recorder/UI dependencies.
sensor_tree = ast.parse((COMPONENT/'sensor.py').read_text())
status_node = next(n for n in sensor_tree.body if isinstance(n, ast.ClassDef) and n.name == 'VaillantApiStatusSensor')
status_env = dict(datetime=datetime, timedelta=timedelta, timezone=timezone,
                  SensorEntity=FakeEntity, EntityCategory=types.SimpleNamespace(DIAGNOSTIC='diagnostic'),
                  callback=lambda f: f, DOMAIN=CONST.DOMAIN,
                  ConfigEntry=object, SystemCoordinator=COORD.SystemCoordinator,
                  QuotaBackoffStore=QUOTA.QuotaBackoffStore, Mapping=dict, Any=object,
                  API_DOWN_PAUSE_INTERVAL=CONST.API_DOWN_PAUSE_INTERVAL,
                  extract_quota_duration=UTILS.extract_quota_duration,
                  is_quota_exceeded_exception=UTILS.is_quota_exceeded_exception,
                  OPTION_FETCH_ENERGY_HISTORY=CONST.OPTION_FETCH_ENERGY_HISTORY,
                  DEFAULT_FETCH_ENERGY_HISTORY=CONST.DEFAULT_FETCH_ENERGY_HISTORY,
                  get_api_refresh_queue=QUEUE.get_api_refresh_queue,
                  dt_util=types.SimpleNamespace(as_local=lambda d: d))
exec(compile(ast.Module(body=[status_node], type_ignores=[]), 'sensor-status-extract', 'exec'), status_env)
StatusSensor = status_env['VaillantApiStatusSensor']


class Entry:
    def __init__(self, number='01', energy=None):
        self.entry_id = 'entry-'+number
        self.title = 'kazan'+number+'@example.invalid'
        self.data = {'username': self.title, 'password':'not-a-real-password'}
        self.options = {}
        if energy is not None:
            self.options[CONST.OPTION_FETCH_ENERGY_HISTORY] = energy
        self.unloads=[]
        self.listeners=[]
    def async_on_unload(self, fn):
        self.unloads.append(fn)
    def add_update_listener(self, fn):
        self.listeners.append(fn)
        return lambda: self.listeners.remove(fn)


class FakeHass:
    def __init__(self, gap=0):
        self.data={CONST.DOMAIN: {}}
        self.data['mypyllant_api_refresh_queue']=QUEUE.ApiRefreshQueue(gap)
        self.storage={}
        self.timers=[]
        self.tasks=[]
        self.config_entries=types.SimpleNamespace(
            async_forward_entry_setups=AsyncMock(), async_reload=AsyncMock(return_value=True),
            async_unload_platforms=AsyncMock(return_value=True))
        self.services=types.SimpleNamespace(async_register=MagicMock())
    async def async_add_executor_job(self, fn, *args):
        return fn(*args)
    def async_create_task(self, coro):
        task=asyncio.create_task(coro)
        self.tasks.append(task)
        return task


def exception(path='homes', status=403, delay='4.15:58:14.', headers=None):
    url=URL('https://api.vaillant-group.com/service-connected-control/end-user-app-api/v1/'+path)
    return ClientResponseError(RequestInfo(url=url, method='GET', headers=CIMultiDictProxy(CIMultiDict())), (),
         status=status, message='Out of call volume quota. Quota will be replenished in '+delay,
         headers=headers)


BUCKETS='emf/v2/system/devices/device/buckets?resolution=HOUR'


class TestQueue(unittest.IsolatedAsyncioTestCase):
    async def test_seven_batches_never_overlap_and_are_fifo(self):
        q=QUEUE.ApiRefreshQueue(0.006)
        events=[]
        async def run(i):
            async with q.slot(str(i), str(i)):
                events.append(('start',i,time.monotonic()))
                await asyncio.sleep(0.003)
                events.append(('end',i,time.monotonic()))
        await asyncio.gather(*(run(i) for i in range(7)))
        self.assertEqual([e[:2] for e in events], [(a,i) for i in range(7) for a in ['start','end']])
        for i in range(1,7):
            self.assertGreaterEqual(events[2*i][2]-events[2*i-1][2],0.005)
    async def test_nested_same_task_uses_single_slot(self):
        q=QUEUE.ApiRefreshQueue(1)
        async with q.slot('one','setup'):
            async with q.slot('one','live'):
                self.assertTrue(q.status('one')['active'])
        self.assertFalse(q.status('one')['active'])
    async def test_cross_account_nesting_rejected(self):
        q=QUEUE.ApiRefreshQueue(0)
        async with q.slot('one','setup'):
            with self.assertRaises(RuntimeError):
                async with q.slot('two','wrong'):
                    pass
    async def test_cancelling_waiter_does_not_block_next(self):
        q=QUEUE.ApiRefreshQueue(0)
        async with q.slot('one','owner'):
            async def waiting():
                async with q.slot('two','waiting'):
                    self.fail('cancelled waiter entered')
            task=asyncio.create_task(waiting())
            await asyncio.sleep(0)
            task.cancel()
            with self.assertRaises(asyncio.CancelledError):
                await task
        self.assertFalse(q.status('two')['waiting'])
        async with q.slot('three','next'):
            pass
    async def test_cancellation_during_gap_releases_lock(self):
        q=QUEUE.ApiRefreshQueue(1)
        async with q.slot('a','first'):
            pass
        async def runner():
            async with q.slot('b','sleep'):
                self.fail('cancelled gap ran')
        task=asyncio.create_task(runner())
        await asyncio.sleep(0.01)
        task.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await task
        self.assertFalse(q._lock.locked())
        self.assertFalse(q.status('b')['waiting'])
    async def test_failure_and_active_cancellation_release_lock(self):
        q=QUEUE.ApiRefreshQueue(0)
        for error in [ValueError, asyncio.CancelledError]:
            with self.assertRaises(error):
                async with q.slot('a','fail'):
                    raise error()
            async with q.slot('b','next'):
                pass
    async def test_singleton_survives_entry_reload(self):
        hass=FakeHass()
        q=QUEUE.get_api_refresh_queue(hass)
        hass.data[CONST.DOMAIN]['a']={}
        hass.data[CONST.DOMAIN].pop('a')
        self.assertIs(q,QUEUE.get_api_refresh_queue(hass))


class TestQuotas(unittest.IsolatedAsyncioTestCase):
    async def test_day_duration_and_normal_and_retry_header(self):
        for text, seconds in [('4.15:58:14.',403094), ('5.09:57:29.',467849),('00:08:54.',534)]:
            self.assertEqual(UTILS.extract_quota_duration(exception(delay=text)),seconds)
        self.assertEqual(UTILS.extract_quota_duration(exception(headers={'Retry-After':'42'})),42)
    async def test_403_buckets_only_classification(self):
        self.assertTrue(QUOTA.is_energy_quota_exception(exception(BUCKETS)))
        self.assertFalse(QUOTA.is_energy_quota_exception(exception(BUCKETS,status=429)))
        self.assertFalse(QUOTA.is_energy_quota_exception(exception('emf/v2/system/currentSystem')))
        self.assertFalse(QUOTA.is_energy_quota_exception(exception()))
    async def test_migrate_energy_preserves_exact_deadline(self):
        hass=FakeHass(); a=QUOTA.QuotaBackoffStore(hass,'a'); b=QUOTA.QuotaBackoffStore(hass,'a',scope='energy')
        await a.async_set_from_exception(exception(BUCKETS)); state=a.state
        self.assertTrue(await QUOTA.async_migrate_energy_quota(a,b))
        self.assertFalse(a.is_active)
        self.assertEqual(state,b.state)
        loaded=QUOTA.QuotaBackoffStore(hass,'a',scope='energy'); await loaded.async_load()
        self.assertEqual(state,loaded.state)
    async def test_migration_does_not_move_account_error(self):
        for path,status in [('homes',403),('unknown',403),(BUCKETS,429)]:
            hass=FakeHass(); a=QUOTA.QuotaBackoffStore(hass,'a'); b=QUOTA.QuotaBackoffStore(hass,'a',scope='energy')
            await a.async_set_from_exception(exception(path,status))
            self.assertFalse(await QUOTA.async_migrate_energy_quota(a,b))
            self.assertTrue(a.is_active)
            self.assertFalse(b.is_active)
    async def test_failed_migration_does_not_remove_original(self):
        hass=FakeHass(); a=QUOTA.QuotaBackoffStore(hass,'a'); b=QUOTA.QuotaBackoffStore(hass,'a',scope='energy')
        await a.async_set_from_exception(exception(BUCKETS))
        with patch.object(b._store,'async_save',side_effect=OSError('disk')):
            with self.assertRaises(OSError):
                await QUOTA.async_migrate_energy_quota(a,b)
        self.assertTrue(a.is_active)
    async def test_migration_does_not_shorten_energy_deadline(self):
        hass=FakeHass(); a=QUOTA.QuotaBackoffStore(hass,'a'); b=QUOTA.QuotaBackoffStore(hass,'a',scope='energy')
        await a.async_set_from_exception(exception(BUCKETS,delay='00:01:00'))
        await b.async_set_from_exception(exception(BUCKETS,delay='5.00:00:00'))
        until=b.until
        await QUOTA.async_migrate_energy_quota(a,b)
        self.assertEqual(until,b.until)


class FakeApi:
    def __init__(self, **kwargs):
        self.username=kwargs.get('username','user')
        self.login=AsyncMock()
        self.refresh_token=AsyncMock()
        self.aiohttp_session=types.SimpleNamespace(close=AsyncMock())
        self.oauth_session_expires=datetime.now(timezone.utc)+timedelta(minutes=10)


class TestCoordinator(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.hass=FakeHass(); self.entry=Entry(energy=True); self.api=FakeApi()
        self.hass.data[CONST.DOMAIN][self.entry.entry_id]={}
        self.data=self.hass.data[CONST.DOMAIN][self.entry.entry_id]
        self.data['quota_backoff']=QUOTA.QuotaBackoffStore(self.hass,self.entry.entry_id)
        self.data['energy_quota_backoff']=QUOTA.QuotaBackoffStore(self.hass,self.entry.entry_id,scope='energy')
        self.system=COORD.SystemCoordinator(self.hass,self.api,self.entry,timedelta(seconds=600))
        self.data['system_coordinator']=self.system
        self.daily=COORD.DailyDataCoordinator(self.hass,self.api,self.entry,timedelta(hours=3))
        self.data['daily_data_coordinator']=self.daily
    async def test_disabled_history_never_touches_api_or_queue(self):
        self.entry.options[CONST.OPTION_FETCH_ENERGY_HISTORY]=False
        with patch.object(QUEUE.get_api_refresh_queue(self.hass),'slot', side_effect=AssertionError('queue')):
            self.assertEqual(await self.daily._async_update_data(),{})
        self.api.refresh_token.assert_not_awaited()
    async def test_empty_account_never_touches_api(self):
        self.system.empty_account=True
        self.assertEqual(await self.daily._async_update_data(),{})
        self.api.refresh_token.assert_not_awaited()
    async def test_daily_bucket_quota_does_not_block_live_store(self):
        self.data['loaded_platforms']=['sensor']
        with self.assertRaises(HAError):
            await self.daily._set_quota_and_raise(exception(BUCKETS))
        self.assertTrue(self.data['energy_quota_backoff'].is_active)
        self.assertFalse(self.data['quota_backoff'].is_active)
        self.assertIn('energy_quota_retry_cancel', self.data)
        self.assertNotIn('quota_reload_cancel', self.data)
        await self.system._raise_if_persistent_quota_hit()
    async def test_429_still_stops_account(self):
        self.data['loaded_platforms']=['sensor']
        with self.assertRaises(HAError):
            await self.daily._set_quota_and_raise(exception(BUCKETS,status=429))
        self.assertTrue(self.data['quota_backoff'].is_active)
        self.assertFalse(self.data['energy_quota_backoff'].is_active)
        with self.assertRaises(HAError):
            await self.system._raise_if_persistent_quota_hit()
    async def test_energy_backoff_prevents_network_but_not_live_guard(self):
        await self.data['energy_quota_backoff'].async_set_from_exception(exception(BUCKETS))
        with self.assertRaises(HAError):
            await self.daily._async_update_data()
        self.api.refresh_token.assert_not_awaited()
        await self.system._raise_if_persistent_quota_hit()
    async def test_live_stays_connected_with_energy_quota(self):
        await self.data['energy_quota_backoff'].async_set_from_exception(exception(BUCKETS))
        self.system.homes_last_refresh='2026-09-30T08:00:00+00:00'
        sensor=StatusSensor(self.entry,self.system,self.data['quota_backoff']); sensor.hass=self.hass
        self.assertEqual(sensor.native_value,'Kapcsolódva')
        attrs=sensor.extra_state_attributes
        self.assertIsNone(attrs['HTTP állapot'])
        self.assertFalse(attrs['API-korlát miatti várakozás'])
        self.assertTrue(attrs['Energiaelőzmények API-korlátja'])
    async def test_energy_retry_never_reloads_entire_account(self):
        await self.data['energy_quota_backoff'].async_set_from_exception(exception(BUCKETS))
        self.daily._schedule_energy_quota_retry()
        self.daily.async_request_refresh=AsyncMock()
        self.hass.timers[-1]['callback'](None)
        await asyncio.gather(*self.hass.tasks)
        self.daily.async_request_refresh.assert_awaited_once()
        self.hass.config_entries.async_reload.assert_not_awaited()
    async def test_cancelled_queued_refresh_does_not_run(self):
        q=QUEUE.get_api_refresh_queue(self.hass)
        self.api.get_homes=MagicMock(side_effect=AssertionError('must not send'))
        async with q.slot('other','busy'):
            task=asyncio.create_task(self.system._async_update_data())
            await asyncio.sleep(0)
            task.cancel()
            with self.assertRaises(asyncio.CancelledError):
                await task
        self.assertIsNone(self.system._quota_exc_info)
    async def test_queued_refresh_rechecks_backoff(self):
        q=QUEUE.get_api_refresh_queue(self.hass)
        self.api.get_homes=MagicMock(side_effect=AssertionError('must not send'))
        async with q.slot('other','busy'):
            task=asyncio.create_task(self.system._async_update_data())
            await asyncio.sleep(0)
            await self.data['quota_backoff'].async_set_from_exception(exception())
        with self.assertRaises(HAError):
            await task
    async def test_queued_refresh_rejects_old_entry_object(self):
        q=QUEUE.get_api_refresh_queue(self.hass)
        async with q.slot('other','busy'):
            task=asyncio.create_task(self.system._async_update_data())
            await asyncio.sleep(0)
            self.hass.data[CONST.DOMAIN][self.entry.entry_id]={}
        with self.assertRaises(asyncio.CancelledError):
            await task


class TestSetup(unittest.IsolatedAsyncioTestCase):
    async def test_initial_setup_and_reload_do_not_construct_history_when_disabled(self):
        hass=FakeHass(); entry=Entry(); api=FakeApi()
        async def initial(coord):
            coord.data=[object()]
        with patch.object(INIT,'MyPyllantAPI',return_value=api), \
             patch.object(COORD.SystemCoordinator,'async_config_entry_first_refresh',initial), \
             patch.object(INIT,'DailyDataCoordinator',side_effect=AssertionError('history created')):
            self.assertTrue(await INIT.async_setup_entry(hass,entry))
            self.assertTrue(await INIT.async_unload_entry(hass,entry))
            self.assertTrue(await INIT.async_setup_entry(hass,entry))
        self.assertNotIn('daily_data_coordinator',hass.data[CONST.DOMAIN][entry.entry_id])
        self.assertEqual(api.login.await_count,2)
    async def test_explicit_energy_services_respect_opt_out(self):
        hass=FakeHass(); entry=Entry(); api=FakeApi()
        async def initial(coord): coord.data=[object()]
        with patch.object(INIT,'MyPyllantAPI',return_value=api), \
             patch.object(COORD.SystemCoordinator,'async_config_entry_first_refresh',initial):
            await INIT.async_setup_entry(hass,entry)
        handlers={call.args[1]:call.args[2] for call in hass.services.async_register.call_args_list}
        for name in [CONST.SERVICE_EXPORT,CONST.SERVICE_REPORT,CONST.SERVICE_GENERATE_TEST_DATA]:
            with self.assertRaisesRegex(HAError,'ki van kapcsolva'):
                await handlers[name](types.SimpleNamespace(data={'data':True}))

    async def test_enabled_history_uses_configured_interval_and_initial_fetch(self):
        hass=FakeHass(); entry=Entry(energy=True); api=FakeApi()
        entry.options[CONST.OPTION_UPDATE_INTERVAL_DAILY]=10800
        async def initial(coord):
            coord.data=[object()]
        daily=MagicMock(); daily.async_refresh=AsyncMock()
        with patch.object(INIT,'MyPyllantAPI',return_value=api), \
             patch.object(COORD.SystemCoordinator,'async_config_entry_first_refresh',initial), \
             patch.object(INIT,'DailyDataCoordinator',return_value=daily) as factory:
            await INIT.async_setup_entry(hass,entry)
        self.assertEqual(factory.call_args.args[-1],timedelta(hours=3))
        daily.async_refresh.assert_awaited_once()
    async def test_legacy_history_quota_does_not_prevent_startup(self):
        hass=FakeHass(); entry=Entry(); api=FakeApi()
        old=QUOTA.QuotaBackoffStore(hass,entry.entry_id)
        await old.async_set_from_exception(exception(BUCKETS))
        async def initial(coord): coord.data=[object()]
        with patch.object(INIT,'MyPyllantAPI',return_value=api), \
             patch.object(COORD.SystemCoordinator,'async_config_entry_first_refresh',initial):
            await INIT.async_setup_entry(hass,entry)
        api.login.assert_awaited_once()
        data=hass.data[CONST.DOMAIN][entry.entry_id]
        self.assertTrue(data['energy_quota_backoff'].is_active)
        self.assertFalse(data['quota_backoff'].is_active)
    async def test_account_quota_loads_diagnostics_without_login(self):
        hass=FakeHass(); entry=Entry()
        old=QUOTA.QuotaBackoffStore(hass,entry.entry_id)
        await old.async_set_from_exception(exception())
        with patch.object(INIT,'MyPyllantAPI',side_effect=AssertionError('API constructed')):
            self.assertTrue(await INIT.async_setup_entry(hass,entry))
        self.assertTrue(hass.data[CONST.DOMAIN][entry.entry_id]['diagnostic_only'])
    async def test_toggle_reload_applies_but_unrelated_setting_is_unchanged(self):
        hass=FakeHass(); entry=Entry(energy=False)
        hass.data[CONST.DOMAIN][entry.entry_id]={'energy_history_enabled':False}
        entry.options[CONST.OPTION_UPDATE_INTERVAL]=900
        await INIT._async_energy_option_updated(hass,entry)
        hass.config_entries.async_reload.assert_not_awaited()
        entry.options[CONST.OPTION_FETCH_ENERGY_HISTORY]=True
        await INIT._async_energy_option_updated(hass,entry)
        hass.config_entries.async_reload.assert_awaited_once_with(entry.entry_id)
    async def test_concurrent_initial_logins_are_serialized(self):
        hass=FakeHass(gap=0.003)
        events=[]
        class TracedApi(FakeApi):
            def __init__(self,**kw):
                super().__init__(**kw)
                async def login():
                    events.append(('start',self.username))
                    await asyncio.sleep(0.001)
                    events.append(('end',self.username))
                self.login=login
        async def initial(coord): coord.data=[object()]
        with patch.object(INIT,'MyPyllantAPI',TracedApi), \
             patch.object(COORD.SystemCoordinator,'async_config_entry_first_refresh',initial):
            await asyncio.gather(*(INIT.async_setup_entry(hass,Entry(str(i))) for i in range(7)))
        for i in range(7):
            self.assertEqual(events[2*i][0],'start')
            self.assertEqual(events[2*i+1][0],'end')
            self.assertEqual(events[2*i][1],events[2*i+1][1])
    async def test_cancelled_login_closes_session(self):
        hass=FakeHass(); entry=Entry(); api=FakeApi()
        api.login=AsyncMock(side_effect=asyncio.CancelledError())
        with patch.object(INIT,'MyPyllantAPI',return_value=api):
            with self.assertRaises(asyncio.CancelledError):
                await INIT.async_setup_entry(hass,entry)
        api.aiohttp_session.close.assert_awaited_once()
        self.assertFalse(QUEUE.get_api_refresh_queue(hass)._lock.locked())
    async def test_empty_account_uses_diagnostics_without_energy(self):
        hass=FakeHass(); entry=Entry(energy=True); api=FakeApi()
        async def initial(coord):
            coord.data=[]; coord.empty_account=True
        with patch.object(INIT,'MyPyllantAPI',return_value=api), \
             patch.object(COORD.SystemCoordinator,'async_config_entry_first_refresh',initial), \
             patch.object(INIT,'DailyDataCoordinator',side_effect=AssertionError('history constructed')):
            await INIT.async_setup_entry(hass,entry)
        self.assertEqual(hass.config_entries.async_forward_entry_setups.call_args.args[1], INIT.DIAGNOSTIC_PLATFORMS)


class TestActualRefreshPaths(unittest.IsolatedAsyncioTestCase):
    async def test_setup_nested_actual_live_fetch_does_not_deadlock(self):
        hass=FakeHass(); entry=Entry(); calls=[]
        class API(FakeApi):
            async def get_homes(self):
                calls.append('homes')
                yield types.SimpleNamespace(system_id='s',home_name='Test',nomenclature='Test')
            async def get_systems(self,*args):
                calls.append('system')
                yield types.SimpleNamespace(id='s',home=args[-1][0],devices=[],timezone=timezone.utc)
            async def get_data_by_device(self,*args):
                calls.append('buckets')
                yield None
        with patch.object(INIT,'MyPyllantAPI',API):
            self.assertTrue(await asyncio.wait_for(INIT.async_setup_entry(hass,entry),1))
        self.assertEqual(calls,['homes','system'])
        self.assertTrue(hass.data[CONST.DOMAIN][entry.entry_id]['system_coordinator'].last_update_success)

    async def test_runtime_updates_for_seven_accounts_do_not_interleave(self):
        hass=FakeHass(gap=0.002); calls=[]; entries=[]
        class API(FakeApi):
            async def get_homes(self):
                calls.append(('homes',self.username))
                await asyncio.sleep(0.001)
                yield types.SimpleNamespace(system_id=self.username,home_name='Test',nomenclature='Test')
            async def get_systems(self,*args):
                calls.append(('system',self.username))
                await asyncio.sleep(0.001)
                yield types.SimpleNamespace(id=self.username,home=args[-1][0],devices=[],timezone=timezone.utc)
        with patch.object(INIT,'MyPyllantAPI',API):
            for i in range(7):
                e=Entry(str(i)); entries.append(e)
                await INIT.async_setup_entry(hass,e)
        calls.clear()
        await asyncio.gather(*(hass.data[CONST.DOMAIN][e.entry_id]['system_coordinator'].async_request_refresh() for e in entries))
        self.assertEqual(len(calls),14)
        for i in range(7):
            self.assertEqual(calls[2*i][0],'homes')
            self.assertEqual(calls[2*i+1][0],'system')
            self.assertEqual(calls[2*i][1],calls[2*i+1][1])

    async def test_startup_energy_failure_does_not_poison_live_refresh(self):
        hass=FakeHass(); entry=Entry(energy=True); counts={'homes':0,'buckets':0}
        class API(FakeApi):
            async def get_homes(self):
                counts['homes']+=1
                yield types.SimpleNamespace(system_id='s',home_name='Test',nomenclature='Test')
            async def get_systems(self,*args):
                device=types.SimpleNamespace(system_id='s', device_uuid='d', data=[])
                yield types.SimpleNamespace(id='s',home=args[-1][0],devices=[device],timezone=timezone.utc)
            async def get_data_by_device(self,*args):
                counts['buckets']+=1
                raise exception(BUCKETS)
                yield None
        with patch.object(INIT,'MyPyllantAPI',API):
            self.assertTrue(await INIT.async_setup_entry(hass,entry))
        data=hass.data[CONST.DOMAIN][entry.entry_id]
        self.assertFalse(data['daily_data_coordinator'].last_update_success)
        self.assertTrue(data['system_coordinator'].last_update_success)
        self.assertTrue(data['energy_quota_backoff'].is_active)
        self.assertFalse(data['quota_backoff'].is_active)
        await data['system_coordinator'].async_request_refresh()
        await data['daily_data_coordinator'].async_request_refresh()
        self.assertEqual(counts,{'homes':2,'buckets':1})

    async def test_actual_energy_refresh_still_works_when_opted_in(self):
        hass=FakeHass(); entry=Entry(energy=True); calls=[]
        class API(FakeApi):
            async def get_homes(self):
                yield types.SimpleNamespace(system_id='s',home_name='Test',nomenclature='Test')
            async def get_systems(self,*args):
                device=types.SimpleNamespace(system_id='s', device_uuid='d', data=[])
                yield types.SimpleNamespace(id='s',home=args[-1][0],devices=[device],timezone=timezone.utc)
            async def get_data_by_device(self,*args):
                calls.append('buckets')
                yield 'TEST_DATA'
        with patch.object(INIT,'MyPyllantAPI',API):
            self.assertTrue(await INIT.async_setup_entry(hass,entry))
        daily=hass.data[CONST.DOMAIN][entry.entry_id]['daily_data_coordinator']
        self.assertTrue(daily.last_update_success)
        self.assertEqual(daily.data['s']['devices_data'],[['TEST_DATA']])
        self.assertEqual(calls,['buckets'])


class TestStatic(unittest.TestCase):
    def test_changed_option_present_and_default_off(self):
        self.assertFalse(CONST.DEFAULT_FETCH_ENERGY_HISTORY)
        text=(COMPONENT/'config_flow.py').read_text()
        self.assertIn('OPTION_FETCH_ENERGY_HISTORY,\n            default=DEFAULT_FETCH_ENERGY_HISTORY',text)
        for rel in ['strings.json','translations/en.json','translations/hu.json']:
            obj=json.loads((COMPONENT/rel).read_text())
            self.assertIn('fetch_energy_history',obj['options']['step']['init']['data'])
    def test_all_python_and_json_parse(self):
        for path in COMPONENT.rglob('*.py'):
            ast.parse(path.read_text())
        for path in COMPONENT.rglob('*.json'):
            json.loads(path.read_text())


if __name__=='__main__':
    logging.disable(logging.CRITICAL)
    unittest.main(verbosity=2)
