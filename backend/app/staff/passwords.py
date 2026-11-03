"""Staff passwords: scrypt hashes (stdlib only), constant-time verification."""

import base64
import hashlib
import hmac
import secrets

# OWASP-recommended scrypt cost (N=2^17, r=8, p=1) uses 128 MiB per hash; this box-friendly
# setting (N=2^14) uses 16 MiB and still costs ~50 ms, which with the login rate limit is plenty.
_N, _R, _P, _DKLEN = 2**14, 8, 1, 32
MIN_LENGTH = 10


def hash_password(password: str) -> str:
    salt = secrets.token_bytes(16)
    dk = hashlib.scrypt(password.encode(), salt=salt, n=_N, r=_R, p=_P, dklen=_DKLEN)
    return f"scrypt${_N}${_R}${_P}${_b64(salt)}${_b64(dk)}"


def verify_password(password: str, stored: str | None) -> bool:
    if not stored:
        # Same work as a real check so unknown ids are not distinguishable by timing.
        hashlib.scrypt(password.encode(), salt=b"\0" * 16, n=_N, r=_R, p=_P, dklen=_DKLEN)
        return False
    try:
        algo, n, r, p, salt, dk = stored.split("$")
        if algo != "scrypt":
            return False
        expected = base64.urlsafe_b64decode(dk + "==")
        got = hashlib.scrypt(password.encode(), salt=base64.urlsafe_b64decode(salt + "=="),
                             n=int(n), r=int(r), p=int(p), dklen=len(expected))
    except (ValueError, TypeError):
        return False
    return hmac.compare_digest(got, expected)


def password_problem(password: str) -> str | None:
    """None when acceptable, else a reason (shown to the admin setting it)."""
    if len(password) < MIN_LENGTH:
        return f"password must be at least {MIN_LENGTH} characters"
    if len(password) > 256:
        return "password is too long"
    return None


def _b64(b: bytes) -> str:
    return base64.urlsafe_b64encode(b).decode().rstrip("=")
