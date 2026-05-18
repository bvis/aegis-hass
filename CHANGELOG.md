# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [1.5.0-beta.2] - 2026-05-19

Beta bump adding the SmartLock leg of the "doorbell ring" event surface. Closes the last of the three doorbell SKUs in the Ajax catalog: Wireless DoorBell (hub-level) and MotionCam Video Doorbell already routed in `1.4.5` stable; this beta adds the SmartLock / LockBridge (Yale) variant. Routed through a new `SmartLockEventQualifier` parser pass that mirrors the existing video qualifier walker, so the user-facing surface (an `event` entity firing with `event_type: doorbell_pressed` and `raw_tag: doorbell_pressed`) is identical across all three SKUs and the same automation works regardless of which hardware the user owns.

### Added
- **`doorbell_pressed` event for Ajax SmartLock / LockBridge (Yale) variants with integrated ring button** (#158, reported by @Sven2410). `SmartLockEventQualifier` from `event/smartlock/qualifier.proto` now walked as Pass 4 in `_extract_event_with_compiled_protos`; the `doorbell_pressed` oneof maps to the same HA event_type the Wireless DoorBell (hub) and MotionCam Video Doorbell (video) already fire. No new device-type registration needed — SmartLock / LockBridge devices already surface as `lock` entities since `1.2.4`. Other SmartLock tags (`locked_by_keypad`, `locked_automatically`, …) intentionally remain unmapped — those transitions already surface via the `lock` entity's state. Translations land in all 14 locales (no-op — `doorbell_pressed` was already translated for the hub/video path).

### Internal
- Test suite at **1282** unit tests (was 1281 in `1.5.0-beta.1`); coverage unchanged at the same band. One new test in `tests/unit/test_notification.py` (`test_smartlock_doorbell_pressed_resolves_to_doorbell_pressed`) uses a real `SmartLockEventQualifier` proto instance to cover Pass 4.

## [1.5.0-beta.1] - 2026-05-18

First MINOR-line beta. Bundles the regression fix for the CRA-company diagnostic sensor (rooted in `1.2.3` and only fully diagnosed today), the UX cleanup that drops the `"multiple"` sentinel in favour of the actual company names, and two foundation pieces for the modern Ajax client-version envelope: a `getMonitoringCompany`-based name resolver and a new `set_photo_on_demand_mode` service. SemVer MINOR because of the new service and the new public `MonitoringCompany.hex_id` field.

### Fixed
- **`sensor.<hub>_compania_cra` populates the CRA company name again** (#154, reported by @bogar). `CLIENT_VERSION` in `const.py` is pinned to `3.30` and `CLIENT_DEVICE_MODEL` to `SM-A536B`; the HTS `build_connect_request` defaults in `api/hts/auth.py` move in lockstep so the over-the-wire client identification stays consistent across gRPC and HTS. Empirical reproduction on a live install established that the Ajax backend gates `SpaceService.stream.monitoring_companies` (and `installation_companies`) on the `client-version-major` gRPC header: reporting `3.46` returned the list empty; reporting `3.30` returned it populated with `name` + `hex_id`. The bump from `3.30` to `3.46` landed in commits `6803852` + `891bc49` post-`1.2.3` (intent: track the real Play Store version) and silently dropped the company data on every release since.

### Added
- **`aegis_ajax.set_photo_on_demand_mode` service** that toggles a hub's Photo on Demand mode for two independent channels — `user` (whether hub users can request photos on demand from the Ajax mobile app) and `scenario` (whether scenarios / automations can trigger captures). Both fields are optional; at least one must be supplied. Underlying gRPC call (`DeviceCommandPhotoOnDemandModeService`) is idempotent, so re-sending the current state succeeds without error. Targets one or more `alarm_control_panel` entities (or every configured space when no target is given). Translations land in all 14 locales.
- **`MonitoringCompany.hex_id` public field** populated from `company_info.hex_id` on every snapshot company. Lets callers address the company on the new resolver.
- **`SpacesApi.get_monitoring_company(space_id, company_hex_id)`** wraps `SpaceMonitoringCompanyService.getMonitoringCompany`, returns a parsed `MonitoringCompany` on success and `None` on RPC error / failure branch. `get_space_snapshot` now uses it as a best-effort fallback: when a snapshot company arrives with empty `name` but a populated `hex_id`, the resolver fills in the name and the snapshot's authoritative `status` is preserved. No behaviour change for installs whose `monitoring_companies` already ship a populated name (resolver is a no-op there). Building block for eventually lifting the `CLIENT_VERSION` pin without losing the diagnostic.

### Changed
- **`sensor.<hub>_compania_cra` state shows the actual company names instead of `"multiple"`** when more than one CRA company is approved on the space. Names are joined with `", "`, sorted alphabetically so the rendered state stays stable across polls that happen to return companies in a different order (`"EXPANSIVA, PROTEGIM"` instead of `"multiple"`). Falls back to a `"N companies"` count sentinel only if the joined form would overflow the 255-char HA state limit (vanishingly unlikely with real CRA company names). `extra_state_attributes` unchanged — automations keying off `approved_companies` / `pending_approval_companies` / `pending_removal_companies` keep working untouched.

### Internal
- Test suite at **1281** unit tests (was 1263 in `1.4.5`); coverage 86.03%. Twelve new tests across `test_sensor.py` (joined-names rendering + overflow fallback), `test_devices.py` (5 new for `DevicesApi.set_photo_on_demand_mode` — single-call, scenario-disable, two-calls, requires-argument, failure-raises), `test_init.py` (4 new for the service handler — missing-channels, no-target, dispatches, skips-without-hub-id) and `test_spaces.py` (8 new for hex_id parsing + `getMonitoringCompany` success/failure/RPC-error + snapshot integration covering resolve-missing-name, skip-when-populated, resolver-failure fallback).

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

## [1.3.0-beta.11] - 2026-05-16

Eleventh beta of the `1.3.0` line. Refinement of the FCM error-classifier shipped in `beta.10`; no Ajax wire-protocol changes; no UI / Repair behaviour changes.

### Changed
- **FCM failure WARNINGs now match the strings `firebase-messaging` actually raises.** The classifier added in `beta.10` (#132) matched four substrings inferred from the library source. Validation against the library's actual runtime behaviour showed two of those branches cannot fire in production — `firebase-messaging` wraps its HTTP layer behind internal logger calls, so the `403` / `API key not valid` wording and the generic network tokens (`connection`, `timeout`, `failed to resolve`) never reach `str(exc)`. The library's public `register()` entrypoint raises three fixed `RuntimeError` strings: `Unable to establish subscription with Google Cloud Messaging.` is the dominant credential-set error (where Hansontech190's #131 landed); `Unable to register with fcm` indicates a malformed `fcm_app_id`; `Unable to register and check in to gcm` is an unambiguous network signal (the GCM checkin step uses none of the four credentials). The classifier now matches those three strings, drops the two unreachable branches, and keeps the generic fallback for any future shape change. Same `fcm_credentials_invalid` card raised in every failure path — only the log line gets sharper. (#133)

## [1.3.0-beta.10] - 2026-05-14

Tenth beta of the `1.3.0` line. Diagnostic-quality improvement on top of `beta.9`; no Ajax wire-protocol changes; no UI / Repair behaviour changes.

### Changed
- **FCM registration / push-start failures now emit a cause-specific WARNING instead of a generic stack trace.** Until now every FCM error landed in the log as `FCM registration failed` plus a traceback, leaving the user with nothing actionable. The library raises plain `RuntimeError` / `Exception` with the cause encoded only in the message string, so the listener now classifies on substrings before logging: `Unable to establish subscription with Google Cloud Messaging` points the user at credential-set consistency (the four values must come from the same Firebase project); HTTP `403` / `API key not valid` describes the expected `fcm_api_key` format (`AIza` + 35 chars); network / DNS / timeout errors name the FCM hosts the HA host needs to reach; everything else falls back to the original message wrapped in `FCM registration failed: ...` so no signal is lost. No UI / Repair changes — same `fcm_credentials_invalid` card is raised in all failure paths, only the log gets sharper. (#131)

## [1.3.0-beta.9] - 2026-05-14

Ninth beta of the `1.3.0` line. Surfaces FCM-misconfiguration as an actionable UI signal so users stop discovering it through silent real-time-event failures. No Ajax wire-protocol changes.

### Added
- **Repair card when FCM credentials are missing.** Surfaces under **Settings → Repairs** as "Push notifications not configured — real-time events disabled" with a one-click fix flow that re-uses the existing `fcm_credentials_invalid` form (Project ID / App ID / API Key / Sender ID). Real-time events (doorbell ring, arm/disarm pushes, alarm) require FCM, but until now an unconfigured install was completely silent — the only log line was at INFO level, which HA hides by default. The repair is raised on every integration start when no `fcm_api_key` is set and cleared on the first successful FCM start. Translations in all 14 locales. (#119, #129)

### Changed
- **"FCM credentials not configured" log promoted from INFO to WARNING.** Now visible in HA's default log level without enabling the integration's debug logger. Healthy installs (FCM configured and registered) remain log-silent during normal operation, so the rule becomes: no FCM line at WARNING = FCM is OK. (#119, #129)

## [1.3.0-beta.8] - 2026-05-13

Eighth beta of the `1.3.0` line. Targeted HTS-traffic fix on top of `beta.7`. No Ajax wire-protocol changes; no entity-level behaviour change for healthy installs.

### Fixed
- **HTS `sub-key 11` heartbeats no longer trigger a full snapshot refresh on every tick.** @Hansontech190 and @b0arkz observed `Hub <id>: requesting fresh HTS snapshot after unknown update sub-key 11` firing every few seconds, each time pulling a `REQUEST_FULL_SETTINGS + REQUEST_FULL_STATUS` round-trip (~8.6 KB) from the Ajax cloud. Sub-key 11 is the hub-network delta channel: longer variants (~50 byte payload) carry the anchor keys we already parse, shorter variants (~34 byte payload) only carry fields we don't surface. Both flow through the same `parse_hub_params`, so the refresh round-trip could not learn anything the next long-form delta wouldn't carry on its own. The handler now drops the short variants silently and only escalates to a snapshot refresh on genuinely unknown sub-keys. Net effect on affected installs: zero behaviour change for hub-network sensors, large drop in HTS traffic and idle CPU. (#111)

## [1.3.0-beta.7] - 2026-05-13

Seventh beta of the `1.3.0` line. Hardening pass on top of `beta.6`. No Ajax wire-protocol changes; no entity-level behaviour change for healthy installs.

### Fixed
- **A single bad device or status update no longer kills the device stream.** Before this beta, a parse exception on one `LightDevice` (or one update inside the `updates` batch) bubbled out of the stream's `async for` loop, hit the outer `except Exception` and put the task into the same exponential-backoff reconnect cycle @Permudious saw 21× in a row before #119 surfaced. The per-device `parse_device` call and the per-update handler are now each wrapped in `try/except`: the offender is logged at WARNING with `exc_info=True` so the device id and full traceback land in the logs without needing DEBUG, and the rest of the snapshot / update batch flows through normally. Same defence applies to `snapshot_update` events inside `Updates`. The per-update payload-build was extracted into `DevicesApi._handle_update` so the wrap is a one-line guard at the call site. (#126, follow-up to #119)

### Internal
- `_parse_statuses` unit tests rewritten to use real `LightDeviceStatus` proto instances instead of `MagicMock` across every sub-message branch (`signal_strength`, `gsm_status`, `sim_status`, `monitoring`, `life_quality`, `temperature`, `wire_input_status`, `transmitter_status`, `smart_lock`, `nfc`, `motion_detected`, `battery`). The MagicMock pattern that masked the original `int(sub_message)` bug in `beta.5` is gone for the high-risk branches; no new latent shape bugs surfaced during the conversion. (#126)
- New `TestSnapshotReplay` exercises the full snapshot path end-to-end: it builds a multi-device `StreamLightDevicesResponse` (including the #119 `wifi_signal_level_status` shape on a `video_edge_channel`), serialises to wire bytes, deserialises through the real proto, and replays through `start_device_stream`. A companion auto-replay loop iterates every `tests/fixtures/*.bin` — currently empty, ready for the first user-supplied capture — so future regressions on real-fleet shapes fail loudly in CI instead of waiting for a user beta. Capture/sanitisation instructions in `tests/fixtures/README.md`. (#126)

## [1.3.0-beta.6] - 2026-05-13

Sixth beta of the `1.3.0` line. Regression fix on top of `beta.5`. No Ajax wire-protocol changes.

### Fixed
- **MotionCam Video Doorbell no longer crashes the device stream in a reconnect loop.** Adding `_parse_video_edge_channel` in `beta.5` (#124) exposed a pre-existing bug in `_parse_statuses`: the `wifi_signal_level_status` branch was reading `int(status.wifi_signal_level_status)` but that field is a sub-message wrapping the actual `wifi_signal_level` enum, not a plain int. `hub_device` devices on most installs didn't surface that status, so the bug stayed dormant; `video_edge_channel` devices (like @Permudious's doorbell) do emit it, and the resulting `TypeError` killed the device-stream task on every reconnect cycle. Read the int from the nested `.wifi_signal_level` field at both call sites (snapshot parser + persistent stream handler). The previous unit test used `MagicMock` with `status.wifi_signal_level_status = 4` and silently let the buggy `int(...)` pass — replaced with a real `LightDeviceStatus` proto so this class of regression stays caught. (#125, surfaced by @Permudious in #119)

## [1.3.0-beta.5] - 2026-05-12

Fifth beta of the `1.3.0` line. Real fix for the doorbell device-card on top of `beta.4`. No Ajax wire-protocol changes.

### Fixed
- **MotionCam Video Doorbell now actually appears as a device card.** `beta.3` registered `motion_cam_video_doorbell` in the device-type map but the doorbell never reached it — the diagnostic log added in `beta.4` revealed why: the device arrives on the `video_edge_channel` oneof of `LightDevice`, not `hub_device`, and was being filtered out of `parse_device` entirely. `parse_device` now handles both cases: hub devices keep the existing path, video-edge channels read `video_edge_channel_properties.video_edge_type` (an `About.Type` enum) and emit a clean device-type string (`video_edge_doorbell`, `video_edge_indoor`, `video_edge_turret`, `_bullet`, `_minidome`, `_unknown`). HL/VF/S- sub-variants collapse to their base shape; unknown enum values map to `_unknown` so a future firmware doesn't silently drop the device. All six new keys are registered in `_DEVICE_TYPE_SENSORS` with `motion_detected + tamper`; `signal_strength` / `battery` sensors skip themselves automatically for VideoEdge channels because the proto doesn't carry those statuses. (#124, surfaced by @Permudious in #119)

### Internal
- `parse_device` split into `_parse_hub_device` and `_parse_video_edge_channel` so the two oneof paths are explicit. `hub_id` for video-edge channels is set to the channel's own id (VideoEdge bridges aren't children of a Jeweller hub in Ajax's model); keeps the HA device registry happy without threading hub context into the parser.

## [1.3.0-beta.4] - 2026-05-10

Fourth beta of the `1.3.0` line. Diagnostic-only on top of `beta.3`. No Ajax wire-protocol changes.

### Changed
- **HTS / FCM startup-failure logs are now visible at default verbosity.** Affected users in #111 reported "HTS streams: 0/1" and "FCM clients: 0/1" with empty `notification.py` and `api/hts/client.py` logs even under DEBUG. Root cause: the most diagnostic-relevant failure paths were at DEBUG level, so the actual reason a listener never came up was invisible at INFO. This release doesn't fix the underlying connection failures — we still don't know what's failing on the affected installs — but makes them visible so the next bug report can paste a useful log. HTS `connect()` exceptions now WARNING with the exception class name (full traceback preserved via `exc_info=True` for DEBUG users); missing session token now WARNING with a pointer to the earlier auth failure; pre-connect setup exceptions also promoted. The first refresh now ends with a one-line INFO summary `Aegis startup: device streams N/M started, HTS lifecycle scheduled/skipped` so the surface state is visible at a glance. On the FCM side: `firebase_messaging` not installed becomes WARNING; missing FCM credentials becomes INFO with hint; "FCM registration successful" / "FCM push client started" promoted to INFO; the no-token-after-register failure becomes WARNING with a re-extraction hint; Ajax server rejection of the push-token register also WARNING. (#122)

## [1.3.0-beta.3] - 2026-05-10

Third beta of the `1.3.0` line. New device-type slice on top of `beta.2`. No Ajax wire-protocol changes.

### Added
- **MotionCam Video Doorbell support.** The Video Doorbell, plus its sibling models `motion_cam_video_indoor` and `motion_cam_video_base`, were silently invisible in HA: the Ajax cloud sent them in every snapshot but the integration didn't have them registered, so no entities were created and HA didn't render a device card. They now appear with the standard MotionCam entity set (`motion_detected` + `tamper` binary sensors, plus the device-agnostic `signal_strength` / `battery_level`). Ring-button events are wired from both possible sources: standalone Wireless DoorBell (Jeweller ring button paired with the hub) fires `RingButtonPressed` through `HubEventQualifier`, while MotionCam Video Doorbell fires it through `VideoEventQualifier` (a new Pass 4 in the notification parser handles that). Both converge on `event_type: doorbell_pressed`, fired through the existing per-space `event.aegis_security_event` entity. Video-side `motion_detected` / `human_detected` map to "motion" so they flow through the same surface as motion sensors. Snapshot-on-press / TTS-on-press automations are standard HA from there. **Streaming video / snapshot-on-demand for the Video Doorbell stays out of scope** for this slice — the existing camera platform is photo-on-demand only and would silently fail against the doorbell's video pipeline; that's a separate follow-up. (#121, surfaced by @Permudious in #119)

### Internal
- New `VIDEO_EVENT_TAG_MAP` parallel to `HUB_EVENT_TAG_MAP` and `SPACE_EVENT_TAG_MAP`, populated with the events that have a HA-meaningful destination today (ring + motion / human detected). The long tail of video-tag events (storage errors, temporary access requests, firmware update progress, etc.) is intentionally left unmapped to avoid inflating `ALL_EVENT_TYPES`.
- All 14 translation locales carry the new `doorbell_pressed` state string.

## [1.3.0-beta.2] - 2026-05-10

Second beta of the `1.3.0` line. One bug fix on top of `beta.1`. No Ajax wire-protocol changes.

### Fixed
- **HTS hub-network sensors no longer go permanently `unavailable`** on installs whose hub firmware emits TLV escape sequences we don't recognise. @uddinr's hub was sending a `0x06 0x6A` pair inside an UPDATES payload; the strict `tlv_unescape_param` raised `ValueError` on the unknown pair, the exception bubbled out of `tlv_decode`, out of `_handle_update`, into `_run_hts_lifecycle`'s broad except — terminating the listen task and leaving every Ethernet / Wi-Fi / GSM / mains-power sensor stuck on the previous value forever (because every reconnect hit the same bad message). The parser is now lenient: an unknown `0x06 <byte>` pair is preserved as two literal bytes with a debug log, and an orphan `0x06` at the end of a segment is preserved the same way. The two known escapes (`0x06 0x35` -> `0x05`, `0x06 0x36` -> `0x06`) keep working unchanged. As belt-and-suspenders, `_handle_update` wraps `tlv_decode` in a try/except that drops the offending message instead of killing the listen loop. If `0x6A` turns out to be a third escape code we don't yet know about, the worst case is now a slightly wrong byte in one field rather than the whole HTS surface being dead. (#120, fixes #108)

## [1.3.0-beta.1] - 2026-05-09

First beta of the `1.3.0` line. New `valve` platform on top of `1.2.4`. No Ajax wire-protocol changes.

### Added
- **Read-only `valve` platform for Ajax WaterStop and WaterStop Fibra** (`water_stop`, `water_stop_base`). The `spread_properties` walker shipped in `1.2.4` (#109) already pulled every other `SpreadProperties` oneof out of `LightHubDevice`; the new `water_stop_channel` branch emits `valve_chN` (open / closed from `STATE_ON` / `STATE_OFF`), `valve_chN_transitioning` (motor moving), and `valve_chN_stuck` (channel-level `MALFUNCTION_IS_STUCK`) keys. New `AjaxValve` (`device_class = WATER`) reads them and reports `is_closed`, `is_opening`, `is_closing`, plus a `stuck` attribute. `STATE_UNKNOWN` / `STATE_UNSPECIFIED` leave the key absent so the entity renders as `unknown` rather than fabricating a closed reading on a comms hiccup. **Read-only on purpose** — there is no `SwitchWaterStopService` in the v3 protos we have, so HA cannot toggle the valve. Surfacing OPEN / CLOSE features would attach buttons that fail silently; `supported_features = 0` and `reports_position = False` keep the entity card honest. Bidirectional control follows once someone with a WaterStop captures the official app's command-side gRPC call. (#117, closes when validated on real hardware)

### Internal
- The `STATE_ON` → "valve open / water flowing" mapping follows the relay parser convention in the same walker; if real-hardware testing reveals the WaterStop firmware uses the inverted mapping, flipping the comparison in `_parse_spread_properties` is a one-line patch.
- 19 new unit tests (parser branch + entity state derivation + `device-missing-from-coordinator` defensive paths). Test suite now at 1115 passing, coverage 83.78%, `valve.py` at 100%.

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

## [1.2.4-beta.11] - 2026-05-08

Eleventh beta of the `1.2.4` line. Closes the boot-phase work on top of `beta.10`. No Ajax wire-protocol changes.

### Fixed
- **First poll cycle no longer waits on `get_devices_snapshot` before platforms can set up.** `beta.10` (#113) moved HTS handshake and FCM startup to background tasks, but the synchronous per-space `get_devices_snapshot` loop inside the first `_async_update_data` was still on the boot path — `coordinator.devices` had to be populated before `async_forward_entry_setups` ran so platforms saw real data when they created entities. On a multi-account install that round-trip was the last contributor pushing the integration past HA's *"integration taking too long"* threshold (around 30 s of pending integrations). New `DevicesCache` wraps a per-entry `Store` and persists the last-known device snapshot; on subsequent boots the first refresh tries `async_load()` first, populates `coordinator.devices` from cache on a hit, and skips the gRPC snapshot call entirely. The persistent device streams started in the same first refresh deliver a fresh snapshot via `_handle_devices_snapshot` within seconds, replacing cached values — and HA's persisted entity state already covers the very first frame anyway, so the visible window of "previous-boot state" is sub-second on a healthy install. Falls back to the heavy path on fresh install or a corrupt cache, so the worst case is unchanged. Cache writes from the stream callback go through `Store.async_delay_save` with a 30 s window so bursts of stream snapshots coalesce into one disk write rather than fanning out fire-and-forget save tasks. (#116, closes #114)

## [1.2.4-beta.10] - 2026-05-08

Tenth beta of the `1.2.4` line. One performance fix on top of `beta.9`. No Ajax wire-protocol changes.

### Fixed
- **Boot-time UX no longer trips HA's *"integration taking too long"* warning.** The first `_async_update_data` cycle was awaiting two multi-second listener startups inline — the HTS handshake (TCP connect + custom application handshake, up to 20 s with the `bb5567e` timeout from #74) and the FCM round-trip (Firebase register → Ajax push token register → start `FcmPushClient`). On a real install with both configured, that pushed the integration's setup phase past the boot threshold (~70 s end-to-end), making HA log *"Something is blocking Home Assistant from wrapping up the start up phase"* with `aegis_ajax` in the pending tasks. Two changes mirror the standard HA-premium pattern of "minimum viable first refresh, long-lived listeners on background tasks": `_start_hts()` now wraps connect-then-listen into a single background task instead of awaiting connect inline (hub-network sensors stay `unavailable` for the few seconds the handshake takes, then become available the moment it succeeds — self-reconnect logic on failure is unchanged), and `async_start_push_notifications()` is dispatched via `entry.async_create_background_task` instead of being awaited from `async_setup_entry` (push delivery already tolerates a brief gap between setup and first push). The critical path (login + `list_spaces` + `get_devices_snapshot`) stays synchronous because platforms need the device dict populated before they can register entities. (#113, closes #112)

## [1.2.4-beta.9] - 2026-05-07

Ninth beta of the `1.2.4` line. Two bug fixes on top of `beta.8`. No Ajax wire-protocol changes.

### Fixed
- **Switch / dimmer entities now reflect the actual hub state.** The on/off state of relays, sockets and light switches lives in the repeated `LightHubDevice.spread_properties` field — separate from the `LightDeviceStatus.statuses` oneof the parser already walked — so `device.statuses["switch_chN"]` was never populated and `AjaxSwitch.is_on` always read `False` regardless of what the hub was actually doing. Same path covers `LightSwitchChannel.brightness.level` for the dimmer. Symptom @EpicManeuver hit in #104: a Relay Jeweller in **Pulse** mode looked correct because the hub auto-resets the channel to off after the pulse fires (matching the parser's permanently-False reading), but in **Bistable** mode the bug was visible — toggling worked at the hub yet HA snapped back to off, and toggling the displayed-off / really-on entity did nothing visible. New `_parse_spread_properties` translates `channel` (RelayChannel), `light_switch_channel` (LightSwitchChannel — multi-gang devices arrive as multiple entries, both populate, plus optional brightness) and `socket_base_channel` (SocketBaseChannel) entries into the existing `switch_chN` / `brightness_chN` keys the entity layer already reads, so a hub-side state change for a relay / socket / light switch propagates through the next snapshot or `snapshot_update` real-time event without any further changes to the entity classes. (#109)
- **System Health card no longer shows a misleading `Reach Ajax cloud (gRPC host): unreachable`** when polling is healthy. The card was probing the gRPC host with plain HTTPS HEAD/GET via `system_health.async_check_can_reach_url`, but the Ajax gRPC endpoint doesn't respond to plain HTTPS — so the probe always returned "unreachable" even when the integration was polling the same host successfully through the actual gRPC channel. The bug only became visible after `beta.8` made the rest of the card render at all (#106). Reachability now comes from the data the card already has: any account that polled successfully within the last 10 min means the cloud is reachable from the integration's perspective. The four possible values are `reachable` (a recent successful poll exists), `unreachable` (every account's last successful poll is older), `never polled` (fresh setup, no cycle completed yet) and `no accounts configured` (empty install). (#110, surfaced by @Hansontech190 in #74)

### Internal
- The `last_update_success_time` exposed on the coordinator in `beta.8` is now load-bearing for the System Health reachability derivation, not just a display value — covered by additional regression tests so a future regression on either side fails loudly.

## [1.2.4-beta.8] - 2026-05-07

Eighth beta of the `1.2.4` line. One bug fix on top of `beta.7`. No Ajax wire-protocol changes.

### Fixed
- **System Health card no longer renders as `error: unknown`.** The card shipped in `beta.3` (#91) reads `coordinator.last_update_success_time` to render the "last poll" age, but that attribute doesn't exist on HA's `DataUpdateCoordinator` base class — only the `last_update_success` boolean does. Every access raised `AttributeError`, HA caught it generically, and the entire diagnostics row collapsed to a single red `error: unknown` line instead of the eight fields it should show (gRPC reachability, configured accounts, spaces, HTS streams alive, FCM clients alive, pushes received, last push age, last poll age). The unit tests "covered" this path by setting the attribute on a `MagicMock`, which silently created it and let the assertions pass even though the real coordinator never had it. Fix is minimal: `AjaxCobrandedCoordinator` now exposes a `last_update_success_time` property backed by `dt_util.utcnow()` set at the two success-return sites in `_async_update_data`. Failure paths leave it untouched, so "last poll: 2h ago" really means 2h since the last successful poll. Regression tests in `test_coordinator.py` construct the real coordinator class (no MagicMock) and assert the attribute exists, returns `None` pre-poll, advances after a successful update, and stays untouched on failure. (#106, surfaced by @Hansontech190 while triaging #74)

## [1.2.4-beta.7] - 2026-05-07

Seventh beta of the `1.2.4` line. Finishes a long-standing TODO that ate every device-control click in the integration. No Ajax wire-protocol changes; only fills in the gRPC dispatch that was missing.

### Fixed
- **Switches and dimmer brightness now actually act on the hub.** `DevicesApi.send_command` was a `NotImplementedError` placeholder since the integration's first release: `AjaxSwitch` (Relay Jeweller, WallSwitch, every Socket and LightSwitch variant) and `AjaxLight` (LightSwitch Dimmer brightness slider) registered correctly, exposed the on/off and brightness controls in the UI, and then surfaced `Failed to perform the action switch/turn_on. Device commands not yet implemented (action=on, device=<id>)` to the user on every click. The dispatcher now routes `on` → `DeviceCommandDeviceOnService.execute`, `off` → `DeviceCommandDeviceOffService.execute`, and `brightness` → `DeviceCommandBrightnessService.execute` (with `BRIGHTNESS_TYPE_ABSOLUTE`, matching HA's slider semantics). Failure responses (`hub_offline`, `hub_busy`, `permission_denied`, `hub_wrong_state`) now bubble up as `DeviceCommandError(<error>)` so HA shows a proper service-call error toast instead of an opaque `NotImplementedError`. (#104)

### Internal
- New `_build_object_type(device_type)` helper marks the matching empty-marker oneof case on the v2 `ObjectType` proto via `SetInParent()`, so the strings produced by `parse_device` (`relay`, `wall_switch`, `socket_*`, `light_switch_*`, etc.) round-trip back into a valid command request without a separate mapping table.
- `DeviceCommandError` exception type for the new failure path; `SmartLockError` (added in `beta.6`) keeps its dedicated subclass.

## [1.2.4-beta.6] - 2026-05-06

Sixth beta of the `1.2.4` line. New `lock` platform on top of `beta.5`. No Ajax wire-protocol changes.

### Added
- **`lock` platform for Ajax SmartLock and Yale LockBridge** (device types `smart_lock` and `smart_lock_yale`). The integration now exposes a native HA `lock.*` entity per smart lock with three operations wired to the v3 `SwitchSmartLockService.execute` gRPC endpoint: `lock.lock` → `LOCK`, `lock.unlock` → `UNLOCK`, and HA's `lock.open` → `UNLATCH` (pull the latch without keeping the bolt thrown — same semantics as the Ajax mobile app's "unlatch" button). State (locked / unlocked / unlatched) is parsed from the `LightDeviceStatus.smart_lock` `LockStatus` oneof and refreshes both via the hourly poll snapshot and via real-time status updates on the persistent device stream. gRPC failures (lock offline, permission denied, etc.) are caught and logged so a failed call doesn't crash automations; the next coordinator refresh re-syncs the displayed state. (#102)

### Internal
- New `SmartLockError` exception type and `DevicesApi.switch_smart_lock(space_id, smart_lock_id, action)` wrapping the gRPC stub. `SMART_LOCK_ACTION_LOCK` / `_UNLOCK` / `_UNLATCH` constants exported from `api/devices.py` for callers.
- `_STATUS_KEY_MAP` in the coordinator gains `smart_lock → smart_lock_state` so streamed status updates land on the same key the snapshot parser uses.
- `Platform.LOCK` added to the integration's `PLATFORMS` list, bringing the platform count from 8 to 9.

## [1.2.4-beta.5] - 2026-05-06

Fifth beta of the `1.2.4` line. One additive entity slice on top of `beta.4`. No Ajax wire-protocol changes.

### Added
- **`tilt` and `steam` binary sensors** filling out the device-type matrix. `tilt` (TAMPER device class) is exposed on every DoorProtect Plus variant — `door_protect_plus`, `door_protect_plus_fibra`, `door_protect_s_plus`, `door_protect_plus_g3_fibra` — surfacing the accelerometer's anti-removal status alongside the existing `vibration` (knock) entity, so automations can distinguish the sensor being pried off the wall from someone slamming the door. `steam` (PROBLEM device class) is exposed on every FireProtect 2 variant whose smoke chamber is physically present (`fire_protect_2`, `fire_protect_two`, `fire_protect_two_base`, `fire_protect_two_plus`, `fire_protect_two_plus_sb`, `fire_protect_two_sb`, `fire_protect_two_hcrb`, `fire_protect_two_hcsb`, `fire_protect_two_hs_ac`, `fire_protect_two_hsc_ac` and the matching `*_ul` UL-listed siblings) — chamber-level steam-vs-smoke discriminator that lets automations gate on real smoke instead of firing on shower / cooking steam false positives. Heat-only / CO-only sub-models and `range_extender_2_fire` stay without `steam` because they have no smoke chamber. (#101)

### Internal
- All 14 translation locales carry the two new `binary_sensor.tilt` / `binary_sensor.steam` strings; technical wording matches the README's binary-sensor inventory in every language.

## [1.2.4-beta.4] - 2026-05-05

Fourth beta of the `1.2.4` line. Two HA-platform additions on top of `beta.3`. No Ajax wire-protocol changes; both replace silent log lines with first-class HA UX.

### Added
- **DHCP discovery for Ajax hubs on the local LAN.** When a hub broadcasts a DHCP packet from the registered Ajax Systems OUI `9C:75:6E`, the integration now appears as a **Discovered** card under Settings → Devices & Services with the hub's hostname / IP in the title — no more searching for "Aegis" by name. Clicking through forwards into the existing credential prompt; subsequent DHCP renewals don't spam the discovery list (per-MAC dedupe on the flow + `already_configured` abort once an entry exists). Single OUI in this slice, captured from a real Hub Plus and verified via the IEEE registry as Ajax Systems DMCC; OUIs from other hub families can be added in one-line follow-up commits as users contribute their MACs. (#92)
- **`fcm_credentials_invalid` Repair is now fixable in-place.** The Repair card promoted from `is_fixable=False` (informational, told the user to detour through the Options menu) to `is_fixable=True` with a dedicated `RepairsFlow`. Click Submit on the Repair card → guided form with the four FCM fields pre-filled with the currently-stored (broken) values → Submit → the integration reloads with the new credentials. If they work the Repair clears in `notification.async_start()`; if they're still wrong the same Repair re-raises and the user can try again. Defensive abort path covers the entry-removed-while-Repair-open race. The other two Repairs (`hub_offline_24h`, `hts_chronic_failure`) stay informational because their fix is physical (hub power, firewall). (#89 follow-up)

### Internal
- New `FcmCredentialsRepairFlow(RepairsFlow)` + module-level `async_create_fix_flow(hass, issue_id, data)` discovery hook on `repairs.py`. The fix flow recovers the entry id either from the issue's `data` field or by parsing the namespaced `issue_id` as a defensive fallback.
- New `async_step_dhcp(discovery_info)` on `config_flow.py`. Sets `format_mac(macaddress)` as the *flow's* `unique_id` (the eventual entry's stays as email) so HA dedupes repeat DHCP packets.
- All 14 translation locales carry the new strings (`fix_flow.step.init` + `fix_flow.abort.entry_missing` for the FCM repair). Other locales fall back to English form labels for FCM tokens (Project ID / App ID / API Key / Sender ID), which match the README verbatim in every language.

## [1.2.4-beta.3] - 2026-05-05

Third beta of the `1.2.4` line. Three quality-of-life additions in the
HA-platform direction — none touch the Ajax wire protocol, all replace
silent `home-assistant.log` errors with first-class HA UX.

### Added
- **Reauth flow.** When the Ajax session is rejected (password rotated, account session revoked, 2FA newly enabled, server-side forced logout), the integration now raises `ConfigEntryAuthFailed` instead of `UpdateFailed`. Home Assistant reacts by showing the orange **Reconfigure** banner on the integration card and dispatching the new `async_step_reauth` config-flow path: a single password prompt (with optional TOTP step) that keeps the same `unique_id`, so all entity ids, areas, automations and history survive untouched. Plaintext password never lands on the entry; the freshly minted session token is persisted for the next restart. Email-rename stays under the existing reconfigure flow's responsibility. (#90)
- **HA Repairs surface for diagnosable conditions.** Three new Repair cards under **Settings → Repairs** — `hub_offline_24h` (a space reported OFFLINE on consecutive snapshots for 24h+; clears as soon as ONLINE returns), `hts_chronic_failure` (HTS reconnect has been failing for 30 min straight; clears on the next successful reconnect), and `fcm_credentials_invalid` (`firebase_messaging` rejected the configured api_key/app_id/project_id/sender_id; clears at every `async_start()` so updating credentials in Options re-evaluates from scratch). All three are `is_fixable=False` in this slice — descriptions tell the user where to act — with stable per-scope `issue_id` namespacing so multi-space / multi-account installs don't collide. (#89)
- **System Health card.** `Settings → System → Repairs → System Information` now shows a one-line snapshot for Aegis: gRPC host reachability via `system_health.async_check_can_reach_url`, configured-account count, total spaces, ratios of HTS streams and FCM clients alive (`N/M`), total non-deduped pushes received since startup, and humanised "last push" / "last successful poll" ages. Replaces log archaeology as the first triage step for "events stopped arriving" reports. (#91)

### Internal
- Coordinator gains `is_hts_connected` property + `_first_offline_at` / `_hts_first_failure_at` tracking. `AjaxNotificationListener` gains `pushes_received` / `last_push_at` / `is_fcm_connected` properties and an `entry_id` argument so the FCM repair scopes per-account. New `repairs.py` helper module wraps `homeassistant.helpers.issue_registry` with the domain pre-bound and stable per-scope ids.
- All 14 translation locales (ca, cs, de, en, es, fr, it, nl, pl, pt-BR, pt, ro, tr, uk) now carry the new `reauth_confirm`, `reauth_2fa`, `issues.*`, and `system_health.info` strings. Best-effort translations; technical tokens (FCM Project ID, MotionCam, security_event, README, HTS, Wi-Fi, gRPC, Home Assistant) kept in English so users find the same string in the docs.

## [1.2.4-beta.2] - 2026-05-02

Second beta of the `1.2.4` line. Three end-to-end fixes on top of `beta.1`'s per-group alarm panels, all surfaced by @Cingar01 testing the real Hub Hybrid 4G in Zone Mode (#86).

### Fixed
- `arm_group` / `disarm_group` no longer fail with `module ... has no attribute 'ArmGroupRequest'`. The gRPC client referenced `ArmGroupRequest` / `DisarmGroupRequest` but the actual proto classes are `ArmSpaceGroupRequest` / `DisarmSpaceGroupRequest`. Arming any group from Home Assistant under `beta.1` was broken; with this release it works against the real `SpaceSecurityService/armGroup` and `disarmGroup` endpoints. The new `TestGroupProtoIntegration` regression suite exercises the real proto module so this class of drift between `security.py` and the generated descriptors fails loudly instead of silently passing through `MagicMock` — addressing the gap that let `beta.1` ship the bug.
- Per-group alarm panels no longer flap to `unavailable` between hourly snapshot refreshes. The coordinator's poll path uses `list_spaces()` which doesn't return groups, and was overwriting the cached `Space` with one whose `groups=()` and `group_mode_enabled=False`. Only the hourly snapshot populates groups, so per-group panels were unavailable for almost every poll cycle, with empty `extra_state_attributes`. The coordinator now preserves `groups` and `group_mode_enabled` from the previous `Space` across polls in the same merge step that already preserves `monitoring_companies`.
- The whole-space alarm panel no longer disappears when Group/Zone Mode is enabled. `beta.1` replaced the space-level panel with per-group panels; users lost access to night mode (Ajax exposes night mode only space-wide — there is no `armGroupToNightMode` endpoint) and the pre-upgrade `aegis_ajax_alarm_<space>` entity from `1.2.3` was orphaned in the registry as `restored: true`. The integration now always creates the whole-house panel and additionally adds per-group panels when Group Mode is on, giving users `1 + N` panels per space: night-mode through the whole-house one, independent arm/disarm per group through the rest.

## [1.2.4-beta.1] - 2026-05-01

First beta of the `1.2.4` line. Adds initial support for Ajax Group / Zone Mode and clarifies the FCM credential extraction docs. Needs real-hardware validation from Group/Zone-Mode users before promotion to stable.

### Added
- New `alarm_control_panel` entity per Ajax security group when the space is in **Group Mode** (also exposed as **Zone Mode** on newer hub firmwares — same `GroupSecurity` proto under the hood). Each entity arms/disarms its group independently via the existing `armGroup` / `disarmGroup` gRPC calls. State attributes include `group_id`, `group_name`, `space_id`, `hub_id`, `connection_status` so automations can target a single group ("turn off outlets only when *Villa* arms"). Spaces in regular (non-group) mode keep the existing single space-wide panel — no entity churn for those installs. Per-group state refreshes via the hourly snapshot path plus optimistic local updates on HA-initiated arm/disarm; remote arms from the Ajax mobile app may take up to one hour to reflect in HA in this initial slice. A long-lived `SpaceService/stream` subscription for real-time per-group updates is intentionally deferred to a follow-up. Night mode is not exposed on per-group panels because the underlying flag is space-wide on Ajax. (#84, #86)

### Fixed
- Documentation: the README's "How to obtain FCM credentials" section was ambiguous about where to look for the four Firebase values and pointed users at a placeholder for `google_api_key`. The section now distinguishes the three values that live together from the API key, which is stored elsewhere. (#83)

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

## [1.2.1-beta.10] - 2026-04-27

### Changed
- The alarm control panel now reflects external arm/disarm/night-mode events in real time when FCM is configured: the integration applies the new `security_state` directly from the parsed push payload instead of waiting for the next poll cycle (up to 5 min). HA-initiated optimistic state still wins inside its 10s race window, and `group_*` tags keep falling through to the regular refresh path because their resulting space-level state depends on the other groups. (#68, #46)

## [1.2.1-beta.9] - 2026-04-27

### Fixed
- The Transmitter Jeweller exposed the new `wire_input_alert` entity from beta.8 but it never toggled, because the device emits the bridged sensor's alert through the `transmitter_status` proto oneof (field 75) rather than `wire_input_status` (field 74). Both oneofs are now handled identically by the snapshot parser, the stream forwarder, and the coordinator's status update / REMOVE paths, so the entity reflects the wired sensor's state in real time. (#65)

## [1.2.1-beta.8] - 2026-04-27

### Fixed
- The Transmitter Jeweller (Ajax `transmitter`), which bridges a third-party wired sensor to the hub, now exposes a `wire_input_alert` binary sensor in addition to the case tamper, so the intrusion line of the bridged sensor is reflected in HA. The entity OR-reduces the three oneofs hub firmwares may use to signal the wired alert (`wire_input_status`, `external_contact_broken`, `external_contact_alert`), matching the existing handling for `wire_input` / `wire_input_mt`. (#65)

## [1.2.1-beta.7] - 2026-04-27

### Fixed
- REMOVE events on the device stream now clear every `device.statuses` key the snapshot parser writes for that status, not just the one matching the proto field name. Previously `life_quality`, `gsm_status`, and `motion_detected` left stale sub-keys behind (`temperature`/`humidity`/`co2`, `mobile_network_type`/`gsm_connected`, `motion_detected_at`) that lingered until the next full snapshot. Centralised the parent-to-extra-keys mapping so future additions stay in sync. (#61)

## [1.2.1-beta.6] - 2026-04-27

### Fixed
- Numeric/structured sensor values streamed in real time (temperature, humidity, CO2, signal strength, GSM/SIM/NFC/Wi-Fi diagnostics) were being overwritten with `True` whenever an ADD/UPDATE event arrived between snapshots, causing temperature entities to drop to `1 °C` intermittently on `DoorProtect Plus` and `MotionCam` devices among others. The stream parser now extracts the actual values (mirroring the snapshot path) and the coordinator applies them as scalars or sub-keys instead of coercing every non-binary update to a boolean. (#59)

## [1.2.1-beta.5] - 2026-04-27

### Added
- Each Ajax device entry in Home Assistant now exposes its hardware identifier as the device `serial_number`, so users can locate sensors physically without walking around triggering each one. Combined with the new `suggested_area` (mapped from the device's Ajax room), HA can auto-assign devices to matching areas the first time the integration is set up. (#55)

## [1.2.1-beta.4] - 2026-04-26

### Fixed
- Reloading the integration no longer accumulates new active sessions in the user's Ajax account. Three related gaps closed: (a) the latest session token is now persisted back to the config entry after every successful login (it used to be saved only on the initial config flow), (b) the coordinator detects `UNAUTHENTICATED` errors from the gRPC API, forces a fresh login, persists it and retries the failed call once instead of falling out as `UpdateFailed`, and (c) when the user permanently removes the integration, `LogoutService.execute` is now called server-side via the new `async_remove_entry` hook so the dangling session disappears from the Ajax account too. Reload paths still leave the session alive on purpose so the next setup can reuse the token. (#53)

## [1.2.1-beta.3] - 2026-04-26

### Fixed
- FireProtect 2 detectors no longer fall back to a tamper-only entity set: Ajax's hub catalog uses the `fire_protect_two*` naming for the current generation while we only knew the legacy `fire_protect_2`, so smoke / heat / CO binary sensors were never created. All FireProtect 2 variants known to the v3 ObjectType (`fire_protect_two`, `_plus`, `_sb`, `_hcrb`, `_hcsb`, `_hrb`, `_hsb`, `_crb`, `_csb`, `_h_ac`, `_c_ac`, `_hc_ac`, `_hs_ac`, `_hsc_ac`, plus the UL-listed sub-models) now map to the appropriate sensor set, with single-sensor variants exposing only the relevant entity. (#51)

### Changed
- Audited `_DEVICE_TYPE_SENSORS` and `SWITCH_DEVICE_TYPES` against the current Ajax v3 ObjectType catalog and added missing aliases that were silently falling back to a tamper-only entity set:
  - **Hub family** — `hub_two`, `hub_two_plus`, `hub_two_lte_rtk`, `hub_three`, `hub_4g`, `hub_lite`, `hub_fibra`, `hub_hybrid_2`, `hub_hybrid_4g`, `hub_mega`, `hub_void_4g`, `hub_yavir`, `hub_yavir_plus`, `hub_fire`, `hub_superior`. The existing `hub`, `hub_plus`, `hub_two_4g` entries are kept for backwards compatibility.
  - **Range extender** — `range_extender`, `range_extender_2`, `range_extender_2_fire` (the `rex` / `rex_2` legacy keys are kept).
  - **DoorProtect** — `door_protect_plus_g3_fibra`.
  - **MotionProtect / MotionCam** — added `motion_protect_g3`, `motion_protect_g3_fibra`, `motion_protect_g3_fibra_new`, `motion_protect_plus_g3`, `motion_protect_plus_fibra`, `motion_protect_s`, `motion_protect_s_plus`, `motion_protect_curtain_base`, `motion_protect_curtain_outdoor_base`, `motion_protect_curtain_outdoor_mini`, `motion_protect_curtain_outdoor_plus`, `motion_cam_g3`, `motion_cam_hd`, `motion_cam_fibra_base`, `motion_cam_phod_fibra`, `motion_cam_outdoor_phod`, `motion_cam_outdoor_two_four_phod`, `motion_cam_s_phod`, `motion_cam_s_phod_am`, `motion_cam_superior_phod`.
  - **Sirens** — `home_siren_g3`, `street_siren_plus_fibra`, `street_siren_plus_g3`, `street_siren_s`, `street_siren_s_double_deck`, `street_siren_double_deck_fibra`.
  - **Wire input** — `wire_input_rs`.
  - **Keypads** — `keypad_plus`, `keypad_plus_g3`, `keypad_s_plus`, `keypad_outdoor`, `keypad_outdoor_fibra`, `keypad_touchscreen`, `keypad_touchscreen_fibra`, `keypad_touchscreen_g3`.
  - **Life quality / water stop** — `life_quality_plus`, `water_stop_base`.
  - **Switches** (`switch.py`) — `relay_fibra_base`, additional socket variants (`socket_b`, `socket_g`, `socket_outlet_type_e`, `socket_outlet_type_f`, `socket_type_g_plus`) and light-switch wiring variants (`light_switch_one_gang`, `_one_gang_na`, `_2_way`, `_crossover`, `_three_way_na`, `_two_channel_two_way`, `_four_way_na`).

## [1.2.1-beta.2] - 2026-04-26

### Added
- New service `aegis_ajax.press_panic_button` that triggers the Ajax SOS / panic button on a space (same endpoint the official mobile app's red SOS button uses). Requires an explicit `confirm: true` field at call time as a safety lock; pressing the button forwards a Panic / Hold-up alarm to the monitoring station (CRA) and on most contracts triggers immediate police dispatch with no verification window — see the README for the legal/financial caveats and the recommended Transmitter-based path for non-emergency automation triggers. (#48)

## [1.2.1-beta.1] - 2026-04-26

### Added
- New optional `auto_create_labels` toggle in the integration's options. When disabled the integration no longer recreates and reassigns the `aegis_*` labels on every restart, so users who manage Home Assistant labels manually can clean them up without having them come back. Default stays enabled to preserve previous behaviour. (#47)

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
