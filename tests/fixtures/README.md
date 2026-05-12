# Binary snapshot fixtures

Drop captured `StreamLightDevicesResponse` payloads here as `*.bin` files
to regression-test the parser against real-world wire data. Every
`.bin` in this directory is automatically replayed through
`DevicesApi.start_device_stream` by
`TestSnapshotReplay::test_fixture_files_round_trip`, asserting no
exception leaks out of `parse_device` or the stream-update path.

## How to capture

1. Enable debug logging for the integration:

   ```yaml
   logger:
     default: info
     logs:
       custom_components.aegis_ajax: debug
   ```

2. Reproduce the issue on a real Home Assistant install (or a Compute
   client) that exhibits the offending device.

3. Capture the raw `StreamLightDevicesResponse` bytes — either with
   `mitmproxy` against the cloud-side gRPC stream, or by adding a
   one-shot `Path(...).write_bytes(msg.SerializeToString())` patch in
   `_run_stream` while you reproduce.

4. Save the file as `tests/fixtures/<issue-number>-<short-name>.bin`
   (e.g. `119-permudious-video-doorbell.bin`).

5. **Sanitize PII first.** Any human-readable strings (device names,
   room names, hub IDs that map to a real account) should be scrubbed
   or replaced. The parser doesn't care about the values, only the
   shape.

## Why

Issue #119 (1.3.0-beta.5 → beta.6) was a latent
`int(status.wifi_signal_level_status)` bug that only fired on real
hardware emitting that status — every unit test passed because
`MagicMock` silently coerced anything to int. A binary-fixture replay
test would have caught the regression in CI without waiting for a user
beta. See `feedback_audit_parser_before_extending.md` for the broader
lesson.

## Captures committed

- **`bvis_home_fleet.bin`** — 11-device snapshot from a `Hub 2 4G`
  install (the maintainer's). Mix of `door_protect`,
  `door_protect_plus`, `motion_cam_phod`, `keypad_combi`, plus one
  unsupported `LightDevice` oneof case (parser returns `None`, exercising
  that path). All ids/names/`hub_id`/sorting keys scrubbed to
  `dev-NNN` / `hub-001` / `<object_type> fixture N`; statuses, states,
  enums and oneof shapes preserved verbatim so the parser exercises
  the wire shape it sees in the wild. **No `video_edge_channel` device** —
  the maintainer doesn't own one. A capture with one (e.g. from
  @Permudious's MotionCam Video Doorbell, #119) would be the next
  highest-value addition.
