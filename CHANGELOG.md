# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [1.8.1] - unreleased

### Added
- **Outdoor curtain PIR internal temperature sensor (#229).** MotionProtect Curtain Outdoor detectors report their internal temperature only in the rich per-device snapshot (same as sirens, #220), so no sensor appeared. The per-device temperature refresh is now device-agnostic and covers them.
- **`aegis_ajax.disarm_night_mode` service (#233).** Stands down only the night-mode groups via Ajax's native `disarmFromNightMode`, leaving independently away-armed groups armed — previously, exiting night mode required a full disarm that also stood down those groups. Optional alarm-panel target; applies to all panels when omitted.

### Fixed
- **SmartLock / Yale lock now locks and unlocks from Home Assistant (#219).** Hub-attached Jeweller locks (including installer-added Yale modules on a third-party monitoring backend) aren't in the SmartLock cloud registry, so the command is issued through the generic device on/off path: lock = On, unlock = Off, sent with the generic `smart_lock` ObjectType on channel 1. (Lock *state* was already restored in 1.7.0.)
- **Device stream resumes after a peer reset instead of going silent for hours (#236).** The reconnect loop reused a possibly half-open cached gRPC channel; the retried stream neither errored nor delivered until a full restart. The channel is now recreated on reconnect.
- **gRPC keepalive detects a silently-dropped connection (#236).** The long-lived device stream now sends periodic HTTP/2 keepalive pings, so a half-open link (e.g. a router silently dropping an idle connection, leaving the stream blocked with no error and no updates) surfaces as a normal error the reconnect path recovers from, instead of staying silent until a restart. The ping interval **self-tunes**: it starts high (gentle on the server, below a typical router idle timeout) and halves toward a 60s floor whenever the stream keeps dying after an idle stretch, converging under whatever idle timeout the network path enforces — while refusing to shorten on a `too_many_pings` rejection (the opposite problem).
- **System Information card no longer flips to "unreachable" on a healthy install (#236).** Reachability was derived only from the polled-refresh timestamp, which HTS/FCM updates starve. It now treats the integration as reachable when the poll is fresh or the HTS stream is live — the paths that carry live sensor/device state.
- **System Information distinguishes a "push only" state (#236).** FCM push carries only security events, not live sensor state, so an install where push is alive but the poll and HTS stream are both down now reads as "push only — sensor data may be stale" instead of plain "reachable", keeping a degraded data path visible rather than hiding it behind a healthy-looking indicator.
- **Reconfiguring to a different Ajax account updates the entry title and unique_id on the first try (#241).** Previously the integration's front page kept the old email until a second reconfigure.
- **No phantom Carbon-monoxide sensor on Heat/Smoke FireProtect 2 units (#231).** The generic `fire_protect_two` mapping blanket-attached a CO sensor stuck at "Clear"; CO is dropped from the generic mapping (only CO-encoded SKUs keep it). Smoke + heat are unchanged, and a real CO alarm on a CO-equipped unit still arrives via push.

### Documentation
- **Recommend a separate Ajax account with notification access for reliable push (#234).** A limited User-role account registers for FCM but receives no events; the Home Assistant account needs its own login and notification access.

## [1.8.0] - 2026-06-01

Alexa voice control, siren temperature, and an FCM-registration hardening. Consolidates the 1.8.0-beta series.

### Added
- **Alexa / Home Assistant Cloud support for the alarm panel (#221).** The alarm panel now advertises `ARM_HOME` and reports `code_format: number` when a PIN is configured, so the Nabu Casa / Alexa skill discovers it (it won't discover a panel exposing Night without Home) and the Lovelace alarm card renders a numeric keypad. Ajax has a single partial-arm mode ("Night mode"), so **Arm Home** maps to it just like **Arm Night** (both settle to `armed_night`), kept under both names. A bare Alexa "arm" defaults to Armed Home (Alexa's own behaviour); see the README for the away-mode / Routine workarounds and the discovery caveats (no code required to arm; 4-digit voice PIN only).
- **HomeSiren / StreetSiren internal temperature sensor (#220).** Sirens report their internal (board) temperature, which isn't carried in the device stream the integration runs continuously, so no sensor appeared. The value is now pulled from the per-device snapshot on a dedicated 15-minute timer (with an initial fetch at startup) and surfaced as the standard temperature sensor. On indoor HomeSirens this tracks the Ajax app; on outdoor StreetSirens the board runs warmer than shade-ambient (documented). Covers the HomeSiren / StreetSiren family.

### Changed
- **Don't re-attempt FCM registration for credentials Google already rejected (#227).** A well-formed-but-wrong FCM api-key was re-tried against the cobranded Firebase project on every Home Assistant restart, because rejected credentials are never persisted. The integration now remembers a terminally-rejected credential set by a one-way hash (the secret is never stored) and skips the network attempt until the values change, keeping the Repair card raised; transient / host-unreachable failures stay retryable.

## [1.7.0] - 2026-05-30

Doorbell, lock and bypass improvements, plus thread-safety and reliability fixes. Consolidates the 1.6.2-beta and 1.7.0-beta series.

### Added
- **Per-device doorbell `event` entity.** Video-edge doorbells get their own `event` entity (`device_class: doorbell`) on the doorbell device card, advertising and emitting Home Assistant's canonical `ring` event. (#173)
- **Doorbell motion turns the doorbell's motion sensor on.** Video-edge doorbells report motion only over FCM push; a motion push now flips the doorbell's `motion_detected` on with a 30-second auto-off, attributed to the doorbell via the push device id (a twin→sibling alias keeps attribution correct on multi-doorbell installs). (#173)
- **Device automation triggers.** Every Ajax security event (alarm, arm, disarm, night mode, motion, door open, doorbell, fire, flood, CO, glass break, tamper, panic, battery low, connection lost, malfunction) is now a named device trigger on the hub device, selectable in the automation editor. Translated across all 14 locales.
- **Per-device bypass switch + `bypass_switches` option (auto / always / never).** Each non-hub device gets a `bypass` switch to deactivate/reactivate it. `auto` (default) only creates them when the account holds the `DEVICE_EDIT` permission; `always` keeps the previous behaviour; `never` disables them. Orphaned bypass switches are evicted automatically when the option changes. Translated across all 14 locales.

### Fixed
- **SmartLock / Yale LockBridge state is read again (#206).** Current firmware moved the lock state to a sub-message on field 99 of `LightDeviceStatus`, which was dropped as unsupported; it is now defined and parsed in the snapshot, the live stream and the coordinator, mapped empirically (`1=locked`, `2=open`). State is push-on-change, so it reads `unknown` until the first lock/unlock after a restart.
- **LeakProtect units now get their leak (moisture) binary_sensor (#211).** The sensor was keyed on `leaks_protect` but the device type is `leak_protect`, so it never appeared — only the generic tamper/temperature/battery entities did.
- **FCM push events are dispatched on the event loop (thread-safety).** Hub-level and per-device (doorbell) event entities were updated directly from the FCM worker thread, and the motion auto-off ran in an executor thread via a non-`@callback` timer — both wrote entity state off-loop (a storm of `async_write_ha_state from a thread other than the event loop` errors). All push and timer paths now run on the loop. (#173)
- **HTS connection survives a malformed frame** instead of tearing the whole connection down and blanking hub-network sensors until the next poll.
- **Doorbell ring no longer double-fires** — the per-device doorbell entity updates its own state only.
- **Clear, translated errors when the hub rejects a device command** (no permission, hub offline, wrong state) instead of a generic failure. Applies to switch on/off, bypass and brightness.
- **Duplicate video-doorbell card no longer survives restarts (#173).** The dedup re-runs across the full merged device set and evicts the ghost from the device registry.
- **FCM 403 warning names `API_KEY_SERVICE_BLOCKED` alongside `API_KEY_ANDROID_APP_BLOCKED`** (#194) — both 403 sub-codes share the same wrong-key cause.
- **gRPC channels no longer leak on failed setup or failed config-flow login**, `set_photo_on_demand_mode` is removed on unload, and `CancelledError` during login is re-raised for clean shutdown.

### Known limitation
- **Locking/unlocking a Yale lock from Home Assistant is not yet supported (#206).** Lock *state* is shown correctly, but these hub-attached Yale (Assa Abloy) locks aren't listed in the Ajax SmartLock service, so the command path can't address them yet. Tracked as a follow-up.

### Internal
- **Security/performance audit remediation (#208):** FCM worker-thread state-write fix, photo-URL log redaction, S3 SSRF anchor, HTS malformed-frame containment, read-buffer cap.
- **Parser extractions:** proto-to-`Device` logic into `api/devices_parser.py` and FCM event parsers into `notification_event_parser.py`; `manifest.json` is now the single source of truth for the version. No behaviour change.

## [1.6.1] - 2026-05-26

### Changed
- **The "Capture photo" button now reports failures in the UI instead of doing nothing** (#193, reported by @runnermhr). A photo capture runs through several asynchronous steps (request accepted by the hub → wait for the FCM photo notification → fetch the image URL → download → save), and any of them can fail on some camera firmwares or when FCM isn't configured. Previously every failure path logged at DEBUG and returned silently, so a user who pressed the button just found an empty media folder with nothing in the default-level log to explain why. Each step now raises a translated `HomeAssistantError` — surfaced as a UI notification — and logs at WARNING: a rejected capture or missing image URL ("the photo capture did not complete"), no FCM configured ("photo on demand requires FCM push notifications"), or a timeout waiting for the camera ("timed out waiting for the camera to deliver the captured photo"). Translations land in all 14 locales. Note: captured photos are saved under a per-device subfolder, `media/ajax_photos/<device name>/`, not directly in `media/ajax_photos/`.

## [1.6.0] - 2026-05-26

MINOR release. New live-reading surface for the Outlet Type E / F socket family (power, voltage, current, energy), a one-shot manual hub refresh button usable from both the UI and automations, and a periodic STATUS_BODY refresh loop so live device readings stay current without waiting for the hub's own sparse delta pushes. Plus a chain of FCM-registration fixes for co-branded users (Ajax's co-brand Firebase api-key has Google's Android-app package restriction enabled; the upstream Python library wasn't sending `X-Android-Package`), an Options-form bug that wiped users' saved FCM API key on a benign re-submit, the doorbell-duplicate fix from the parked `1.5.2-beta.1`, and a sizeable internal refactor pass collapsing several hand-rolled duplicated classes.

### Added
- **Outlet Type E / Type F live electrical sensors** (#179, calibrated against @SaetanSaDiablo's load-calibrated reboot capture). Direct power (W), measured voltage (V — no nominal fallback needed for this family because the firmware reports it), current (A), and cumulative electric energy consumed (kWh, wired into HA's Energy dashboard via `state_class=total_increasing`). `parse_device_readings` now dispatches on device_type through a per-family sub-key table (`_WALLSWITCH_KEY_MAP` / `_OUTLET_KEY_MAP`) so each family stays isolated and adding a third family is a one-entry change. WallSwitch behaviour is unchanged. The Outlet's new direct-reading `power` entity is enabled by default; the WallSwitch family's existing `_power_derived` (current × voltage) stays as-is.
- **Periodic STATUS_BODY refresh per hub** (#179). The Outlet firmware emits per-device STATUS_UPDATE deltas extremely sparsely — empirically about one push every several hours regardless of load activity, confirmed in a 6-hour user capture under varying load on 5 outlets. Without an explicit refresh, the integration's live readings stayed frozen at whatever the boot snapshot delivered. A new HTS `_status_refresh_loop` issues `REQUEST_FULL_STATUS` to each hub every 60 s; bandwidth cost is ~2.7 KB per hub per cycle. WallSwitch family pushes deltas reliably so it's unaffected by the asymmetry, but the periodic re-sync also catches dropped deltas as a side benefit.
- **Manual hub refresh button** (#179 follow-up). One `button.<hub>_refresh_hub` entity per configured hub (diagnostic category). Pressing it (or calling `button.press` from an automation) dispatches the same `REQUEST_FULL_STATUS` the periodic loop sends, so a reading the user wants *right now* arrives in 1–2 seconds instead of waiting up to a minute for the next periodic tick. The button is rate-limited to one press per 60 s per hub — below the periodic cadence a manual refresh wouldn't surface fresher data anyway, and the cap stops a stuck automation from generating unusual traffic against Ajax's servers. The button goes `unavailable` while HTS is disconnected, consistent with `mains_power` and other HTS-gated entities.
- **FCM credentials pre-flight shape validator + `fcm_credentials_malformed` Repair card** (#182). The four FCM values get a structural check before the integration contacts Firebase — `fcm_app_id` parses as `1:<digits>:android:<hex>`, `fcm_api_key` matches Google's `AIza` + 35-char format, `fcm_sender_id` is digits and matches the digit chunk in `fcm_app_id`, `fcm_project_id` is non-empty. When something's off, the Repair names the specific failing field instead of leaving the user to read Google's opaque 403 in the logs. Same fix flow as the existing runtime-rejection Repair (re-enter the four values, integration reloads, check re-runs). Mutually exclusive with `fcm_credentials_invalid` — shapes-bad OR Firebase-rejected, never both. Translations in all 14 locales.

### Changed
- **README's *Where the values live* section expanded for FCM credential extraction.** Names `apktool` as the standard tool because Android compiles `strings.xml` to binary AXML and a naïve unzip won't give readable values. Adds a `strings | grep -oE 'AIza[…]+'` recipe with an explicit "try each candidate through the Repair flow" note because `libnative-lib.so` ships two `AIza…` strings — one is FCM-scoped and accepted by Firebase Installations, the other is for a different Google service and gets refused with `API_KEY_ANDROID_APP_BLOCKED`. Adds a note that XAPK installers don't unpack the native library into the base APK — `libnative-lib.so` ships in the per-architecture config split (`config.armeabi_v7a.apk` / `config.arm64_v8a.apk`). Corrects the previously-stated `google_app_id` length description (variable per Firebase project, not a fixed ~40-char hash tail).
- **`_classify_fcm_failure` log warning now names the wrong-AIza-string failure mode explicitly** so users who hit `API_KEY_ANDROID_APP_BLOCKED` have a clear next step (try the other `AIza…` candidate from `libnative-lib.so`) instead of an opaque "credentials rejected" message.

### Fixed
- **MotionCam Video Doorbell no longer appears twice in the device list** (#173, reported by @brunovdw68; previously parked as `1.5.2-beta.1`). On some Ajax cloud builds (`ajax_pro` PRO 2.47 confirmed) the same physical doorbell ships in the `StreamLightDevices` snapshot under two `LightDevice` oneofs at once — a `hub_device` Jeweller-side ghost (`object_type=motion_cam_video_doorbell`, single status, `malfunctions=1`) and the canonical `video_edge_channel` (`video_edge_type=DOORBELL`, full sensor set). The ghost's spurious `malfunctions=1` bubbled up to the space-level counter and surfaced a duplicate device card with a warning indicator. Snapshot consolidation now drops any `motion_cam_video_*` hub_device whose name matches a `video_edge_*` sibling in the same snapshot. The unbalanced case from #119 (only the hub_device branch present) is unchanged so that setup keeps its doorbell. Existing HA device cards for the ghost will be orphaned after upgrade and can be removed via the Devices UI.
- **FCM registration now sends the `X-Android-Package` header on Firebase Installations calls** (#155, #182, reported by @aitrus22, @alt-BadBatch, @zwagerzaken). The Ajax co-branded api-key on the Firebase project has Google's Android-app package restriction enabled: requests without an `X-Android-Package` header come through as `androidPackage: <empty>` and get refused with `API_KEY_ANDROID_APP_BLOCKED`. The upstream `firebase_messaging` Python library doesn't send the header at all, so co-brand users were stuck behind a 403 they couldn't fix from their side. The integration now maps `app_label` to the matching Android package id via a new `APP_LABEL_TO_ANDROID_PACKAGE` constant in `const.py` and threads it through to the notification listener; when the app_label has a known mapping, the listener constructs an `aiohttp.ClientSession` with `X-Android-Package` as a default header and passes it to `FcmRegister`. aiohttp merges per-request headers on top so the library's own `x-firebase-client` / `x-goog-api-key` stay untouched. Co-brands without a mapping fall back to no-header (pre-`1.6.0` behaviour), so the change is no-op for any user who was already working. Verified mappings ship for `Ajax → com.ajaxsystems`, `ajax_pro → com.ajaxsystems.pro`, `AIKO → com.ajaxsystems.aiko`, `Protegim_alarma → com.ajaxsystems.protegim`; more will be added as users confirm.
- **Saved FCM API Key no longer wiped by a benign re-submit of the Options form** (#183, reported by @raven2k24). HA's password TextSelector never displays a saved secret, so re-opening **Configure → Options** left the `FCM API Key` field blank regardless of what was stored. Clicking Submit then sent an empty string, which the handler interpreted as "clear this field" and popped the saved key out of `entry.data` — leaving three of four FCM values and an `FCM credentials not configured` warning on next reload. An empty submission on `fcm_api_key` is now treated as "leave alone"; only the explicit `Delete FCM credentials` toggle wipes the key. The other three FCM fields keep their existing clear-via-empty behaviour because they DO round-trip their values via `suggested_value`. Symmetric companion to the no-can-delete fix in `1.4.0`: now you can keep what's there AND still delete via the toggle, without the password-selector blind spot biting either way.
- **Per-device HTS deltas (current / power / energy) now reach the per-device handler instead of being silently mis-routed to the hub-network-state parser** (#179). The hub-network-state delta heuristic was over-matching: `_extract_direct_kv` ran against every non-body `UPDATES` message and the operational state byte `0x03` is so common as a value across Ajax devices that it fired on essentially every per-device delta. `device_readings` then never received the live update, electrical sensors stayed at whatever the initial STATUS_BODY snapshot set, and `RestoreSensor` made the stale values look "live" after a restart — masking the bug entirely. The router now classifies by payload shape (a 4-byte `params[1]` means per-device) before running the network-state heuristic. Affected every electrical-measuring device, not just the Outlet that exposed it.
- **Orphan `_power_derived` sensors for Outlet Type E / F devices removed automatically on setup** (#179 follow-up). Between `1.4.0` (when the WallSwitch family's derived-power entity first shipped, and Outlet got registered with the same shape) and this release (when Outlet got a proper direct `_power` entity), Outlet devices had a stale `_power_derived` entry in the registry that rendered as `unavailable`. A setup-time sweep removes the orphan for `DIRECT_POWER_DEVICE_TYPES`; WallSwitch family's own `_power_derived` is untouched.

### Internal
- **HTS DEBUG probe upgraded to log raw hex sub-key values with PII redaction.** The previous `0x37(4b)` format made mapping an unfamiliar device family a guessing game; the new `0x37=00112233` format lets a single capture under known load pin every reading to its sub-key. ASCII-text values ≥ 3 chars (device names, emails, phones) render as `<text:Nb>` so a DEBUG capture can be pasted into a public issue without leaking the user's data; numeric readings keep their full hex because they always contain at least one non-printable byte. Tiny protos (≤ 16 bytes total) bypass the redaction since there's no room for user-set text fields at that size. Default-level installs pay nothing — both lines are gated on DEBUG.
- **Hub-network sensors, binary sensors, and alarm panels collapsed from per-variant subclass duplication to descriptor-driven base classes.** Nine hand-rolled hub-network sensor classes folded into one `AjaxHubNetworkSensor` + a `_HubNetSpec` tuple; three hub-network binary sensor classes folded into `AjaxHubNetworkBinarySensor`; ~180 lines of duplication between `AjaxAlarmControlPanel` and `AjaxGroupAlarmControlPanel` extracted to a `_AjaxAlarmPanelBase`. Visible side-effect: arm failures on per-group panels now name blocking devices/issues in the error message (previously a flat `str(err)`) and use the translated `invalid_alarm_code` message in the user's HA language, matching the space-panel behaviour. Backwards-compatible aliases keep public class names and entity unique_ids identical, so no entity registry churn for users.
- **`coordinator._async_update_data` (227-line god method) split into six named sub-steps** (`_ensure_authenticated`, `_refresh_spaces`, `_maybe_refresh_sim_and_firmware`, `_maybe_refresh_rooms`, `_first_startup_init`, `_maybe_fallback_device_snapshot`, `_maybe_restart_hts`). The outer method is now a 12-line orchestrator.
- **FCM credentials helpers extracted from `notification.py` to a new `notification_fcm_creds.py` module** (`_validate_fcm_shape`, `_classify_fcm_failure`, the two regex constants). Re-exported from `notification.py` so callers stay unchanged.

Tests: 1384 passing, coverage 87.4%.

## [1.5.1] - 2026-05-22

PATCH release. Phantom security-event phone notifications that fired hours after the original arm/disarm — observed live as a stale "desarmada" landing on the user's phone with the alarm untouched for hours — are now suppressed. Root cause was an FCM-server replay window the dedupe layer wasn't covering. Verified on a live install where the next reconnect dropped 8 buffered pushes (3.96–4.16 h old) cleanly with the new filter.

### Fixed
- **Stale security-event phone push after an FCM reconnect no longer fires** (#174). When the underlying TCP socket against Google's FCM MCS endpoint (`mtalk.google.com:5228`) gets reset — typically piggybacking on the same network blip that resets the gRPC device stream — Google replays any push that Ajax dispatched but never got acked by the previous session, sometimes hours after the original event. The existing `notification_id`-based dedupe is bounded to 5 s (there for Ajax's two-pushes-per-event pattern, #80) so a replay arriving minutes later slipped through and fired the matching `aegis_ajax_event` again, surfacing a phantom `desarmada` on the user's phone. The listener now reads `Notification.server_timestamp` (set by Ajax cloud at dispatch time) on every incoming push and drops anything older than 120 s before touching any side effect, logging the rejection at WARNING with the measured age so the path is visible in HA logs. Fail-open: a payload we can't recover a timestamp from falls through unchanged so a parser miss never silences a real event. The integration resyncs from the next snapshot regardless.

## [1.5.0] - 2026-05-21

MINOR release. Three independent threads of work converge here: a new "what doorbell got rung / who armed which group / what sensor actually tripped" surface for event-driven automations; the long-missing per-group push routing for spaces with Ajax groups (zones), which used to lag up to an hour after arming a single group from the mobile app; and a regression fix on the CRA-company diagnostic sensor that had silently been returning empty since `1.2.3`. Plus quality-of-life cleanups in setup flow, a new Photo on Demand service, and resilience fixes for the Reload flow.

### Added
- **`aegis_ajax.set_photo_on_demand_mode` service** that toggles a hub's Photo on Demand mode for two independent channels — `user` (whether hub users can request photos on demand from the Ajax mobile app) and `scenario` (whether scenarios / automations can trigger captures). Both fields are optional; at least one must be supplied. Underlying gRPC call (`DeviceCommandPhotoOnDemandModeService`) is idempotent, so re-sending the current state succeeds without error. Targets one or more `alarm_control_panel` entities (or every configured space when no target is given). Translations land in all 14 locales.
- **`doorbell_pressed` event for Ajax SmartLock / LockBridge (Yale) variants with integrated ring button** (#158, reported by @Sven2410). Closes the last of the three doorbell SKUs in the Ajax catalog: Wireless DoorBell (hub-level) and MotionCam Video Doorbell already routed in `1.4.5`; SmartLock now joins them via a new `SmartLockEventQualifier` parser pass. The user-facing surface (an `event` entity firing with `event_type: doorbell_pressed` and `raw_tag: doorbell_pressed`) is identical across all three SKUs and the same automation works regardless of which hardware the user owns. SmartLock devices already surface as `lock` entities since `1.2.4`; other SmartLock tags (`locked_by_keypad`, `locked_automatically`, …) intentionally remain unmapped — those transitions already surface via the `lock` entity's state.
- **Per-group `alarm_control_panel` entities now react to FCM arm/disarm pushes** (#148, reported by @ArshSoni). Arming or disarming a single group from the Ajax mobile app flips the matching `alarm_control_panel.<group>` within ~1 second instead of waiting for the next poll (~5 min). The seven `space_group_*` variants of `SpaceEventTag` (armed, armed_with_malfunctions, auto_armed, auto_armed_with_malfunctions, disarmed, auto_disarmed, duress_disarmed) dispatch through a new `apply_push_group_security_state` coordinator helper. Group identifier is resolved from `additional_data.space_display_groups.DisplayGroups.Group` (`group_hex_id`/`group_name`) on the push payload, with sanity checks that the id is hex and ≤ 16 chars so unrelated payload bytes can't accidentally surface as a group id. Group pushes also fire an `aegis_ajax_event` carrying `raw_tag` + `group_id` + `group_name` so automations can target a specific group. Space-level state is intentionally left alone — arming one group doesn't imply the whole space is armed, the hub-level panel still relies on the next poll to resolve that.
- **`MonitoringCompany.hex_id` public field** populated from `company_info.hex_id` on every snapshot company, plus **`SpacesApi.get_monitoring_company(space_id, company_hex_id)`** that wraps `SpaceMonitoringCompanyService.getMonitoringCompany`. `get_space_snapshot` uses the resolver as a best-effort fallback when a snapshot company arrives with empty `name` but populated `hex_id`. Building block for eventually lifting the `CLIENT_VERSION` pin without losing the diagnostic.

### Changed
- **`sensor.<hub>_compania_cra` state shows the actual company names instead of `"multiple"`** when more than one CRA company is approved on the space. Names are joined with `", "` and sorted alphabetically so the rendered state is stable across polls (`"EXPANSIVA, PROTEGIM"` instead of `"multiple"`). Falls back to a `"N companies"` count sentinel only if the joined form would overflow the 255-char HA state limit (vanishingly unlikely with real names). `extra_state_attributes` unchanged — automations keying off `approved_companies` / `pending_approval_companies` / `pending_removal_companies` keep working untouched.
- **Setup-flow Space selection now starts empty and filters by name** (#166, reported by @Sven2410). The selector switched to dropdown mode with the built-in name-filter autocomplete instead of a checkbox list. `default=[]` makes the initial state empty — no Space is selected until the installer explicitly adds it. A server-side length guard rejects an empty submission. Single-Space users (the common case) pay one extra click; installers with many customer Spaces — the case @Sven2410 reported — get a setup flow that scales. Reconfigure and options flows are unaffected.
- **Event-entity classification now reflects what the sensor actually did, not just the surrounding space state.** When Ajax bundles a sensor-trip qualifier (`HubEventQualifier(motion_detected)`, `door_opened`, `tamper_opened`, etc.) together with a state-context qualifier (`SpaceEventQualifier(space_night_mode_on)`, …) in the same FCM payload, the integration now picks the sensor-level event as the primary signal. The previous logic walked qualifier types in fixed order and returned the first match, which let the state context shadow the activity. Real-world impact: motion / door / tamper / panic / fire automations now fire with the expected `event_type` regardless of whether the underlying push came in during armed-away, armed-night, partially-armed, or any other state. Confirmed-incident events (`intrusion_alarm`, `panic_button_pressed`) likewise take precedence over the surrounding state context.

### Fixed
- **`sensor.<hub>_compania_cra` populates the CRA company name again** (#154, reported by @bogar). `CLIENT_VERSION` is pinned to `3.30` and `CLIENT_DEVICE_MODEL` to `SM-A536B`; HTS `build_connect_request` defaults move in lockstep so the over-the-wire client identification stays consistent across gRPC and HTS. Empirical reproduction established that the Ajax backend gates `SpaceService.stream.monitoring_companies` (and `installation_companies`) on the `client-version-major` gRPC header: reporting `3.46` returned the list empty; reporting `3.30` returned it populated. The version bump that triggered the regression landed in `1.2.3` and silently dropped the company data on every release since.
- **Mid-flight `CancelledError` during refresh now triggers Home Assistant's standard retry instead of a permanent failure** (#148 follow-up). When clicking Reload, the previous client's teardown could race with the new client's first refresh and the in-flight gRPC call got cancelled mid-flight, which `except Exception` didn't catch (`CancelledError` is a `BaseException`) — leaving the entry in a permanently failed state until HA was restarted. `_async_update_data` now distinguishes the two cancellation paths: if our own task is being cancelled (HA shutdown, options-listener reload), the `CancelledError` re-raises so the coroutine exits cleanly; if the cancellation came from a sub-call, it surfaces as `UpdateFailed` so HA retries with backoff and the integration recovers on its own. Real-world impact: clicking Reload no longer leaves the integration unusable until you restart HA.

### Internal
- **Diagnostics now expose `groups` and `group_mode_enabled` per space**, recoverable from a single Download Diagnostics dump. The previous schema only emitted `name / security_state / online / malfunctions`, which made a missing-vs-empty `space.groups` impossible to distinguish from the JSON alone.
- **`_parse_and_fire_event` logs `event_type / raw_tag / group_id` at DEBUG** every time the parser resolves a push payload — closes the long-standing blind spot where the only way to know what the parser extracted was to add ad-hoc logging mid-debugging.
- **Diagnostic `WARNING` + raw hex dump when a `space_group_*` push event lands without a resolvable `group_id`** stays in place as a permanent observability piece. If Ajax ever ships another wire shape for the group identifier, the WARNING surfaces the failing tag plus the first 2048 bytes of the raw push so the heuristic can be fixed from a single reproducer — same instrumentation that made the `DisplayGroups` discovery possible in this cycle.
- Test suite at **1312** unit tests (was 1263 in `1.4.5`); coverage 86.17% (was 85.84%). +49 tests across new functionality: per-group push (parsing, dispatching, `apply_push_group_security_state`), `DisplayGroups` extractor regressions including the `space_id` look-alike reject, SmartLock doorbell pass, event priority resolution, Photo on Demand service handler + RPC binding, CRA-company name resolution and joined-state rendering, dropdown space-selector schema, reload-CancelledError retry semantics, diagnostics group fields, and the parser-observability logging.

## [1.4.5] - 2026-05-18

Diagnostic patch. When the integration fails to load because of duplicate protobuf descriptors (almost always a stale or backup copy of `aegis_ajax` sitting next to the live one in `custom_components/`), Home Assistant used to surface the bare `TypeError("Couldn't build proto file into descriptor pool: duplicate file name ...")` and render it as the cryptic "Invalid handler specified" in the UI. The integration now logs an `ERROR` that spells out the most likely cause and the remediation before re-raising. No code behaviour change for successful installs.

### Fixed
- **Friendlier failure mode when two copies of the integration coexist in `custom_components/`** (#151, reported by @mschev). The first proto-triggering import in `__init__.py` is now wrapped in a narrow `try/except TypeError`; when the exception text contains "duplicate file name" the integration logs an `ERROR` naming the scenario (stale backup folder, partial HACS update) and the remediation path (list `custom_components/`, move or rename any non-active `aegis_ajax*` folder, restart). The original exception is re-raised so HA's existing broken-integration handling is unchanged.

### Internal
- Test suite at **1263** unit tests (was 1261 in `1.4.4`); coverage 85.84% (was 85.88%; small dip is the new helper). The classification + log message live in `_log_proto_descriptor_collision`; two new tests cover the duplicate-file-name path and the no-op-for-unrelated-TypeError path.

## [1.4.4] - 2026-05-18

Patch release fixing a regression in the `binary_sensor.<hub>_conexion_cra` entity. The CRA-connection sensor stopped reflecting the hub's real-time `monitoring.cms_active` flag after `1.2.3-beta.1` and started deriving its state from the `Space.monitoring_companies` snapshot — which is empty for cobranded installs (Protegim, AIKO, others) and for accounts that don't have an explicit APPROVED monitoring-company entry. The entity rendered `off` ("Desconectada") on those installs even with a healthy CMS channel — visibly out of sync with the "Central receptora de alarmas → Conectada" row the Ajax mobile app surfaces from the same hub status. SemVer PATCH; no schema, behaviour, or migration impact for installs whose CRA already showed correctly.

### Fixed
- **`binary_sensor.<hub>_conexion_cra` reads the hub's `monitoring.cms_active` flag again as the primary signal** (regression from #78 / commit 5699b8f in `1.2.3-beta.1`). The parser still populates `hub.statuses["monitoring_active"]` from the device snapshot's `monitoring` oneof — that's what the Ajax mobile app uses for the "Conectada / Desconectada" row, and it's the right source of truth for real-time channel health. The `space.has_monitoring` derivation is preserved as a fallback for the path #78 cared about (hub firmwares that don't emit a `monitoring` status entry but do have an APPROVED CRA company on the account). `unique_id`, `translation_key`, and `device_class=connectivity` unchanged — no migration impact.

### Internal
- Test suite at **1261** unit tests (was 1258 in `1.4.3`); coverage 85.88% (was 85.86%). Three new `TestAjaxCraConnectionSensor` cases cover the primary-signal path (`is_on_when_hub_reports_cms_active`, `is_off_when_hub_reports_cms_inactive`, `available_via_hub_status_when_space_snapshot_not_loaded`). The original three fallback tests stay — their fixture sets `hub.statuses = {}` so they implicitly exercise the legacy path.

## [1.4.3] - 2026-05-18

Patch release. Saving FCM credentials through the integration's Configure menu now reliably restarts the push client end-to-end — no manual reload required. Reported by @ArshSoni in #148 on a fresh `1.4.0` install: the "Push notifications" repair card cleared on save, but real-time pushes (arm/disarm, doorbell, alarm) never reached HA until the integration was reloaded by hand. SemVer PATCH; no schema, behaviour, or migration impact for installs where FCM was already working.

### Fixed
- **Options flow now awaits `async_reload` explicitly when `entry.data` changes** (#148, reported by @ArshSoni). The flow used to rely on `_async_options_update_listener` to fire after the framework writes `options`, but when only FCM creds change (FCM keys live in `data`, not `options`) and the user didn't touch any other option, the framework's second `async_update_entry(options=...)` short-circuits without firing a listener — leaving the FCM client running with the old credentials until a manual reload. Mirrors the pattern already used by `FcmCredentialsRepairFlow`: write the new data, then `await async_reload`. Serialised on `entry.setup_lock`, so racing with any listener-triggered reload is safe.

### Internal
- Test suite at **1258** unit tests (was 1256 in `1.4.2`); coverage 85.86% (was 85.85%). New `test_options_flow_reloads_when_data_changes` (asserts the reload fires) and `test_options_flow_no_reload_when_data_unchanged` (guards against over-reloading on poll-interval-only tweaks).

## [1.4.2] - 2026-05-18

Cosmetic i18n patch. The WallSwitch power sensor's display name in the device card drops the parenthetical "(derived)" / "(derivada)" / equivalent across all 14 locales, falling in line with the HA convention that every `device_class=power` sensor labels simply "Power". The value is still computed as `current × voltage` (with a 230V nominal fallback when firmware doesn't emit voltage); the "(derived)" suffix added visual noise without giving the average user anything actionable. No code, schema, or `entity_id` changes — `translation_key`, `unique_id`, class name and computation logic stay untouched, so zero migration impact for existing installs.

### Changed
- **`power_derived` sensor renders as "Power"** in all 14 locales (#123, reported by @brunovdw68). Was "Power (derived)" / "Potencia (derivada)" / "Vermogen (afgeleid)" / etc. The computation is unchanged and the technical "this is calculated" signal lives elsewhere now: the entity is `entity_registry_enabled_default=False` (only users who explicitly enable it see it), the `translation_key` and `unique_id` still carry `power_derived` (visible in dev-tools and template editor), and the README documents the computation. Cosmetic change only — display name in the entity card.

## [1.4.1] - 2026-05-18

Patch release. The transient HTS reconnect cycle (typically ~5 min on busy installs, multiple times per day) no longer blanks HTS-cached sensors to `unavailable`. Hub-cached state (per-device electrical readings, hub IP / SSID / DNS / signal level, ethernet/wifi/gsm channel flags) keeps rendering its last value through the dropout and refreshes in place on the next `STATUS_UPDATE` / `STATUS_BODY` delta. The single deliberate exception is `binary_sensor.<hub>_alimentacion_externa` (mains power) which still flips to `unavailable` so a real hub-power loss during an HTS outage can't be silenced by a cached `on` snapshot. No new functionality, no breaking changes.

### Fixed
- **HTS-cached sensors stop flapping to `unavailable` on every transient reconnect** (#146, follow-up to #144 in `1.4.0`). `1.4.0` shipped `RestoreSensor` on the four electrical-reading sensors (current, voltage, energy_consumed, power_derived) so they survived HA restarts, but the mid-session disconnect path still wiped the cached state: a 5-minute reconnect cycle blanked the sensors even though the hub remembered the values across our socket outage. `_handle_hts_disconnect` now preserves both `hub_network` and `device_readings`; the next live delta refreshes the cached value in place when HTS comes back. The cached state is also preserved when `_async_update_data` notices a dead HTS task and restarts the stream.
- **Mains-power binary sensor keeps its alert semantics** (#146). `binary_sensor.<hub>_alimentacion_externa` ANDs its `available` with the new `coordinator.is_hts_alive` property — if the stream is down we refuse to fall back to the cached `externally_powered=True` snapshot, since a real power loss during the dropout would otherwise be silenced. The other hub-network binaries (ethernet / wifi / gsm channel flags) stay in the "preserved last value" bucket because they describe which channel the hub last reported as active, not an operational alert.

### Internal
- Test suite at **1256** unit tests (was 1248 in `1.4.0`); coverage 85.85% (was 85.76%). New `test_handle_hts_disconnect_preserves_hub_network`, `test_handle_hts_task_done_drops_client_and_broadcasts`, `test_hts_disconnect_preserves_cached_state`, `test_is_hts_alive_reflects_client_presence`, `TestAjaxHubPowerSensor`, `test_diagnostic_sensor_stays_available_when_hts_dead`, and an integration-level `test_sensor_stays_available_across_hts_disconnect` that exercises the real coordinator end-to-end.

## [1.4.0] - 2026-05-17

Stable release rolling up the `1.4.0-beta.1` … `1.4.0-beta.7` line. Two big themes: **WallSwitch / Socket electrical readings** (`current` A, `voltage` V, `energy_consumed` kWh wired into HA's Energy dashboard, opt-in `power_derived` W) — closes the largest user-visible gap in the integration's device surface — and a new **read-only firmware update entity** for each Ajax hub, bringing the integration to **11 HA platforms**. Also adds an unambiguous deletion path for FCM credentials in the options form. No Ajax wire-protocol changes; everything was already on the wire and the integration was either silent or fragile around it. MINOR bump because new functionality ships; no breaking changes.

### Added
- **Electrical readings for WallSwitch and Socket-family devices** (#123, #137, #140). Each WallSwitch / Socket / `relay` / `relay_fibra_base` / `socket_b` / `socket_g` / `socket_outlet_type_e` / `socket_outlet_type_f` / `socket_type_g_plus` now exposes four sensors that mirror what the official Ajax app shows on the device card: `sensor.<name>_current` (A, `device_class=current`, `state_class=measurement`), `sensor.<name>_voltage` (V, `device_class=voltage`, `state_class=measurement`), `sensor.<name>_energy_consumed` (kWh, `device_class=energy`, `state_class=total_increasing` — ties into HA's Energy dashboard with proper meter-reset semantics), and `sensor.<name>_power_derived` (W, `device_class=power`, disabled by default, computed as `current × voltage` when the device reports a voltage and falling back to a nominal 230 V baseline otherwise). Values arrive through HTS in the per-device payload alongside the existing hub fields. The four sensors now survive HA restarts via `RestoreSensor` — on some hub firmwares the readings are absent from the boot snapshot and only arrive via per-device delta pushes on change, so without restoration a constant load (e.g. relay driving fixed-speed ventilation) would render `unknown` for hours after every restart. Translations in all 14 locales.
- **`update.<hub>_firmware` entity per hub** (#142, #143, #144). Surfaces the pending hub firmware update Ajax has queued: shows the target version with a download progress indicator while the cloud is pushing bytes, renders as "Up-to-date" when no update is pending. **Read-only on purpose** — no install feature is declared and `async_install` is not implemented, so HA renders no install button at all; firmware updates remain Ajax-scheduled and Ajax-triggered. A `release_summary` on the entity detail panel clarifies that "Up-to-date" only means "no update queued right now" (the actual installed firmware version is not carried by the Ajax stream). **11th HA platform.** Translations in all 14 locales.
- **"Delete FCM credentials" toggle** in the options form (#141). Toggling it on and saving drops all four FCM keys from the entry unconditionally, regardless of what the form fields currently contain. The unambiguous deletion path, immune to a HA frontend quirk where a `TextSelectorType.PASSWORD` field with a pre-filled default can't be reliably emptied through the UI. Translations in all 14 locales.
- **Per-device extraction in HTS bodies** (#137). The parser now walks the entire status/settings payload and emits one record per device — previously it only extracted the hub's row and dropped every other device's data silently. Used by the readings parser above.

### Changed
- **HTS per-device delta pushes are now consumed in place** (#137). Per-device deltas from the hub carry the same shape as one row of the periodic full snapshot. They're routed through the same callback the readings parser uses for the boot snapshot, so live electrical readings feel near-instant (whatever debounce window the hub applies) instead of waiting for the next periodic refresh. Subsumes the silent-drop behaviour added in `1.3.0-beta.7` (#128 / #111): the problem there was firing a snapshot refresh on every heartbeat (~8.6 KB round-trip), not the drop itself; we now read the delta in-place and never schedule a refresh from it.
- **FCM credential fields use `suggested_value` instead of `default`** (#141). The four FCM fields in the options form previously declared `default=existing_value`, which made voluptuous re-inject the prior value when the frontend omitted the key on submit — a path Hansontech190 reported on #138 with the password field that doesn't reliably round-trip empty. The fields now use `description={"suggested_value": ...}` so an empty submission stays empty end-to-end. Combined with the explicit clear toggle above.

### Fixed
- **WallSwitch electrical sensors no longer drop to `unknown` on every relay toggle** (#140, regression introduced in `1.4.0-beta.1`, reported by @brunovdw68 in #123). Per-device delta pushes from the hub rebuilt the readings snapshot from scratch on every message: deltas that didn't carry the current / energy fields produced an all-empty snapshot and overwrote the cached values, leaving the sensors rendering `unknown` until the next periodic full snapshot. The parser now merges deltas against the cached snapshot, so only fields actually present in the new message get updated.
- **Clearing FCM credentials in the options flow now actually removes them** (#139, #141, fixes #138, reported by @Hansontech190). Until `1.4.0` the options handler silently treated empty submissions as "no change" instead of "clear", so credentials could never be removed through the UI. Two iterations: the persistence handler was fixed in `beta.2`; `beta.4` added the explicit clear toggle and switched the schema to `suggested_value` after the password-field UI quirk surfaced.
- **`update.hub_firmware` entity renders as "Up-to-date" when no firmware update is pending** (#143). HA's `UpdateEntity.state` returns `unknown` whenever either `installed_version` or `latest_version` is `None`; the entity now reports a constant `installed_version` and mirrors it on `latest_version` when no update is queued, landing on `STATE_OFF` ("Up-to-date") instead.
- **`power_derived` uses the device-reported voltage** (#140). When the WallSwitch reports a voltage, the sensor renders `current × voltage`; the 230 V baseline survives only as the fallback for firmwares that don't emit a voltage reading.

### Internal
- Test suite grew from **1157** (`1.3.0`) to **1248** unit tests; coverage 85.76% (was 85.13%). New `TestExtractAllDevicesKv`, `TestStatusUpdatePush`, `TestParseDeviceReadings`, `TestOnHtsDeviceKv`, `TestAjaxDeviceElectricalSensors`, `TestParseFirmwareFromHubObject`, `TestGetFirmwareInfo`, `TestHubFirmwareRefresh`, `TestAjaxHubFirmwareUpdate`, `TestHubFirmwareUpdateInfo` cover the parser + push handler + coordinator routing + sensor entity surface + the new firmware update path.
- All four electrical-reading sensor classes share a common `_AjaxDeviceReadingsBase` that handles `RestoreSensor` integration; subclasses provide `_live_native_value` rather than overriding `native_value` directly. The base class falls back to the persisted last-known value when no live reading is available and filters non-numeric persisted states.

## [1.3.0] - 2026-05-16

Stable release rolling up the `1.3.0-beta.1` … `1.3.0-beta.11` line. Two big themes: **MotionCam Video Doorbell support** — the first device family on Ajax's `video_edge_channel` oneof now appears as a HA device card with `doorbell_pressed` events firing from both Wireless DoorBell (Jeweller ring button) and Video Doorbell push paths — and a sustained push on **FCM-misconfiguration observability** that turns silent push failures into actionable Repair cards and cause-specific WARNINGs at the default log level. Also adds a read-only `valve` platform for WaterStop (10th HA platform), hardens the device-stream loop against single-device parse errors, drops a noisy HTS-snapshot refresh cycle, and exposes startup-listener failures that used to hide at DEBUG. No Ajax wire-protocol changes anywhere in the line.

### Added
- **MotionCam Video Doorbell support.** The Video Doorbell, plus its `motion_cam_video_indoor` / `motion_cam_video_base` siblings, were silently invisible in HA: the Ajax cloud sent them in every snapshot but the parser dropped them because they arrive on `LightDevice.video_edge_channel` (not the `hub_device` oneof the parser walked). They now appear as device cards with the standard MotionCam entity set (`motion_detected` + `tamper` binary sensors, `signal_strength` / `battery_level` skipped because the channel proto doesn't carry them). Ring-button presses wire through both possible event sources: standalone Wireless DoorBell (Jeweller ring paired with the hub) via `HubEventQualifier.RingButtonPressed`, MotionCam Video Doorbell via a new `VIDEO_EVENT_TAG_MAP` walking `VideoEventQualifier`. Both converge on `event_type: doorbell_pressed` on the existing per-space `event.aegis_security_event` entity — snapshot-on-press / TTS-on-press automations are standard HA from there. Streaming video and snapshot-on-demand for the Video Doorbell stay out of scope for this release. (#121, #124, surfaced by @Permudious in #119)
- **Read-only `valve` platform** for Ajax WaterStop and WaterStop Fibra (`water_stop`, `water_stop_base`). New `water_stop_channel` branch in `_parse_spread_properties` emits `valve_chN` (open / closed from `STATE_ON` / `STATE_OFF`), `valve_chN_transitioning` (motor moving), and `valve_chN_stuck` (`MALFUNCTION_IS_STUCK`). `AjaxValve` (`device_class = WATER`) reports `is_closed` / `is_opening` / `is_closing` plus a `stuck` attribute; `STATE_UNKNOWN` leaves the key absent so the entity renders as `unknown` instead of fabricating a closed reading on a comms hiccup. **Read-only on purpose** — no `SwitchWaterStopService` exists in the v3 protos we have, so `supported_features = 0` and bidirectional control would silently fail. Bidirectional control follows once a WaterStop user captures the official-app command-side gRPC call. Brings the integration to **10 HA platforms**. (#118)
- **`fcm_not_configured` Repair card** under Settings → Repairs ("Push notifications not configured — real-time events disabled") with a one-click fix flow that re-uses the existing `fcm_credentials_invalid` form. Real-time events (doorbell ring, arm/disarm push, alarm) require FCM, but until this release an unconfigured install was completely silent — the only signal was at INFO level which HA hides by default. The repair is raised at every integration start when no `fcm_api_key` is set, cleared on the first successful FCM register. Translations in all 14 locales. (#130, surfaced by @Permudious / @Hansontech190 in #119 / #129)
- **Snapshot-replay test harness with the first real-fleet fixture.** `TestSnapshotReplay` deserialises a `StreamLightDevicesResponse` and replays it through `start_device_stream` end-to-end. Two layers: a synthetic multi-device snapshot (including the #119 `wifi_signal_level_status` shape on a `video_edge_channel`) and an auto-replay loop over every `tests/fixtures/*.bin`. First binary fixture is `bvis_home_fleet.bin` (11 devices from the maintainer's real install, PII scrubbed end-to-end); future doorbell-shape or WallSwitch-shape captures from users drop in as siblings with no glue-code per file. (#126, #127)

### Changed
- **FCM registration / push-start failures emit cause-specific WARNINGs instead of a generic stack trace.** Until this release every FCM error landed as `FCM registration failed: ...` plus a 38-line traceback, leaving the user with nothing concrete to act on. The classifier now maps the three `RuntimeError` strings the `firebase-messaging` library actually raises from its public `register()` entrypoint to actionable WARNINGs: `Unable to establish subscription with Google Cloud Messaging.` — the dominant credential-set error — points the user at four-credential consistency (`fcm_sender_id` must be the numeric prefix of `fcm_app_id`, `fcm_api_key` must be paired with that same `fcm_project_id`); `Unable to register with fcm` points at malformed `fcm_app_id`; `Unable to register and check in to gcm` names the FCM hosts the HA host needs to reach. Same `fcm_credentials_invalid` card is raised in every failure path — only the log gets sharper. The substring map was validated against the library's runtime behaviour, not inferred from source, so the four-branch heuristic shipped during the beta cycle is now down to the three branches the library can actually produce. (#132, #134, driven by @Hansontech190 in #131)
- **HTS / FCM startup-failure logs are visible at the default log level.** Affected installs in #111 reported "HTS streams: 0/1" and "FCM clients: 0/1" with empty logs even under DEBUG. HTS `connect()` exceptions are now WARNING with the exception class name (full traceback preserved via `exc_info=True` for DEBUG users), missing session token now WARNING with a pointer to the earlier auth failure, pre-connect setup exceptions also promoted. The first refresh ends with a one-line INFO summary `Aegis startup: device streams N/M started, HTS lifecycle scheduled/skipped` so the surface state is visible at a glance. On the FCM side: `firebase_messaging` not installed becomes WARNING; "FCM registration successful" / "FCM push client started" promoted to INFO; the no-token-after-register failure becomes WARNING with a re-extraction hint; Ajax server rejection of the push-token register also WARNING. (#122, #130)
- **"FCM credentials not configured" log promoted from INFO to WARNING.** Healthy installs (FCM configured and registered) remain log-silent during normal operation, so the implicit rule becomes: no FCM line at WARNING = FCM is OK. (#130)

### Fixed
- **HTS hub-network sensors no longer go permanently `unavailable`** on installs whose hub firmware emits TLV escape sequences the parser doesn't recognise. @uddinr's hub was sending a `0x06 0x6A` pair inside an `UPDATES` payload; the strict `tlv_unescape_param` raised `ValueError` on the unknown pair, terminating the listen task and leaving every Ethernet / Wi-Fi / GSM / mains-power sensor stuck on the previous value forever. The parser is now lenient: unknown `0x06 <byte>` pairs are preserved as two literal bytes with a debug log; the two known escapes (`0x06 0x35` → `0x05`, `0x06 0x36` → `0x06`) keep working unchanged. Belt-and-suspenders: `_handle_update` wraps `tlv_decode` in `try/except` and drops the offending message instead of killing the listen loop. (#120, fixes #108, thanks @uddinr)
- **MotionCam Video Doorbell no longer crashes the device-stream task in a reconnect loop.** The `_parse_video_edge_channel` path added during the beta cycle exposed a pre-existing bug in `_parse_statuses`: the `wifi_signal_level_status` branch was reading `int(status.wifi_signal_level_status)` but that field is a sub-message wrapping the actual `wifi_signal_level` enum, not a plain int. `hub_device` devices on most installs didn't surface that status so the bug stayed dormant; `video_edge_channel` devices (like @Permudious's doorbell) emit it on every snapshot, triggering a `TypeError` on every reconnect. Read the int from the nested `.wifi_signal_level` field at both call sites (snapshot parser + persistent stream handler). (#125, surfaced by @Permudious in #119)
- **A single bad device or status update no longer kills the device stream.** Before this release, a parse exception on one `LightDevice` (or one update inside the `updates` batch) bubbled out of the stream's `async for` loop, hit the outer `except Exception` and put the task into an exponential-backoff reconnect cycle @Permudious saw 21× in a row before #119 surfaced. The per-device `parse_device` call and the per-update handler are each wrapped in `try/except`: the offender is logged at WARNING with `exc_info=True` so the device id and full traceback land in the logs without DEBUG, and the rest of the snapshot / update batch flows through normally. (#126, follow-up to #119)
- **HTS `sub-key 11` heartbeats no longer trigger a full snapshot refresh on every tick.** @Hansontech190 and @b0arkz observed `Hub <id>: requesting fresh HTS snapshot after unknown update sub-key 11` firing every few seconds, each time pulling a `REQUEST_FULL_SETTINGS + REQUEST_FULL_STATUS` round-trip (~8.6 KB) from the Ajax cloud. Sub-key 11 is the hub-network delta channel: longer variants (~50 byte payload) carry the anchor keys already parsed, shorter variants (~34 byte payload) only carry fields not surfaced. The handler now drops the short variants silently and only escalates to a snapshot refresh on genuinely unknown sub-keys. Net effect on affected installs: zero behaviour change for hub-network sensors, large drop in HTS traffic and idle CPU. (#128, fixes #111)

### Internal
- `_parse_statuses` unit tests rewritten to use real `LightDeviceStatus` proto instances instead of `MagicMock` across every sub-message branch (`signal_strength`, `gsm_status`, `sim_status`, `monitoring`, `life_quality`, `temperature`, `wire_input_status`, `transmitter_status`, `smart_lock`, `nfc`, `motion_detected`, `battery`). The MagicMock pattern that masked the original `int(sub_message)` bug is gone for the high-risk branches; no new latent shape bugs surfaced during the conversion. (#126)
- `parse_device` split into `_parse_hub_device` and `_parse_video_edge_channel` so the two `LightDevice` oneof paths are explicit. `hub_id` for video-edge channels is set to the channel's own id (VideoEdge bridges aren't children of a Jeweller hub in Ajax's model). (#124)
- Test suite grew from **1092** (1.2.4) to **1143** unit tests, coverage 84.13%. All 14 translation locales (ca, cs, de, en, es, fr, it, nl, pl, pt-BR, pt, ro, tr, uk) carry the new strings (`event_type.doorbell_pressed`, `issues.fcm_not_configured.*`, `valve.*`).

## [1.2.4] - 2026-05-08

Stable release rolling up the `1.2.4-beta.1` … `1.2.4-beta.11` line. Two big themes: a new device-platform slice (lock + per-group alarm panels + tilt/steam binary sensors) finally turning every advertised Ajax surface into a first-class HA entity, and a sustained boot-time push that drops the integration out of HA's *"integration taking too long"* warning even on multi-account installs. No Ajax wire-protocol changes anywhere in the line.

### Added
- **`lock` platform** for Ajax SmartLock and Yale LockBridge (`smart_lock` / `smart_lock_yale`). Native HA `lock.*` entities with `lock` / `unlock` / `lock.open` (= unlatch) wired to `SwitchSmartLockService`; state (locked / unlocked / unlatched) parsed from the `LockStatus` oneof and refreshed via both poll snapshots and persistent stream updates. (#102)
- **Per-group `alarm_control_panel` entities** when a space runs in **Group / Zone Mode**. Each group arms/disarms independently via `armGroup` / `disarmGroup`; the whole-house panel stays alongside (so night mode — only space-wide on Ajax — remains accessible). Spaces in regular mode keep their single panel, no entity churn. State exposes `group_id`, `group_name`, `space_id`, `hub_id`, `connection_status` so automations can target a single group. (#84, #86)
- **`tilt` and `steam` binary sensors** filling out the device-type matrix. `tilt` (TAMPER) on every DoorProtect Plus variant exposes the accelerometer's anti-removal status alongside the existing `vibration` (knock). `steam` (PROBLEM) on every FireProtect 2 variant whose smoke chamber is physically present discriminates real smoke from shower / cooking steam. Heat-only / CO-only sub-models stay without `steam`. (#101)
- **DHCP discovery** for Ajax hubs on the local LAN — hubs broadcasting from OUI `9C:75:6E` appear as **Discovered** cards under Settings → Devices & Services with hostname / IP in the title; per-MAC dedupe and `already_configured` keep DHCP renewals from spamming the discovery list. (#92)
- **HA Repairs surface for diagnosable conditions.** Three Repair cards under Settings → Repairs: `hub_offline_24h` (space OFFLINE for 24h+), `hts_chronic_failure` (HTS reconnect failing 30 min+), and `fcm_credentials_invalid` (now `is_fixable=True` with a guided form pre-filled with the broken values; submit reloads the entry with the new credentials). The first two are informational because the fix is physical (hub power, firewall). (#89)
- **System Health card** under Settings → System → Repairs → System Information: gRPC reachability, configured-account count, total spaces, HTS/FCM alive ratios (`N/M`), pushes received since startup, humanised "last push" / "last successful poll" ages. Replaces log archaeology as the first triage step for "events stopped arriving". (#91, #106 follow-up, #110)
- **Reauth flow.** Rejected sessions raise `ConfigEntryAuthFailed` instead of `UpdateFailed`, so HA shows the orange Reconfigure banner and the new `async_step_reauth` runs a single password prompt (with optional TOTP) keeping the same `unique_id` — entity ids, areas, automations, history all survive untouched. (#90)

### Fixed
- **Boot phase no longer blows past HA's "integration taking too long" threshold.** Three changes compound. (1) HTS handshake (TCP + custom application handshake, up to 20 s) and FCM startup (Firebase register → Ajax push token register → start `FcmPushClient`) move to background tasks so the first refresh stops awaiting them inline (#113, closes #112). (2) The synchronous per-space `get_devices_snapshot` loop on the boot path is replaced with a persistent device-snapshot cache (`Store`-backed, per entry): on subsequent boots the first refresh warm-starts `coordinator.devices` from cache and skips the gRPC snapshot entirely; persistent device streams then deliver fresh data within seconds via `_handle_devices_snapshot`. Falls back to the heavy path on fresh install or a corrupt cache. (3) Stream-delivered snapshot saves go through `Store.async_delay_save` with a 30 s window, coalescing bursts into a single disk write. Real-HA measurement on a one-account install: ~10 s shaved off HA's total boot, ~9 s off aegis_ajax's setup-to-platforms-online window. (#116, closes #114; #113, closes #112)
- **Switches, dimmer brightness and locks now actually act on the hub.** `DevicesApi.send_command` was a `NotImplementedError` placeholder since the integration's first release: every relay / wall-switch / socket / light-switch / dimmer click failed with `Device commands not yet implemented`. The dispatcher now routes `on` / `off` to `DeviceCommandDeviceOn/OffService.execute`, `brightness` to `DeviceCommandBrightnessService.execute` (`BRIGHTNESS_TYPE_ABSOLUTE`, matching HA's slider), and lock operations to `SwitchSmartLockService`. Failure responses (`hub_offline`, `hub_busy`, `permission_denied`, `hub_wrong_state`) bubble up as `DeviceCommandError(<error>)` for proper HA error toasts. (#104, #105)
- **Switch / dimmer / valve state now reflects the hub.** The on/off state of relays, sockets and light switches lives in `LightHubDevice.spread_properties` — separate from the `LightDeviceStatus.statuses` oneof the parser already walked — so `device.statuses["switch_chN"]` was never populated and `AjaxSwitch.is_on` always read `False`. New `_parse_spread_properties` translates `RelayChannel` / `LightSwitchChannel` (multi-gang devices arrive as multiple entries; brightness included) / `SocketBaseChannel` / `WaterStopChannel` into the existing `switch_chN` / `brightness_chN` / `valve_chN` keys the entity layer already reads. Symptom @EpicManeuver hit in #104: bistable Relay Jeweller toggling worked at the hub but HA snapped back to `off`. (#109)
- **`event.aegis_security_event` no longer triggers twice** per arm / disarm / night-mode transition. The Ajax FCM backend dispatches two separate messages per security event (`Notification` + silent `DispatchEvent`) ~20–30 ms apart, both carrying the same `SpaceEventQualifier`. The notification listener now dedupes by Ajax `notification_id` over a 5 s window. Photo-URL extraction and notification-id-future resolution stay above the dedupe gate; pushes without an extractable `notification_id` skip dedupe (defensive). (#80)
- **FCM-driven instant `security_state`** shortcut now fires for co-brand arm / disarm / night-mode pushes that were silently falling through to the poll-refresh path. The parser tries `SpaceEventQualifier` first (mapped via the new `SPACE_EVENT_TAG_MAP`) before `HubEventQualifier`. Real-HA validation: arm-night and disarm from the Ajax mobile app now land within 20–40 ms of the push instead of up to one poll cycle. (#68)
- **HTS-backed hub-network sensors no longer flap** on healthy idle connections. The listen loop tolerates up to 3 consecutive read timeouts (~120 s of silence) before closing, resetting the counter on any real inbound message. A failed PING still closes immediately. (#76, thanks @bogar)
- **HTS authentication is bounded by an overall 20 s timeout** (`AUTH_TIMEOUT`). A server feeding bytes slowly used to keep the handshake await alive forever, blocking `_async_update_data()` for hours. (#74)
- **`CRA connection` binary sensor** reflects actual approved monitoring-company assignments from the full `Space` snapshot instead of the hub-status `monitoring.cms_active` flag, which could stay `off` on installations that do have a CRA attached. New disabled-by-default diagnostic `CRA company` sensor exposes the approved name(s); both entities stay `unavailable` until the first monitoring snapshot loads so they don't show a false initial `off`. (#78, thanks @bogar)
- Per-group panel entities no longer flap to `unavailable` between hourly snapshots — coordinator preserves `groups` and `group_mode_enabled` from the previous `Space` across polls in the same merge step that already preserves `monitoring_companies`. The whole-space panel is no longer dropped when Group Mode is enabled. `arm_group` / `disarm_group` reference the correct proto classes (`ArmSpaceGroupRequest` / `DisarmSpaceGroupRequest`); the new `TestGroupProtoIntegration` regression suite exercises the real proto module so this class of drift fails loudly instead of passing through `MagicMock`. (#86)
- README "How to obtain FCM credentials" now points users at the correct location for the API key, which is not co-located with the other three values. (#83)

### Internal
- New `device_cache.py` module (`DevicesCache` wrapping a per-entry `Store`); coordinator gains `is_hts_connected`, `last_update_success_time`, `_first_offline_at`, `_hts_first_failure_at`, `_devices_cache`. `repairs.py` helper module wraps `homeassistant.helpers.issue_registry` with the domain pre-bound and stable per-scope ids. New `FcmCredentialsRepairFlow(RepairsFlow)` + `async_create_fix_flow` discovery hook. New `_build_object_type(device_type)` helper marks the matching empty-marker oneof case on the v2 `ObjectType` proto via `SetInParent()` so command requests round-trip cleanly.
- Test suite grew from ~870 to **1092** unit tests (coverage 83.5%); new `TestGroupProtoIntegration` and the 11-test `device_cache.py` + warm-start coverage make the new code paths regression-safe.
- All 14 translation locales (ca, cs, de, en, es, fr, it, nl, pl, pt-BR, pt, ro, tr, uk) carry the new strings (`reauth_*`, `issues.*`, `fix_flow.*`, `system_health.info`, `binary_sensor.tilt` / `binary_sensor.steam`). Best-effort translations; technical tokens kept verbatim across locales.

## [1.2.3] - 2026-04-29

Stable release rolling up the `1.2.3-beta.1` and `1.2.3-beta.2` line. Closes #78 and ships another community contribution from @bogar.

### Fixed
- The `CRA connection` binary sensor now reflects actual approved monitoring-company assignments from the full `Space` snapshot instead of the hub-status `monitoring.cms_active` flag, which could stay `off` on installations that do have a CRA attached. The integration preserves the legacy entity id / unique id for backwards compatibility, adds a disabled-by-default diagnostic `CRA company` sensor with the approved company name(s), and keeps both entities `unavailable` until the first monitoring snapshot has been loaded so they do not show a false initial `off`. The diagnostic sensor unwraps Ajax's `google.protobuf.StringValue` company-name wrapper to a plain string before storing it in entity state / attributes, so enabling the sensor doesn't break Home Assistant's `/api/states` endpoint. (#78, thanks @bogar)

## [1.2.2] - 2026-04-28

Stable release rolling up the `1.2.2-beta.1` … `1.2.2-beta.3` line. Closes the two follow-up items left open in `1.2.1` (the FCM-driven instant security state path and the coordinator stall) and adds a community contribution from @bogar.

### Fixed
- The FCM-driven instant `security_state` shortcut now fires for arm / disarm / night-mode pushes in co-brand setups where it had been silently falling through to the legacy poll-refresh path. The push payload encodes the primary transition in a `SpaceEventQualifier` (inside `SpaceNotificationContent.qualifier`), but the parser only inspected `HubEventQualifier` candidates, which in those payloads carry secondary zone-incident tags such as `ext_contact_opened` / `roller_shutter_alarm`. The parser now tries `SpaceEventQualifier` first and maps the `space_armed` / `space_disarmed` / `space_night_mode_*` family to a new `SPACE_EVENT_TAG_MAP`, falling back to `HubEventQualifier` for genuine hub-level events (alarm, tamper, …). The `event.aegis_security_event` entity also benefits because it shares the same parser. Real-HA validation: arm-night and disarm from the Ajax mobile app now land on the alarm panel within 20–40 ms of the push, instead of waiting up to one poll cycle. (#68)
- The HTS authentication handshake is now bounded by an overall 20 s timeout (`AUTH_TIMEOUT`). Previously `_authenticate()` only relied on the per-chunk `READ_TIMEOUT`, so a server that kept the TCP connection alive while feeding bytes slowly could keep the handshake await alive forever — blocking `_async_update_data()` for hours and freezing the alarm panel state. On timeout the connection is closed and `HtsConnectionError` is raised, so the coordinator surfaces `UpdateFailed` and reschedules the next poll on the normal cadence. (#74)
- HTS-backed hub-network sensors (`connection_type`, Wi-Fi SSID / IP / signal, ethernet IP / gateway / DNS, cellular network) no longer flap to `unavailable` on healthy idle connections. The HTS listen loop used to treat the very first `READ_TIMEOUT=40s` of inbound silence as a hard disconnect — but a healthy server can legitimately stay quiet beyond that window. The loop now tolerates up to `MAX_CONSECUTIVE_READ_TIMEOUTS=3` consecutive idle timeouts (~120 s of full silence) before closing, resetting the counter on any real inbound message. A failed PING still closes the connection immediately, so genuine disconnects are detected without delay. (#76, thanks @bogar 🙏)
- Automations bound to `event.aegis_security_event` no longer trigger twice for every arm / disarm / night-mode transition. The Ajax FCM backend dispatches **two separate FCM messages** per security event (a user-facing `Notification` and a silent `DispatchEvent`) ~20–30 ms apart, both carrying the same `SpaceEventQualifier`. With the new in-memory shortcut from #68 they were both reaching the event-fire / refresh path. The notification listener now dedupes by Ajax `notification_id` over a 5 s window: the second push short-circuits before `_parse_and_fire_event` and `async_request_refresh()`. Photo-URL extraction and notification-id-future resolution stay above the dedupe gate, and pushes without an extractable `notification_id` skip dedupe (defensive). The alarm panel state path was already idempotent so panel state and HTS data are unchanged. (#80)

## [1.2.1] - 2026-04-28

Stable release rolling up the `1.2.1-beta.1` … `1.2.1-beta.10` line. Highlights:

### Added
- New optional `auto_create_labels` toggle in the integration's Options. When disabled the integration no longer recreates and reassigns the `aegis_*` labels on every restart, so users who manage Home Assistant labels manually can clean them up without having them come back. Default stays enabled. (#47)
- New `aegis_ajax.press_panic_button` service that triggers the Ajax SOS / panic button on a space (same endpoint the official mobile app's red SOS button uses). Requires an explicit `confirm: true` field as a safety lock; the call forwards a Panic / Hold-up alarm to the monitoring station (CRA) and on most contracts triggers immediate police dispatch with no verification window — see the README for caveats and the recommended Transmitter-based path for non-emergency automations. (#48)
- Each Ajax device now exposes its hardware identifier as the device `serial_number`, so you can locate sensors physically without walking around triggering each one. (#55)
- Devices are automatically associated with HA areas matching their Ajax room (via `suggested_area`) the first time they're added. (#55)
- The `wire_input_alert` binary sensor is now exposed for Transmitter Jeweller devices, reflecting the bridged third-party sensor's intrusion line in addition to the case tamper. (#65)

### Changed
- The alarm control panel applies external arm/disarm/night-mode events from the parsed FCM push payload directly when the new in-memory shortcut succeeds, falling back to the existing `async_request_refresh()` path otherwise. The fallback keeps the panel in sync regardless of co-brand parser variants — see follow-ups in #68 for the FCM-driven instant path being investigated for some payload shapes.
- Audited `_DEVICE_TYPE_SENSORS` and `SWITCH_DEVICE_TYPES` against the current Ajax v3 ObjectType catalog and added missing aliases that were silently falling back to a tamper-only entity set: hub variants (`hub_two`, `hub_two_plus`, `hub_three`, `hub_4g`, `hub_lite`, `hub_fibra`, `hub_hybrid_2`, `hub_hybrid_4g`, `hub_mega`, `hub_yavir`, `hub_fire`, `hub_superior`, …); range extenders (`range_extender`, `range_extender_2`, `range_extender_2_fire`); DoorProtect Plus G3 Fibra; MotionProtect / MotionCam G3, Plus, S, Curtain, Outdoor, Fibra and PhOD variants; sirens (`home_siren_g3`, `street_siren_plus_*`, `street_siren_s_*`, `street_siren_double_deck_fibra`); `wire_input_rs`; keypad family (`keypad_plus`, `keypad_plus_g3`, `keypad_s_plus`, `keypad_outdoor*`, `keypad_touchscreen*`); `life_quality_plus`, `water_stop_base`; switch wiring variants (`relay_fibra_base`, several socket types, multi-gang and multi-way light switches). (#51)

### Fixed
- Reloading the integration no longer accumulates new active sessions in the user's Ajax account: the latest session token is persisted back to the config entry after every login, the coordinator detects `UNAUTHENTICATED` errors from the gRPC API and forces a fresh login + retry instead of falling out as `UpdateFailed`, and removing the integration permanently calls `LogoutService.execute` server-side so the dangling session disappears from the Ajax account too. (#53)
- FireProtect 2 detectors no longer fall back to a tamper-only entity set: all `fire_protect_two*` variants known to the v3 ObjectType (including UL-listed sub-models) now map to the appropriate smoke / heat / CO sensor set, with single-sensor sub-models exposing only the relevant entity. (#51)
- Numeric/structured sensor values streamed in real time (temperature, humidity, CO2, signal strength, GSM/SIM/NFC/Wi-Fi diagnostics) were being overwritten with `True` whenever an ADD/UPDATE event arrived between snapshots, causing temperature entities to drop to `1 °C` intermittently on `DoorProtect Plus` and `MotionCam` devices among others. The stream parser now extracts the actual values and the coordinator applies them as scalars or sub-keys instead of coercing every non-binary update to a boolean. (#59)
- REMOVE events on the device stream now clear every `device.statuses` key the snapshot parser writes for that status, not just the one matching the proto field name. Previously `life_quality`, `gsm_status`, and `motion_detected` left stale sub-keys behind that lingered until the next full snapshot. (#61)
- The Transmitter Jeweller's `wire_input_alert` entity now toggles correctly because the device's `transmitter_status` proto oneof (field 75) is handled identically to `wire_input_status` (field 74) across the snapshot parser, the real-time stream and the coordinator's REMOVE path. (#65)

### Known issues

Two items remain open and will be addressed in a follow-up release (both resolved in `1.2.2`):
- #68 — the FCM-driven instant security_state path is implemented and unit-tested but in some co-brand payload shapes the parser doesn't surface the right qualifier, so the panel still updates via the legacy poll-refresh path on those installs.
- #74 — `_async_update_data()` can stall indefinitely under specific HTS reconnect scenarios; reload the integration as a workaround until the fix lands.

## [1.2.0] - 2026-04-25

### Added
- MultiTransmitter wired inputs (`wire_input_mt`) and hub-wired inputs (`wire_input`) now expose a single SAFETY binary sensor that toggles when the wired third-party sensor is triggered. The entity reflects the alert state regardless of which status oneof the hub firmware uses (`wire_input_status`, `external_contact_broken`, or `external_contact_alert`). The Ajax alarm category reported by the hub (intrusion, glass_break, fire, vibration, …) is exposed as an `alarm_type` attribute on the entity. Translations added for all 14 supported languages (#36)

## [1.1.1] - 2026-04-24

### Fixed
- DoorProtect external wired contact state now exposed via `external_contact_alert` binary sensor — the previous `external_contact_broken` entity only reflected cable-fault events, so the window open/closed state wired through the sensor's external input never changed (#25)

## [1.1.0] - 2026-04-23

### Added
- **Force arm option** — new checkbox in Options to always arm ignoring open sensors and malfunctions (#32)
- **Descriptive arm/disarm error messages** — when arming fails, the error lists the specific devices causing the issue (e.g. "Front Door: open; Keypad: low battery")
- All user-facing error messages fully translated in 14 languages (arm, disarm, PIN code, hub busy, etc.)

## [1.0.9] - 2026-04-23

### Changed
- Diagnostic entities disabled by default to reduce noise on device pages — hub network sensors (IPs, DNS, gateway, Wi-Fi/cellular details), per-device connectivity and problem sensors, hub Ethernet/Wi-Fi/mains power binary sensors. Users can enable them individually if needed.

## [1.0.8] - 2026-04-23

### Fixed
- Proto C extension imports moved to module level in `client.py` — fixes `Detected blocking call to import_module` crash on HA 2025+/2026+
- Reconfigure flow now handles 2FA (`TwoFactorRequiredError`) — previously showed "unknown error" for 2FA accounts
- Session token persisted in config entry to survive HA restarts — avoids re-login and repeated 2FA prompts
- Document SHA-256 password hash as protocol constraint (CodeQL false positive)
- Add `permissions: contents: read` to hassfest workflow (CodeQL `actions/missing-workflow-permissions`)

### Added
- `reconfigure_2fa` config flow step with translations for all 14 languages

## [1.0.7] - 2026-04-23

### Security
- FCM credentials moved from options to config entry data (encrypted storage) with automatic v1→v2 migration
- HTS debug logs no longer leak session tokens or auth payload hex dumps
- Photo URL domain validation in camera download (defense-in-depth against SSRF)

### Fixed
- Replace all `assert` statements with explicit checks in HTS client and config flow
- `send_command()` now raises `NotImplementedError` instead of silent no-op
- Event entity unregisters on removal via `async_will_remove_from_hass` (prevents stale refs)
- Fix redundant `except (HtsConnectionError, Exception)` clause in coordinator
- Replace deprecated `asyncio.get_event_loop()` with `get_running_loop()`
- Alarm panel model from actual device type instead of hardcoded "Hub"
- Timezone-aware photo timestamps using `dt_util.now()`
- ProblemSensor `available` property now checks device exists
- Fix `_encode_varint_field` to handle values > 127 (proper multi-byte encoding)
- OptionsFlow compatible with HA 2024.11+ property descriptor
- Notification parser exception logging now includes traceback
- Remove redundant `_attr_icon` on button (already in `icons.json`)
- Remove duplicate `AjaxCobrandedConfigEntry` type alias in diagnostics

### Changed
- `force_arm` / `force_arm_night` services now support entity target selector
- Cache SIM info (refresh once per hour instead of every poll cycle)
- Skip device snapshot when persistent gRPC streams are healthy
- HTS frame reading uses 4096-byte chunk buffering instead of byte-by-byte
- `async_refresh()` → `async_request_refresh()` after arm/disarm (debounced)
- Restore normal poll interval after successful re-authentication
- HTS reconnect deferred to next poll cycle instead of immediate retry
- Photo cleanup deferred to background task (no longer blocks startup)
- Centralize proto `sys.path` setup in single module (removed 9 scattered copies)
- Service field translations added for all 14 languages

### Documentation
- README: services target selector, FCM storage location
- `services.yaml` with target and fields definitions
- Sync `pyproject.toml` version with manifest

## [1.0.6] - 2026-04-23

### Fixed
- Recompiled proto stubs with grpcio-tools 1.75.1 to fix compatibility with HA OS (ships grpcio 1.75.1) — resolves "grpcio version mismatch" error on login (#26)

## [1.0.5] - 2026-04-23

### Fixed
- Security: constant-time PIN comparison with `hmac.compare_digest()`
- Security: proper URL validation with `urlparse` in media module
- Security: IMEI sensor disabled by default to protect PII
- Performance: cached SSL context in HTS client (no longer blocks event loop)
- Performance: FCM register/start run in executor when synchronous
- Performance: media source filesystem I/O wrapped in async executor
- Thread safety: HTS and FCM callbacks now use `call_soon_threadsafe`
- Immediate HTS reconnect on disconnect instead of waiting for next poll
- Missing `pin_code` translation added to all 14 languages

## [1.0.4] - 2026-04-22

### Fixed
- External contact sensor now available for all DoorProtect models (standard, Fibra, S, G3), not just Plus variants (#25)
- Logbook descriptions clarified with "(via device)" format
- mypy type errors in logbook module

## [1.0.3] - 2026-04-22

### Fixed
- Logbook now shows detailed event descriptions (e.g., "Alarm triggered: Front Door (Kitchen)") instead of just timestamps — fires bus event in parallel with EventEntity state change
- Release notes now auto-populated from CHANGELOG instead of empty

### Changed
- Logbook entries include device name and room when available

## [1.0.2] - 2026-04-21

### Added
- Dedicated HACS validation and hassfest workflows (required for HACS default repo submission)
- Brand directory with icon and logo
- Data sources by protocol documentation in README

## [1.0.1] - 2026-04-21

### Fixed
- Enforce minimum poll interval (60s) to prevent excessive API requests

### Added
- README badges (HACS, release, tests, license, code style)
- One-click HACS install and "Add Integration" buttons in README
- MIT LICENSE file
- SECURITY.md with responsible disclosure instructions
- CI coverage summary rendered in GitHub job summary

## [1.0.0] - 2026-04-21

### Changed
- **BREAKING**: Rebranded to **Aegis for Ajax** — domain renamed from `ajax_cobranded` to `aegis_ajax`. Users must remove and re-add the integration after updating.
- All UI strings updated to Aegis branding across 14 languages
- Repository renamed to `bvis/aegis-hass`
- Services renamed: `aegis_ajax.force_arm`, `aegis_ajax.force_arm_night`

### Added
- Automation blueprints: door opened while armed (preventive alert), remind to arm with TTS voice announcement
- Updated nobody-home-remind-arm blueprint with optional TTS support

## [0.10.0] - 2026-04-19

### Added
- Wi-Fi network sensors: SSID, signal level, and connected status via HTS protocol
- Simplified and consolidated translation files across all supported languages

## [0.9.2] - 2026-04-19

### Fixed
- Options update (e.g. FCM credentials) now triggers automatic integration reload — previously required manual HA restart

## [0.9.1] - 2026-04-19

### Fixed
- HTS incremental updates: hub network state now refreshes on delta messages (not just full settings/status bodies), preventing stale sensor values
- HTS reconnection: coordinator detects dead HTS task, clears stale network state (entities become unavailable), and reconnects on next poll cycle

## [0.9.0] - 2026-04-18

### Added
- Security events now include source device info: `device_name`, `device_id`, `device_type`, and `room_name` — enables automations to identify which device triggered an event
- Documentation: event data attributes table in README, 3 new automation examples (detailed security notification, intrusion alarm with camera capture, tamper alert)

## [0.8.4] - 2026-04-18

### Added
- 2FA (TOTP) authentication: config flow now sends the TOTP code to the Ajax API via `LoginByTotpService` — accounts with two-factor authentication enabled can now complete setup (#7)

### Fixed
- Compiled `login_by_totp` proto stubs added to the repository

## [0.8.3] - 2026-04-17

### Fixed
- Entity naming: add `translations/en.json` so HA resolves `translation_key` at runtime — fixes sensors showing device name with `_2`, `_3` suffixes instead of semantic names (#13)
- Push event routing: events now matched to correct space by hub_id instead of broadcasting to all spaces (#8)
- Photo concurrency: photo URLs now correlated to the requesting device instead of resolving all pending captures (#9)
- Photo cleanup task: properly unregistered on integration reload to prevent duplicate tasks (#10)
- Reconfigure: `unique_id` now updates when email changes (#11)
- Device hierarchy: normalized `via_device` to use `hub_id` consistently across switch, light, sensor, and binary_sensor platforms (#12)

## [0.8.2] - 2026-04-17

### Fixed
- Prevent account lockout: authentication errors (wrong password, locked account) now back off to 30-minute retry interval instead of retrying every poll cycle
- Log clear error message with instructions to reconfigure when auth fails

### Added
- "Already in progress" abort message translated in 14 languages
- "Reconfigure successful" abort message translated in 14 languages

## [0.8.1] - 2026-04-17

### Added
- **Reconfigure flow**: change email, password, or app label without removing the integration (Settings → Devices & Services → Ajax → Reconfigure)
- Translations for reconfigure step in 14 languages

## [0.8.0] - 2026-04-17

### Added
- Hub network sensors via HTS protocol (related to #2, #3, #5):
  - `binary_sensor: Ethernet` — hub ethernet link status
  - `binary_sensor: Mains power` — hub external power supply
  - `sensor: Connection type` — primary active connection (ethernet/wifi/gsm/none)
  - `sensor: Ethernet IP address` — hub ethernet IP
  - `sensor: Ethernet gateway` — hub ethernet default gateway
  - `sensor: Ethernet DNS` — hub ethernet DNS server
  - `sensor: Cellular signal` — cellular signal level (weak/normal/strong)
  - `sensor: Cellular network` — cellular network type (2g/3g/4g)
- HTS binary protocol client for real-time hub-level data not available via gRPC
- Translations for all new sensors in 14 languages (ca, cs, de, es, fr, it, nl, pl, pt, pt-BR, ro, tr, uk)
- `pycryptodome` dependency for protocol encryption
- GitHub Actions release workflow for automated pre-release/release creation on tags
- CI now runs on feature branches (`feat/**`)

### Notes
- HTS runs alongside gRPC — if unavailable, only the new network sensors show as unavailable (graceful degradation)
- No additional configuration required — reuses existing account credentials
- Only one HTS connection per account is allowed by the server (shared with the mobile app session)

## [0.7.0] - 2026-04-16

### Changed (BREAKING)
- Renamed `gsm_type` sensor to `mobile_network_type` — entity IDs will change (e.g., `sensor.*_gsm_type` → `sensor.*_mobile_network_type`)
- Renamed `signal_level` sensor to `signal_strength` — entity IDs will change
- Signal strength sensor now shows text (Strong/Normal/Weak/No signal) instead of numeric values
- SIM status sensor now shows text (OK/Missing/Malfunction/Locked) instead of numeric values

### Fixed
- Issues #4, #5, #6: sensor names are now clear and descriptive

## [0.6.6] - 2026-04-16

### Fixed
- Optimistic state now survives stale server responses for 10 seconds — prevents UI flickering/reverting after arm/disarm when the server hasn't propagated the state change yet (issue #1)
- Used `dataclasses.replace()` for safer Space state updates

## [0.6.5] - 2026-04-15

### Fixed
- Optimistic state update after arm/disarm commands prevents UI from flickering or reverting to stale state
- Timestamp overlay on captured photos now works correctly (RGBA alpha compositing)
- GitHub issue templates added for bug reports and feature requests

## [0.6.4] - 2026-04-15

### Fixed
- Integration reload no longer leaves entities unavailable (fetches device snapshot before starting streams)
- Removed verbose debug logging from push notification handler

## [0.6.3] - 2026-04-14

### Fixed
- Disarm retries automatically on `hub_busy` and `another_transition_is_in_progress` (3 attempts with 2s backoff)
- Removed "disarm from triggered state" from roadmap — no separate triggered state exists; disarm works from armed state with retry

## [0.6.2] - 2026-04-14

### Fixed
- Arm/disarm state now updates immediately in HA UI (switched from debounced to immediate refresh)
- `already_in_the_requested_security_state` errors handled gracefully instead of raising exceptions
- Improved error messages for arm/disarm failures (include server error type)

## [0.6.1] - 2026-04-14

### Added
- Media Browser integration: browse captured photos per device via HA Media Browser (Ajax Security Photos)
- Photo gallery with thumbnails, sorted newest first, photo count per device

### Fixed
- Logbook startup error (`async_describe_events` not found) resolved

## [0.6.0] - 2026-04-14

### Added
- **Photo on Demand**: working photo capture with URL retrieval via NotificationLogService media stream
- Photo storage to `/media/ajax_photos/{device}/` with timestamp overlay (date/time burned into image)
- Configurable photo retention: days (1-365, default 30) and max photos per device (0-10000, default 100)
- Photo persistence across HA restarts (last photo saved to disk per device)
- Automatic photo cleanup on startup and every 24 hours
- Photos browsable via HA Media Browser (Local media → ajax_photos)

### Changed
- Device model identifier changed from "Home Assistant" to Android model for better server compatibility
- Camera entity no longer auto-triggers captures — use the button entity for on-demand photos
- Photo capture button only shown on MotionCam PhOD models (not regular MotionCam)
- Notification ID filtering now matches by device ID for correct multi-camera support
- `DELIVERED_WAS_ALREADY_PERFORMED` response treated as success in photo capture

### Fixed
- Security API errors (arm/disarm rejected) now show proper error messages instead of HTTP 500

## [0.5.0] - 2026-04-13

### Added
- Force arm services (`aegis_ajax.force_arm`, `aegis_ajax.force_arm_night`) to arm ignoring open sensors
- Event platform for FCM push notification events (alarm, arm/disarm, tamper, panic, fire, flood, motion, and more)
- Logbook integration with human-readable security event descriptions and icons
- Glass break binary sensor for GlassProtect and CombiProtect devices
- Vibration binary sensor for DoorProtect Plus devices
- MDI icons for all entity types (`icons.json`)

### Changed
- Event parsing uses compiled protobuf definitions from the official Ajax app for accurate event identification
- Push notifications now fire HA events in addition to triggering coordinator refresh
- Tamper sensor renamed to "Case tamper" and problem sensor to "Device problem" for clarity
- Photo capture button now only shown on MotionCam PhOD models (not regular MotionCam)

### Fixed
- Security API errors (arm/disarm rejected) now show proper error messages instead of HTTP 500
- CI workflow now uses explicit `permissions: contents: read` (resolved 7 CodeQL alerts)
- Proto files excluded from coverage calculation to prevent false coverage drops

## [0.4.0] - 2026-04-13

### Added
- IMEI sensor for hub cellular modem identifier
- 11 new language translations (Ukrainian, Polish, German, French, Italian, Portuguese, Dutch, Turkish, Romanian, Czech, Brazilian Portuguese) — total 14 languages
- Example automations (21) for alerts, auto-arm, battery monitoring, and more
- Example Lovelace security dashboard (6-section panel)

### Changed
- GSM type sensor now shows text (2G/3G/4G) instead of raw number
- Removed redundant SIM status sensor (already covered by Cellular connected)

### Fixed
- SIM data now fetched on first refresh (entities created at setup)
- SIM sensors no longer use numeric state_class (string values)

### Security
- Automatic migration of legacy plaintext passwords to SHA-256 hash
- Photo URL domain validation prevents SSRF (only `*.ajax.systems` accepted)
- FCM credentials added to diagnostics redaction set
- Email removed from debug log messages
- Narrowed exception catch from BaseException to Exception
- Internal design docs removed from public repository

## [0.3.0] - 2026-04-12

### Added
- Diagnostics platform for troubleshooting (redacts sensitive data)
- Per-device connectivity binary sensor (online/offline)
- Per-device problem binary sensor (malfunctions detected)
- Hub sensors: GSM type, cellular connected, CRA monitoring, lid tamper
- 46 device type mappings (glass, combi, sirens, REX, transmitters, and more)
- Photo on-demand capture button entity for MotionCam devices
- Status parsing for 30+ device status fields
- Motion detection timestamp (`detected_at`) as attribute
- Disclaimer and legal notice in documentation

### Changed
- FCM credentials now provided by user in options flow (not hardcoded)
- Push notifications are optional — integration works without FCM config
- Hub device no longer duplicated (alarm panel shares device with hub sensors)
- Polling interval defaults to 300s (stream handles real-time)

### Fixed
- `via_device` references corrected across all entity platforms
- Security: removed sensitive data from debug logs
- Security: FCM API key no longer in source code

## [0.2.0] - 2026-04-12

### Added
- Real-time device updates via persistent gRPC stream
- Firebase Cloud Messaging (FCM) push notifications
- Device registry support with hub-peripheral hierarchy
- Entity categories and translation-based naming
- runtime_data pattern (modern HA)

### Fixed
- Config flow space selection (SelectSelector)
- Config flow login timeout (30s)
- gRPC proto version compatibility with HA's grpcio 1.78.0

## [0.1.0] - 2026-04-11

### Added
- Initial release
- Alarm control panel (arm/disarm/night mode/group arming with PIN)
- Binary sensors (door, motion, smoke, CO, heat, leak, tamper)
- Diagnostic sensors (battery, temperature, humidity, CO2, signal)
- Switches and lights for relays and dimmers
- Config flow with 2FA, co-branded app label, space selection
- Translations: English, Spanish, Catalan
- gRPC client with retry/backoff, rate limiting, session refresh
