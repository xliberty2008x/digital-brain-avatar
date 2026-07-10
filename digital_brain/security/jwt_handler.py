import os
import jwt
from datetime import datetime, timedelta, timezone
from typing import Optional

# Experimental / learning JWT helpers — not production multi-user auth.
# Prefer JWT_SECRET_KEY from the environment. The insecure default is only for
# local unit tests; do not use it on shared systems.
_DEFAULT_DEV_SECRET = "your-super-secret-key-change-me"
SECRET_KEY = os.getenv("JWT_SECRET_KEY", _DEFAULT_DEV_SECRET)
ALGORITHM = "HS256"
ACCESS_TOKEN_EXPIRE_MINUTES = 30


def create_access_token(data: dict, expires_delta: Optional[timedelta] = None) -> str:
    """Create a signed JWT (experimental helper)."""
    if expires_delta:
        expires = datetime.now(timezone.utc) + expires_delta
    else:
        expires = datetime.now(timezone.utc) + timedelta(minutes=ACCESS_TOKEN_EXPIRE_MINUTES)
    to_encode = data.copy()
    to_encode.update({"exp": expires})
    return jwt.encode(to_encode, SECRET_KEY, algorithm=ALGORITHM)


def decode_access_token(token: str) -> Optional[dict]:
    """Decode and verify a JWT (experimental helper)."""
    try:
        return jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
    except jwt.ExpiredSignatureError:
        print("Token expired")
        return None
    except jwt.PyJWTError:
        print("Token validation failed")
        return None
