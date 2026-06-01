"""FCM credentials validation + library-error classification helpers.

Lives in its own module so the listener doesn't drag every regex /
validation rule / known-error string into the same 1000-line file.
Importers (notification.py + tests) keep working unchanged because
notification.py re-exports the public surface.
"""

from __future__ import annotations

import hashlib
import re

# Permissive Firebase-Installations shape match for `fcm_app_id`. We do NOT
# enforce a hash-length range — `1.5.3-beta.4` tried that with `30..64`
# chars and false-positived against the official Ajax Play Store APK
# (#182 follow-up, @zwagerzaken). Upper and lower hex accepted to be
# lenient against transcripts that uppercase content.
_FCM_APP_ID_RE = re.compile(r"^1:(\d+):android:([0-9a-fA-F]+)$")
_FCM_API_KEY_RE = re.compile(r"^AIza[0-9A-Za-z_-]{35}$")


def _validate_fcm_shape(
    *,
    fcm_project_id: str,
    fcm_app_id: str,
    fcm_api_key: str,
    fcm_sender_id: str,
) -> str | None:
    """Pre-flight structural check on the four FCM values.

    Catches paste-truncation and mixed-projects errors offline, before
    `firebase_messaging` hits Firebase Installations. Without this, a
    half-pasted `fcm_app_id` surfaces as `API_KEY_ANDROID_APP_BLOCKED`
    / `androidPackage: <empty>` from Google — accurate but unactionable,
    because the API key isn't actually the problem (#155, #182).

    Validation rules mirror Firebase's own client-side checks; we don't
    add tighter constraints because doing so false-positived against the
    real Ajax Play Store APK in 1.5.3-beta.4 (#182 follow-up). What we
    keep: shape (`1:<digits>:android:<hex>` for app_id, `AIza` + 35 for
    api_key), digit-chunk consistency between sender_id and app_id, and
    a non-empty project_id. What we dropped: a hash-length range, which
    Firebase itself doesn't enforce.

    Returns a short English description of the first problem found
    (suitable for a Repair card `{problem}` placeholder), or `None`
    when every shape is coherent. We surface one problem at a time so
    the Repair card has a single concrete next-action; the user
    re-enters all four values regardless.
    """
    if not fcm_project_id:
        return "fcm_project_id is empty"

    app_id_match = _FCM_APP_ID_RE.match(fcm_app_id)
    if app_id_match is None:
        return 'fcm_app_id does not match the expected shape "1:<digits>:android:<hex>"'
    app_id_sender = app_id_match.group(1)

    if not _FCM_API_KEY_RE.match(fcm_api_key):
        return (
            'fcm_api_key does not match the expected shape (starts with "AIza", exactly 39 chars)'
        )

    if not fcm_sender_id.isdigit():
        return "fcm_sender_id must contain only digits"
    if fcm_sender_id != app_id_sender:
        return (
            f"fcm_sender_id ({fcm_sender_id}) does not match the digit "
            f"chunk inside fcm_app_id ({app_id_sender}) — values come "
            f"from two different Firebase projects"
        )

    return None


def _fcm_creds_hash(
    *,
    fcm_project_id: str,
    fcm_app_id: str,
    fcm_api_key: str,
    fcm_sender_id: str,
) -> str:
    """Stable one-way fingerprint of a four-value FCM credential set.

    Used to remember that a specific set was terminally rejected by Google so
    we don't re-attempt registration against the Firebase project on every
    restart (#227). It is a SHA-256 digest, never the secret itself.

    This is a change-detection fingerprint, NOT password storage: it's only
    compared against itself to answer "are these the same four values I already
    tried?", never used to authenticate anything, so a fast hash is correct and
    a slow KDF (bcrypt/argon2) would be pointless here. `usedforsecurity=False`
    documents that intent (and keeps it FIPS-safe).
    """
    joined = "|".join((fcm_project_id, fcm_app_id, fcm_api_key, fcm_sender_id))
    return hashlib.sha256(joined.encode("utf-8"), usedforsecurity=False).hexdigest()


def _is_terminal_fcm_failure(exc: BaseException) -> bool:
    """True when an FCM registration error is a Google credential rejection
    (won't change on retry), False for transient / host-unreachable errors.

    Mirrors the `_classify_fcm_failure` taxonomy: the two credential-rejection
    strings are terminal; "unable to register and check in to gcm" is the
    FCM-hosts-unreachable case (DNS / firewall / proxy) and must stay
    retryable. Unknown errors default to NOT terminal, so a transient blip is
    never mistaken for a permanently-bad credential set (which would suppress
    a legitimate retry until the user re-enters the values).
    """
    lower = (str(exc) if exc else "").lower()
    if "subscription" in lower and "google cloud messaging" in lower:
        return True
    return "unable to register with fcm" in lower


def _classify_fcm_failure(exc: BaseException) -> str:
    """Return a user-actionable WARNING message for an FCM registration / push-client error.

    `firebase-messaging` 0.4.5 raises plain `RuntimeError` with one of three
    fixed message strings, hiding any HTTP status and aiohttp cause behind
    internal `_logger` calls — so `__cause__` / `__context__` are always None
    and the only signal we get is the literal `str(exc)`.

    The three branches below were measured empirically (probe against real FCM
    endpoints with deliberate credential corruptions + a DNS block of the FCM
    hosts), not inferred from the source:

      * "Unable to establish subscription with Google Cloud Messaging."
        — dominant failure mode for any credential-set error (bad sender_id,
        api_key, project_id, or app_id with valid shape).

      * "Unable to register with fcm"
        — fires when Firebase Installations returns HTTP 403
        API_KEY_ANDROID_APP_BLOCKED for the api-key (#182 root cause:
        the user picked a non-FCM `AIza` string from the APK's native
        library).

      * "Unable to register and check in to gcm"
        — the four credentials are not used in the GCM checkin step, so this
        string only appears when the FCM hosts are unreachable (DNS, firewall,
        proxy). aiohttp errors are swallowed by the library's retry loop.
    """
    msg = str(exc) if exc else ""
    lower = msg.lower()

    if "subscription" in lower and "google cloud messaging" in lower:
        return (
            "FCM registration rejected by Google. The four credentials must all "
            "come from the same Firebase project — fcm_sender_id must match the "
            "numeric prefix inside fcm_app_id, and fcm_api_key must be paired "
            "with that same fcm_project_id. Re-enter all four together via the "
            "Repair card under Settings → Repairs."
        )
    if "unable to register with fcm" in lower:
        return (
            "Firebase Installations refused the api-key (HTTP 403 — "
            "API_KEY_ANDROID_APP_BLOCKED or API_KEY_SERVICE_BLOCKED). Both point "
            "at the wrong `AIza…` key: the Ajax APK's native library ships several "
            "`AIza…` strings — one for FCM and one or more for other Google "
            "services (Maps / ML Kit), and `strings.xml`'s `google_maps_key` is a "
            "real `AIza…` that is NOT the FCM key. Only the FCM-scoped key from "
            "`libnative-lib.so` is accepted here; a Maps-scoped key surfaces as "
            "API_KEY_SERVICE_BLOCKED, a package-restricted one as "
            "API_KEY_ANDROID_APP_BLOCKED. If you extracted the wrong `AIza…`, try "
            "the others via the Repair card under Settings → Repairs. See "
            "https://github.com/bvis/aegis-hass#where-the-values-live for the "
            "extraction guide."
        )
    if "unable to register and check in to gcm" in lower:
        return (
            "Couldn't reach Google FCM servers. Check the HA host can reach "
            "android.clients.google.com / firebaseinstallations.googleapis.com "
            "(firewall, DNS, or proxy issue). The Repair card stays raised until "
            "the next successful registration."
        )
    return f"FCM registration failed: {msg or exc.__class__.__name__}"
