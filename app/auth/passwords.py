"""Argon2 password hashes. Verification never raises for a bad password."""

from argon2 import PasswordHasher
from argon2.exceptions import VerificationError, VerifyMismatchError

_hasher = PasswordHasher()
_DUMMY_HASH = _hasher.hash("not-a-real-password")


def hash_password(password: str) -> str:
    """Return an Argon2id hash. The plaintext password is not stored."""
    return _hasher.hash(password)


def verify_password(password: str, encoded: str) -> bool:
    """Return True when the password matches. A missing or bad hash returns False."""
    try:
        return _hasher.verify(encoded, password)
    except (VerifyMismatchError, VerificationError):
        return False


def verify_password_or_dummy(password: str, encoded: str | None) -> bool:
    """Check a stored hash, or spend the same work when the account does not exist."""
    return verify_password(password, encoded if encoded is not None else _DUMMY_HASH)
