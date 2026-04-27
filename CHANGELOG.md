# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

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
