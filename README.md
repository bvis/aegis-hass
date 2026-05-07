# Aegis for Ajax — Home Assistant Integration

[![HACS Custom](https://img.shields.io/badge/HACS-Custom-orange.svg)](https://github.com/hacs/integration)
[![GitHub Release](https://img.shields.io/github/release/bvis/aegis-hass.svg)](https://github.com/bvis/aegis-hass/releases)
[![Tests](https://github.com/bvis/aegis-hass/actions/workflows/ci.yml/badge.svg)](https://github.com/bvis/aegis-hass/actions)
[![License: MIT](https://img.shields.io/github/license/bvis/aegis-hass.svg)](LICENSE)
[![Code style: ruff](https://img.shields.io/badge/code%20style-ruff-000000.svg)](https://github.com/astral-sh/ruff)

> **Disclaimer**: This is an **unofficial** third-party integration and is not affiliated with, endorsed by, or supported by Ajax Systems. Use at your own risk. This integration communicates with Ajax Systems servers using the same protocol as the official mobile app. Ajax Systems may change their API at any time, which could break this integration without notice.

**Aegis** is a Home Assistant custom integration for **Ajax Security Systems** — works with any co-branded Ajax app (Protegim, ADT, G4S, and many more).

Communicates via **gRPC** (the same protocol the official mobile app uses). No Enterprise API key required — just your regular account credentials.

## How It Works

Ajax Systems provides co-branded versions of their mobile app to security companies worldwide. Each co-branded app connects to the same Ajax cloud backend but uses a unique **application label** to identify itself. This integration emulates the mobile app's gRPC protocol, so it works with any co-branded variant.

**You need to know the application label of your Ajax provider.** This is an internal identifier that the app sends to the Ajax cloud (see the Known App Labels table below). If you use the main Ajax app, the label is `Ajax`.

## Features

- **Alarm Control Panel**: Arm away, disarm, night mode, group arming with PIN code support
- **Force Arm Services**: `aegis_ajax.force_arm` and `aegis_ajax.force_arm_night` to arm ignoring open sensors
- **Binary Sensors**: Door open/close, motion detection, smoke, steam (FireProtect 2 chamber discriminator), leak, tamper, CO, heat, glass break, vibration, tilt (DoorProtect Plus accelerometer), CRA monitoring status, cellular connection, lid tamper, external contact alert (wired reed switches on DoorProtect/Hub Hybrid inputs), external contact fault, MultiTransmitter wired-input alert with alarm category, anti-masking, interference detection, ethernet link, Wi-Fi link, mains power
- **Hub Network**: Real-time hub network data — ethernet/wifi/gsm connection status, Wi-Fi SSID and signal strength, IP addressing, cellular signal strength and network type, power supply status
- **Sensors**: Battery level, temperature, humidity, CO2, signal strength, GSM type (2G/3G/4G), Wi-Fi signal level, Wi-Fi SSID, Wi-Fi IP, IMEI, Ethernet IP/gateway/DNS, cellular signal/network, connection type
- **Switches**: Relays, wall switches, sockets (multi-channel support) — turn on/off via `DeviceCommandDeviceOn` / `DeviceCommandDeviceOff` gRPC services
- **Lights**: Dimmers with absolute brightness control via `DeviceCommandBrightness`
- **Locks**: Ajax SmartLock and LockBridge (Yale) — lock, unlock, and unlatch (HA's `lock.open`) via `SwitchSmartLockService`
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
| Relays/Switches | Relay, WallSwitch, Socket, LightSwitch | On/off per channel |
| Lights | LightSwitch Dimmer | Brightness control |
| Locks | SmartLock, LockBridge (Yale) | Lock, unlock, unlatch (HA `lock.open` → momentary unlatch). State surfaces locked / unlocked / unlatched |
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
- [ ] Smart lock support (LockBridge)
- [ ] Valve platform (WaterStop)
- [ ] Firmware update platform
- [ ] Number/Select platforms for device settings (sensitivity, brightness)
- [ ] SpaceControl (keyfob) event support
- [ ] "Unknown app label" Repair card (#99)

## Push Notifications (Optional)

For real-time push notifications via Firebase Cloud Messaging (FCM), you need to provide FCM credentials. These are the standard Firebase configuration values used by the Ajax mobile app.

The required fields (configured in the integration's Options flow, stored securely in config entry data):
- **FCM Project ID** — Firebase project identifier
- **FCM App ID** — Firebase application ID
- **FCM API Key** — Firebase Web API key
- **FCM Sender ID** — GCM/FCM sender ID

### How to obtain FCM credentials

Three of the four values are plain strings in the app's `res/values/strings.xml`: `project_id`, `gcm_defaultSenderId` and `google_app_id`. The fourth, `google_api_key`, is **not** in `strings.xml` (the value there is a placeholder); it ships inside `lib/<arch>/libnative-lib.so` bundled with the APK.

> **iOS users:** the iOS Ajax app ships these values in `GoogleService-Info.plist` inside the signed `.ipa` bundle, which is encrypted and cannot be inspected without a jailbroken device. In practice, even if you use the Ajax app on iPhone day-to-day, **extract the credentials from the Android APK** — the Firebase project is the same on both platforms (same `project_id`, `google_app_id`, `gcm_defaultSenderId` and `google_api_key`), so values pulled from the Android build work for FCM push delivery on a Home Assistant install regardless of which OS you personally use.

If FCM credentials are not configured, the integration will still work using the persistent gRPC stream for real-time updates. FCM adds an additional push notification channel for faster event delivery and enables Photo on Demand URL retrieval.

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
