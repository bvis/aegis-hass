# Siren internal temperature sensor (#220)

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

## Approach: throttled one-shot snapshot (not a persistent stream)

`StreamHubDeviceRequest` takes `hub_id` + `hub_device_id` — it is a **per-device**
stream, and `Success` only has a `snapshot` case (no deltas). A persistent
stream would mean one held-open connection *per siren* for a value that barely
changes, contradicting how the app uses the endpoint (transient, on device-detail
open). A throttled one-shot (open → read first snapshot → close) mirrors the
app's usage and minimises Ajax backend load. It also avoids a second persistent
asyncio task / reconnect loop in HA, hanging off the existing poll cycle instead.

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

3. **`coordinator._maybe_refresh_device_temperatures(now)`** — throttled sub-step
   in `_async_update_data`, same shape as `_maybe_refresh_rooms`.
   - Interval `SIREN_TEMP_REFRESH_INTERVAL = 900` (15 min); guarded by a
     `_siren_temp_last_fetch` timestamp.
   - For each device whose `device_type` ∈ `SIREN_TEMPERATURE_DEVICE_TYPES`
     **and** that does not already have `"temperature"` in `statuses`:
     call `get_hub_device_temperature`; on a non-None result, merge into
     `device.statuses["temperature"]` via `dataclasses.replace`.
   - Per-device try/except: one failure must not break the refresh.

4. **`const.py`** — add `SIREN_TEMPERATURE_DEVICE_TYPES` (the 6 oneof types
   above) and `SIREN_TEMP_REFRESH_INTERVAL = 900`.

5. **`sensor.py`** — **no change**. It already creates a temperature sensor for
   any device with `"temperature" in statuses`; the merge makes the sensor
   appear automatically.

## Error handling

- Stream `failure` (`device_not_found` / `bad_request`) → debug log, skip device.
  Some hub-attached sirens on third-party backends may not be addressable (cf.
  the #206 Yale locks); the refresh must degrade gracefully, never raise.
- Bounded per-device `timeout` so a hung device cannot stall `_async_update_data`.
- `CancelledError` discrimination already handled at the `_async_update_data`
  level (#148); we do not add new swallowing there.

## Testing (TDD)

- `parse_hub_device_temperature`: real `HubDevice` proto per siren type →
  correct value; type without `device_temperature` / unknown oneof → `None`.
- `get_hub_device_temperature`: mocked stub yielding a snapshot → value;
  yielding `failure` → `None`.
- `_maybe_refresh_device_temperatures`: merges temperature into the matching
  device's `statuses`; respects the throttle; skips devices that already have
  `temperature`; one device error doesn't abort the others.

## Out of scope

- Buzzer / sounding binary_sensor (needs an FCM alarm-event capture).
- `is_extreme` flag (could later become a sensor attribute / problem binary_sensor).
- Extending the rich `StreamHubDevice` model to non-siren device types.
