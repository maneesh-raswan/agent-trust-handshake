"""Small helpers for signing. Everything signed is canonical JSON."""

import base64
import json

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import (
    Ed25519PrivateKey,
    Ed25519PublicKey,
)


def canonical(obj) -> bytes:
    """Deterministic JSON bytes, so both sides sign and verify the exact same thing."""
    return json.dumps(obj, sort_keys=True, separators=(",", ":")).encode()


def new_keypair() -> Ed25519PrivateKey:
    return Ed25519PrivateKey.generate()


def public_key_b64(private_key: Ed25519PrivateKey) -> str:
    raw = private_key.public_key().public_bytes(
        serialization.Encoding.Raw, serialization.PublicFormat.Raw
    )
    return base64.b64encode(raw).decode()


def sign(private_key: Ed25519PrivateKey, obj) -> str:
    return base64.b64encode(private_key.sign(canonical(obj))).decode()


def verify(public_key_b64_str: str, obj, signature_b64: str) -> bool:
    try:
        key = Ed25519PublicKey.from_public_bytes(base64.b64decode(public_key_b64_str))
        key.verify(base64.b64decode(signature_b64), canonical(obj))
        return True
    except (InvalidSignature, ValueError, TypeError):
        return False
