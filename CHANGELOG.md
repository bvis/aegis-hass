# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [1.20.0] - unreleased

A MotionCam that cannot take a photo on demand now has a camera entity worth looking at, and a
doorbell ring stops landing on another client's entity.

Neither change adds a periodic request to Ajax. The alarm-image import runs on a real camera
alarm and opens exactly one media stream, the same one the Ajax app opens when the user taps
that notification; the manual backfill runs only when called and refuses to run more than once
every five minutes per space. The doorbell fix touches parsing and attribution only, so its
delta is zero.

The two user-visible changes are both unconfirmed in the field so far, and for opposite reasons:
the alarm import needs a real camera alarm, which nobody can schedule, and the doorbell
misattribution needs a PRO account managing several client spaces with a doorbell, which nobody
here has. Both reporters own the hardware and offered to test, so both ship as a beta first.

### Added
- **A MotionCam alarm's photo sequence is imported and kept (#495).** A plain MotionCam has no Photo on Demand, so its camera entity could never show anything, while the Ajax app shows the one, two or three photos the alarm produced immediately. Those photos are now retrieved when the alarm push arrives and stored as an album per alarm: the numbered frames plus a preview, browsable through Media Source, with the preview served by the camera entity — a contact sheet when the alarm carried more than one frame, since a camera entity can only return a single image. Manual Photo on Demand captures are untouched and stay individual files. Retention removes an album as a unit, so a sequence is never half-deleted, and a partly failed sequence is never published as if it were the whole alarm: the stream is read until every frame Ajax has not marked as failed is ready. A new `aegis_ajax.refresh_alarm_images` action backfills recent history for installs where push is not available, or for alarms whose media was not ready at the time; on an account with several spaces it reports how many were skipped for still being inside their cooldown rather than discarding what the others returned. Found, designed and built by @bogar. **Requests to Ajax:** one media stream per real camera alarm, opened with the identifier the push itself carries so no history search is needed, and readiness arriving on that same stream rather than through repeated attempts — which is what the Ajax app does when the notification is tapped. The backfill action adds one history query plus at most ten media reads per space, only when called, behind a five-minute per-space cooldown and skipping albums already complete on disk. Nothing periodic; the three network budgets are each pinned by a test that fails if they regress.

### Fixed
- **A doorbell ring no longer fires on a doorbell belonging to a different space (#494).** When a ring push carried no usable source device, the integration fell back to "the only doorbell in the install" — a list built from every device on the account rather than per space. An account with one registered doorbell between several client spaces therefore attributed every such ring to that one doorbell, so a press at one client's building rang the entity of another's. The fallback now considers only doorbells paired to the hub of the space the push was delivered to, and declines to guess at all when the push cannot be routed and more than one space is configured; a single-space install behaves exactly as before, and a device the push actually names still wins regardless of space. This is the same shape as #358, and invisible with one space for the same reason. Reported by @Sven2410 from a PRO account with three client spaces, with the debug lines that showed the resolved device staying constant while the press moved. No additional requests to Ajax.
- **Two doorbell presses close together are no longer counted as one (#494).** The notification identifier was read by scanning the push for the first sixty-four-character hexadecimal run rather than by reading the field that holds it. That is the real identifier on most pushes, but not when the real one has a different shape — a ring routed through an Ajax NVR is one such case — and the scan then returns some other value that is constant for that device. Since this identifier is what the five-second duplicate window compares, a constant made two genuinely different presses look like one delivery and dropped the second silently. It is now read from the field, with the old scan kept only for payloads that cannot be decoded at all. Reported by @Sven2410, who also worked out this consequence from his log before the cause was known. No additional requests to Ajax.

### Changed
- **The account-session and Photo on Demand actions now appear in German, French, Portuguese, Brazilian Portuguese and Catalan (#475).** Four action blocks — listing sessions, signing one or all other devices out, and setting Photo on Demand mode — held their English text in every language but Italian, Dutch, Ukrainian, Spanish and Danish, which contributors had already completed. Five more languages are done here, each built from wording the same language file already used for the same concepts rather than newly invented, and following the Ajax app's own term for Photo on Demand where the app ships one. The same pass localised the three panic-button field labels, which had stayed English in thirteen of the fifteen languages including the five whose action blocks were already finished. Czech, Polish, Romanian and Turkish remain English and are still open for native speakers on #475, since a confirmation that signs devices out is the worst place for a translation nobody can check. The README now says plainly that part of the translations were produced without a native speaker, and how to report a string that reads wrong.
- **Every declared device-family capability now comes from one place (#332).** The final step of the handler refactor moves the remaining routing gates — the siren-settings refresh and its carry-forward, button press epochs, hub-network temperature and its exclusion from the snapshot temperature read, and the single-doorbell push fallback — onto the handler registry, and deletes the three duplicate family lists that shadowed it. The siren proto-coverage tests now derive their cases from the registry, so registering a siren family whose settings cannot be decoded fails immediately, which is stronger than the frozen list it replaces. No family or capability changes, and no user-visible change. Built by @aavdberg, closing the six-part series he opened in July.
- **The diagnostics dump records whether a degraded snapshot has ever held a deactivation (#419).** The carry that keeps a deactivated device from silently reading as protecting corrects itself within one status refresh, so its only trace was a debug line — and the reporter watching for a natural occurrence lost the decisive moment to log rotation twice, which is what has kept the fix unconfirmed rather than any doubt about the code. The dump now carries how many snapshots have held a deactivation, how many devices that covered, when the last one was, which devices a carry currently applies to, and the age of the hub's bypass report per device, which is the first thing to look at when the count stays at zero. Counts only ever grow, so they still show that a carry happened after the hub's next report withdrew it. Device ids only, no names. No additional requests to Ajax.

## [1.19.1] - 2026-09-09

Two regression fixes, both of them for bugs that only exist on a real installation and that the
test suite could not have caught in the shape it had.

**Push notifications stopped working after upgrading to `1.19.0`** (#487, #491). `1.19.0` began
stamping a fingerprint on the stored FCM registration so that a changed credential set could be
detected. A registration saved by `1.18.0` or earlier carries no fingerprint, and the new check
read that as an invalid cache: it discarded a working registration and registered again — putting
every upgrading install through the one step in the flow that can fail for reasons unrelated to
the user's credentials, which is the failure #464 exists to document as transient. Two reporters
lost push and got it back by reverting to `1.18.0`, because the old registration was still on
disk for `1.18.0` to reuse. A missing fingerprint is now adopted instead of discarded: the token
is kept, the fingerprint is stamped on it, and upgrading does not re-register at all. That is
exactly what `1.18.0` did with the same file, so it cannot be worse than the version people were
reverting to, while a real credential change stays detectable from here on.

The `1.19.0` notes said this migration had run on three installs. It had — and it succeeded on
all three, because re-registration usually works. Success on three installs said nothing about
the fourth, since the step it forced fails probabilistically. That is the lesson, not the fix.

**Video Edge cameras bridged through an NVR lost their entities on `1.18.0`** (#489, #487, #490,
by @aavdberg). Ajax provides no hub parent for a Video Edge channel, so the parser deliberately
uses the channel's own id as its `hub_id`. The device-registry migration in #444 then resolved
that id to the channel's own registry entry and passed it as the parent, and Home Assistant
refuses a device that is its own parent — so the platform stopped providing the Motion and Case
tampering entities and Home Assistant marked them "no longer provided". Channels now stay root
devices. Unique IDs are unchanged, so the orphaned entities are re-adopted rather than duplicated.

Neither fix could be confirmed on a live install here: the FCM one needs a pre-`1.19.0`
registration on disk, which this maintainer's install no longer has after upgrading, and the
registry one needs a Video Edge behind an NVR, which nobody here owns. Both ship on the mechanism
plus unit tests that fail when the fix is reverted, and both issues stay open until a reporter
confirms. The `1.18.0`-era regression also only reproduces on Home Assistant 2026.8 or newer,
which the CI matrix does not reach.

Also in this release: Danish, a fifteenth language, contributed in full by @webliodk (#488), plus
Italian, Dutch and Spanish translations of the four action blocks that were still English (#475,
#477, #483, #484, #485), and the completion of the device-handler refactor (#332) — the `light`,
`valve`, `number`, `select`, `event` and `sensor` platforms now read their device families from
the handler registry (#479, #481, #482).

No change in this release adds a request to Ajax. The push fix removes one: an upgrading install
no longer performs a registration it does not need.

### Fixed

- Push notifications no longer break on upgrade from a pre-`1.19.0` release: a cached FCM
  registration without a credential fingerprint is adopted instead of discarded and re-registered
  (#487).
- Video Edge channels bridged through an NVR keep their Motion and Case tampering entities: they
  are no longer given themselves as their device-registry parent (#489, #487).

### Added

- Danish translation (`da`), the fifteenth language (#488).
- Italian, Dutch and Spanish translations of the account-session and Photo-on-Demand action
  blocks (#475).

### Changed

- The `light`, `valve`, `number`, `select`, `event` and `sensor` platforms, and the coordinator's
  siren-settings, button-press and temperature routing, now take their device families from the
  device-handler registry instead of per-platform tables (#332). No user-visible change: verified
  by an entity-registry census on a live install, byte-identical before and after.
- The characterization suite now pins the neutrality of a newly registered device family
  permanently, and the electrical sensors' creation gate has test coverage for the first time
  (#482).

## [1.19.0] - 2026-09-08

Consolidates the `1.19.0-beta.1` → `beta.7` series, same bits as `beta.7`. The release is
dominated by @aavdberg's work: seven of the ten changes are his, including the account-session
management this version is built around and three steps of the device-handler refactor.

Confirmed on real installs rather than inferred. Session listing was read repeatedly against a
live account, and @aavdberg validated a real termination and its verification on his own. The
three handler refactors were each checked with an entity-registry census before and after the
swap — byte-identical both times, including the camera entities and photo-capture buttons whose
identities the camera refactor could have changed silently, which is the one thing the
characterization suite cannot prove. The repair-card translations were verified by asking a
running Home Assistant what it serves, in German and Ukrainian. The credential-fingerprint
migration has now run on three installs, one of which surfaced #464 in the process. Push delivery
was proven on the release bits themselves with an arm and a disarm from the Ajax app: two pushes
received, no duplicates, and the panel following each within about ten milliseconds rather than
waiting for the poll.

Three changes ship on unit-test evidence instead, deliberately, because their failure situations
cannot be produced on demand: the verification of a session termination whose confirmation was
lost needs the connection to drop between the frame going out and the reply arriving (#330);
the retry of a transient Google registration failure needs that failure to recur, and the
reporter was explicit that his upgrade reused its cached registration and therefore never
exercised the corrected branch (#464); and the keyfob family byte was decoded from another
user's captures on hardware nobody here owns (#460), where the widening is provably inert for
every other family. All three stay open with the tell that would confirm them.

No change in this release adds a periodic request to Ajax. The session actions run only when
called — one list, plus one frame per terminated session, matching what the Ajax app sends from
its own sessions screen — and the verification of an uncertain outcome adds a single read-only
list on that path alone.

### Added
- **Other devices can be signed out of the Ajax account from Home Assistant (#330, #447).** Two new actions complete the session management started in #441: `aegis_ajax.terminate_client_session` logs one selected device out, and `aegis_ajax.terminate_other_client_sessions` logs out every device except this one. Both require an explicit confirmation flag, and both refuse to touch any session carrying the integration's own client identity — not merely the one currently in use — because a stale copy of that identity would otherwise take Home Assistant's own session down with it and force a re-authentication on accounts with two-factor enabled. The list is re-read immediately before acting, so the refusal is checked against what Ajax holds now rather than what was on screen. A bulk call that stops part way reports how many sessions it did terminate, since the endpoint rate-limits and the frames already sent cannot be taken back. Found, decoded and built by @aavdberg. Adds requests to Ajax **only when an action is called**: one list plus one frame per terminated session, matching what the Ajax app sends from its own sessions screen.
- **The active sessions of the Ajax account can be listed from Home Assistant (#330, #441).** A new `aegis_ajax.list_client_sessions` action returns the sessions the Ajax servers hold for the account — device model, OS, client version, application label, creation and expiry — with the integration's own session marked `is_current` when the response allows it. The wire format is not a protobuf: the response is the flat sub-key/value stream the status connection already speaks, decoded by a parser validated against a real ten-session capture whose checksum the test asserts, including the trailing bytes the live server appends. The action is meant for manual, on-demand inspection: the endpoint rate limits repeated requests, and the description says so. Terminating sessions is a separate change (#447) and is not in this release. Found, decoded and built by @aavdberg. Adds one request to Ajax **only when the action is called**, the same request the Ajax app makes when its sessions screen is opened; nothing periodic.

### Fixed
- **A session sign-out whose confirmation is lost is no longer reported as if nothing happened (#330, #447).** Ajax confirms a termination by returning the refreshed session list, so if the status connection drops after the frame has gone out but before that reply arrives, the action failed while the session was in fact logged out — and the obvious response, pressing it again, sends the same frame a second time. That outcome is now told apart from a failure to send at all: the integration waits for its own status connection to come back, reads the session list exactly once, and reports success only when the target is gone, a confirmed non-termination when it is still there, and an explicit outcome-unknown that says not to retry automatically when the verification itself cannot be trusted (#468). That covers more than an empty answer: every session Ajax listed a moment before the request has to come back in the verification, because the decoder deliberately drops a trailing incomplete record rather than failing the read-only service, so a truncated answer arrives non-empty and short — and the moment that is likeliest is right after the connection drop that made the outcome uncertain. A record that comes back unidentifiable is treated the same way, since the creation timestamp that identifies a session decodes only at its exact length and a response clipped inside it would otherwise look like a session that had gone. A session that legitimately ends during those seconds therefore reports the outcome as unknown rather than as a success, which is the safe direction for an operation that cannot be repeated. A bulk call keeps the sessions it had already confirmed and counts the uncertain one only on a positive verification. Found, designed and built by @aavdberg, who also caught that requiring the integration's own identity record in that verification would have broken it on real accounts whose list carries none. On the uncertain path only, one extra read-only session list — the request the Ajax app's own sessions screen makes — and one reconnection the periodic cycle would have made anyway; the termination frame is never sent twice, and nothing periodic changes.
- **A failed Google registration no longer switches push off until a file is deleted by hand (#464).** Registering for push takes two steps at Google: the first obtains a GCM token and carries none of the four FCM credentials; only the second, against Firebase Installations, uses them. A failure in that first step was nevertheless recorded as a terminal credential rejection, after which every restart and reload skipped the registration entirely, and the Repair card the warning pointed to could not help: it pre-fills the stored values, so re-submitting them produced the same fingerprint and hit the same skip. The one-time re-registration in #458 made every upgrading install run that first step once, so a single unlucky attempt silenced push for good — the reporter saw `PHONE_REGISTRATION_ERROR` twice, one second apart, and a clean registration with the same four values twelve minutes later. That failure is now retried on the next restart or reload, its warning names the step that failed instead of blaming the credentials, submitting the Repair card always clears the marker so it is a real retry, and the migration's log line no longer describes a cache made before 1.19.0 as a "different credential set". Refusals from Firebase Installations, the request that does carry the api-key, stay latched as before (#227). Reported by @wip3out3r with the log, the library line that raises the error and the matching fingerprints proving the rejected set and the working set were the same four values. No additional requests to Ajax; towards Google, one registration attempt per restart or reload where the marker previously suppressed it, the same bound every other retryable failure already has.
- **Arm and disarm from a SpaceControl keyfob now reach the panel immediately on more hubs (#460, #359).** The hub announces a keyfob action on the status connection with a family byte that varies between hubs, and the integration only recognised four of them, so on a hub that uses a fifth the keyfob action was ignored and the panel waited for the periodic poll. That family is now recognised too. The frame is used exactly as the others are, as a signal to re-read the authoritative state rather than as a state itself, so a keyfob action can never put a wrong state on the panel. Reported by @wip3out3r with decoded frames and a 65-hour census of every event his hub emitted, on top of two earlier captures by @aavdberg. No additional requests to Ajax where push is working: the keyfob frame and the push arrive a fraction of a second apart and collapse into the single re-read that already happened; where push is not delivering, this is the re-read that was missing.
- **A push registration made for a previous set of Firebase credentials is no longer reused (#452, #458).** Changing any of the four FCM values in the options kept the registration cached for the old values, so the new ones never took effect until the cache was deleted by hand. The registration now carries a one-way fingerprint of the four values it was made with, and a cache whose fingerprint is missing or differs is discarded and registered again — every existing install does this once, on the first start after this update. Credentials Google already refused stay throttled as before (#227). The diagnostics dump shows the fingerprint of the cached registration next to the one of the values in force. Found, designed and fixed by @aavdberg. No additional requests to Ajax: the single extra call is one Firebase registration per install, once.

### Changed
- **The Repair card for rejected push credentials now appears in your own language.** Its form existed only in English and Spanish, so users of the other twelve languages were handed an English dialog by the very card that is supposed to walk them through a fix — including the one #464 had just made functional. Every sentence of the twelve new translations is recombined from wording those language files already carried, rather than newly invented. The same pass repaired the source of the strings: `strings.json` had lost eleven keys that the fourteen language files still carried and the integration still uses — among them the `disarm_night_mode` action and the two firmware update entities — and since Home Assistant reads the language files and never the source, nothing was visibly broken while a single regeneration from that source would have deleted all fourteen copies. A test now pins key parity in both directions, so neither half can drift again unnoticed.
- **Camera and photo-on-demand families are identified from the handler registry (#332).** PR-3 of the same refactor: the camera platform's own device-type set and the photo-on-demand set that `button.py` imported *from the camera platform* — a cross-platform import that existed only to share a list — are now capabilities the registry answers. Verified as a set comparison rather than by eye: across all 139 registered families, the ones that get a camera entity and the ones that get a capture button are exactly the same as before, and the MotionCam handler split that made room for the two flags moves no binary sensor, so the permanent characterization fixture is untouched. No user-visible change and no additional requests to Ajax. Built by @aavdberg.
- **The SmartLock family is identified from one place instead of three (#332).** The two SmartLock device types were listed in the lock platform and mirrored again inside the coordinator — a copy kept only to dodge a circular import — while the binary sensors they get came from neither list. A single handler registration now carries the lock capability that both consumers read, and declares the tamper sensor those families already had. Nothing about the entities changes, and that is not taken on trust: the characterization snapshot for both families was checked against the behaviour they had before, when they fell through the unmapped fallback, and is identical down to the unique ID, device class, entity category and enabled-by-default flag. No runtime change, no user-visible change and no additional requests to Ajax. Built by @aavdberg.
- **The binary-sensor device table became a handler registry (#332).** The hardcoded `_DEVICE_TYPE_SENSORS` mapping that decided which binary sensors each of the 136 modelled device types gets is now a per-family handler module, so a new device family is added in one place instead of being spread across the platform files. Nothing about the entities changes: the refactor shipped behind a characterization suite that pins the unique ID, device class, entity category and enabled-by-default flag of every entity the previous table produced, including the wire-input families and the tamper-only fallback an unmapped family gets, and that suite was committed first and shown to pass against the old table before the swap. Groundwork for the remaining duplication (#332); no runtime change and no additional requests to Ajax. Surveyed, designed and built by @aavdberg.

## [1.18.0] - 2026-09-05

Consolidates the `1.18.0-beta.1` → `beta.5` series, same bits as `beta.5`. The exit / entry delay panel states were confirmed on two installs: the reporter's (@GherardS, including the HomeKit countdown that started #443) and the maintainer's, where the delay settings are read from the hub as expected. The device-registry fix (#444) is confirmed on Home Assistant 2026.9 with zero deprecation warnings, and the "Delay when leaving" sensor reads correctly on the detectors that have it. The push delivery record (#437) has a third-party baseline as well as the maintainer's.

Three changes ship on unit-test evidence rather than a field confirmation, deliberately: the re-authentication latch (#448) and the tokenless push-cache repair (#450) only run in failure situations no install can produce on demand — the normal paths they guard ran clean here across several starts — and the siren settings (#438) need a hub with sirens, which no participating install has yet. Both #448 and #450 stay open until an affected install confirms them.

No change in this release adds a request to Ajax; the delay states ride the status connection the integration already keeps open.

### Added
- **The alarm panel can show the hub's exit and entry delays as `arming` and `pending` (#454, #443).** Ajax runs the per-detector "Delay when leaving" / "Delay when entering" inside the hub and reports **armed** the instant you arm, so those delays never reached the panel. The hub does announce both ends of them on the status channel the integration already keeps open — one event when the exit delay completes, one when an entry delay starts, the latter carrying the moment it expires — and the panel now follows those signals: `arming` from the arm until the hub says the exit delay is over (the longest configured delay bounds it, so a missed frame cannot leave the panel stuck), `pending` from the hub's entry-delay event until its expiry, a disarm or an alarm. Opt-in through a new option, off by default, because automations waiting for `armed_away` then fire once the exit delay completes; with it on, the panel also exposes `hub_state`, `exit_delay_seconds` and `delay_ends_at`. Nothing is persisted across restarts. Detectors without a delay are live during `arming` — the README says so. Prompted by @GherardS on #443, corroborated with a capture from @dheuts90 (#284). No additional requests to Ajax: both events and the delay settings ride the existing status connection.
- **Detectors show whether they have a "Delay when leaving" configured (#443).** A disabled-by-default diagnostic binary sensor on door, motion and combi detectors and MotionCams mirrors the per-detector exit-delay setting from the Ajax app. It came out of #443: the exit delay configured on a detector never shows up as the panel's `arming` state — that state is the app's own countdown, not the hub's — so until the countdown itself can be read, the one thing Home Assistant can show about the delay is that it exists. The flag already arrived with every device snapshot and was only missing an entity. No additional requests to Ajax.
- **The hub's siren behaviour settings are now visible (#438).** Two disabled-by-default diagnostic binary sensors on the hub device — "Siren on panic button" and "Siren on tamper" — plus a `hub_siren_settings` block in the diagnostics dump. When a panic fires and the sirens stay quiet, the answer used to be guesswork (#435): the panic request carries no siren field, the hub alone decides. These settings live only in the hub's legacy device model, unreachable over gRPC, so they are read from the hub's HTS settings row — confirmed present on real hardware first. Read-only by design; the values load at startup/reload (the periodic status refresh doesn't carry them) and a change made in the Ajax app becomes visible after a reload. No additional requests to Ajax: the settings row already flows at startup.
- **Diagnostics report whether push has ever delivered anything (#437).** The dump gains a `push` block: whether the client is connected and for how long, how many pushes have arrived since startup, when the last one landed, and — the field that matters — `ever_delivered`, whether this credential set has *ever* delivered a single push. That last one is persisted and keyed to a fingerprint of the four FCM values, so it survives restarts and resets by itself when the values change. The two existing counters are per-process, which is why an FCM registration that Ajax accepts and then never delivers to has been indistinguishable, after every restart, from a house that simply had no events: the client connects, stays connected, and receives nothing, with no error raised anywhere. Diagnosing that state in #359 took a month and 40 comments of logs; the first diagnostics download now answers it. Found through @aavdberg's captures, which is what made the shape of the hole visible. No additional requests to Ajax — it only counts what already flows.

### Fixed
- **A valid 2FA code is no longer rejected during re-authentication (#448).** When the Ajax session Home Assistant uses is revoked externally, the integration opens the re-authentication flow — but the old coordinator kept making its own login attempts in the background, and each one made Ajax issue a fresh two-factor challenge, invalidating the code the user was typing until Home Assistant was restarted. The coordinator now latches into the re-authentication state at the first challenge and makes no further login requests until the flow completes and reloads it. Found, diagnosed and fixed by @aavdberg. No additional requests to Ajax: it removes requests.
- **A push registration saved without a token is repaired instead of reused (#450).** An interrupted Firebase registration could be persisted with no token; on every later start the integration treated that cache as valid, connected the push client and heartbeated happily while it had nothing to register with Ajax — the exact "connected but never delivers" shape #437 describes, from a different cause. Such a cache is now treated as absent and the registration is run again. The four push credential fields also have surrounding whitespace stripped when pasted into the options form. Found, diagnosed and fixed by @aavdberg. No additional requests to Ajax.
- **Home Assistant 2026.9 no longer warns about deprecated device-registry calls (#444).** HA 2026.9 flags two calls the integration made for every entity — the `via_device` link that attaches each detector to its hub, and the device lookup used when a device is removed on the panel side or the hub's firmware version is written to its device page — and HA 2027.8 removes them, at which point the integration would stop loading. Both now use the replacements. The via link is not a plain rename: the new form wants the hub's registry entry rather than its identifier, so every hub is registered before any platform adds entities, and a child whose hub has no entry yet is created without the link rather than rejected. Older cores (the integration still supports 2024.1) keep the previous behaviour, chosen at runtime from what the running core understands. Reported by @AnthonyOD with the exact file and line references. No additional requests to Ajax: this touches only Home Assistant's own registry.

### Changed
- **Setup tests no longer depend on a lazily created device registry.** Home Assistant 2026.8 stopped creating the device registry on first access, so the hub pre-registration added for #444 made every setup test that drives the integration with a bare mock fail on a current core. Test scaffolding only; no runtime change.

### Documentation
- **The example dashboard shows the exit / entry delays (#454).** A card under the alarm panel with the hub's own state, the space's exit delay and a live countdown to the end of a running delay, built from the attributes the `delay_panel_states` option adds. Standard cards only.

## [1.17.1] - 2026-08-26

Consolidates the `1.17.1-beta.1` → `beta.7` series, same bits as `beta.7`. Three of the six changes were confirmed directly on the reporters' own hardware: the recorder card by @pvangorp (7/7 channels), the dimmer by @mediaman66-dev, and the MultiTransmitter contact state by @Taknok's four-state capture — which also caught the first beta reading that state backwards, alongside @mfroger's report.

The temperature carry-forward rests on two separate live measurements by @wip3out3r rather than on a direct test of itself: the carry mechanism doing its job on the drop-plus-snapshot coincidence for the fields it already covered, and an independent sizing of the temperature hole taken the day before the fix shipped (seven of nine readings emptied, the two that already had a carry held). Temperature simply joins that proven list — his measurement sized the gap, it did not test the patch, and he was careful to say so.

Two more ship without hardware confirmation, deliberately and with unit-test evidence instead. The intrusion-alarm `triggered` state cannot be exercised on demand — it takes a real alarm, sirens and all — and its worst case is an over-reported alarm that clears on the next disarm or restart, nothing persisted. The DualCurtain motion sensor is a single entry in the per-family entity map, whose failure mode is the sensor staying `off`: exactly the situation it replaces. Both issues stay open until their reporters confirm; anything wrong there gets a patch release rather than waiting for the next feature.

### Added
- **MultiTransmitter wire inputs expose their contact state (#413).** Each wire input gets a "Contact" binary sensor (open/closed) mirroring the Ajax app's per-input Alerte/OK display, alongside the existing alert entity. Built on @Taknok's four-state hardware capture and corrected by his and @mfroger's field reports, which caught the state reading backwards in the first beta. An input with nothing wired reads as permanently open (broken loop), the same way the app shows it. No additional requests to Ajax: the state rides the status channel already flowing.
- **The alarm panel shows `triggered` during an intrusion alarm (#426).** The arming state Ajax serves has no "alarm firing" value, so the panel could never show it. It is now derived from the intrusion alarm push — set the moment the event arrives, shown over any armed state, and cleared when the system is next seen disarmed. Nothing is persisted, so a restart never resurrects an old alarm. Requires push notifications (FCM) to be configured.
- **NVR / video recorder boxes appear as devices (#425).** The recorder arrives in the device stream as its own row type, which was being skipped as unsupported — so the box never showed up while its cameras did. It now surfaces with its name, online state and channel counters (online / total). Disk, CPU, RAM and temperature are deliberately not included: the Ajax app reads those from a separate per-device endpoint the integration does not call.

### Fixed
- **The DualCurtain Outdoor now gets its motion sensor (#434).** The detector was missing from the per-family entity map, so it fell through to a tamper-only default and produced no motion entity at all — leaving an outdoor perimeter detector invisible to every automation while its battery and tamper entities suggested it was fully supported. It now reports motion like the other curtain detectors. Its two detection channels stay combined in one sensor: nothing the hub reports distinguishes which side saw the movement.
- **The LightSwitch Dimmer now actually switches the load on and off (#429).** Turning the light on or off from Home Assistant only wrote the brightness level, which the dimmer stores independently of its power state — so the percentage moved between 1 and 100 while the light itself stayed off. On and off now use the same power command the non-dimmer light switches already use, and the entity reads the dimmer's real switch state instead of inferring it from brightness. A turn-on that also sets a brightness sends the level first and then the power command — the same pair of actions the two gestures take in the Ajax app; nothing changes on the periodic traffic to Ajax.
- **Temperature readings now survive a degraded device snapshot (#403).** `temperature` joins the carry-forward that already protected battery, signal strength, humidity and CO₂ since 1.16.0: a snapshot that omits a previously-reported temperature keeps the last value instead of blanking the sensor. The gap was measured live by @wip3out3r the day before the fix shipped — seven of nine temperatures emptied within milliseconds of a snapshot burst, while the two devices that already had a carry held their values throughout.

## [1.17.0] - 2026-08-19

Consolidates `1.16.3-beta.1` unchanged — the beta and the stable are the same bits. Validated by @lexius on the reporting install (ghost deleted, live removals working), with a clean regression soak on a second install. The version is MINOR rather than PATCH because the release adds user-visible capability: "Delete device" on the integration's device pages.

### Fixed
- **A device deleted in the Ajax app is now deleted in Home Assistant too (#422).** The device stream announces a deletion explicitly, carrying a stripped residual record of the deleted device. The integration ignored the deletion marker and treated the notice as a regular update — so instead of removing the device it merged the residual back in, which also overwrote the device's name with the empty one the residual carries. That is the reporter's "Unnamed device": a ghost kept alive by the very message that should have removed it, surviving reloads through the warm-start cache, and undeletable by hand because Home Assistant only offers "Delete device" when the integration implements the removal hook. Three changes, none of them adding a single request to Ajax:
  - The deletion notice now removes the device everywhere — entities, device registry card, warm-start cache — the moment it arrives.
  - The complete device list the hub sends on every stream reconnect now resyncs membership: devices the hub no longer reports (deleted while Home Assistant was offline, or ghosts predating this fix) stop claiming state. Their leftover card is deliberately not auto-deleted — absence is weaker evidence than an explicit deletion notice, and a wrong delete would cost the card's area and name customizations.
  - "Delete device" now appears on the integration's device pages, and works for any device the hub no longer reports. Devices still alive on the hub are refused, and a wrongly deleted card re-creates itself from the next snapshot.

### Documentation
- README: the device-removal lifecycle (automatic on app-side deletions, manual "Delete device" for leftovers) added to the Features list — it shipped with this release and was otherwise undocumented outside the CHANGELOG.

## [1.16.2] - 2026-08-09

Consolidates `1.16.2-beta.1`. The status-channel signal the fix leans on was validated on hardware in both directions by @wip3out3r, whose baseline measurements also pinned the fix's bounds; #419 stays open as a watch until a real degraded snapshot exercises the carry.

### Changed
- Retired two leftovers of the #338 investigation, now that the fix is hardware-confirmed. The `HTS bypass probe` debug line answered its question — the hub's `0xB6`/`0xB7` status keys carry the deactivation *state*, now first-class on the bypass switch — and the one thing it could still do is mislead: on a transition it pairs a fresh byte with a deactivation state read from a model no HTS branch writes, so the two halves can briefly disagree. Also removed is the `1.16.0` error message saying a deactivation could not be cleared from Home Assistant, which `1.16.1` made unreachable.

### Fixed
- **The Bypass switch no longer silently flips to "protecting" when a degraded snapshot omits the deactivation state (#419).** A full device snapshot whose rows arrive without their usual detail — the same kind of event that emptied battery readings in #403 — also cleared the deactivation state, so a device the panel had deactivated read as protecting: on the reporting install, three deactivated sensors showed as active for over four hours. That is the wrong direction for a security integration to fail in, and a cleared switch cannot be told apart from a genuine reactivation.

  The hub itself reports each device's bypass state on the status channel the integration already listens to (the read validated on hardware in #338), so a snapshot's silence is now corroborated against it: still reported engaged — the deactivation is carried forward; reported lifted, too old to trust, or not reported at all — the clear goes through exactly as before. If the hub later reports the bypass lifted while the carried state is the only one in force, the carry is withdrawn on the spot. No additional requests to Ajax are made anywhere in this — the corroborating value already arrives on the existing stream. Found, measured and dated by @wip3out3r in #403.

## [1.16.1] - 2026-08-08

Consolidates `1.16.1-beta.1`, hardware-confirmed in both directions by @wip3out3r.

### Fixed
- **The Bypass switch now actually deactivates a device, and can put it back into protection (#338).** Deactivating from Home Assistant has never worked: the hub accepted every request and did nothing. The cause was the command's own vocabulary reading backwards — the value we sent to deactivate a device is in fact the one that *clears* a deactivation, so the hub was being asked to remove a bypass the device did not have, correctly reported that as done, and changed nothing. Turning the switch back off was then blocked outright in `1.16.0` on the conclusion that no clear value existed, when that value was the one we had been misusing all along.

  Both directions now send the right value, so the switch deactivates and reactivates like the Ajax app does. A device deactivated from the app still shows up here exactly as before, and the read-back that warns when the hub accepts a write without acting is unchanged — a success response is still not treated as proof the hardware moved. Found by @wip3out3r, whose measurements ruled out every other explanation and whose test of a second value is what made the pattern legible.

### Documentation
- The README no longer says re-activating a device requires the Ajax app — that described the `1.16.0` restriction this release removes.

## [1.16.0] - 2026-08-08

Consolidates the `1.16.0-beta.1` … `-beta.12` series.

### Added
- **The hub firmware entity now shows the version your hub is actually running (#388).** Until now it only knew about updates Ajax had *queued*, with no "installed" side to compare against — which is why it could look uninformative, and why every hardware-specific bug report had to start by asking the reporter to read the version off the Ajax app by hand. The version turns out to ride the same hub status channel that already supplies ethernet, cellular and signal strength, rather than the cloud snapshot the entity was reading.

  The `update.<hub>_firmware` entity now reports the running version as its installed version, and compares a queued update against it instead of against a placeholder. The diagnostics download gains a `hub_installed_firmware` section reporting it per hub.

  **The version also appears on the hub's device page**, under Firmware, which is the first place anyone looks and means it rides along on every future bug report without anyone having to be asked for it. This needed more than filling in a field: Home Assistant reads a device's details once, when the entity is first added, and the hub reports its firmware over a channel that is still connecting at that moment — so the version is written to the device registry as it arrives instead, and follows the hub across a firmware upgrade. Contributed by @aavdberg, who spotted the gap, diagnosed the timing, and confirmed it on his own hub by clearing the stored value and watching it come back.

  **If your hub does not report it, nothing changes** — the entity behaves exactly as before, and the diagnostics section says `null` for that hub rather than omitting it, so "my hub doesn't send it" stays distinguishable from "I'm on an older build". Hub firmwares genuinely differ in what they include on this channel, so this is a real outcome and not a failure. The version is also dumped in its raw packed form, so a hub that encodes it differently can be diagnosed from a diagnostics download rather than needing a live capture. Raised by @aavdberg, split out of #379.

- **You can now see which devices belong to each Ajax group (#366).** Group membership was only visible in the Ajax mobile app, so from Home Assistant there was no way to answer "which group is this motion sensor in?" — anyone automating against a multi-group system had to hard-code their own knowledge of the layout. Each group's alarm panel now lists its members in two attributes, `member_device_ids` and `member_device_names`, and the diagnostics download reports each device's group id and name.

  No new entities are created: the information rides the group panels that already exist when the space is in group / zone mode. Note that Ajax **rooms** are a separate taxonomy and do not answer this — rooms already map to Home Assistant areas, and a device has a room and a group independently of each other.

- **MotionCam Outdoor PhoD detectors now report their internal temperature (#412).** The family reads its temperature from the hub's status stream, the same channel the outdoor curtain PIRs and the sirens use, and was simply not on the list of families that channel is read for. Contributed by @Taknok, who captured the reading on **two** of his own detectors and checked each against what the Ajax app showed for the same device — which is what puts the family on the confirmed list rather than the inferred one. He also declined to add the non-PhoD `motion_cam_outdoor` on the assumption that it is the same board: since a temperature sensor is now created from the device family rather than from a value arriving, a family added on a guess that turns out not to report it would leave an empty sensor with nothing in the logs to explain it. That one waits for a capture.

- **Street Siren Double Deck sirens now report their internal temperature.** The three Double Deck variants — Street Siren DoubleDeck, Street Siren S DoubleDeck and Street Siren DoubleDeck Fibra — gain the **internal temperature** sensor the rest of the siren family already had. `1.15.1` added their volume and alarm-duration controls but deliberately left temperature out, because nobody with the hardware had confirmed which channel carried it; it turns out to be the hub's status stream, the same one the other sirens use.

  Alongside it, a fix to *when* the entity is created. A temperature that arrives over the status stream is absent from the snapshot the integration reads at startup, so an entity created only for devices already reporting a value would never exist for these sirens and the value would have nowhere to land. Creation now follows from the device family having a known source, not from a value being present yet — so the sensor appears at startup and fills in on the first update. Families whose temperature comes from the slower snapshot instead are unchanged and still wait for a real value, so no permanently-empty sensor is created for them.

  Thanks to @Taknok, who owns a Double Deck, found both device-type lists, worked out why there are two, and tested the change on the real siren — the confirmation this needed and that could not be produced without the hardware.

### Changed
- **The integration now understands 107 device families on the rich per-device read, up from 33 (#408).** That read is the only source for several per-device details — internal temperature, siren volume and duration, whether a device is deactivated — and for most families it returned nothing at all, because our copy of the protocol didn't describe them. The device wasn't broken and nothing was logged; the data simply had nowhere to land. One reporter measured 8 of his 13 devices reading as empty.

  This is why the same underlying gap was fixed four separate times, once per family (#229, #339, #354, #383), each costing whoever owned the hardware a capture and several restarts. The definitions are now taken wholesale rather than one family at a time.

  **What it does not do is invent data.** A family being understood does not mean it reports anything: several carry nothing but their arming state, and the outdoor curtain PIRs still have no temperature here — theirs continues to come from the hub status stream. Of the 107, 42 expose the deactivation detail behind #338 and 13 an internal temperature. Four families are deliberately still not modelled (LifeQuality, LifeQuality Lite and the two roller shutters): part of their definition isn't available to us, and a placeholder would be indistinguishable from a device reporting nothing — they are reported by the probe above instead. Found by @wip3out3r.

### Fixed
- **The no-stream fallback refresh can no longer blank readings the stream path preserves (#403).** When no live device stream is running — a failed start, or a teardown race — the integration refreshes its device list by polling, and that path replaced every device wholesale: battery, signal strength and the other carried-forward values were dropped silently, bypassing the very protection this release adds to the stream path. The polled fallback now applies snapshots through the same merge as the stream, and each application leaves a debug line naming which path it came through, so the two are distinguishable in a log.

- **A MotionProtect no longer reports a case tamper that never happened (#406).** On a hub that carries case tampering only on its status stream, `1.15.0` began reading two of that stream's per-device keys as the tamper signal. On the reporter's MotionProtect one of those keys sits at `01` permanently, on a device the Ajax app shows as perfectly fine and that a physical remount does not change — so the sensor came on at the upgrade and stayed on, with nothing the owner could do about it.

  The keys are simply not a tamper field on every device family. The routing is now limited to the two families where a capture tied a key to a *physical* tamper — a MotionProtect Curtain pulled off its SmartBracket and a Transmitter with its enclosure opened, both confirmed on hardware in #339 — and every other family is read for diagnostics only, as it was before `1.15.0`.

  **A tamper an earlier version raised is now actively withdrawn on upgrade.** Narrowing the rule was not enough on its own, and `1.16.0-beta.9` and `-beta.10` did not fix the reported symptom: the integration restores its devices from a saved copy, so the flag `1.15.x` set came back on every restart, and the only code path that could clear it was the one the new rule skips. A device whose family is no longer read this way now has that flag removed on the first status update after upgrading, and a full device refresh no longer re-applies it. If a tamper is also being reported the ordinary way, it stays — only the status-stream one is withdrawn.

  **If your hub reports tampering the ordinary way, nothing changes.** The regular per-device sources are untouched and still drive the sensor everywhere; this affects only the status-stream fallback. The cost of the narrower rule is that a family not on the list can miss a tamper that only that stream carries, which is the recoverable half of the trade — a sensor stuck on for an intact device is not. The debug probe still reports the keys for every family, so widening the list needs one capture of a key moving during a real tamper. Reported by @D0NY3NK0, who also caught that the first attempt had not worked, with a second install measured by @wip3out3r.

- **A device family our copy of the protocol doesn't describe no longer disappears without trace (#408).** When the hub reports a device in a shape we don't model, the data is discarded before the integration ever sees it — silently, at every log level. "This family is missing from our definitions" and "this device reports nothing" therefore produced identical logs, so each affected family had to be diagnosed from scratch by whoever owned the hardware, which is how the same underlying gap came to be fixed four separate times (#229, #339, #354, #383).

  The debug probes on the rich per-device read now report the wire number of the case that was dropped, which identifies the family exactly. Diagnostics only — no entity behaviour changes. Found by @wip3out3r, who measured 8 of his 13 devices reading as empty on that endpoint and named all five missing families.

- **The Bypass switch now explains itself when it cannot put a device back into protection (#338).** Turning the switch *off* never worked, on any install: the command went out carrying a value the Ajax hub rejects outright, and the attempt failed with a raw protocol error that named nothing you could act on. The hub's command vocabulary has no value that clears a deactivation — it can only name which kind to apply — so Home Assistant now says exactly that, in all supported languages, and points you at the Ajax app instead of failing obscurely.

  Deactivating a device from Home Assistant is unchanged, and so is what the switch reads: it still shows `on` for a device deactivated by anyone, including from the Ajax app or by an installer. Found by @wip3out3r, who measured the rejected command on his own hub and flagged it as a separate fault from the one #338 is about.

- **Re-authenticating an account with two-factor authentication now works, instead of looping forever (#399, #401).** If your stored session was rejected — most often because you revoked Home Assistant's session from the Ajax app, or it simply expired — the integration could get stuck in a state there was no way out of from the UI. It retried setup on a doubling delay indefinitely, and if you tried *Reconfigure* the code you typed was invalidated by the next background retry before you could submit it, so it looked as though your password or code were wrong. Disabling the entry stopped the loop but also hid the Reconfigure option.

  Two separate faults, both fixed. A login that needs a 2FA code now opens the reauthentication dialog rather than being treated as a temporary network problem. And completing that dialog now *sticks*: the session token was being read after the connection had already been closed, which wipes it, so the entry silently kept its old rejected token and sent you straight back to the dialog. Adding an account was never affected, which is why this only showed up for people who had been running for a while — the flow reads the token while the connection is still open.

  The integration also no longer registers a new device against your Ajax account on each reauthentication, and entries created before this recover on their next one. Reported, diagnosed and fixed by @aavdberg, who traced the real cause after his first fix turned out to address a genuine but different bug.

- **Battery and signal-strength readings no longer go blank for hours (#403).** When the hub sends a full refresh of its device list, each device is rebuilt from what that refresh contained — so any reading it left out disappeared until that device happened to report it again. Battery is the worst case: one sitting at 100% has nothing new to send, so it could stay empty indefinitely. Measured on a real system as 1 of 13 batteries and 1 of 11 signal strengths still empty **four hours** later, while the feed itself was demonstrably healthy the whole time.

  Battery, signal strength, humidity and CO₂ now keep their last known value when a refresh omits them, on the principle that a refresh leaving a measurement out does not make the previous value wrong, only older. A value that *is* included always wins, so readings still track your devices live.

  Deliberately limited to measurements. Alerts such as case tampering and lid-opened are left to clear exactly as before, because for those the absence of the signal *is* the all-clear — carrying them forward would pin an alert on permanently.

  **Temperature is not covered yet** and can still blank for the same reason. Fixing it conflicts with a deliberate safeguard against showing a temperature on devices that have no temperature source, and untangling the two needs more care than this release allows; #403 stays open for it. Found, measured and traced by @wip3out3r, whose data identified the exact function responsible — including ruling out the two other places that could have caused it.

- **A routine record in the hub's device list is no longer reported as something the integration couldn't handle (#383).** One entry in that list arrives in a shape our copy of the protocol doesn't describe, and the debug log called it an *unsupported device*. It turns out not to be a device at all: it is the space's **access-card count** — how many cards or tags exist, plus the key the Ajax app sorts them by — which is why it appears exactly once per full refresh whatever your system contains, and why the installation it was first seen on had no hardware matching it.

  The log line now says what the record is and reports the count, instead of dumping bytes under a heading that implied something was wrong. Nothing was ever broken: no entity was affected, and skipping the record is still the right thing to do since a count of credentials is not a device. Two installations confirmed the reading independently — one with access tags reports a count, and @wip3out3r's, with no keypad and no cards, reports none at all. Found and fully decoded by @wip3out3r.

  A record that is *not* this one still gets the full diagnostic treatment, deliberately: the recogniser is strict, so a genuinely new variant surfaces its structure rather than being quietly absorbed.

- **The hub IMEI sensor is now created whether or not the SIM read has succeeded yet (#379).** It was only offered for hubs the integration had already read SIM details from, so a read that failed or had not completed produced no entity at all — and because Home Assistant never removes an entity an integration stops offering, one created on an earlier start sat `unavailable` indefinitely with nothing to explain it. Creation now follows from the hub being present; whether the SIM details are readable is left to the entity's availability, which is what availability is for.

  The sensor is disabled by default, as before, so a hub that never reports SIM details does not gain a visible dead entity. Fixed by @aavdberg, who reported the issue and sent the fix.

- **Diagnostics downloads no longer contain the names you gave your devices, spaces, groups or keyfobs.** These downloads are routinely attached to bug reports, and Ajax names are free text that people set to where a thing is — street names and home addresses turn up as device names in practice. Each name is now reported as a **length** instead of a value, which still distinguishes a named device from an unnamed one while identifying nothing. It is the same rule the hub IMEI already followed.

  Nothing is lost for troubleshooting: devices are keyed by their id, which is what links them across the file, and a device's group id still matches an entry in that space's group list. The one casualty is the resolved group *name* that briefly accompanied each device's group id in `1.16.0-beta.1`; the id linkage replaces it. Raised by @wip3out3r, whose own device names include a street and an address.

- **A hub on battery no longer asks for a full snapshot every time one of its devices reports in (#386).** During a grid outage a hub was requesting a complete status refresh — about 8.6 KB — every few minutes, at the exact moment it was running on battery over a degraded link. The cause was a routine status update from an ordinary device being counted as evidence about the *hub's* mains power: the check that separates the two only recognised one of the layouts the hub uses to frame these messages, so on the other layout a device's operational-state byte was read as the hub's power flag. Since it disagreed with the stored state, every such update triggered a refresh to settle the disagreement.

  The refresh is now skipped for any message that carries device data, which is never evidence about mains power. A genuine power change still arrives on its own flat message and still requests immediate confirmation, so nothing gets slower to react. Thanks to @aavdberg, whose log excerpt caught both halves of the message in the same millisecond and made this findable. Note this addresses the request storm; whether it also explains a second `unplugged` being logged is still being investigated in #386.

- **Arming with a malfunction present now says so in the logbook (#387).** When you arm from the Ajax app while something is faulty, the app calls it "activated with malfunction" — but Home Assistant's logbook showed a plain "Armed", with nothing to distinguish it from a clean arm. The detail was never lost, only hidden: the hub sends it as a distinct qualifier, which the integration deliberately flattens to a plain `arm` so automations keep matching, leaving the original on the event's `raw_tag` where only someone inspecting events by hand would find it.

  The logbook entry now ends with **"— with malfunctions"** for those arms, on both normal and night-mode arming, and for group-level as well as space-wide events. Nothing else changes: the event type your automations match on is still `arm`, and the alarm panel state is unaffected. Thanks to @aavdberg for spotting it and for correctly identifying where the detail was being dropped.

- **A hub whose SIM details can't be read now says so, instead of quietly dropping the IMEI sensor (#379).** The IMEI sensor is only created for hubs the integration has successfully read SIM details from. When that read failed there was no sign of it: the error was swallowed into a debug-level line that named neither the cause nor the status code, so the sensor either never appeared or — if it had been created on an earlier start — sat at `unavailable` indefinitely, because Home Assistant does not remove entities an integration stops offering.

  The first failure per hub is now a warning naming the cause and saying which sensor it affects; repeats stay at debug so a hub that can never report a SIM doesn't fill the log. A hub that genuinely has no modem is not an error and stays silent. The diagnostics download also gains a `sim_info` section saying, per hub, whether the read has ever succeeded — the IMEI itself is not included, only its length, since these downloads get shared publicly.

  This is diagnostic groundwork rather than a cure: if your IMEI sensor is unavailable it will now tell you *why*, which is what we need in order to fix the underlying cause.

### Documentation
- **Two things the keyfob documentation got wrong (#311).** It said a keyfob can only be deactivated by an installer or monitoring company because "there's no toggle in the Ajax app". There is one — *forced deactivation* on a SpaceControl — but it is a **different mechanism**: measured on hardware, it is the same temporary exclusion the bypass switch already shows, and it leaves the flag the **Active** sensor reads untouched. Anyone who tried it expecting to produce the missing *inactive* example got a reading that had not moved.

  And it implied every install gets keyfob sensors. Some hubs report the keyfob as an **ordinary device** instead, which gives it its own device page with the usual bypass switch and **no Active sensor at all** — so an install can have a SpaceControl and no *Keyfobs* device, by design rather than as a fault. That also scopes the "every keyfob seen so far is active" observation: it only ever covered hubs of the other kind.

  The README now says both, and a debug-level probe reports that second kind of hub's keyfob settings so it can contribute to confirming the indicator, which until now it structurally could not. No entity, state or attribute changes. Both findings are @wip3out3r's, from a hub that has a SpaceControl and no keyfob entity.

## [1.15.1] - 2026-08-04

Consolidates the `1.15.1-beta.1` – `1.15.1-beta.12` series. Every fix below was confirmed on the reporter's hardware or on a live Home Assistant install before this release.

### Added
- **An Ajax Button in control mode now fires a Home Assistant event (#348).** A Button set to *control* mode was invisible to Home Assistant: the hub sends no push notification for it, so there was nothing to trigger an automation with. Each Button now gets its own `event` entity (`device_class: button`) that fires **`pressed`**, with the press timestamp the hub reports as an attribute. It rides the hub's status channel rather than push, so **it works without FCM configured**, and it fires whether the system is armed or disarmed. A Button in *panic* mode is unaffected and keeps firing `panic` on the hub's security event entity.

  **One event, not two.** Short and long click cannot be told apart — the hub moves a single value for both, identically, and it does not push control-mode presses at all, so no other source exists to distinguish them. If you were hoping to bind two different actions, that isn't possible with what the hardware reports.

  Two notes. The DoubleButton is panic-only and reports nothing at all in control mode, so it gets no such entity. And as with any event entity, a Home Assistant restart re-delivers the last event with a fresh timestamp — guard automations with `not_from: unavailable` / `unknown` on the state trigger, exactly as the bundled blueprints do. Thanks to @raven2k24 for the hardware captures, including a 20-hour-old timestamp at boot that proved the value only moves on a real press, and to @wip3out3r for the independent negative control on hardware without a Button.

- **Siren volume and alarm duration now work on six more siren models (#354).** The Street Siren DoubleDeck, Street Siren S DoubleDeck, Street Siren DoubleDeck Fibra, Street Siren S, Street Siren Fibra and Street Siren Plus Fibra only showed case tamper, bypass and battery — the two config entities the other sirens have got skipped, because the integration couldn't read those models' settings from the hub and creating entities it could never fill in would have been worse than leaving them out. It can read them now, so all six gain a **Siren volume** select and an **Alarm duration** number, behaving exactly as on the models that already had them (values appear on the same throttled refresh after startup, and changing one needs an account with device-edit permission). Note these models report only their settings on this path, so they still get no internal temperature — if you own one and would like that too, say so in #354. Thanks to @nimahel for reporting it on a DoubleDeck.

### Fixed
- **One unreadable push message no longer stops real-time events for good (#373).** A single incoming notification that the push library could not decrypt shut the push connection down — and because the shutdown happened before that message was acknowledged, Ajax's push provider kept redelivering it, so every reconnection died on the same message. One reporter measured it killing the connection 16 times over three and a half hours; restarting Home Assistant did not help, and neither did rebooting the machine, because the undelivered message is held on the server. The only escape was to get a new push registration, which some people had stumbled on by changing their Ajax account password.

  Three things change. A message that can't be read is now skipped instead of taking the connection down, so **one event is lost rather than every event from then on** — and because the connection survives, the message is acknowledged and stops coming back. If the connection ever does get stuck in that loop for another reason, the integration now **replaces its own push registration** to break it, instead of leaving you to it. And since none of this was visible — your alarm state stays correct throughout, because it comes from polling and the hub connection and never from push — repeated failures now raise a **Repair notice** under Settings → Repairs, which clears itself once push has been stable for a while.

  The underlying fault is in the push library, in how it decodes one of the keys that accompanies each message; a fix has been submitted upstream and this workaround will be removed once a release includes it. Thanks to @wip3out3r, who found this while investigating something unrelated and measured the redelivery pattern precisely enough to prove what was happening rather than leaving it a plausible theory. If push has ever stopped for you until a restart or a password change, this is very likely why.
- **Siren volume, alarm duration and some per-device temperatures were dead in the 1.15.1 betas (#354).** Between `1.15.1-beta.6` and `1.15.1-beta.10`, everything sourced from the hub's rich per-device snapshot failed on **every** install: the two siren config entities stayed `unknown` for all siren models — not just the six added in this release — and so did the internal temperature of the device families that read it from that same snapshot. Stable releases were never affected.

  The cause was in the release artifact rather than in any code path. The integration's generated protobuf stubs embed the version of the compiler that produced them, and protobuf refuses at import time to load a stub built by a version newer than the runtime installed alongside it. Three of the 1614 stubs — the ones touched while adding the siren models — were regenerated by a newer compiler than the other 1611, so importing them raised `VersionError` on any Home Assistant, all of which ship an older protobuf than that compiler required. The dedicated read that fills in these values imports those stubs, so it raised before issuing a single request. That is also why the diagnostic added in beta.7 never printed anything useful: it sat downstream of the import that was failing.

  The three stubs are rebuilt with the pinned compiler, so all 1614 now agree. Two related latent faults are fixed alongside: the integration declared it worked with `protobuf>=4.25.0` and `grpcio>=1.60.0`, while its generated code has long required `6.31.1` and `1.75.1` respectively — an install that resolved to either advertised floor would have failed on import. The requirements now state what the code actually needs. Nothing else changes, and no entity behaviour depends on this beyond the values above returning.

  This class of defect had occurred once before and was fixed by recompiling, without anything to stop it recurring. It now cannot ship again unnoticed: the compiler is pinned to an exact version instead of a floor, the build fails if the installed compiler disagrees with that pin, and a test asserts that every generated stub agrees on one version and that both versions are loadable by the oldest protobuf and grpcio the manifest accepts. Regenerating a single proto is now a first-class command (`make proto PROTOS=…`) that routes through the pinned compiler, which is what the ad-hoc invocation behind this bug bypassed.
- **Security events now report *what kind of thing* caused them (#367).** Every security event carries the name of whatever triggered it — the keyfob, the keypad, the person who armed from the app — alongside a `device_type` saying what that source is. The name was always right, but for arm/disarm the type was nonsense: it reported a hub model such as `HUB_PLUS` where it should have said the source was a person, a keyfob or a scenario. The cause is that the source was located by searching the message for a device-shaped record, and the four kinds of source Ajax sends share an identical shape — so the wrong one was found and its type translated with the wrong dictionary. The source is now read from the part of the message that states which kind it is, and each is translated with its own vocabulary, so `device_type` is meaningful for every event. **If you filtered automations on `device_type` for arm/disarm, those values change** — from meaningless hub model names to real ones like `SPACE_MEMBER` (a person), `SPACE_CONTROL` (a keyfob) or `KEYBOARD`. Nothing that filtered on `device_name` is affected. This is the same class of defect as #320, fixed there for the event type itself; the source field had been left out.
- **A siren whose settings can't be read now says why, at the default log level (#354).** When the read that fills in **Siren volume** and **Alarm duration** failed outright, both entities sat on `unknown` indefinitely and nothing explained it: the failure was logged at debug level only, and the one fact that distinguishes a permission denial from a timeout — the error's status code — was buried at the end of a traceback, exactly the part a log viewer truncates. The first failure for each siren is now a warning naming the cause and saying which entities are affected; repeats stay at debug so a permanently unreadable siren can't fill the log. This is diagnostics only — no entity behaviour changes. Thanks to @nimahel, whose log showed the read was raising an error rather than returning empty, which the previous diagnostic couldn't have revealed.
- **Arm/disarm device triggers no longer fire for every Ajax system (#358).** With more than one hub configured, an automation using a device trigger scoped to one alarm system also ran when any *other* system armed, disarmed, or fired any other security event. Two independent defects had to be fixed, and the first release to address this (1.15.1-beta.8) only fixed one of them, so the symptom survived it.

  First, the trigger matched only the event type, because the underlying bus event never said which hub it came from. The bus event now carries the firing hub's `hub_id` (and `space_id`), and device triggers filter on it.

  Second — and this is what kept the bug alive — an incoming push was matched to its space by scanning the payload for the hub's id encoded as raw bytes, but the hub's id travels as ASCII text, so the scan never matched a genuine payload. Every push therefore took a fallback path that delivered it to **every** space on the account. Each copy was stamped with a real hub id, so the filter above worked perfectly and was simply handed five authentic-looking events. Routing now reads the space the push names (`Notification.space.id`) directly, and a push that still can't be placed is no longer fanned out: on a single-space account it goes to that space, and on a multi-space account it is dropped with a warning rather than delivered to the wrong hubs, with alarm state following from the snapshot refresh that every push already triggers.

  The second defect also meant a hub's arm/disarm was applied to the alarm panel of every other system until the next snapshot corrected it, so multi-system installs should see panel states settle correctly too. Single-system installs were never affected by it: with one space, delivering to "every space" was always right. Existing automations pick up the fix on the next reload with no re-configuration; anyone listening to raw `aegis_ajax_event` bus events gains the two new fields and loses nothing. Thanks to @nerdtechse for reporting it and for testing beta.8 and telling us it was still broken.
- **A Relay's Voltage sensor no longer reads ~1000x too high (#325).** A Jeweller Relay fed from a 12 V supply reported `11,671 V`. The reading was surfaced exactly as the device sends it, which is correct for the WallSwitch and Socket families — they report whole volts — but the Relay reports **millivolts**, so every Relay voltage was inflated by a factor of 1000. It is now converted, and the same Relay reads `11.671 V`. The opt-in derived Power sensor multiplies current by voltage and so inherited the same error; it is corrected by the same change. The unit does not depend on how the Relay is powered — a mains-fed unit was equally affected — and no other device family changes. `relay_fibra_base` is deliberately left as-is because its unit has not been observed on hardware; if you own one and its Voltage sensor looks wrong by a factor of 1000, please say so in #325. Thanks to @AdamG100 for the screenshot that made the scale obvious.
- **The Button activity probe no longer misses the first event after a restart (#348).** The diagnostic probe added in 1.15.1-beta.4 records a key's first sighting silently, so the boot snapshot doesn't look like a button press at every restart. That reasoning holds for a key the hub re-reports in its periodic snapshot, but not for one that only ever arrives in a live delta — there the first sighting *is* the event, and it was being swallowed. The probe now tells the two apart: once a device's snapshot row has arrived without a given key, a later delta carrying that key is logged. This affects DEBUG diagnostics only, no entities. Thanks to @wip3out3r for spotting it from a press that produced no log line.
- **"FCM credentials rejected" now tells you which cause you actually hit (#344).** That failure has at least four explanations needing different fixes — Google doesn't recognise the string as an api-key at all (`API_KEY_INVALID`), the key is real but not the FCM-scoped one (`API_KEY_SERVICE_BLOCKED`), the key is fine and its Android restriction rejected the request (`API_KEY_ANDROID_APP_BLOCKED`), or the key is real but not authorised for the project's app (`PERMISSION_DENIED`) — and the library the integration uses collapses all of them into one error string, keeping Google's reason to its own logger. The warning therefore asserted the wrong-key explanation for every case, sending users who already had the right key off extracting more keys. The integration now asks Google directly why it rejected the key, on the failure path only, and logs the reason with advice matching it: re-check the pasted value in the first case, try another key in the second, check the app label chosen during setup in the third (it determines the package the request identifies itself as, and an unmapped co-brand sends none at all), and re-read all four values from a single app build in the fourth — the api-key is the one value no offline check can tie to the other three. The general message no longer picks a side, and no longer claims an HTTP status the failure doesn't guarantee. Thanks to @Thomas-v87 for the logs that showed the real reasons were none of the two originally modelled.
- **A device deactivated in the Ajax app now shows as bypassed in Home Assistant (#338).** Deactivating a device from the Ajax app (or having an installer do it) excludes it from protection, but Home Assistant kept showing its bypass switch `off` and the sensor itself as live protection — the integration only read the snapshot's bypass flag, which stays unset for that path, and never read the four deactivation statuses the hub actually reports. The bypass switch now reads `on` for a device deactivated by anyone, through either the initial snapshot or a real-time change, and a new `deactivation_kinds` attribute names the mode in force so an automation can distinguish a fully disabled device from one with only its tamper protection off. Mind Ajax's own wording, which the attribute preserves: `temporary_deactivation_*` is the **permanent** deactivation (until re-enabled) and `one_time_deactivation_*` lasts a single arming cycle. The diagnostics download reports both sources side by side. Thanks to @wip3out3r for the four-mode hardware mapping and to @aavdberg for the analysis that scoped it.
- **A bypass command the hub silently ignores no longer leaves the switch showing the wrong state (#338).** Toggling the bypass switch could return "success" from the hub while nothing changed on the device, so the switch sat in the requested position indefinitely. Every bypass write is now followed by an independent read-back of the device a few seconds later; the switch reverts to what the panel really reports and a warning naming the device is logged, making the silent no-op visible instead of invisible. The warning states what happened and deliberately does not guess why: the obvious explanation (the account lacking rights) was ruled out on hardware — the same account deactivates the same device successfully from the Ajax app, so only the command path is inert. If you hit this, deactivating from the Ajax app works; the cause is still being investigated in #338. Thanks to @wip3out3r for validating the whole change against a live deactivation fixture and for catching the misleading wording.

### Documentation
- **The "Learn more" button on some Repair notices went to the wrong place.** Three of the push-notification Repairs and the hub-network-sensors one linked to README sections that no longer existed under those names, so the button dropped you at the top of a very long page instead of at the relevant section. The links now resolve, and the README carries explicit anchors so that rewording a heading cannot silently break them again — a test now checks every Repair link against the README and fails if one stops resolving.
- **README corrections at the stable cut.** The Repairs feature list now includes the push-recovery Repair added by #373; the Sirens row states which models report internal temperature and which report only their settings; the Relays row explains that the dry-contact Relay has no load metering, so its Voltage sensor shows the module's supply voltage while Current and Energy read 0 by design (#325); and the event-attribute table documents the real arm/disarm source types (`SPACE_MEMBER`, `SPACE_CONTROL`, `KEYBOARD`) introduced by #367.

## [1.15.0] - 2026-07-26

Push security events are decoded correctly, device tamper sensors work for the first time, and sirens become configurable from Home Assistant. Consolidates the 1.15.0-beta series.

### Added
- **StreetSiren and HomeSiren settings are now adjustable from Home Assistant (#310).** Each siren gets two config entities: a **Siren volume** select (Very loud / Loud / Quiet / Disabled) and an **Alarm duration** number (seconds). Both are read from — and written back to — the same settings the Ajax app exposes, using the hub's per-device update path. Changing a value requires an account with device-edit permission (a limited account gets a clear error). The current values are read from the rich per-device snapshot on the same throttled timer as the internal temperature, so they may take a moment to populate after startup.
- **Persistent notifications for security events (2.2).** A new option (Options → "Show security events as persistent notifications") surfaces selected events as Home Assistant persistent notifications that stay visible until dismissed or Home Assistant restarts — a built-in alternative to wiring your own `persistent_notification.create` automation off the event entity. The event set is fully configurable; it defaults to real incidents (alarm, panic, tamper, fire, CO, flood, glass break) and can be widened to include arm/disarm, motion, doorbell and more. Off by default. These events are delivered over push (FCM), so push must be configured for them to arrive. Repeats of the same event on the same device refresh the existing card instead of stacking duplicates.
- **Per-device firmware update entities (2.1).** The hub-level firmware update entity (1.4.0-beta.5) now has a per-device counterpart: each non-hub device gets an `update.<device>_firmware` entity sourced from the same read-only `streamHubObject` snapshot (field 200, `device_firmware_updates`). It surfaces the pending target version, download progress (during the download phase) and a security-critical flag, and renders "Up to date" when Ajax has no update queued for that device. A failed install attempt is called out in the entity's summary, and both firmware maps are included in the diagnostics download. Like the hub entity it is informational only — no install button and the integration never calls the install RPC. Entities are **disabled by default** (a typical install has 10-30 devices); enable the ones you want to watch.

### Fixed
- **Push security events are no longer mislabelled or dropped (#339, #320).** Two independent defects in how push (FCM) payloads were decoded. First, the event type was guessed by trying each of the four event vocabularies (space / hub / video / smart lock) in turn and keeping the first that decoded — but those vocabularies share field numbers, so a hub event decodes cleanly as an unrelated space event: a case-tampering event came out as a duress disarm, a malfunction as "armed night", and a genuine arm as a disarm. Since arm/disarm events also drive the alarm panel's state, a device event could flip the panel's displayed mode. Events the integration doesn't map were affected too: a photo-on-demand notification from a MotionCam was reported as "armed night" (and set the panel to night mode), relay switching as arm/disarm, and a device turning off as a panic button press. The event's own container now identifies its type, so a hub event is read against the hub vocabulary and nothing else; an unrecognised event is reported as nothing at all rather than as a made-up arm/disarm. **Behaviour change:** if you built an automation on one of those phantom arm/disarm/panic events, it will stop firing — that event was never real. Everything a push triggers beyond the event itself (the authoritative state re-read) is unchanged and still happens on every push, including pushes that carry no event the integration recognises. Second, the scan that locates the event inside the payload advanced through it incorrectly: it did not skip over numeric field values, so a single value byte could throw the scan out of step and make it step over the event; it discarded the shortest valid events; and it refused to look inside any container larger than 500 bytes, which silently swallowed every event in a larger push (arming from a scenario, for example). The payload is now walked properly, with no size limits. Thanks to @wip3out3r for the hardware captures that exposed both, and to @Daniel-Vitanza for the log in #320.
- **Case tampering is now also read from the hub's status stream (#339).** On some hubs the device stream carries no case-tampering signal at all, so the fix below had nothing to work with and the tamper sensor stayed off even when a device was physically pulled off its bracket. Those hubs report it on the status stream instead, which the integration now routes to the same tamper sensor — and keeps across a device-stream refresh, which would otherwise wipe it. It clears when the device reports intact again, but not while the device stream still reports an open lid or a drilled case, so the two sources can't cancel each other out. Only the two known values are acted on; anything else is logged and ignored rather than risking a false alarm. Thanks to @wip3out3r for the hardware capture that identified it.
- **Device tamper sensors can now actually turn on (#339).** The per-device "Case tampering" binary sensor of ~40 device families (Door Protect, MotionProtect, MotionCam, keypads, sirens, …) was bound to a status the Ajax stream never emits, so it could never trigger — physically opening a device or pulling it off its SmartBracket showed nothing in Home Assistant (while Ajax itself alarmed, confirmed live with a monitoring-station callout). The real granular signals (`lid_opened`, `smart_bracket_unlocked`, `case_drilling_detected`) now fold into the tamper sensor on both the snapshot and the real-time delta paths, clearing correctly when the case is closed / remounted. Hub lid sensors keep their separate entity. The alarm panel's "issues" summary also reports tampered devices again.
- **Siren volume/duration entities now confirm the new value within seconds of a change (#310).** After a successful write the values were only re-read on the shared 900 s snapshot timer, so the entity kept showing the previous value for up to ~15 minutes — indistinguishable in the UI from a rejected write. A successful write now schedules a targeted per-device settings re-read (single-flight per device) so the entity reflects the actual hub value shortly after. It remains a read-back rather than an optimistic update, so an accept-but-inert hub response would still surface as the value snapping back rather than being masked. Thanks to @wip3out3r for the 2×StreetSiren hardware validation that surfaced this.
- **Event-entity blueprints no longer fire a phantom notification on integration reload or HA restart.** The `security_event_notification`, `tamper_alert` and `intrusion_alarm_capture` blueprints triggered on any state change of the event entity; when the entity is restored (options change, integration reload, HA restart) it re-delivers its last event with a fresh `last_changed`, which slipped past the recency condition and re-sent the last notification (or re-captured photos). The state triggers now carry the same `not_from: unavailable/unknown` guard the other blueprints got in 1.11.4. Re-import the blueprint (or re-copy the file) and reload automations to pick up the fix.
- **Hub firmware update entity now reports download progress.** The `PROGRESS` feature flag was missing, so Home Assistant silently ignored the entity's in-progress signal while the hub was downloading a queued firmware update.

### Documentation
- README updated for this release: the siren volume / alarm duration entities, the per-device firmware update entities and the persistent-notifications option are now listed under Features and in the Supported Devices table; the data-source table records that case tampering is dual-sourced (device stream on some hubs, status stream on others) and that per-device internal temperature and keyfob activity ride the status stream; the settings roadmap item is marked as partially shipped.

## [1.14.0] - 2026-07-18

Cloud live-video feasibility probe in diagnostics and mains-power flapping fixes. Consolidates the 1.14.0-beta series.

### Added
- **Diagnostics now probe whether Ajax's cloud live-video stream is available to the account (#322).** Groundwork for possible camera support on cloud-hosted Home Assistant (where the local ONVIF/RTSP path isn't reachable): the diagnostics download now includes a read-only, best-effort check of the WebRTC signalling the Ajax app uses for remote live view. It reports only whether the account is authorised to start a session and a summary of the offered connection servers — never any credentials, addresses or video — and negotiates no actual stream. It has no effect on normal operation and is skipped when there are no video devices.

### Fixed
- **Mains power no longer flips to "Plugged in" from a mis-parsed delta while the hub is on battery (#323).** During an outage the hub emits many small update frames; the fragile positionally-paired "direct delta" path could surface a stray `0x03` byte (the power key) from a mis-aligned per-device delta (escape handling can also shift byte boundaries) and wrongly report mains power restored — even though the Ajax app showed a stable "no power". The mains-power flag is now only trusted from the authoritative full STATUS/SETTINGS snapshot (which locates the hub section by an exact hub-id marker). When a direct delta *does* carry a power flag that differs from the last-known state, an immediate authoritative snapshot refresh is requested (single-flight per hub, so the outage delta burst can't turn into a request storm), so a genuine change is confirmed within a couple of seconds rather than waiting for the periodic poll. A DEBUG diagnostic logs the source frame and raw bytes whenever the power flag changes.
- **Mains power no longer resets to "Unplugged" on every HTS reconnect (#323).** A fresh streaming client is created on each (re)connect, and the first snapshot after reconnecting was parsed from scratch — so any field the hub didn't repeat in that frame (most visibly the mains-power flag) silently fell back to its default of "Unplugged". During an outage the hub reconnects repeatedly, which turned into a burst of spurious Unplugged/Plugged-in events. The client now carries the last-known hub state across reconnects, so a field only changes when the hub actually reports a new value.

## [1.13.0] - 2026-07-14

MotionProtect Outdoor internal temperature. Consolidates the 1.13.0-beta series.

### Added
- **MotionProtect Outdoor now exposes its internal temperature (#269).** The outdoor motion detector doesn't report temperature on the device stream the integration reads for most families, so no temperature sensor was created for it. It is now sourced from the same live internal-temperature value the Ajax app shows (HTS sub-key 0x02, the same path already used for the Curtain Outdoor Plus/Base and the sirens), confirmed against a reporter's capture of a MotionProtect Outdoor Jeweller.

## [1.12.1] - 2026-06-20

Siren temperature fix. Consolidates the 1.12.1-beta series.

### Fixed
- **Siren temperature is no longer stuck and now matches the Ajax app (#312).** The HomeSiren/StreetSiren temperature was read once at startup and then frozen for the lifetime of the integration (both the per-device gRPC refresh and the HTS path skipped any device that already had a value), and the gRPC source was the internal board sensor, which reads a few degrees high on a sun-exposed StreetSiren. Sirens are now sourced from the same live internal-temperature value the Ajax app shows (HTS sub-key 0x02), so the reading matches the app and updates over the push channel. The freeze is fixed for every per-device temperature source, so the Curtain Outdoor family also updates live now.

## [1.12.0] - 2026-06-19

WaterStop valve open/close control. Consolidates the 1.12.0-beta series.

### Added
- **WaterStop valves can now be opened and closed from Home Assistant (#308).** The Ajax WaterStop (and WaterStop Fibra) valve entity was read-only; it now supports open and close, so you can shut off or restore the water supply from the dashboard or an automation. State, transition and the `stuck` attribute keep working as before.

## [1.11.5] - 2026-06-18

LifeQuality air-quality sensors, NVR-bridged camera fixes, and groundwork for camera support. Consolidates the 1.11.5-beta series.

### Added
- **LifeQuality now exposes temperature, humidity and CO₂ sensors (#302).** Previously the LifeQuality air-quality monitor only surfaced battery and signal. Its temperature (°C), humidity (%) and CO₂ (ppm) readings are now created as standard Home Assistant sensors, confirmed against the Ajax app on real hardware. Any sensor threshold/fault flags the device reports are included in the diagnostics download.
- **Diagnostics now probe each VideoEdge camera's ONVIF/RTSP settings and LAN address (#282).** As a step towards camera support, the diagnostics download now reports, per VideoEdge, whether ONVIF/RTSP are reachable and on which ports (auth flag and user count, never usernames) plus the device's LAN IP and MAC — the connection details needed to point Home Assistant's native ONVIF integration at the camera. Read-only and best-effort; it never affects normal operation, and the video stream itself stays local RTSP/ONVIF, not carried over the Ajax cloud.

### Fixed
- **An Ajax NVR no longer makes a doorbell or camera appear twice, with the activity on the empty card (#290).** When an NVR (e.g. the NVR HAC) is added, it re-publishes an existing camera/doorbell channel as a second device. That republished twin carries no sensors and no doorbell event entity, yet the doorbell-ring and motion pushes attributed to it — so after 1.11.4 named the NVR channel properly, the doorbell card showed all the sensors but no activity, while a second bare card had the activity. The republished channel is now recognised (via the channel-source linkage exposed in 1.11.4) and collapsed into the primary device, and its pushes are redirected there, so a single card shows both the sensors and the ring/motion activity. A genuine NVR-native channel (one that isn't a republish of an existing device) is unaffected. Motion in particular is now attributed correctly on an NVR-bridged camera: such a camera has no Jeweller "twin" device, so the push's hardware id (the camera's primary video id) had nothing to resolve against and motion was dropped — the doorbell ring still showed only because it has a single-doorbell fallback that motion doesn't. That hardware id now maps to the camera, so motion lands too.

## [1.11.4] - 2026-06-15

Group/zone night-mode arming correctness and FCM connection resilience. Consolidates the 1.11.4-beta series.

### Fixed
- **Partial (Night) arming from the Ajax app or a keypad now reports `armed_night`, not `armed_custom_bypass` (#284).** On a space in group / zone mode, the panel derived its state from the per-group arm flags and treated a partial arm as a custom-bypass arm, so an app-side or keypad Night arm left the Home Assistant panel showing `armed_custom_bypass`. The panel now recognises the night-mode flag and settles to `armed_night`, matching whole-space behaviour and the Ajax app.
- **Group panels now follow an arm/disarm driven from a keyfob, keypad or scenario, without push (#287, #284).** Group-level arm/disarm initiated from a peripheral (rather than the app) arrived only as a hub status update with no `type=0x08` space event, so per-group panels didn't re-read and could stay on the previous state until the next snapshot. The integration now recognises these peripheral-originated space events and nudges an immediate group/night snapshot re-read on the matching FCM security event, so group panels update within about a second on installs without push (and stay instant with push).
- **A keypad *Full Arm* of a single group is now picked up (#284).** Arm Away / Full Arm of a group via the Keypad Plus reaches the integration purely as a local hub status update (the internal arm flag flipping), with no space event and no clear trigger to re-read — so the group panel didn't follow. That arm flag in the STATUS_UPDATE stream now drives the re-read.
- **Keyfobs no longer disappear from Home Assistant after an integration reload (#284).** The keyfob discovery dispatch ran off the event loop, which dropped the entities on reload; it now runs on the loop so keyfob entities survive a reload.
- **The FCM push client no longer pegs the Home Assistant event loop during a reconnect storm (#285).** A burst of firebase-messaging reconnects could saturate the loop; the client is now throttled, restarts under supervision, and detects a zombie client and retries a failed initial start instead of silently giving up.
- **Example/blueprint state triggers no longer false-fire on reload (#293).** Bare `to:` state triggers in the shipped automation blueprints fired on the `unavailable → state` transition that happens on every integration reload; they now guard against `unavailable`/`unknown` transitions.

### Changed
- **Video channels republished by an Ajax NVR are now identified for diagnostics (#290, #282).** When an NVR (e.g. the NVR HAC) republishes a doorbell or camera, the channel arrives as a video type the integration didn't recognise, producing a second generic card that the device's activity followed. The NVR `About.Type` variants are now mapped and the raw video-channel identity is exposed, as the groundwork for de-duplicating the republished device (full dedup pending a diagnostics dump).

### Documentation
- **Clarified that the MotionCam Video Doorbell has no photo capture or live view in Home Assistant yet (#283).** The Supported Devices doorbell row and trade-offs note now state this explicitly and point to the video-support exploration (#282).

## [1.11.3] - 2026-06-10

### Fixed
- **The manual-refresh rate limiter no longer remembers hubs removed from the account (#276).** The per-hub map behind the manual refresh button gained an entry on every press and never dropped any, so hubs that left the account kept their entry for the life of the session. Entries are now pruned after each poll against the account's current hub set. Impact was negligible (a few bytes per removed hub), fixed for consistency with the cleanup discipline applied to every other cache in the integration.

### Documentation
- **Contributor setup now wires the local CI hooks automatically (#275).** A new one-time `make setup` step configures git to run the repository's pre-push hook (the full lint/typecheck/test pipeline in Docker) — previously a fresh clone never ran it unless the contributor discovered and configured it by hand.

## [1.11.2] - 2026-06-09

### Fixed
- **Photo-on-demand and notification-id handoff no longer touches asyncio state from the push worker thread (#274).** The FCM push callback runs on the firebase_messaging worker thread; the futures that hand a photo URL / notification id back to a waiting camera or button request were resolved inline on that thread, which is not thread-safe and raced the request's own timeout cleanup — a recipe for intermittent, hard-to-reproduce failures when a photo push arrived while Home Assistant was busy, plus a latent crash path if the wait timed out at exactly the wrong moment. Resolution is now marshaled onto the event loop, like every other push dispatch already was.

## [1.11.1] - 2026-06-08

### Fixed
- **Alarm panel could miss a rapid arm/disarm/arm transition without push (#270).** When the panel state had to fall back on the hub's arm/disarm event (because an FCM push didn't arrive promptly), the re-read of the authoritative state went through Home Assistant's shared refresh debouncer, whose 10-second cooldown collapsed a quick arm→disarm→arm sequence into a single delayed re-read — so the panel could keep showing the previous state for up to ~10 seconds (until the next event or the periodic poll). The event-triggered re-read now uses a dedicated ~1-second debouncer, so each transition is picked up within about a second while true sub-second duplicate frames are still collapsed. Installs where FCM push delivers promptly were already instant and are unaffected.

## [1.11.0] - 2026-06-07

### Added
- **Internal temperature for the MotionProtect Curtain Outdoor Plus / Base (#229).** Unlike the Mini, the Plus and Base variants don't carry an internal-temperature field in the per-device gRPC data, so no temperature sensor appeared. Their temperature is now read from the hub's HTS status channel (the same channel the WallSwitch electrical readings use) and surfaced as the standard temperature sensor. The reading is additive and safe: it only fills in when no temperature is already provided over gRPC, so every other device is untouched, and an implausible value is declined rather than shown.

### Fixed
- **Per-group/zone alarm panels follow an app-side arm/disarm without push (#266).** Arming or disarming a single group (e.g. a dedicated "Garage" zone) from the Ajax app only changes that group's state, which the lightweight per-cycle poll doesn't carry — per-group state comes from the heavier hourly snapshot. The space-level panel already re-read its state on the hub's arm/disarm event, but group panels didn't, so without FCM push a zone panel could lag up to an hour behind. The same hub event now also forces an immediate re-read of group states, so per-group panels update within about a second on installs without push (and remain instant with push). The heavier read runs only on an actual arm/disarm event, not on every poll.

### Documentation
- **Clarified that arm/disarm panel state now follows within ~1 s without push.** The README's FCM comparison table and account-separation tip still said app-side arm/disarm only updated on the next poll without FCM; since the hub status-event re-read (space panel, then per-group panels) that's no longer true. Real-time *event* notifications (alarm, doorbell, motion) still require FCM.

## [1.10.0] - 2026-06-06

### Added
- **"I don't use push notifications" opt-out to silence the FCM reminder for good (#252).** Users who deliberately run without push saw the "FCM not configured" Repair card return after every restart. The card is fixable (it opens the credentials form), so closing that form is easy to mistake for dismissing the issue — but it never records Home Assistant's per-issue dismissal, so the card legitimately came back. A new **I don't use push notifications** option (Configure, off by default) suppresses the recurring card and the WARNING log and clears any card already shown; enable it once and the reminder never returns. Real-time push stays off until FCM is configured — this only hides the reminder. Translated across all 14 locales.
- **Option to hide the "Arm Home" button on the alarm panel (#259).** Arm Home duplicates Ajax's single partial (Night) mode and is advertised mainly so the Nabu Casa / Alexa skill discovers the panel — users without Alexa / Home Assistant Cloud saw a redundant button on the Lovelace alarm card. A new **Show "Arm Home" button** option (Configure, enabled by default to preserve Alexa discovery) hides it when turned off, leaving Arm Away and Arm Night intact. Applies to both the space-level and per-group panels. Translated across all 14 locales.
- **SpaceControl keyfobs now appear in Home Assistant, with an experimental "Active" sensor.** Keyfobs (llaveros) are reported only over the hub's HTS link, never in the gRPC device snapshot, so they were invisible until now. They are grouped under a single **Keyfobs** device per hub, with one diagnostic **Active** binary sensor per keyfob (named after it). The active value is **experimental and unverified**: every observed keyfob reports as active and we have no deactivated sample to confirm against (only an installer/CRA can deactivate a keyfob), so the sensor reads "active" until a diagnostic from a genuinely deactivated keyfob confirms the indicator. To help with that, keyfob detail is logged at debug level (names redacted) and included in the diagnostics download. Who armed/disarmed via a keyfob already appeared in the logbook ("Disarmed (via NAME)") and is unaffected.

### Fixed
- **Alarm panel follows an app-side arm/disarm faster, without push (#258).** Arming or disarming from the Ajax app emits a hub event that the integration previously ignored (it only matched one hub's event source id), so a hub without FCM push only caught up on the next poll — up to a few minutes later. The event now matches across hubs and triggers an immediate re-read of the authoritative state over gRPC, so the panel updates within about a second. The event's raw byte is deliberately **not** trusted as the state itself — arming initiates an exit delay rather than arming immediately, a disarm during that delay emits no event, and events can be dropped on a reconnect, any of which would otherwise leave the panel showing a wrong state — so the re-read reads ground truth and the periodic poll remains the backstop. Installs with FCM push were already instant and are unaffected.

### Documentation
- **Documented keyfobs as experimental and asked for deactivated-keyfob logs.** README now lists keyfobs in Supported Devices, adds a Keyfobs (experimental) entity section explaining the unconfirmed active/inactive state, and a Help Wanted entry inviting a diagnostics dump + debug log from anyone with a keyfob deactivated by their installer/CRA so the indicator can be confirmed.

## [1.9.1] - 2026-06-05

### Fixed
- **Alarm state and the hourly refresh now update on their own on active hubs, without relying on push (#178).** On a hub that streams frequent network updates, every update reset Home Assistant's poll timer before it could fire, so the periodic refresh that reads the alarm state (and hourly re-reads rooms, groups, chime, monitoring company, SIM and firmware) effectively never ran on its own — leaving those values dependent entirely on FCM push. If push was delayed or not configured, the panel only updated on a manual reload. A dedicated refresh timer now runs the poll on a fixed cadence regardless of stream activity, restoring the safety net behind push.

## [1.9.0] - 2026-06-04

### Added
- **Hub Chime enable/disable switch (#239).** A per-hub `Chime` switch toggles the hub-wide Chime (the Ajax app's bottom-left toggle that plays a tone on opening monitored doors while disarmed). State is read from the hub's `chime_status` in the space snapshot (the same field the app shows); toggling requires the account's `EDIT_CHIMES` permission, surfacing a clear error otherwise. Only created for hubs that expose the feature. Translated across all 14 locales. A change made from the Ajax app is reflected **immediately**: the new state is decoded directly from the hub's live HTS event, instead of only at the periodic snapshot refresh. (An interim approach used the event as a trigger to re-read the gRPC snapshot, but that read lagged the toggle and could return a stale value right after an app-side change, so the switch didn't always follow.)
- **Outdoor curtain PIR internal temperature sensor (#229).** MotionProtect Curtain Outdoor detectors report their internal temperature only in the rich per-device snapshot (same as sirens, #220), so no sensor appeared. The per-device temperature refresh is now device-agnostic and covers them.
- **`aegis_ajax.disarm_night_mode` service (#233).** Stands down only the night-mode groups via Ajax's native `disarmFromNightMode`, leaving independently away-armed groups armed — previously, exiting night mode required a full disarm that also stood down those groups. Optional alarm-panel target; applies to all panels when omitted.

### Fixed
- **SmartLock / Yale lock now locks and unlocks from Home Assistant (#219).** Hub-attached Jeweller locks (including installer-added Yale modules on a third-party monitoring backend) aren't in the SmartLock cloud registry, so the command is issued through the generic device on/off path, sent with the generic `smart_lock` ObjectType on channel 1. The polarity is inverted relative to a relay — matching the Ajax app, **lock = Off** and **unlock = On** (On energises the relay and retracts the bolt) — correcting an earlier swap where the hub accepted the command but actuated the wrong way. (Lock *state* was already restored in 1.7.0.) The **Open / unlatch** button is no longer shown on these locks: unlatch is only available through the cloud SmartLock service, which hub-attached locks aren't registered with (the Ajax app can't unlatch them either), so the button only ever failed.
- **Device stream resumes after a peer reset instead of going silent for hours (#236).** The reconnect loop reused a possibly half-open cached gRPC channel; the retried stream neither errored nor delivered until a full restart. The channel is now recreated on reconnect.
- **gRPC keepalive detects a silently-dropped connection (#236).** The long-lived device stream now sends periodic HTTP/2 keepalive pings, so a half-open link (e.g. a router silently dropping an idle connection, leaving the stream blocked with no error and no updates) surfaces as a normal error the reconnect path recovers from, instead of staying silent until a restart. The ping interval **self-tunes**: it starts high (gentle on the server, below a typical router idle timeout) and halves toward a 60s floor whenever the stream keeps dying after an idle stretch, converging under whatever idle timeout the network path enforces — while refusing to shorten on a `too_many_pings` rejection (the opposite problem).
- **System Information card no longer flips to "unreachable" on a healthy install (#236).** Reachability was derived only from the polled-refresh timestamp, which HTS/FCM updates starve. It now treats the integration as reachable when the poll is fresh or the HTS stream is live — the paths that carry live sensor/device state.
- **System Information distinguishes a "push only" state (#236).** FCM push carries only security events, not live sensor state, so an install where push is alive but the poll and HTS stream are both down now reads as "push only — sensor data may be stale" instead of plain "reachable", keeping a degraded data path visible rather than hiding it behind a healthy-looking indicator.
- **Reconfiguring to a different Ajax account updates the entry title and unique_id on the first try (#241).** Previously the integration's front page kept the old email until a second reconfigure.
- **No phantom Carbon-monoxide sensor on Heat/Smoke FireProtect 2 units (#231).** The generic `fire_protect_two` mapping blanket-attached a CO sensor stuck at "Clear"; CO is dropped from the generic mapping (only CO-encoded SKUs keep it). Smoke + heat are unchanged, and a real CO alarm on a CO-equipped unit still arrives via push.
- **"FCM not configured" Repair card stays dismissed across restarts (#252).** The card was deleted and re-created on every start, which wiped the user's dismissal in Home Assistant's issue registry, so it reappeared after every reboot for users who intentionally left push off. It's now registered idempotently — a dismissal persists — and is cleared only once credentials are actually present. The WARNING log line is unchanged, and the four-field fix form is still available from the card and from Configure.

### Documentation
- **Recommend a separate Ajax account with notification access for reliable push (#234).** A limited User-role account registers for FCM but receives no events; the Home Assistant account needs its own login and notification access.
- **README synced with the current lock and chime behaviour.** Documented the hub Chime switch, corrected the lock capabilities (lock/unlock work; unlatch isn't exposed and why), and dropped the stale "lock/unlock not wired yet" note and the "chime mode" entry from the unsupported list.

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
