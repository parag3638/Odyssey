from cryptography.fernet import Fernet


def encrypt_secret(plaintext: str, key: str) -> str:
    return Fernet(key.encode()).encrypt(plaintext.encode()).decode()


def decrypt_secret(token: str, key: str) -> str:
    return Fernet(key.encode()).decrypt(token.encode()).decode()


def mask_secret(secret: str) -> str:
    return "••••" + secret[-4:] if len(secret) >= 4 else "••••"
