"""AES-128-CBC encryption/decryption for the Ajax HTS binary protocol.

Uses `cryptography` rather than `pycryptodome`: Home Assistant core ships it
and `firebase-messaging` already requires it, so these two calls were the
only reason `pycryptodome` appeared in `manifest.json` — and every
requirement we declare is one more thing that has to resolve inside HA's
constraints before the integration can install at all (#513).
"""

from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes

# Protocol-defined AES key and IV used by the Ajax HTS transport layer.
# These are fixed constants required by the protocol specification;
# the server expects exactly these values for all HTS connections.
_KEY = b"We@zEd;80Z1@pc2Y"
_IV = b"V:e<*tMv6qVU#WRC"


def encrypt(data: bytes) -> bytes:
    """AES-128-CBC encrypt data.

    Args:
        data: Plaintext bytes. Must be a multiple of 16 bytes.

    Returns:
        Ciphertext bytes of the same length.

    Raises:
        ValueError: If len(data) is not a multiple of 16.
    """
    if len(data) % 16 != 0:
        raise ValueError(f"Input length {len(data)} is not a multiple of 16 (AES block size)")
    encryptor = Cipher(algorithms.AES(_KEY), modes.CBC(_IV)).encryptor()
    return encryptor.update(data) + encryptor.finalize()


def decrypt(data: bytes) -> bytes:
    """AES-128-CBC decrypt data.

    Args:
        data: Ciphertext bytes. Must be a multiple of 16 bytes.

    Returns:
        Plaintext bytes of the same length.

    Raises:
        ValueError: If len(data) is not a multiple of 16.
    """
    if len(data) % 16 != 0:
        raise ValueError(f"Input length {len(data)} is not a multiple of 16 (AES block size)")
    decryptor = Cipher(algorithms.AES(_KEY), modes.CBC(_IV)).decryptor()
    return decryptor.update(data) + decryptor.finalize()
