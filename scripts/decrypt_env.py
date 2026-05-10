"""Decrypt an env.enc blob extracted from an OMNIME backup tar.gz.

Use this on a fresh server when restoring from backup, BEFORE doing
the data restore (you need the .env to even start the container).

Usage:
    # 1) Extract env.enc from the tar:
    tar -xzf omnime_backup_20260510-031500.tar.gz env.enc

    # 2) Decrypt it (will prompt for the passphrase):
    python -m scripts.decrypt_env env.enc > .env
    chmod 600 .env
"""
from __future__ import annotations

import getpass
import sys
from pathlib import Path


HEADER = b"OMNIMEENV1"


def main() -> None:
    if len(sys.argv) < 2:
        print(__doc__)
        sys.exit(0)

    blob = Path(sys.argv[1]).read_bytes()
    if not blob.startswith(HEADER):
        print("Not an OMNIMEENV1 blob.", file=sys.stderr)
        sys.exit(1)

    salt = blob[len(HEADER):len(HEADER) + 16]
    nonce = blob[len(HEADER) + 16:len(HEADER) + 28]
    ct = blob[len(HEADER) + 28:]

    passphrase = getpass.getpass("Backup passphrase: ")

    from cryptography.hazmat.primitives.ciphers.aead import AESGCM
    from cryptography.hazmat.primitives.kdf.scrypt import Scrypt

    kdf = Scrypt(salt=salt, length=32, n=2**15, r=8, p=1)
    key = kdf.derive(passphrase.encode("utf-8"))
    try:
        plaintext = AESGCM(key).decrypt(nonce, ct, None)
    except Exception as exc:
        print(f"Decryption failed: {exc}", file=sys.stderr)
        print("Wrong passphrase?", file=sys.stderr)
        sys.exit(2)

    sys.stdout.buffer.write(plaintext)


if __name__ == "__main__":
    main()
