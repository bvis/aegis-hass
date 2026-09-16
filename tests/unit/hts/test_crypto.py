"""Tests for AES-128-CBC crypto helpers."""

import pytest

from custom_components.aegis_ajax.api.hts.crypto import decrypt, encrypt


class TestEncryptDecrypt:
    def test_roundtrip_single_block(self) -> None:
        plaintext = b"0123456789abcdef"  # exactly 16 bytes
        assert decrypt(encrypt(plaintext)) == plaintext

    def test_roundtrip_multi_block(self) -> None:
        plaintext = b"0123456789abcdef" * 2  # 32 bytes
        assert decrypt(encrypt(plaintext)) == plaintext

    def test_decrypt_known_vector(self) -> None:
        """Encrypt a known plaintext and verify decrypt inverts it."""
        plaintext = b"AjaxProtegimHTS!"  # 16 bytes
        ciphertext = encrypt(plaintext)
        # ciphertext must differ from plaintext
        assert ciphertext != plaintext
        assert decrypt(ciphertext) == plaintext

    def test_matches_the_bytes_the_previous_implementation_produced(self) -> None:
        """Pin the wire format, not just self-consistency.

        The round-trip tests above pass under *any* symmetric cipher, so they
        could not have caught a swap that changed the bytes on the wire — and a
        changed byte here means every HTS connection fails to parse. These two
        vectors were captured from the pycryptodome implementation before it was
        replaced by `cryptography`.
        """
        assert encrypt(bytes(range(32))).hex() == (
            "5785bb421c06945b90c654fb78a4700b103674c1d65c05962db4d49da3ead5ee"
        )
        assert encrypt(b"AjaxProtegimHTS!").hex() == "a44a98d5105d1d688c48d7240c40817e"

    def test_decrypt_inverts_the_pinned_vector(self) -> None:
        ciphertext = bytes.fromhex(
            "5785bb421c06945b90c654fb78a4700b103674c1d65c05962db4d49da3ead5ee"
        )
        assert decrypt(ciphertext) == bytes(range(32))

    def test_encrypt_not_aligned_raises(self) -> None:
        with pytest.raises(ValueError, match="multiple of 16"):
            encrypt(b"short")

    def test_decrypt_not_aligned_raises(self) -> None:
        with pytest.raises(ValueError, match="multiple of 16"):
            decrypt(b"tooshort_data123x")  # 17 bytes
