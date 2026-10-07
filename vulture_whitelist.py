# Vulture whitelist — parameters required by external interfaces but not used by our code
width = None  # noqa: F821
height = None  # noqa: F821
obj = None  # noqa: F821 — firebase_messaging callback signature
call = None  # noqa: F821 — ServiceCall parameter in service handlers
disarm_from_night_mode = None  # noqa: F821 — API method kept for future use (server rejects it currently)
async_describe_event = None  # noqa: F821 — HA logbook platform callback parameter
async_get_media_source = None  # noqa: F821 — HA media_source platform entry point