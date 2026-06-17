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
    # Guacamole's web client runs in hashbang mode and reads its auth/login
    # parameters from the URL fragment (everything after "#") via
    # $location.search(); a query placed before the "#" is never seen. So the
    # token must live in the fragment. Point straight at the connection's
    # /client route (which also re-authenticates) so the student lands in the
    # terminal rather than on the connection-list home page.
    #
    # Client identifier (Guacamole ClientIdentifier.toString): unpadded
    # base64url of "<connection id>\0<type>\0<data source>", where type "c"
    # means a connection and "json" is the guacamole-auth-json data source.
    client_id = (
        base64.urlsafe_b64encode(f"{CONNECTION_NAME}\0c\0json".encode("utf-8"))
        .decode()
        .rstrip("=")
    )
    return f"{PUBLIC_URL}/#/client/{client_id}?data={urllib.parse.quote(token, safe='')}"
