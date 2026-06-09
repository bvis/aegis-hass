# Security Policy

## Reporting a Vulnerability

If you discover a security vulnerability in this integration, please report it responsibly.

**Do NOT open a public GitHub issue for security vulnerabilities.**

Instead, use GitHub's [private vulnerability reporting](https://github.com/bvis/aegis-hass/security/advisories/new) so the report stays confidential while it's investigated.

Include:
- A description of the vulnerability
- Steps to reproduce
- Potential impact

You will receive an initial response within 48 hours. We will work with you to understand and address the issue before any public disclosure.

## Scope

This integration communicates with Ajax Systems cloud servers using the same protocol as the official mobile app. Security concerns may include:

- Credential handling and storage
- Network communication security
- Local file access (photo storage)
- FCM push notification handling

## Supported Versions

Only the latest release is supported with security updates.

## Threat model notes

These are known, accepted design constraints rather than open issues. They are
documented here so users can reason about the integration's security posture.

### HTS transport confidentiality relies on TLS

The Ajax HTS binary protocol (`api/hts/`) frames are wrapped in AES-128-CBC using
a fixed key and IV that are constants mandated by the protocol; integrity is a
non-cryptographic CRC-16. This inner layer therefore provides no meaningful
confidentiality or integrity on its own. Real protection comes from the TLS
tunnel the HTS connection runs over (`ssl.create_default_context()`, full
certificate validation). An attacker would have to defeat TLS first; the static
AES layer cannot be changed without breaking compatibility with Ajax hubs.

### FCM push depends on a reverse-engineered client

Push notifications use `firebase-messaging`, an unofficial reverse-engineered FCM
client, not a Google-supported library. Push is an optional feature: if the
library is missing or Google changes the FCM protocol, the integration logs a
warning and continues without push (polling still works). Treat push delivery as
best-effort rather than a guaranteed channel.

The Firebase Web/Android API key bundled in the public Ajax cobranded apps is a
public client identifier (extracted from a published APK), not a secret. It is
allowlisted in `.gitleaks.toml` for that reason.
