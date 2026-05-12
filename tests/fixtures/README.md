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
