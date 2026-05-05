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
- ~~Tilt + Steam binary sensors~~ (v1.2.4) — `tilt` on DoorProtect Plus family (accelerometer), `steam` on FireProtect 2 smoke-chamber variants (steam-vs-smoke discriminator)

---

## Priority 1 — High impact, moderate effort

### 1.1 Lock Platform (`lock.py`)
**Why:** Users with LockBridge (Yale smart lock) expect a lock entity.

**Implementation:**
1. Create `lock.py` with `AjaxLock` entity
2. Parse `smart_lock` status from `LightDeviceStatus` (field 66)
3. Commands via `SwitchSmartLockService` gRPC (proto exists: `switch_smart_lock/`)
4. States: locked, unlocked, locking, unlocking, jammed

**Effort:** Medium (3-4 hours). Need to compile switch_smart_lock protos.

### 1.2 Valve Platform (`valve.py`)
**Why:** WaterStop devices should be controlled as native HA valves, not switches.

**Implementation:**
1. Create `valve.py` with `AjaxWaterStopValve` entity
2. Parse `water_stop_valve_stuck` status
3. Commands need investigation — may be via device command service

**Effort:** Medium (2-3 hours).

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
