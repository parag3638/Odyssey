from cryptography.fernet import Fernet
from app.crypto import encrypt_secret, decrypt_secret


def test_roundtrip():
    key = Fernet.generate_key().decode()
    token = encrypt_secret("super-secret", key)
    assert token != "super-secret"
    assert decrypt_secret(token, key) == "super-secret"


def test_mask():
    from app.crypto import mask_secret
    assert mask_secret("ABCD1234EF") == "••••34EF"
