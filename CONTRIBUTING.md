# Contributing

## Development Setup

Everything runs in Docker. No local dependencies needed.

```bash
git clone https://github.com/bvis/aegis-hass.git
cd aegis-hass

# Build dev container
make build-docker

# Compile protobuf files
make proto

# Run all checks
make check
```

## Commands

| Command | Description |
|---|---|
| `make check` | Run all checks (lint, format, typecheck, tests, dead code) |
| `make test` | Run unit tests with coverage |
| `make test-e2e` | Run E2E tests (requires AJAX_EMAIL + AJAX_PASSWORD) |
| `make lint` | Run linter |
| `make format` | Format code |
| `make typecheck` | Run type checker |
| `make proto` | Compile protobuf files |
| `make cli` | Interactive connection test |

## Commit Conventions

We use [Conventional Commits](https://www.conventionalcommits.org/):

- `feat(scope):` New feature
- `fix(scope):` Bug fix
- `docs:` Documentation
- `chore:` Maintenance
- `refactor:` Code refactoring
- `test:` Tests

## Credentials and secrets

Do not commit FCM credentials (`fcm_project_id`, `fcm_app_id`, `fcm_api_key`, `fcm_sender_id`), Ajax session tokens, OAuth client secrets, or any other credential — in source, tests, fixtures, issues, PR descriptions, or commit messages. Each user supplies their own values through the integration's config flow (Settings → Devices & Services → Aegis for Ajax → Configure) or through the Repair card. Obtaining those values is the user's responsibility and is out of scope for this project's documentation.

Patterns to watch for in `git diff` before staging:

- `AIza[A-Za-z0-9_-]{35}` (Google API key)
- `1:[0-9]+:android:[0-9a-f]+` (Firebase App ID)
- `.env`, `credentials.json`, `*.pem`, `*.p12` files
- Long alphanumeric strings paired with words like `token`, `secret`, or `bearer`

GitHub's secret scanning runs on every push. Alerts on credentials in current code should be fixed by reverting the commit and re-staging without the value.

## Adding a New Device Type

1. Find the device's `ObjectType` variant in the proto files
2. Add the mapping to `_DEVICE_TYPE_SENSORS` in `binary_sensor.py`
3. If it has switch/relay capabilities, add to `SWITCH_DEVICE_TYPES` in `switch.py`
4. Write tests for the new mappings
5. Update `README.md` device table

## E2E Testing

```bash
AJAX_EMAIL=your@email.com AJAX_PASSWORD=yourpass make test-e2e
```

Destructive tests (arm/disarm) are skipped by default. To run them:

```bash
AJAX_EMAIL=... AJAX_PASSWORD=... pytest tests/e2e/ -v -m "e2e"
```
