# Improvement Plan — Aegis for Ajax

Prioritized list of remaining improvements based on HA platinum integration patterns and real-world testing.

## Completed

- ~~Event Platform~~ (v0.5.0 + v0.9.0) — 16 event types with enriched device source info
- ~~Force Arm Services~~ (v0.5.0) — `aegis_ajax.force_arm`, `aegis_ajax.force_arm_night`
- ~~Logbook Integration~~ (v0.5.0) — human-readable event descriptions with icons
- ~~icons.json~~ (v0.5.0) — MDI icons for all entity types
- ~~Hub Network Sensors~~ (v0.8.0) — ethernet, Wi-Fi, GSM, power via HTS protocol
- ~~2FA TOTP~~ (v0.8.4) — LoginByTotpService
- ~~Event enrichment~~ (v0.9.0) — device_name, device_id, device_type, room_name
- ~~Wi-Fi sensors~~ (v0.10.0) — SSID, signal level, connected status
- ~~Automation blueprints~~ (v1.0.0) — 8 blueprints (security events, intrusion, tamper, remind arm, battery, connectivity, door-while-armed, TTS)
- ~~Rebrand~~ (v1.0.0) — Aegis for Ajax identity
- ~~Binary sensors~~ (partial) — glass_break, vibration, external_contact
- ~~Per-group `alarm_control_panel` for Group/Zone Mode~~ (v1.2.4) — one panel per Ajax security group + whole-house panel for night mode (#84, #86)
- ~~Reauth flow~~ (v1.2.4) — `ConfigEntryAuthFailed` + `async_step_reauth` so HA shows the Reconfigure banner instead of failing silently (#90)
- ~~HA Repairs~~ (v1.2.4) — `hub_offline_24h`, `hts_chronic_failure`, `fcm_credentials_invalid` (with guided fix flow), `grpcio_version_mismatch` Repair cards (#89)
- ~~System Health card~~ (v1.2.4) — gRPC reachability, HTS/FCM ratios, pushes received, last push / last poll ages under Settings → System (#91)
- ~~DHCP discovery~~ (v1.2.4) — Ajax hubs on the LAN appear as Discovered cards via OUI `9C:75:6E` (#92)
- ~~Tilt + Steam binary sensors~~ (v1.2.4) — `tilt` on DoorProtect Plus family (accelerometer), `steam` on FireProtect 2 smoke-chamber variants (steam-vs-smoke discriminator) (#101)
- ~~Lock platform~~ (v1.2.4) — `lock.py` with `AjaxLock` for `smart_lock` / `smart_lock_yale` device types. State (locked / unlocked / unlatched) parsed from the `smart_lock` LockStatus oneof; `lock` / `unlock` / HA's `lock.open` (= unlatch) wired to `SwitchSmartLockService` (#102)
- ~~Device commands wired up~~ (v1.2.4) — `DevicesApi.send_command` no longer raises `NotImplementedError`; relays / sockets / wall switches act on the hub via `DeviceCommandDeviceOn` / `DeviceCommandDeviceOff`, dimmer brightness via `DeviceCommandBrightness`. Was a placeholder since v1.0. (#105)
- ~~Switch state read-back~~ (v1.2.4) — `parse_device` now walks `LightHubDevice.spread_properties` for `RelayChannel` / `LightSwitchChannel` / `SocketBaseChannel` so `AjaxSwitch.is_on` reflects the actual hub state. Fixes the bistable Relay Jeweller symptom where the entity always read `False`. (#109)
- ~~System Health diagnostics~~ (v1.2.4) — `last_update_success_time` exposed on the coordinator so the card stops rendering as `error: unknown` (#106); `Reach Ajax cloud (gRPC host)` derived from poll freshness instead of an HTTPS HEAD probe that always returned `unreachable` (#110)
- ~~Non-blocking startup~~ (v1.2.4) — HTS connect-then-listen and FCM startup move to background tasks; first refresh no longer awaits multi-second listener startups so the integration drops out of HA's *"integration taking too long"* warning much sooner (#113, closes #112)
- ~~Cached-snapshot warm start~~ (v1.2.4) — first `_async_update_data` warm-starts `coordinator.devices` from a per-entry `Store`-backed cache and skips the synchronous `get_devices_snapshot` loop on subsequent boots; persistent streams deliver fresh data within seconds. Cache writes from the stream callback go through `Store.async_delay_save` (30 s window) to coalesce bursts. Real-HA measurement: ~10 s shaved off HA total boot, ~9 s off aegis_ajax setup-to-platforms-online (#116, closes #114)
- ~~Valve platform (read-only)~~ (v1.3.0) — `WaterStopChannel.state` / `is_transitioning` / `MALFUNCTION_IS_STUCK` surfaced via the existing `spread_properties` walker as `valve_chN` / `_transitioning` / `_stuck` keys; new `valve.py` exposes them as native HA `valve.*` entities for `water_stop` and `water_stop_base` device types. Bidirectional control still waits on capturing the official app's command-side calls (no `SwitchWaterStopService` in v3 protos) (#117)
- ~~WallSwitch / Socket electrical readings~~ (v1.4.0) — `sensor.<name>_current` (A), `sensor.<name>_voltage` (V, beta.3+), `sensor.<name>_energy_consumed` (kWh, ties into HA Energy dashboard with `total_increasing`), and `sensor.<name>_power_derived` (W, disabled by default, uses the device-reported voltage when present and falls back to `NOMINAL_GRID_VOLTAGE_V` otherwise). Per-device delta pushes from the hub are consumed in place, with merge semantics so partial updates don't blank the cached readings on every relay toggle (#123, #137, #140, beta.3 partial-update fix in #140)
- ~~"Delete FCM credentials" toggle in options flow~~ (v1.4.0) — explicit boolean that drops the four FCM keys from `entry.data` unconditionally, plus a switch from `default=` to `description={"suggested_value": ...}` so the form fields actually round-trip an empty submission instead of restoring the prior value. Two iterations: #139 fixed the persistence handler, #141 fixed the form schema after Hansontech190 reported the password field couldn't be cleared through the UI (#138, #139, #141)
- ~~Parser hardening~~ (v1.3.0-beta.7) — `_parse_statuses` sub-message branches now build inputs with real `LightDeviceStatus(...)` instances instead of `MagicMock` (sweep across signal_strength, gsm_status, sim_status, monitoring, life_quality, temperature, wire_input_status, transmitter_status, smart_lock, nfc, motion_detected); per-device / per-update `try/except` in `_run_stream` so one bad device or update no longer kills the stream; `TestSnapshotReplay` deserialises a real `StreamLightDevicesResponse` and replays it end-to-end, with auto-replay over every `tests/fixtures/*.bin` (#126, #127, follow-up to #119)
- ~~Read-only `update.<hub>_firmware` entity~~ (v1.4.0) — surfaces the pending hub firmware update from `streamHubObject` (field 201 `system_firmware_update`). No `INSTALL` feature declared, `async_install` not implemented even though `Start*FirmwareUpdate` RPCs exist in the protos — firmware updates remain Ajax-scheduled and the integration is purely informational. `installed_version` returns a constant placeholder ("current") because Ajax doesn't expose the installed version to clients; `latest_version` mirrors it when no update is queued and reflects the target version when one is. `release_summary` clarifies the semantic gap in the entity detail panel ("Up-to-date" means "no update queued", not "running latest"). **11th HA platform.** Three iterations: beta.5 shipped, beta.6 fixed the `unknown` rendering, beta.7 added the release_summary (#142, #143, #144)
- ~~`RestoreSensor` on electrical readings~~ (v1.4.0) — the four sensor classes (`current`, `voltage`, `energy_consumed`, `power_derived`) now extend `RestoreSensor` so they survive HA restarts. Bruno's hub doesn't include readings in the boot snapshot — only in change-deltas — so for constant loads the sensors went `unknown` for hours after every restart. With this in place, they fall back to the last persisted value until a fresh delta arrives. Caveat: only restores numeric values; if the previous shutdown had the sensor in `unknown`, there's nothing to restore until a delta fires (#144, #123)

---

## Priority 1 — High impact, moderate effort

### 1.0 Don't clear `device_readings` on HTS disconnect (`1.4.1` patch — #146)
**Why:** During a transient HTS reconnect cycle (5-min outage on bvis-home is canonical), the four electrical-reading sensors render `unavailable` because `coordinator._handle_hts_disconnect()` calls `self.device_readings.clear()`. Hub remembers device state across our socket outages, so the cached value remains the truth until proven otherwise by a fresh delta.

**Fix:** one-line change — drop `device_readings.clear()` from the disconnect handler, keep `hub_network.clear()` (hub-network entities accurately reflect "we don't know" during an outage). Plus rename the regression test and add a new one asserting the entity stays available with its cached value. See [[feedback_restore_sensor_for_change_only_streams]] for the broader split-on-disconnect pattern.

**Effort:** 1-2 h including tests. Pure bug fix, no new functionality — SemVer PATCH (`1.4.1`).

**Per-device firmware** (was P2.1): Hub-level firmware update entity shipped in `1.4.0-beta.5`. The same `streamHubObject` snapshot also exposes per-device firmware updates (field 200 `DeviceFirmwareUpdates`). Promoted to P2 below.

---

### 1.1 Valve Platform (`valve.py`) — bidirectional control
**Why:** Read-only valve entity shipped in `1.3.0` (#117). The remaining gap is **opening / closing the valve from HA** — automations can react to the valve being closed by a leak, but can't trigger the shut-off themselves nor reopen after a false-positive.

**Status:** Blocked on protocol capture. There is no `SwitchWaterStopService` in the v3 protos we have. Need someone with a WaterStop to run the rig (Frida + mitmproxy on the official mobile app) and capture the gRPC call the app makes when toggling the valve from its UI.

**Effort:** Unknown — likely 2-3 h once the wire shape is in hand.

---

## Priority 2 — Medium impact, moderate effort

### 2.1 Per-device firmware update entities
**Why:** Hub-level firmware update entity shipped in `1.4.0-beta.5`. The same `streamHubObject` snapshot also exposes per-device firmware updates (field 200 `DeviceFirmwareUpdates`), which would surface as `update.<device>_firmware` entities — useful for installs with many devices on different firmware versions.

**Effort:** Low-medium (2-3 h). Same read-only-by-design pattern as the hub entity. Per-device entities should be disabled-by-default (typical install has 10-30 devices and most users won't care which sensor is on which firmware unless one is failing).

**Data source:** `HubObject.device_firmware_updates.device_firmware_update[]`, each entry carries `device_id`, `is_critical` (`BoolValue`), and a `Status` oneof with the full `not_started` / `downloading[%]` / `downloaded` / `installing` / `completed` / `failed` cycle.

### 2.2 Persistent Notification Service
**Why:** Show alarm events as HA persistent notifications with configurable filters.

**Effort:** Medium (2 hours).

---

## Priority 3 — Nice to have, higher effort

### 3.1 Number/Select Platforms
**Why:** Expose configurable device settings (shock sensitivity, LED brightness, etc.)

**Data source:** Requires `UpdateHubDeviceService` gRPC.

**Effort:** High (4-5 hours each).

### 3.2 Device Tracker (`device_tracker.py`)
**Why:** Show hub location on HA map from geoFence coordinates.

**Effort:** Low (1-2 hours) if data is available.

### 3.3 Device Handler Architecture Refactor
**Why:** Per-device-type handler pattern instead of monolithic `_DEVICE_TYPE_SENSORS` dict.

**Effort:** High (6-8 hours). Should be done when adding new entity types.

---

## Known Limitations

These are protocol-level limitations that cannot be resolved:

- **Hub tamper (lid) real state** — status exists in proto but server doesn't send it in `StreamLightDevices`
- **Photo on-demand URL retrieval** — v2 capture works but photo URL via v3 detection area stream returns `permission_denied`
- **SpaceControl keyfob listing** — keyfobs don't appear in `StreamLightDevices`
- **Motion detection when disarmed** — Ajax firmware disables motion reporting when disarmed (battery conservation)
- **Shock/vibration as persistent sensor** — these are alarm events, not persistent statuses
