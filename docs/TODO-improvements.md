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
- ~~Valve platform (read-only)~~ (v1.2.5) — `WaterStopChannel.state` / `is_transitioning` / `MALFUNCTION_IS_STUCK` surfaced via the existing `spread_properties` walker as `valve_chN` / `_transitioning` / `_stuck` keys; new `valve.py` exposes them as native HA `valve.*` entities for `water_stop` and `water_stop_base` device types. Bidirectional control still waits on capturing the official app's command-side calls (no `SwitchWaterStopService` in v3 protos) (#117)

---

## Priority 1 — High impact, moderate effort

### 1.1 Valve Platform (`valve.py`) — bidirectional control
**Why:** Read-only valve entity shipped in `1.2.5` (#117). The remaining gap is **opening / closing the valve from HA** — automations can react to the valve being closed by a leak, but can't trigger the shut-off themselves nor reopen after a false-positive.

**Status:** Blocked on protocol capture. There is no `SwitchWaterStopService` in the v3 protos we have. Need someone with a WaterStop to run the rig (Frida + mitmproxy on the official mobile app) and capture the gRPC call the app makes when toggling the valve from its UI.

**Effort:** Unknown — likely 2-3 h once the wire shape is in hand.

---

## Priority 2 — Medium impact, moderate effort

### 2.1 Update Platform (`update.py`)
**Why:** Users want to see firmware status and update availability.

**Data source:** `streamHubObject` v2 field 200 (`DeviceFirmwareUpdates`) and field 201 (`SystemFirmwareUpdate`).

**Effort:** Medium (3 hours). Need to parse firmware proto fields.

### 2.2 Persistent Notification Service
**Why:** Show alarm events as HA persistent notifications with configurable filters.

**Effort:** Medium (2 hours).

### 2.3 Unknown App Label Repair (#99)
**Why:** When the user configures a label the Ajax backend rejects, the integration today surfaces UNAUTHENTICATED / PERMISSION_DENIED gRPC errors with no clear remediation. A Repair card with a fix flow (the same `KNOWN_APP_LABELS` dropdown the config flow uses) would replace stack traces with a one-click fix.

**Implementation:** Capture the gRPC error shape backend returns for unknown labels (vs. wrong credentials), introduce `UnknownAppLabelError(AuthenticationError)` subclass, branch in coordinator's auth-failure path, add `UnknownAppLabelRepairFlow`. Same pattern as the FCM fix flow shipped in #96.

**Effort:** Medium (3-4 hours, gated on capturing the discriminating error shape).

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
