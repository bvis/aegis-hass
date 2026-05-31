# Siren internal temperature sensor (#220)

> **Update (1.8.0-beta.3):** the refresh was originally hung off the poll cycle
> (below). That was starved on push-heavy hubs — every HTS push calls
> `async_set_updated_data`, which resets HA's poll timer, so the scheduled poll
> never fires again after startup and the refresh never ran. It now runs on a
> dedicated `async_track_time_interval` timer, independent of the poll. The
> Approach and Components sections below reflect the final timer-driven design.

## Problem

HomeSiren / StreetSiren devices report their internal temperature in the Ajax
app, but the integration does not expose it. Motion/door devices already get a
temperature sensor because the `StreamLightDevices` stream we consume carries a
`temperature` entry in `LightDeviceProfile.statuses` for them. For sirens the
server omits that status entry, so no sensor is created.

Buzzer / sounding state is **out of scope** here — `CommonSirenPart` only carries
settings/capabilities, and the live "is sounding" signal is almost certainly an
alarm event (FCM), not a polled status. Tracked separately.

## Where the value lives

The siren temperature is carried by a different RPC, `StreamHubDevice`
(`v3/mobilegwsvc/service/stream_hub_device`):

- `StreamHubDeviceResponse.Success.Snapshot.hub_device` → `HubDevice`
- `HubDevice` has a per-type `device` oneof; each siren case
  (`street_siren`, `street_siren_plus_g3`, `home_siren`, `home_siren_g3`,
  `home_siren_s`, `home_siren_fibra`) carries
  `device_temperature` (`DeviceTemperature { value: int32 (field 1),
  is_extreme: bool (field 2) }`).

All required protos are already compiled. The Ajax app renders this via
`DeviceInfoController.buildTemperatureSensorParts`, so it is a consumed value,
not a dead field.

## Approach: one-shot snapshot on a dedicated timer (not a persistent stream)

`StreamHubDeviceRequest` takes `hub_id` + `hub_device_id` — it is a **per-device**
stream, and `Success` only has a `snapshot` case (no deltas). A persistent
stream would mean one held-open connection *per siren* for a value that barely
changes, contradicting how the app uses the endpoint (transient, on device-detail
open). A one-shot (open → read first snapshot → close) mirrors the app's usage
and minimises Ajax backend load.

The refresh runs on its **own `async_track_time_interval` timer**, not the poll
cycle. The poll cannot be relied on here: on hubs with an active HTS stream every
push calls `async_set_updated_data`, which resets HA's poll timer, so the
scheduled poll never fires again after startup and a poll-driven refresh is
starved (the sensor never materialises). The dedicated timer is immune to that;
a non-blocking initial fetch at startup makes the sensor appear within seconds
rather than waiting a full interval.

## Components

1. **`devices_parser.parse_hub_device_temperature(hub_device_proto) -> float | None`**
   - `which = hub_device.WhichOneof("device")`; `getattr(hub_device, which)`.
   - If the sub-message `HasField("device_temperature")`, return
     `device_temperature.value` as float; else `None`. Defensive: any
     missing-field / unknown-type path returns `None`.

2. **`DevicesApi.get_hub_device_temperature(hub_id, hub_device_id) -> float | None`**
   - Mirrors `get_devices_snapshot`: open `StreamHubDeviceService` stub, send
     `StreamHubDeviceRequest(hub_id, hub_device_id)`, `timeout≈10s`, read first
     `success.snapshot`, parse, `break`. `failure` → log debug, return `None`.
     gRPC errors caught by caller.

3. **`coordinator._schedule_siren_temperature_refresh()`** — registers an
   `async_track_time_interval` timer (interval `SIREN_TEMP_REFRESH_INTERVAL = 900`,
   15 min) from `_first_startup_init`, stores the unsub in `_unsub_siren_temp`
   (cancelled in `async_shutdown`), and kicks a non-blocking initial fetch via
   `hass.async_create_task`. Idempotent.

   **`coordinator._async_refresh_siren_temperatures(_now=None)`** — the timer
   callback (runs on the loop, so no worker-thread marshalling).
   - For each device whose `device_type` ∈ `SIREN_TEMPERATURE_DEVICE_TYPES`
     **and** that does not already have `"temperature"` in `statuses`:
     call `get_hub_device_temperature`; on a non-None result, merge into
     `device.statuses["temperature"]` via `dataclasses.replace`.
   - Per-device try/except: one failure must not break the refresh.
   - If anything changed, push it to listeners via `async_set_updated_data` so
     the entity platform materialises the sensor. The timer cadence is the
     throttle; there is no separate `_siren_temp_last_fetch` rate-limit.

4. **`const.py`** — add `SIREN_TEMPERATURE_DEVICE_TYPES` (the 6 oneof types
   above) and `SIREN_TEMP_REFRESH_INTERVAL = 900`.

5. **`sensor.py`** — **no change**. It already creates a temperature sensor for
   any device with `"temperature" in statuses`; the merge makes the sensor
   appear automatically.

## Error handling

- Stream `failure` (`device_not_found` / `bad_request`) → debug log, skip device.
  Some hub-attached sirens on third-party backends may not be addressable (cf.
  the #206 Yale locks); the refresh must degrade gracefully, never raise.
- Bounded per-device `timeout` so a hung device cannot stall the timer callback.
- The timer callback runs independently of `_async_update_data`, so it never
  affects the poll's `CancelledError` handling (#148).

## Testing (TDD)

- `parse_hub_device_temperature`: real `HubDevice` proto per siren type →
  correct value; type without `device_temperature` / unknown oneof → `None`.
- `get_hub_device_temperature`: mocked stub yielding a snapshot → value;
  yielding `failure` → `None`.
- `_async_refresh_siren_temperatures`: merges temperature into the matching
  device's `statuses` and notifies listeners; skips devices that already have
  `temperature` (and then doesn't notify); one device error doesn't abort the
  others. `_schedule_siren_temperature_refresh` registers the timer + initial
  kick and is idempotent; `async_shutdown` cancels the timer; the poll
  (`_async_update_data`) does **not** touch siren temperatures.

## Out of scope

- Buzzer / sounding binary_sensor (needs an FCM alarm-event capture).
- `is_extreme` flag (could later become a sensor attribute / problem binary_sensor).
- Extending the rich `StreamHubDevice` model to non-siren device types.
