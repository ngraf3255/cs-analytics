"""Authenticated encryption for CS2 match-history auth codes.

Uses Fernet (AES-128-CBC + HMAC-SHA256) through MultiFernet so keys can be
rotated: ``TOKEN_ENCRYPTION_KEYS`` is a comma-separated list, newest first.
New ciphertexts use the first key; any listed key can decrypt. To rotate,
prepend a new key, run :meth:`AuthCodeCipher.rotate` over stored rows (or let
re-links pick it up), then drop the old key.

Each ciphertext is bound to the owner's SteamID so a row copied to another user
won't decrypt for them.

Generate a key with::

    python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
"""

from __future__ import annotations

from cryptography.fernet import Fernet, InvalidToken, MultiFernet


class DecryptionError(Exception):
    """Ciphertext could not be decrypted or is bound to another user."""


class AuthCodeCipher:
    def __init__(self, keys: list[str]):
        if not keys:
            raise ValueError("at least one encryption key is required")
        try:
            self._fernet = MultiFernet([Fernet(key.encode()) for key in keys])
        except (ValueError, TypeError) as exc:
            raise ValueError("TOKEN_ENCRYPTION_KEYS contains an invalid Fernet key") from exc

    def encrypt(self, steam_id: str, auth_code: str) -> str:
        return self._fernet.encrypt(f"{steam_id}:{auth_code}".encode()).decode()

    def decrypt(self, steam_id: str, ciphertext: str) -> str:
        try:
            plaintext = self._fernet.decrypt(ciphertext.encode()).decode()
        except InvalidToken as exc:
            raise DecryptionError("invalid ciphertext") from exc
        owner, sep, code = plaintext.partition(":")
        if not sep or owner != steam_id:
            raise DecryptionError("ciphertext bound to a different user")
        return code

    def rotate(self, ciphertext: str) -> str:
        """Re-encrypt with the current primary key."""

        try:
            return self._fernet.rotate(ciphertext.encode()).decode()
        except InvalidToken as exc:
            raise DecryptionError("invalid ciphertext") from exc
