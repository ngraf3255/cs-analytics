import pytest
from cryptography.fernet import Fernet

from steamlink.crypto import AuthCodeCipher, DecryptionError

STEAM_ID = "76561198000000001"
CODE = "ABCD-EFGHI-JKLM"


def test_roundtrip_and_no_plaintext_in_ciphertext():
    cipher = AuthCodeCipher([Fernet.generate_key().decode()])
    token = cipher.encrypt(STEAM_ID, CODE)
    assert CODE not in token
    assert cipher.decrypt(STEAM_ID, token) == CODE


def test_bound_to_owner():
    cipher = AuthCodeCipher([Fernet.generate_key().decode()])
    token = cipher.encrypt(STEAM_ID, CODE)
    with pytest.raises(DecryptionError):
        cipher.decrypt("76561198000000002", token)


def test_tamper_detected():
    cipher = AuthCodeCipher([Fernet.generate_key().decode()])
    token = cipher.encrypt(STEAM_ID, CODE)
    tampered = token[:-5] + ("A" if token[-5] != "A" else "B") + token[-4:]
    with pytest.raises(DecryptionError):
        cipher.decrypt(STEAM_ID, tampered)


def test_key_rotation():
    old, new = Fernet.generate_key().decode(), Fernet.generate_key().decode()
    old_cipher = AuthCodeCipher([old])
    token = old_cipher.encrypt(STEAM_ID, CODE)
    rotating = AuthCodeCipher([new, old])
    assert rotating.decrypt(STEAM_ID, token) == CODE
    rotated = rotating.rotate(token)
    new_only = AuthCodeCipher([new])
    assert new_only.decrypt(STEAM_ID, rotated) == CODE
    with pytest.raises(DecryptionError):
        new_only.decrypt(STEAM_ID, token)


def test_invalid_key_rejected():
    with pytest.raises(ValueError):
        AuthCodeCipher(["not-a-key"])
