# Aegis for Ajax — Home Assistant Integration

[![HACS Custom](https://img.shields.io/badge/HACS-Custom-orange.svg)](https://github.com/hacs/integration)
[![GitHub Release](https://img.shields.io/github/release/bvis/aegis-hass.svg)](https://github.com/bvis/aegis-hass/releases)
[![Tests](https://github.com/bvis/aegis-hass/actions/workflows/ci.yml/badge.svg)](https://github.com/bvis/aegis-hass/actions/workflows/ci.yml)
[![Validate with hassfest](https://github.com/bvis/aegis-hass/actions/workflows/hassfest.yaml/badge.svg)](https://github.com/bvis/aegis-hass/actions/workflows/hassfest.yaml)
[![HACS Validation](https://github.com/bvis/aegis-hass/actions/workflows/validate.yaml/badge.svg)](https://github.com/bvis/aegis-hass/actions/workflows/validate.yaml)
[![License: MIT](https://img.shields.io/github/license/bvis/aegis-hass.svg)](LICENSE)
[![Code style: ruff](https://img.shields.io/badge/code%20style-ruff-000000.svg)](https://github.com/astral-sh/ruff)

> **Disclaimer**: This is an **unofficial** third-party integration and is not affiliated with, endorsed by, or supported by Ajax Systems. Use at your own risk. This integration communicates with Ajax Systems servers using the same protocol as the official mobile app. Ajax Systems may change their API at any time, which could break this integration without notice.

**Aegis** is a Home Assistant custom integration for **Ajax Security Systems** — works with any co-branded Ajax app (Protegim, ADT, G4S, and many more).

Communicates via **gRPC** (the same protocol the official mobile app uses). No Enterprise API key required — just your regular account credentials.

## How It Works

Ajax Systems provides co-branded versions of their mobile app to security companies worldwide. Each co-branded app connects to the same Ajax cloud backend but uses a unique **application label** to identify itself. This integration emulates the mobile app's gRPC protocol, so it works with any co-branded variant.

**You need to know the application label of your Ajax provider.** This is an internal identifier that the app sends to the Ajax cloud (see the Known App Labels table below). If you use the main Ajax app, the label is `Ajax`.

## Features

- **Alarm Control Panel**: Arm away, disarm, night mode, group arming with PIN code support. When the space is in group / zone mode, per-group panels are created and react to FCM push events instantly — arming or disarming a single group from the Ajax mobile app reflects in HA within seconds (whole-space transitions already worked this way; group-level transitions joined in `1.5.0`)
- **Force Arm Services**: `aegis_ajax.force_arm` and `aegis_ajax.force_arm_night` to arm ignoring open sensors
- **Binary Sensors**: Door open/close, motion detection, smoke, steam (FireProtect 2 chamber discriminator), leak, tamper, CO, heat, glass break, vibration, tilt (DoorProtect Plus accelerometer), CRA monitoring status, cellular connection, lid tamper, external contact alert (wired reed switches on DoorProtect/Hub Hybrid inputs), external contact fault, MultiTransmitter wired-input alert with alarm category, anti-masking, interference detection, ethernet link, Wi-Fi link, mains power
- **Hub Network**: Real-time hub network data — ethernet/wifi/gsm connection status, Wi-Fi SSID and signal strength, IP addressing, cellular signal strength and network type, power supply status
- **Sensors**: Battery level, temperature, humidity, CO2, signal strength, GSM type (2G/3G/4G), Wi-Fi signal level, Wi-Fi SSID, Wi-Fi IP, IMEI, Ethernet IP/gateway/DNS, cellular signal/network, connection type
- **Electrical readings** for WallSwitch / Socket family devices: live current draw (A), measured line voltage (V) when the firmware reports it, cumulative electric energy consumed (kWh, wired into HA's Energy dashboard via `state_class=total_increasing`), and instantaneous power (W). Power is a direct firmware reading on the Outlet Type E / F family and a derived `current × measured voltage` reading (opt-in, with a 230 V nominal fallback) on the WallSwitch family. Updates arrive on the hub's push channel; a per-hub 60 s STATUS refresh fills the gap on device families whose firmware emits live deltas sparsely (Outlet Type E / F). The sensors also persist across HA restarts so a constant load like a fixed-speed pump doesn't render as `unknown` after a reboot until the next state change.
- **Manual hub refresh button**: one `button.<hub>_refresh_hub` per configured hub. Pressing it (or calling `button.press` from an automation) forces an immediate STATUS refresh from the hub — useful after toggling an appliance, when waiting for the next 60 s periodic tick would feel sluggish. Rate-limited to one press per 60 s per hub so a stuck automation can't generate unusual traffic against Ajax's servers.
- **Switches**: Relays, wall switches, sockets (multi-channel support) — turn on/off via `DeviceCommandDeviceOn` / `DeviceCommandDeviceOff` gRPC services
- **Lights**: Dimmers with absolute brightness control via `DeviceCommandBrightness`
- **Locks**: Ajax SmartLock and LockBridge (Yale) — lock, unlock, and unlatch (HA's `lock.open`) via `SwitchSmartLockService`
- **Valves** (read-only): Ajax WaterStop and WaterStop Fibra surface as native `valve.*` entities reflecting `WaterStopChannel.state` (open / closed), `is_transitioning`, and a `stuck` attribute pulled from the channel-level `MALFUNCTION_IS_STUCK`. Bidirectional control waits on capturing the official app's command-side calls — file an issue with a packet capture if you have a WaterStop and we'll wire the open / close path
- **Hub firmware update** (read-only): each hub exposes an `update.<hub>_firmware` entity that shows whether Ajax has queued a firmware update for the hub, with download progress when the cloud is pushing bytes. No install button is exposed on purpose — firmware updates remain Ajax-scheduled and the entity is informational. Click the entity for a short explainer of what "Up-to-date" actually means here.
- **Cameras**: MotionCam Photo on Demand — capture photos and view them in HA (PhOD models only)
- **Photo Storage**: Captured photos saved to `/media/ajax_photos/` with timestamp overlay, configurable retention
- **Media Browser**: Browse captured photos per device via HA Media Browser
- **Event Platform**: Security events from FCM push notifications (alarm, arm/disarm, tamper, panic, fire, flood, motion, and more)
- **Logbook**: Human-readable security event descriptions with icons
- **Real-time updates**: Persistent gRPC stream for instant sensor state changes (< 1 second latency)
- **Push notifications**: FCM integration for immediate event delivery
- **2FA support** (TOTP)
- **Reauth flow**: when the Ajax session is rejected (password rotated, 2FA newly enabled, server-side logout), HA shows the orange "Reconfigure" banner with a guided password prompt — entity ids, areas, automations and history survive untouched
- **HA Repairs**: diagnosable conditions surface as cards under **Settings → Repairs** instead of being buried in `home-assistant.log` — hub offline > 24h, sustained HTS reconnect failure, FCM credentials rejected (with one-click fix flow), or grpcio version below the floor on Home Assistant OS
- **System Health card**: one-line snapshot under **Settings → System → Repairs → System Information** with gRPC reachability, HTS / FCM connection ratios, last push / last poll ages — replaces log archaeology as the first triage step
- **DHCP discovery**: Ajax hubs on the same LAN appear as a "Discovered" card in **Settings → Devices & Services**, no need to search by name
- **MDI icons** for all entity types
- **Automation Blueprints**: Ready-to-use automation templates (see below)

## Requirements

- Home Assistant 2024.1.0 or later
- An Ajax Security account (email + password)
- At least one Ajax hub online
- The **application label** of your co-branded Ajax app

## Installation

### HACS (Recommended)

[![Open HACS Repository](https://my.home-assistant.io/badges/hacs_repository.svg)](https://my.home-assistant.io/redirect/hacs_repository/?owner=bvis&repository=aegis-hass&category=integration)

Click the button above, or manually:

1. Open HACS in Home Assistant
2. Click the three dots menu (top right) and select **Custom repositories**
3. Add `https://github.com/bvis/aegis-hass` with category **Integration**
4. Search for "Aegis for Ajax" in HACS and click **Install**
5. Restart Home Assistant
6. Add the integration:

[![Add Integration](https://my.home-assistant.io/badges/config_flow_start.svg)](https://my.home-assistant.io/redirect/config_flow_start/?domain=aegis_ajax)

### Manual

1. Download this repository
2. Copy `custom_components/aegis_ajax/` to your Home Assistant `config/custom_components/` directory
3. Restart Home Assistant
4. Go to **Settings > Devices & Services > Add Integration** and search for "Aegis for Ajax"

## Configuration

1. Enter your Ajax account **email** and **password**
2. Enter the **App Label** of your co-branded app (see table below, or type your own)
3. If 2FA is enabled, enter your TOTP code
4. Select which spaces (hubs) to add
5. Done

### Options

After setup, configure these in **Settings > Devices & Services > Aegis for Ajax > Configure**:

| Option | Default | Description |
|---|---|---|
| Poll interval | 300s | Fallback polling interval, allowed range 60-300 seconds (real-time stream handles most updates) |
| Force arm | disabled | Arm ignoring open sensors and malfunctions (bypasses hub safety checks) |
| PIN code | disabled | Require PIN for arm/disarm from HA UI |
| FCM credentials | — | Firebase credentials for push notifications (optional) |
| Photo retention (days) | 30 | How many days to keep captured photos (1-365) |
| Max photos per device | 100 | Maximum photos stored per camera (0 = unlimited) |
| Auto-create labels | enabled | Create and assign `aegis_*` labels (camera, hub, door, motion, …) to your entities for easy grouping in dashboards/automations. Disable if you prefer to manage labels manually — with the option enabled the integration re-creates the labels on every restart. |
| Bypass switches | auto | Whether to create a `bypass` switch per device. `auto` only creates them if your Ajax account has the `DEVICE_EDIT` permission (required to deactivate devices); `always` creates them unconditionally; `never` skips them. **Note:** Home Assistant does not auto-remove entities when a platform stops providing them — if switching from `always` (or upgrading from an older version that always created them) to `auto`/`never` on an account that can't deactivate devices, the existing `switch.*_bypass` entries linger as `unavailable` until you delete them manually (Settings → Devices & Services → device → entity → Delete). |

### Known App Labels

Each co-branded Ajax app uses an internal **label name** to identify itself to the Ajax cloud. This label is not always the same as the app's display name. The integration includes all known labels in a dropdown during setup.

| App Label | App Name | Region |
|---|---|---|
| `Ajax` | Ajax Security System | Worldwide |
| `ajax_pro` | Ajax PRO | Worldwide |
| `AIKO` | AIKO | Estonia |
| `3dAlarma` | 3D Alarma | Spain |
| `E-Pro` | E-Pro | — |
| `G4S_SHIELDalarm` | G4S SHIELDalarm | Europe |
| `GSS_Home` | GSSecurity | — |
| `HomeSecure` | HomeSecure | — |
| `Hus_Smart` | Hus Smart | Scandinavia |
| `Novus_alarm` | Novus | — |
| `Protegim_alarma` | Protegim | Spain |
| `Protecta` | Protecta | — |
| `SecureAjax` | SecureAjax | — |
| `Smart_Secure` | Smart & Secure | — |
| `Verux` | Verux | — |
| `Videotech_alarm` | Videotech | — |
| `kale_alarm_x` | Kale Alarm X | Turkey |
| `ADT_Alarm` | ADT Alarm | — |
| `ADT_Secure` | ADT Secure | — |
| `Yoigo_ADT_Alarma` | Yoigo ADT Alarma | Spain |
| `Masmovil_ADT_Alarma` | Másmóvil ADT Alarma | Spain |
| `Euskaltel_ADT_Alarma` | Euskaltel ADT Alarma | Spain |
| `Elotec` | Elotec Ajax | Norway |
| `Yavir` | Yavir | Ukraine |
| `Oryggi` | Oryggi | Iceland |
| `acacio` | acacio | — |
| `esahome` | esahome | — |

### How to find your app label

If your provider is not in the list above, you can find the correct label by:

1. **Check the app's Google Play URL** — search for the package name (e.g. `com.ajaxsystems.yourapp`) and cross-reference with the table
2. **Inspect network traffic** — the app sends its label in the `application-label` gRPC metadata header on every request
3. **Check the app's resources** — the label is stored as `ajax_app_name` in `strings.xml`

You can type any custom label during setup if yours is not listed.

## Supported Devices

| Type | Devices | Entities |
|---|---|---|
| Hub | Hub, Hub Plus, Hub 4G, Hub Lite, Hub 2, Hub 2 Plus, Hub 2 4G, Hub 3, Hub Hybrid (2 / 4G), Hub Mega, Hub Fibra, Hub Yavir / Yavir Plus, Hub Fire, Hub Superior | Alarm panel, battery, GSM type/connected, CRA monitoring status, CRA company (diagnostic), lid tamper, IMEI, hub network sensors (Ethernet/Wi-Fi/GSM, IP data, cellular signal/network, mains power) |
| Door Sensors | DoorProtect, DoorProtect Plus, DoorProtect Fibra, DoorProtect S, DoorProtect S Plus, DoorProtect Plus Fibra, DoorProtect Plus G3 Fibra, DoorProtect G3 | Door open/close, tamper, vibration (Plus), tilt (Plus, accelerometer), battery, temperature, signal, external contact alert (wired contact triggered), external contact fault (wiring broken) |
| Motion Sensors | MotionProtect, MotionProtect Plus, MotionProtect Outdoor, MotionProtect Curtain (and outdoor / mini / plus base variants), MotionProtect S / S Plus, MotionProtect G3 family (incl. Fibra), MotionProtect Plus Fibra / G3 | Motion detected (real-time), tamper, battery, temperature, signal |
| Cameras | MotionCam, MotionCam Outdoor, MotionCam Fibra (& base), MotionCam G3, MotionCam HD, MotionCam PhOD, MotionCam PhOD Fibra, MotionCam Outdoor PhOD, MotionCam Outdoor 2/4 PhOD, MotionCam S PhOD (& AM), MotionCam Superior PhOD | Photo on-demand capture + storage (PhOD models), motion detected, tamper, battery |
| Glass Break | GlassProtect, GlassProtect S, GlassProtect Fibra | Glass break detection, tamper, battery |
| Combi | CombiProtect, CombiProtect S, CombiProtect Fibra | Motion, glass break, tamper, battery |
| Fire/Smoke | FireProtect, FireProtect Plus, FireProtect 2 (all sub-models — heat-only `*hrb`/`*hsb`, CO-only `*crb`/`*csb`, multi-sensor `*hcrb`/`*hcsb`, AC-powered `*_ac`, UL-listed `*_ul`) | Smoke, steam (FireProtect 2 only — chamber discriminator), CO, high temperature, tamper, battery — sub-models without a given sensor expose only the relevant entity |
| Water Leak | LeaksProtect | Leak detected, tamper, battery |
| Relays/Switches | Relay, WallSwitch, Socket (and outlet variants) | On/off per channel; electrical readings (current, voltage, energy) across the WallSwitch / Socket family. Power is exposed as a direct reading on the Outlet Type E / F family (the firmware reports it natively) and as a derived `current × voltage` reading on the WallSwitch family. |
| Light switches | LightSwitch (Jeweller / Fibra) | On/off per channel |
| Lights | LightSwitch Dimmer | Brightness control |
| Locks | SmartLock, LockBridge (Yale) | Lock, unlock, unlatch (HA `lock.open` → momentary unlatch). State surfaces locked / unlocked / unlatched |
| Doorbells | Wireless DoorBell (standalone Jeweller ring button), MotionCam Video Doorbell, SmartLock / LockBridge variants with integrated ring button | `doorbell_pressed` event on the hub's security event entity, with `device_name` / `device_id` / `device_type` enriched on the event payload. Video doorbell also exposes photo capture; SmartLock variant exposes lock state on the lock entity. Requires FCM credentials configured |
| Keypads | Keypad, KeypadPlus, KeypadCombi, KeypadTouchscreen | Battery, tamper, temperature, signal, NFC status |
| Sirens | HomeSiren, HomeSiren S, HomeSiren Fibra, HomeSiren G3, StreetSiren, StreetSiren S, StreetSiren Plus, StreetSiren Plus Fibra/G3, StreetSiren Fibra, StreetSiren Double Deck (& S / Fibra) | Battery, tamper, signal |
| Range extenders | ReX, ReX 2, ReX 2 Fire | Battery, signal |
| Wired-Input Modules | MultiTransmitter, MultiTransmitter Fibra, Hub Hybrid wired inputs | Tamper of the module itself; each registered wired sensor appears as its own device with an alert binary sensor and an `alarm_type` attribute (intrusion / fire / glass_break / vibration / …) |

## Photo on Demand

MotionCam **PhOD** (Photo on Demand) models support capturing photos remotely:

1. Press the **"Capture photo"** button entity in HA
2. The hub triggers the camera, and the photo URL is retrieved via the Ajax notification system
3. The photo is downloaded, a **timestamp overlay** is burned into the image, and it's saved to `/media/ajax_photos/{device_name}/`
4. View photos in the camera entity or browse all captures via **Media Browser → Aegis Security Photos**

> **Note**: Regular MotionCam models (without PhOD) do not support on-demand photo capture. The button entity is only created for PhOD models.

Photos are automatically cleaned up based on your retention settings (configurable in integration options).

## Custom Services

| Service | Description |
|---|---|
| `aegis_ajax.force_arm` | Arm the system ignoring open sensors and active alarms. Supports entity target to arm a specific panel. |
| `aegis_ajax.force_arm_night` | Arm night mode ignoring open sensors and active alarms. Supports entity target to arm a specific panel. |
| `aegis_ajax.press_panic_button` | **⚠️ SOS / panic button.** See dedicated section below before using. |

Both `force_arm` services accept an optional `entity_id` target (alarm control panel entity). If no target is specified, all panels across all configured accounts are armed.

### Arm modes & voice assistants (Alexa via Home Assistant Cloud)

Ajax has a single partial-arm mode (the app calls it **Night mode**) plus full arm. The panel exposes **Arm Away** (full arm) and both **Arm Home** and **Arm Night** for that one partial mode — so "Arm Home" and "Arm Night" do the same thing and both settle the panel to `armed_night`. "Arm Home" is advertised mainly so the **Nabu Casa / Alexa** skill discovers the panel (it won't discover a panel that exposes Night without Home).

Notes for Alexa:

- The skill only exposes a panel that does **not** require a code to arm. If you enable the PIN option (`use_pin_code`), the panel won't be discovered by Alexa.
- Alexa requests a PIN only on **disarm**, and only supports a **4-digit** numeric code — Ajax PINs longer than 4 digits won't work for voice disarm.
- When a PIN is configured, the panel reports `code_format: number`, which also makes the Home Assistant Lovelace alarm card render a numeric keypad.

### Panic button (SOS)

The integration exposes the same panic button that the official Ajax mobile app does. It calls the underlying `SpaceService/pressPanicButton` endpoint.

> **⚠️ READ THIS BEFORE USING IT — FALSE PANIC ALARMS HAVE LEGAL AND FINANCIAL CONSEQUENCES.**
>
> Pressing the panic button forwards a **Panic / Hold-up alarm** to your monitoring station (CRA). On most professional monitoring contracts this means **immediate police dispatch** with **no verification window** — the operator is not allowed to call you back to confirm. False activations may result in fines, breach of your monitoring contract, and in some jurisdictions criminal liability.
>
> Use this service only for **genuine emergencies**. Do **not** wire it to noisy automations such as "trigger when my NVR detects motion" — for that case, route the trigger through a hardware path (e.g. an Ajax Transmitter Fibra connected to a relay controlled from HA), so the hub's normal alarm engine and verification rules apply.

What it does (controlled by hub configuration):

- Always fires regardless of whether the system is armed or disarmed.
- Triggers a `panic_button_pressed` event on the hub (mapped to event type `panic` on the integration's security event entity).
- Forwards the panic to the monitoring station.
- Optionally activates sirens depending on the hub setting `panic_siren_on_panic_button`.

### Service: `aegis_ajax.press_panic_button`

| Field | Required | Description |
|---|---|---|
| `confirm` | **Yes** (must be `true`) | Safety lock. The service refuses to run unless explicitly set to `true` to prevent accidental triggers from automations. |
| `entity_id` | No | Alarm panel entity for the space whose panic button to press. If omitted, the panic is sent to **every** configured space. |
| `latitude` | No | Optional caller latitude forwarded to Ajax. Use together with `longitude`. |
| `longitude` | No | Optional caller longitude forwarded to Ajax. Use together with `latitude`. |

Example:

```yaml
service: aegis_ajax.press_panic_button
target:
  entity_id: alarm_control_panel.aegis_home
data:
  confirm: true
```

## Example Dashboard

An example Lovelace dashboard is included in [`docs/dashboard.yaml`](docs/dashboard.yaml) with sections for:

- Alarm control panel + system status
- Hub network (Ethernet, Wi-Fi, cellular, power)
- Door & window sensors with batteries
- Cameras & motion detectors with photo capture buttons
- Temperature overview with 24h history
- Tamper, connectivity, and problem diagnostics
- Alarm, door, window, motion, and hub connectivity history

To use it, copy the YAML into a new dashboard view in **Settings → Dashboards → Edit → Add View → YAML mode**, and replace the example entity IDs with your own.

An [example automations file](docs/automations.yaml) is also available with 24 automation examples covering common security scenarios.

## Entity Details

### Hub sensors
- **CRA connection** — binary sensor showing whether the space has at least one approved monitoring company (CRA)
- **CRA company** — disabled-by-default diagnostic sensor showing the approved monitoring company name; returns `multiple` if more than one approved CRA is attached
- **Cellular connected** — binary sensor for GSM/4G connection status
- **GSM type** — sensor showing connection type (2G/3G/4G)
- **Hub network sensors** — Ethernet/Wi-Fi connectivity, Wi-Fi SSID/signal, Ethernet/Wi-Fi IP addressing, cellular signal/network, and mains power status
- **Lid opened** — tamper detection for the hub enclosure
- **Battery** — hub battery level
- **IMEI** — hub cellular modem identifier

> **Note on hub network sensors**: These entities are backed by the HTS connection. If HTS is unavailable or reconnecting, they may temporarily become unavailable instead of showing stale data.

### Real-time event sensors
Door open/close and motion detection are **transient events** — they appear when the event occurs and clear automatically. The integration uses a persistent gRPC stream for instant delivery (typically < 1 second latency).

> **Note on motion detection**: Ajax motion sensors only report motion events when the system is **armed**. This is a firmware-level behavior — when disarmed, motion detectors are inactive for battery conservation.

### Security event entity
Each hub has a **Security event** entity that fires events from FCM push notifications: alarm, arm/disarm, tamper, panic, fire, flood, motion, glass break, CO, and more. Use these in automations to trigger actions on security events.

Each event includes enriched data attributes:

| Attribute | Description | Example |
|-----------|-------------|---------|
| `raw_tag` | Original protobuf event tag | `intrusion_alarm`, `door_opened` |
| `transition` | Event transition state | `triggered`, `restored` |
| `device_name` | Name of the device that triggered the event | `Front Door`, `Hallway Cam` |
| `device_id` | Hex ID of the source device | `A1B2C3D4` |
| `device_type` | Device type enum | `DOOR_PROTECT`, `MOTION_CAM_PHOD` |
| `room_name` | Room the device is assigned to (when available) | `Kitchen`, `Entrance` |

Use these in automation templates, e.g. `{{ trigger.event.data.device_name }}`.

### Security sensors
- **Case tamper** — physical manipulation of device enclosure
- **Device problem** — device malfunction or communication issue
- **Anti-masking** — detector obstruction attempt
- **Case drilling** — enclosure drilling attempt
- **Interference** — RF jamming detection
- **Glass break** — glass break detection (GlassProtect, CombiProtect)
- **Vibration** — vibration/shock detection (DoorProtect Plus)
- **Tilt** — accelerometer tilt / device removed from wall (DoorProtect Plus family)
- **Steam** — chamber-level steam vs. smoke discriminator (FireProtect 2 with smoke chamber). Lets automations distinguish a real fire from cooking/shower steam false positives
- **External contact alert** — triggered state of an externally wired contact (e.g. reed switch on a window) connected to a DoorProtect's input terminals; toggles open/closed
- **External contact fault** — circuit-fault indicator for the same external wiring (cable disconnect or short)
- **Wire input alert** — triggered state of a third-party sensor wired into a MultiTransmitter or a Hub Hybrid wire input. Exposes an `alarm_type` attribute reflecting the category Ajax assigned to that input (intrusion, fire, glass_break, vibration, etc.). Available on `wire_input_mt` and `wire_input` device types

## Automation Blueprints

Aegis includes 8 ready-to-use automation blueprints. Import them via URL in **Settings → Automations → Blueprints → Import Blueprint**:

| Blueprint | Description | Import URL |
|-----------|-------------|------------|
| **Security Event Notification** | Push notification with emoji-labeled event type, device name and room. Optional inputs for a tap-through dashboard URL and for translating/customising the per-event-type labels. Ignores stale events on reload. | [Import](https://github.com/bvis/aegis-hass/blob/main/custom_components/aegis_ajax/blueprints/automation/security_event_notification.yaml) |
| **Intrusion Alarm + Capture** | Capture all cameras and send critical notification when intrusion alarm fires | [Import](https://github.com/bvis/aegis-hass/blob/main/custom_components/aegis_ajax/blueprints/automation/intrusion_alarm_capture.yaml) |
| **Tamper Alert** | Critical notification when device tampering is detected | [Import](https://github.com/bvis/aegis-hass/blob/main/custom_components/aegis_ajax/blueprints/automation/tamper_alert.yaml) |
| **Door Opened While Armed** | Preventive alert when a door opens with the alarm armed, with optional warning script | [Import](https://github.com/bvis/aegis-hass/blob/main/custom_components/aegis_ajax/blueprints/automation/door_opened_while_armed.yaml) |
| **Nobody Home Remind Arm** | Notification when nobody is home and the alarm is disarmed, with optional TTS | [Import](https://github.com/bvis/aegis-hass/blob/main/custom_components/aegis_ajax/blueprints/automation/nobody_home_remind_arm.yaml) |
| **Remind Arm with TTS** | Push + voice announcement when someone is home and alarm is disarmed | [Import](https://github.com/bvis/aegis-hass/blob/main/custom_components/aegis_ajax/blueprints/automation/remind_arm_with_tts.yaml) |
| **Low Battery Weekly** | Weekly summary of devices with battery below threshold | [Import](https://github.com/bvis/aegis-hass/blob/main/custom_components/aegis_ajax/blueprints/automation/low_battery_weekly.yaml) |
| **Connectivity Loss Escalation** | Progressive alerts on hub connectivity loss (warning → critical → restored) | [Import](https://github.com/bvis/aegis-hass/blob/main/custom_components/aegis_ajax/blueprints/automation/connectivity_loss_escalation.yaml) |

You can also manually copy the blueprint files from `custom_components/aegis_ajax/blueprints/automation/` to your `config/blueprints/automation/aegis_ajax/` directory.

> **Note**: Blueprints are templates — existing automations created from them are **not** updated automatically. After updating the integration, delete and re-import the blueprint in **Settings → Automations → Blueprints** to get the latest version, then recreate the automation.

## Troubleshooting

| Problem | Solution |
|---|---|
| "Invalid credentials" | Verify email/password work in your Ajax app |
| "Cannot connect" | Check internet connection; Ajax servers may be down |
| Hub shows offline | Verify hub has internet in your Ajax app |
| 2FA code rejected | Ensure your device clock is synchronized |
| Unexpected errors | Verify your app label matches your co-branded app exactly |
| Motion/door not updating | Check that the gRPC stream is connected (look for "Device stream started" in logs) |
| Sensors unavailable after reload | Use full HA restart instead of integration reload (gRPC streams require restart) |
| Photo capture button missing | Only MotionCam PhOD models support on-demand capture |
| Arm fails with "malfunctions detected" | Open sensors or low batteries prevent arming. Enable **Force arm** in Options, or use the `aegis_ajax.force_arm` service. The error message lists the blocking devices. |
| Disarm not working | Check HA logs for specific error; ensure the system is armed before disarming |

## Data Sources by Protocol

This integration uses three communication channels. Each entity type depends on a specific protocol:

| Protocol | Entities | Transport | Notes |
|----------|----------|-----------|-------|
| **gRPC stream** | Door open/close, motion, tamper, connectivity, problem, battery, temperature, signal, alarm panel state, switches, lights | `mobile-gw.prod.ajax.systems:443` | Persistent stream, < 1s latency |
| **gRPC space snapshot** | CRA connection, CRA company, room metadata | Same server | One-shot `SpaceService/stream` snapshot read at startup / refresh time, then cached between polls |
| **gRPC request** | Arm/disarm, force arm, photo capture trigger | Same server | On-demand commands |
| **HTS** | Ethernet (IP, gateway, DNS), Wi-Fi (SSID, signal, IP), cellular (signal, network), mains power, connection type | `hts.prod.ajax.systems:443` | Proprietary binary protocol over TCP+TLS |
| **FCM push** | Security events (alarm, arm/disarm, tamper, panic, fire, flood, motion, door_open, etc.), photo URL retrieval | Firebase Cloud Messaging | Requires FCM credentials (configured in Options) |

If a specific group of sensors stops working:
- **Door/motion/battery unavailable** → gRPC stream disconnected (check logs for "Device stream started")
- **Hub network sensors unavailable** → HTS connection lost (auto-reconnects on next poll cycle)
- **Security events not firing** → FCM not configured or push client not started (check logs for "FCM push client started")
- **Arm/disarm fails** → gRPC request issue (check logs for specific error)

## Roadmap

- [ ] Video stream support (VideoEdge, RTSP)
- [ ] Valve platform — bidirectional control. Read-only `valve` entity ships in `1.3.0`; full open / close still waits on capturing the official app's command-side calls (no `SwitchWaterStopService` in the v3 protos)
- [ ] Per-device firmware update entities. Hub-level entity ships in `1.4.0` via `streamHubObject` field 201; field 200 (`device_firmware_updates`) carries the same shape per device for the per-device entities, same read-only-by-design pattern
- [ ] Number/Select platforms for device settings (sensitivity, brightness)
- [ ] SpaceControl (keyfob) event support

## Help Wanted

This integration covers the hardware I personally own and can validate against a live hub. Anything beyond that — the **video stream from cameras**, less common device families, or features I don't have on my own install — depends on input from people who do own that hardware. There's no realistic way for me to extend coverage to devices I can't try myself.

Areas where the integration could grow with community input:

- **Video streaming** (live view on MotionCam Video, Video Edge cameras) — the biggest open gap.
- **Bidirectional WaterStop control** — read-only valve entity ships since `1.3.0`; full open / close is still pending.
- **SpaceControl (keyfob) events**, **per-device firmware updates**, **device settings** (sensitivity, brightness, chime mode, alert thresholds).
- **Co-branded apps** the integration doesn't yet recognise in the `App Label` dropdown.
- **Any new device family** that shows up in the snapshot without entities.

If you own any of these and would like it covered, open an issue on the [tracker](https://github.com/bvis/aegis-hass/issues/new) describing what you have and what's missing. Diagnostics dumps and debug logs from your install (Settings → Devices & Services → Aegis for Ajax → ⋮ → Download diagnostics, plus `custom_components.aegis_ajax: debug` in the logger config when relevant) are usually the most useful starting point — we can iterate over beta releases against your hardware until it works. Without that kind of involvement from people running the affected hardware, the integration can only cover what I personally use.

## Push Notifications (Optional, but strongly recommended)

The integration can run with or without Firebase Cloud Messaging (FCM) push, but the two configurations behave very differently:

| Behaviour | With FCM | Without FCM |
| --- | --- | --- |
| Arm / disarm / night-mode state in the alarm panel | Real-time (push) | Up to 5 minutes late (next poll cycle) |
| Event entity firings (alarm, tamper, panic, doorbell ring, fire / smoke, CO, flood, glass break, motion, door open, battery low, connection lost, malfunction) | Real-time | Never — these only ride the FCM channel |
| Photo-on-Demand URL retrieval (snapshot pulls) | Available | Not available |
| Device sensor state (temperature, signal strength, open / closed contacts, …) | Real-time (gRPC stream) | Real-time (gRPC stream) |
| Hub network info, SIM info, room / space topology | Polled | Polled |

If you cannot configure FCM, the integration still works as a polled view of your Ajax installation, but automations that rely on alarm-panel events will not fire. You can dismiss the related Repair card; the integration will not break.

### The four values

Configured in the integration's Options flow (Settings → Devices & Services → Aegis for Ajax → Configure) and stored securely in config entry data:

- **FCM Project ID**
- **FCM App ID**
- **FCM API Key**
- **FCM Sender ID**

All four belong to the **same Firebase project** — the one your Ajax mobile app uses for its own push notifications. The integration registers with that project as an additional FCM client, so it needs the same four values the app already carries.

These four values are **app-wide identifiers**, not per-user secrets — every installation of the same Ajax build (or co-branded variant) uses the same four values, and they identify the Firebase project the app talks to rather than any individual account. The project still doesn't ship them for you because (a) the values differ per co-branded variant — Ajax, Protegim, AIKO, etc. each have their own — and (b) project policy keeps third-party identifiers out of our repository regardless.

### Where the values live

Three of the four are stored in the Android APK's resource file at `res/values/strings.xml`. Note that Android compiles resource XML to a binary format before packaging, so simply renaming the APK to `.zip` and extracting it will give you unreadable bytes for that file — you'll want a standard Android resource viewer like [`apktool`](https://apktool.org/) to read it back into plain text.

Once readable, three of the four values appear as named entries:

- `project_id` — short slug, e.g. `my-firebase-project-id` (kebab-case, no spaces)
- `gcm_defaultSenderId` — pure digit string (~12 digits, no prefix); the same digits that appear between the first two colons of `google_app_id`
- `google_app_id` — `1:<sender_id>:android:<hex tail>` (starts with `1:`, followed by the sender digits, the literal `:android:`, and a hex string whose length varies by Firebase project — current Ajax builds ship a 16-char tail; Firebase's [own canonical example](https://firebase.google.com/support/guides/init-options) is also 16)

The fourth, `google_api_key`, is **not** in `strings.xml` (the entry there is a placeholder string); it ships inside the native library bundled with the APK at `lib/<arch>/libnative-lib.so`. The value starts with `AIza` and is exactly 39 characters long, so it's easy to spot when scanning printable strings out of the binary.

> **If you downloaded the APK as a `.xapk` split bundle**, the native library does NOT live inside the base `com.ajaxsystems.apk` — it ships in one of the architecture-specific config splits (`config.armeabi_v7a.apk` / `config.arm64_v8a.apk`). Unpack the XAPK first, then unpack the matching architecture split, then read `lib/<arch>/libnative-lib.so` from there.

> **Watch out — the native library typically contains more than one `AIza…` string.** Google Cloud SDKs share the same key format across services, and an Ajax APK can ship a separate key for FCM, another for ML Kit, and so on; only the **FCM-scoped** one is what Firebase Installations accepts. There is no label next to the keys to tell them apart. The pragmatic recipe is to list every match (`strings libnative-lib.so | grep -oE 'AIza[A-Za-z0-9_-]+' | sort -u`) and try each one through the integration's Repair flow until registration succeeds. If you get `Push notifications disabled — FCM credentials rejected by Google` with the warning text mentioning `API_KEY_ANDROID_APP_BLOCKED`, that's the signal you've picked a non-FCM key — pull the next candidate from the list and retry.

> **Using the Ajax app on iPhone?** The iOS build ships these values in `GoogleService-Info.plist` inside the signed `.ipa` bundle, which is encrypted and unreadable without a jailbroken device. The Firebase project is identical on both platforms, so extract the four values from the Android APK regardless of which OS you use day-to-day — pulled from the Android build, they work for FCM push delivery on a Home Assistant install on any phone OS.

### Sanity-check before submitting

Two quick consistency checks that catch the most common "credentials rejected" reports:

1. **`fcm_sender_id` must equal the digit chunk between the first two colons of `fcm_app_id`.** Example: if `fcm_app_id = 1:608123456502:android:…`, then `fcm_sender_id` is `608123456502`. If they don't match, those two values came from different Firebase projects.
2. **All four values must come as a coherent set from one Firebase project** — not picked individually from different sources.

### If submission still fails

Since `1.6.0` the integration also runs the same shape checks above as a **pre-flight** before contacting Google. When something is structurally off (e.g. `fcm_sender_id` doesn't match the digit chunk in `fcm_app_id`), a **Push notifications disabled — FCM credentials malformed** Repair card appears with the specific problem named, and the values aren't sent to Firebase at all. That's faster and clearer than Google's opaque 403.

If the shape checks pass and Google still rejects, the WARNING line under **Settings → System → Logs** names the specific cause (project consistency, app-id format, or network reach to Google's FCM hosts). Re-enter the four values together via the Repair card under **Settings → Repairs** once you have a coherent set.

If you cannot obtain a coherent set, leaving FCM unconfigured is a fully supported (degraded) configuration — device sensors and a polled view of the alarm panel still work.

## Translations

The integration is available in 14 languages: English, Spanish, Catalan, German, French, Italian, Dutch, Polish, Portuguese, Brazilian Portuguese, Romanian, Turkish, Ukrainian, and Czech.

## Legal Notice

This integration is provided for **personal, non-commercial use** and for **interoperability purposes** as permitted under applicable law (including EU Directive 2009/24/EC on the legal protection of computer programs).

- This project is **not affiliated with Ajax Systems** in any way
- Ajax Systems trademarks and product names belong to their respective owners
- The protobuf definitions included in this integration were derived from publicly available mobile applications for interoperability purposes
- **No warranty** is provided — this software is provided "as is"
- The authors are not responsible for any damage, data loss, or security issues arising from the use of this integration
- By using this integration, you accept full responsibility for its use with your Ajax security system

## License

MIT
