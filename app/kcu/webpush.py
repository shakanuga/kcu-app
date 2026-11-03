"""Web-Push ohne Zusatzpakete: VAPID (RFC 8292) und Verschlüsselung aes128gcm (RFC 8291).

Funktioniert mit Chrome/Android, Firefox, Edge und Safari/iOS (ab iOS 16.4, App auf dem Home-Bildschirm).
"""
import base64
import json
import os
import struct
import time
import urllib.error
import urllib.request
from urllib.parse import urlparse

import jwt
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.hkdf import HKDF


def b64u(data):
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode()


def b64u_dec(s):
    s = s.strip()
    return base64.urlsafe_b64decode(s + "=" * (-len(s) % 4))


def _pub_bytes(key):
    return key.public_key().public_bytes(serialization.Encoding.X962, serialization.PublicFormat.UncompressedPoint)


class Vapid:
    def __init__(self, data_dir):
        self.path = os.path.join(data_dir, "vapid_private.pem")
        if not os.path.exists(self.path):
            key = ec.generate_private_key(ec.SECP256R1())
            pem = key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
                                    serialization.NoEncryption())
            tmp = self.path + ".tmp"
            with open(tmp, "wb") as f:
                f.write(pem)
            os.chmod(tmp, 0o600)
            os.replace(tmp, self.path)
        with open(self.path, "rb") as f:
            self.pem = f.read()
        self.key = serialization.load_pem_private_key(self.pem, password=None)
        self.public_b64 = b64u(_pub_bytes(self.key))

    def auth_header(self, endpoint, subject):
        u = urlparse(endpoint)
        claims = {"aud": f"{u.scheme}://{u.netloc}", "exp": int(time.time()) + 12 * 3600, "sub": subject}
        token = jwt.encode(claims, self.key, algorithm="ES256")
        return f"vapid t={token}, k={self.public_b64}"


def encrypt(payload: bytes, p256dh: str, auth: str, salt: bytes = None, server_key=None):
    """RFC 8291 – Nachricht für genau ein Gerät verschlüsseln."""
    ua_pub_bytes = b64u_dec(p256dh)
    auth_secret = b64u_dec(auth)
    ua_pub = ec.EllipticCurvePublicKey.from_encoded_point(ec.SECP256R1(), ua_pub_bytes)
    server_key = server_key or ec.generate_private_key(ec.SECP256R1())
    as_pub = _pub_bytes(server_key)
    shared = server_key.exchange(ec.ECDH(), ua_pub)
    ikm = HKDF(hashes.SHA256(), 32, salt=auth_secret, info=b"WebPush: info\x00" + ua_pub_bytes + as_pub).derive(shared)
    salt = salt or os.urandom(16)
    cek = HKDF(hashes.SHA256(), 16, salt=salt, info=b"Content-Encoding: aes128gcm\x00").derive(ikm)
    nonce = HKDF(hashes.SHA256(), 12, salt=salt, info=b"Content-Encoding: nonce\x00").derive(ikm)
    ct = AESGCM(cek).encrypt(nonce, payload + b"\x02", None)
    header = salt + struct.pack("!IB", 4096, len(as_pub)) + as_pub
    return header + ct


def send(vapid, sub, data: dict, subject="mailto:kcu@example.org", ttl=86400, timeout=10):
    """Rückgabe: (ok, gone). gone=True heißt: Abo ungültig (abgemeldet) -> löschen."""
    body = encrypt(json.dumps(data, ensure_ascii=False).encode(), sub["p256dh"], sub["auth"])
    req = urllib.request.Request(sub["endpoint"], data=body, method="POST", headers={
        "TTL": str(ttl),
        "Urgency": "normal",
        "Content-Encoding": "aes128gcm",
        "Content-Type": "application/octet-stream",
        "Authorization": vapid.auth_header(sub["endpoint"], subject),
    })
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return 200 <= r.status < 300, False
    except urllib.error.HTTPError as e:
        return False, e.code in (404, 410)
    except (urllib.error.URLError, OSError, TimeoutError):
        return False, False
