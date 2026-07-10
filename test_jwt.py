#!/usr/bin/env python3
"""
Simple smoke test for jwt_handler.py
Run: PYTHONPATH=. python test_jwt.py
"""

from digital_brain.security.jwt_handler import create_access_token, decode_access_token
from datetime import timedelta
import time

print("=" * 60)
print("JWT HANDLER SMOKE TEST")
print("=" * 60)

print("\n1) Create token for user 'alice'...")
test_data = {
    "sub": "alice@example.com",
    "role": "admin",
    "user_id": 123
}

token = create_access_token(test_data)
print("OK: token created")
print(f"Token prefix: {token[:50]}...")
print(f"Token length: {len(token)}")

print("\n2) Decode token...")
decoded = decode_access_token(token)
if decoded:
    print("OK: token decoded")
    for key, value in decoded.items():
        if key != "exp":
            print(f"   - {key}: {value}")
else:
    print("FAIL: decode error")

print("\n3) Create short-lived token (2s)...")
short_token = create_access_token(
    {"sub": "test_user"},
    expires_delta=timedelta(seconds=2)
)
print("OK: short token created")
print("Waiting 3 seconds...")
time.sleep(3)

print("Check expired token...")
expired_decoded = decode_access_token(short_token)
if expired_decoded is None:
    print("OK: expired token rejected")
else:
    print("FAIL: expired token accepted")

print("\n4) Check tampered token...")
fake_token = token[:-10] + "HACKED1234"
hacked_decoded = decode_access_token(fake_token)
if hacked_decoded is None:
    print("OK: tampered token rejected")
else:
    print("FAIL: tampered token accepted")

print("\n" + "=" * 60)
print("DONE")
print("=" * 60)
