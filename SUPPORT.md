# Getting Help

Thanks for using Aegis for Ajax. Here's where to get help, ask questions or report problems.

## Before opening anything

- Read the **[README](README.md)** — it covers setup, supported devices, app labels, options and the example dashboard.
- Browse **[existing issues](https://github.com/bvis/aegis-hass/issues?q=is%3Aissue)** (open *and* closed) — most setup hiccups have already been answered.
- Make sure you're on the latest release. Many problems disappear after a HACS update.

## Reporting a bug

Open a **[Bug Report](https://github.com/bvis/aegis-hass/issues/new?template=bug_report.yml)**. The template asks for the minimum we need to investigate (HA version, integration version, app label, FCM status, debug logs).

Debug logs help a lot. Enable them with:

```yaml
logger:
  logs:
    custom_components.aegis_ajax: debug
```

Then restart Home Assistant, reproduce the issue, and paste the relevant lines into the bug report.

## Requesting a feature

Open a **[Feature Request](https://github.com/bvis/aegis-hass/issues/new?template=feature_request.yml)**. Include the Ajax device(s) involved when relevant — many features depend on the device exposing the right data over the API.

## Asking a question

Use **[GitHub Discussions](https://github.com/bvis/aegis-hass/discussions)** (Q&A category). If it turns out to be a bug, it will be moved to an issue.

## Reporting a security vulnerability

Please do **not** open a public issue. Use GitHub's [private vulnerability reporting](https://github.com/bvis/aegis-hass/security/advisories/new). Details in [SECURITY.md](SECURITY.md).

## Code of Conduct

Participation is governed by the [Code of Conduct](CODE_OF_CONDUCT.md).
