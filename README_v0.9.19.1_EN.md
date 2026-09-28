# mypyllant-component v0.9.19.1 – per-system gateway failure isolation

This release is a fork-specific patch based on **v0.9.19** of `signalkraft/mypyllant-component`.

- Upstream: `signalkraft/mypyllant-component` v0.9.19
- Fork: `vicktor1979/mypyllant-component`
- Fork version: **v0.9.19.1**
- Goal: when multiple myVAILLANT systems are attached to the same account, a single unreachable/broken gateway must not make every other system unavailable.

## Problem

When one myVAILLANT account contains multiple homes/systems/gateways, the original component refreshes them through one shared `SystemCoordinator` update.

The systems were fetched through a single `get_systems()` iteration. If, for example, the third system out of five failed with a `503`, `504`, timeout, or another system-specific exception, the complete coordinator refresh could fail.

Example:

```text
System 1 -> OK
System 2 -> OK
System 3 -> 504 Gateway Time-out
System 4 -> actually healthy
System 5 -> actually healthy

Original result:
entities for ALL five systems could become unavailable.
```

This also made it difficult to identify which gateway was actually causing the problem.

## Changes

### 1. Fetch systems independently

`SystemCoordinator` now fetches every `Home` / system separately:

```text
System 1 -> individual fetch -> OK
System 2 -> individual fetch -> OK
System 3 -> individual fetch -> 504
System 4 -> individual fetch -> OK
System 5 -> individual fetch -> OK
```

A failure in one system no longer aborts refreshes for the remaining systems.

### 2. Per-system availability

The coordinator tracks the latest update state for each system individually.

New helper:

```python
coordinator.is_system_available(system_id)
```

Entities belonging to a system become unavailable only when **that system's** latest refresh failed.

Expected result:

```text
System 1 -> Available
System 2 -> Available
System 3 -> Unavailable
System 4 -> Available
System 5 -> Available
```

### 3. Preserve last known system objects

If a previously working system temporarily becomes unreachable, its last known `System` object is retained.

This is important because:

- existing entities keep their mapping to the correct system;
- list/index mappings do not shift when one system fails.

The affected entities are still marked `unavailable`, so stale values are not exposed as current data.

### 4. Stable system/index mapping

Several entities in the integration reference their system by `system_index`.

Simply removing a failed system from the data list could cause later entities to point to a different system.

The patch therefore preserves existing order and tracks systems by stable `system.id` values.

### 5. New diagnostic binary sensor for every gateway

Every myVAILLANT Home/gateway receives a diagnostic entity:

```text
<Home name> Gateway API Connection
```

Example:

```text
Honved u. 41 Gateway API Connection
```

Device class:

```text
connectivity
```

State:

- `on` = the latest API refresh for this system succeeded;
- `off` = the latest refresh for this system failed.

Diagnostic attributes:

```text
system_id
last_success
last_error
last_error_type
last_error_at
last_http_status
last_error_url
gateway_online_state
```

Example:

```yaml
last_http_status: 504
last_error_type: ClientResponseError
last_error: "504, message='Gateway Time-out'"
last_error_url: "https://api.vaillant-group.com/.../currentSystem"
```

This makes the failing gateway directly visible from Home Assistant.

### 6. DailyDataCoordinator isolation

Daily/energy data updates are also isolated per system.

When one system fails:

- daily/energy data for healthy systems continues updating;
- previous daily data for the failed system is retained;
- if `SystemCoordinator` already knows that the gateway is unavailable, `DailyDataCoordinator` avoids unnecessary additional requests to that system.

### 7. Account-wide quota errors remain global

Vaillant API quota/rate-limit handling intentionally keeps its existing global behavior.

Quota state is generally account/API-wide and should not be treated as a single-gateway failure.

### 8. Coordinator state is instance-local

The `homes` list used to be declared as a class attribute:

```python
homes: list[Home] = []
```

It is now instance-local:

```python
self.homes: list[Home] = []
```

This prevents potential state leakage between multiple config entries.

### 9. Python exception syntax fixes

Two invalid/legacy exception clauses were corrected:

```python
except ValueError, TypeError:
```

to:

```python
except (ValueError, TypeError):
```

and:

```python
except ValueError, AttributeError:
```

to:

```python
except (ValueError, AttributeError):
```

## Modified files

| File | Change |
|---|---|
| `custom_components/mypyllant/coordinator.py` | Per-system API error isolation, failure state, last-success tracking, DailyData isolation |
| `custom_components/mypyllant/binary_sensor.py` | New `Gateway API Connection` diagnostic entity and per-system availability |
| `custom_components/mypyllant/utils.py` | Shared entity availability and exception syntax fix |
| `custom_components/mypyllant/calendar.py` | Exception syntax fix |
| `custom_components/mypyllant/climate.py` | Per-system availability |
| `custom_components/mypyllant/number.py` | Existing availability rules combined with coordinator availability |
| `custom_components/mypyllant/sensor.py` | Per-system availability across sensor entity types |
| `custom_components/mypyllant/switch.py` | Per-system availability |
| `custom_components/mypyllant/ventilation_climate.py` | Per-system availability |
| `custom_components/mypyllant/water_heater.py` | Per-system availability |
| `custom_components/mypyllant/manifest.json` | Fork version set to `v0.9.19.1` |
| `pyproject.toml` | Fork version set to `0.9.19.1` |
| `uv.lock` | Fork package version updated |

## Fork versioning convention

The fork adds a fourth patch component to the upstream version:

```text
upstream v0.9.19
fork     v0.9.19.1
```

Additional fork-only changes on the same upstream base become:

```text
v0.9.19.2
v0.9.19.3
...
```

When upstream moves to `v0.9.20`, the first fork release becomes:

```text
v0.9.20.1
```

## Installation / testing

1. Back up the current `custom_components/mypyllant` directory.
2. Copy the modified files to their matching paths.
3. Restart Home Assistant.
4. Verify that every Home/gateway has a `Gateway API Connection` diagnostic entity.
5. Test with a known unreachable gateway.
6. Verify that only entities belonging to that system become `unavailable`.
7. Verify that healthy systems continue refreshing.
8. Check the diagnostic attributes of the failed gateway entity.

## Debug logging

The upstream documentation recommends the following logging configuration:

```yaml
logger:
  default: warning
  logs:
    custom_components.mypyllant: debug
    myPyllant: debug
```

**Important:** before uploading logs to GitHub, redact email addresses, passwords, access tokens, system UUIDs, serial numbers, physical addresses, and any other personal/private information.

## Validation performed

Static validation performed on the v0.9.19.1 patch:

- Python `compileall`: passed
- `manifest.json` parsing: passed
- `pyproject.toml` parsing: passed

A real multi-gateway myVAILLANT/Home Assistant runtime test is still required for final validation against the live API.

## Known edge case

If a gateway cannot return a usable `System` object **during the integration's initial setup**, the gateway diagnostic entity can still be created, but normal system entities cannot yet be built because no system model exists.

After the gateway recovers, reloading the integration may be required to create the complete set of entities for that system.

## Upstream contribution note

The functional changes can be submitted to `signalkraft/mypyllant-component` as a Pull Request.

For an upstream PR, fork-specific version changes (`v0.9.19.1`) should normally be excluded. The PR should contain the functional fix only; upstream release versioning should remain under the maintainer's control.
