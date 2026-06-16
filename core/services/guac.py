import base64
import hashlib
import hmac
import json
import time
import urllib.parse

from Crypto.Cipher import AES
from Crypto.Util.Padding import pad

from config import CONNECTION_NAME, JSON_SECRET_KEY, PUBLIC_URL, SSH_PASS, SSH_PORT, SSH_USER


def build_token(username: str, hostname: str, ttl: int) -> str:
    key = bytes.fromhex(JSON_SECRET_KEY)
    payload = {
        "username": username,
        "expires": int((time.time() + ttl) * 1000),
        "connections": {
            CONNECTION_NAME: {
                "protocol": "ssh",
                "parameters": {
                    "hostname": hostname,
                    "port":     str(SSH_PORT),
                    "username": SSH_USER,
                    "password": SSH_PASS,
                },
            }
        },
    }
    json_bytes = json.dumps(payload).encode("utf-8")
    signature  = hmac.new(key, json_bytes, hashlib.sha256).digest()
    plaintext  = signature + json_bytes
    cipher     = AES.new(key, AES.MODE_CBC, bytes(16))
    ciphertext = cipher.encrypt(pad(plaintext, 16))
    return base64.b64encode(ciphertext).decode()


def build_url(token: str) -> str:
    return f"{PUBLIC_URL}/?data={urllib.parse.quote(token, safe='')}"
