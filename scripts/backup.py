"""Dump everything important to a single archive and (optionally) ship it off-site.

Usage:
    python -m scripts.backup
"""
from __future__ import annotations

import io
import json
import logging
import shutil
import subprocess
import tarfile
from datetime import datetime
from pathlib import Path
from typing import Any

from sqlalchemy import select

from src.config import settings
from src.memory.db import session_scope
from src.memory import models as m


logger = logging.getLogger(__name__)


# All structured tables we want to capture in the JSON dump.
TABLES = [
    (m.UserProfile, "user_profile"),
    (m.Project, "projects"),
    (m.WorkExperience, "work_experience"),
    (m.Education, "education"),
    (m.Skill, "skills"),
    (m.Contact, "contacts"),
    (m.Achievement, "achievements"),
    (m.LifeEvent, "life_events"),
    (m.Idea, "ideas"),
    (m.Conversation, "conversations"),
    (m.MemorySummary, "memory_summaries"),
    (m.FileRecord, "files"),
    (m.AuditLog, "audit_log"),
    (m.Goal, "goals"),
    (m.WeeklyReview, "weekly_reviews"),
    (m.Book, "books"),
    (m.Decision, "decisions"),
    (m.HealthEvent, "health_events"),
    (m.Quote, "quotes"),
    (m.JobOpportunity, "job_opportunities"),
    (m.CVVariant, "cv_variants"),
]


def _row_to_dict(row) -> dict[str, Any]:
    cols = row.__table__.columns
    return {c.name: getattr(row, c.name) for c in cols}


def dump_json() -> dict[str, Any]:
    payload: dict[str, Any] = {"_meta": {"created_at": datetime.utcnow().isoformat() + "Z"}}
    with session_scope() as s:
        for cls, key in TABLES:
            try:
                payload[key] = [_row_to_dict(r) for r in s.scalars(select(cls)).all()]
            except Exception as exc:
                logger.warning("Skipping %s in backup: %s", key, exc)
                payload[key] = []
    return payload


def run_backup() -> Path:
    """Build a tar.gz with the JSON dump + the uploaded files + (best-effort) pg_dump."""
    settings.backups_dir.mkdir(parents=True, exist_ok=True)
    ts = datetime.utcnow().strftime("%Y%m%d-%H%M%S")
    archive = settings.backups_dir / f"omnime_backup_{ts}.tar.gz"

    json_payload = dump_json()
    json_bytes = json.dumps(json_payload, default=str, indent=2).encode("utf-8")

    pg_dump_bytes = _pg_dump()

    with tarfile.open(archive, "w:gz") as tar:
        # Structured JSON dump
        info = tarfile.TarInfo("structured.json")
        info.size = len(json_bytes)
        info.mtime = int(datetime.utcnow().timestamp())
        tar.addfile(info, io.BytesIO(json_bytes))

        # pg_dump if available — most authoritative recovery format.
        if pg_dump_bytes:
            info = tarfile.TarInfo("postgres.dump")
            info.size = len(pg_dump_bytes)
            info.mtime = int(datetime.utcnow().timestamp())
            tar.addfile(info, io.BytesIO(pg_dump_bytes))

        # Uploaded files (PDFs, photos, voice, etc.)
        if settings.uploads_dir.exists():
            tar.add(settings.uploads_dir, arcname="uploads")

        # Encrypted copy of the .env so a fresh recovery has the secrets too.
        # Skipped silently if no passphrase is configured.
        env_blob = _build_encrypted_env_blob()
        if env_blob is not None:
            info = tarfile.TarInfo("env.enc")
            info.size = len(env_blob)
            info.mtime = int(datetime.utcnow().timestamp())
            tar.addfile(info, io.BytesIO(env_blob))
            logger.info("backup includes encrypted .env (AES-GCM)")

    _prune_old_backups()
    _push_offsite(archive)
    return archive


def _build_encrypted_env_blob() -> bytes | None:
    """Encrypt the running container's .env file with AES-GCM keyed off
    BACKUP_ENV_PASSPHRASE (scrypt-derived). Returns None if no passphrase
    is set or the .env can't be located.

    Output bytes layout: ``b"OMNIMEENV1" || salt(16) || nonce(12) || ciphertext``
    so the restore script can read it without a separate manifest.
    """
    passphrase = settings.backup_env_passphrase
    if not passphrase:
        return None
    env_path = Path("/app/.env") if Path("/app/.env").exists() else Path(".env")
    if not env_path.exists():
        logger.warning("BACKUP_ENV_PASSPHRASE set but .env not found at %s", env_path)
        return None
    try:
        from cryptography.hazmat.primitives.ciphers.aead import AESGCM
        from cryptography.hazmat.primitives.kdf.scrypt import Scrypt
        import os as _os
    except ImportError:
        logger.warning("cryptography missing; skipping .env encryption")
        return None

    plaintext = env_path.read_bytes()
    salt = _os.urandom(16)
    kdf = Scrypt(salt=salt, length=32, n=2**15, r=8, p=1)
    key = kdf.derive(passphrase.encode("utf-8"))
    nonce = _os.urandom(12)
    ct = AESGCM(key).encrypt(nonce, plaintext, None)
    return b"OMNIMEENV1" + salt + nonce + ct


def _pg_dump() -> bytes | None:
    """Attempt a full pg_dump. Falls back to None if pg_dump isn't available
    (the bot container doesn't ship postgres-client by default; we use the
    structured JSON in that case)."""
    if shutil.which("pg_dump") is None:
        return None
    try:
        env = {
            "PGPASSWORD": settings.db_password,
            "PATH": "/usr/local/bin:/usr/bin:/bin",
        }
        result = subprocess.run(
            [
                "pg_dump",
                "-h", settings.db_host,
                "-p", str(settings.db_port),
                "-U", settings.db_user,
                "-d", settings.db_name,
                "--no-owner",
                "--clean",
                "--if-exists",
            ],
            capture_output=True,
            check=True,
            env=env,
            timeout=120,
        )
        return result.stdout
    except Exception as exc:
        logger.warning("pg_dump failed: %s", exc)
        return None


def _prune_old_backups() -> None:
    keep = max(1, settings.backup_keep)
    files = sorted(settings.backups_dir.glob("omnime_backup_*.tar.gz"))
    for old in files[:-keep]:
        try:
            old.unlink()
            logger.info("Pruned old backup: %s", old.name)
        except Exception as exc:
            logger.warning("Could not remove %s: %s", old, exc)


def _push_offsite(archive: Path) -> None:
    target = (settings.backup_remote or "none").lower()
    if target == "none":
        return
    try:
        if target in ("s3", "b2"):
            _push_s3(archive)
        elif target == "scp":
            _push_scp(archive)
        elif target == "rclone":
            _push_rclone(archive)
        elif target in ("gdrive", "drive"):
            _push_gdrive(archive)
        else:
            logger.warning("Unknown BACKUP_REMOTE: %s", target)
    except Exception as exc:
        logger.error("Off-site backup push failed (%s): %s", target, exc)


def _push_gdrive(archive: Path) -> None:
    """Upload backup to a 'Backups' subfolder of the OMNIME workspace folder
    in the user's Drive. Reuses the same OAuth token as the rest of the
    Drive integration."""
    from src.integrations.drive_client import DriveClient

    client = DriveClient()
    if not client.enabled:
        logger.warning("GDRIVE_* not configured; skipping Drive backup push")
        return

    backups_folder_id = _ensure_drive_backups_folder(client)
    try:
        result = client.upload(
            local_path=archive,
            description=f"OMNIME backup created {archive.name}",
        )
        # Move the freshly uploaded file from the workspace root into the
        # Backups subfolder.
        client.move(file_id=result["id"], new_parent_id=backups_folder_id)
        logger.info(
            "Backup uploaded to Drive: %s (%s)",
            archive.name, result.get("webViewLink"),
        )
    except Exception as exc:
        logger.error("Drive backup upload failed: %s", exc)
        raise


def _ensure_drive_backups_folder(client) -> str:
    """Return the id of the 'Backups' subfolder under OMNIME, creating it
    on first run."""
    existing = client.find_by_name("Backups", only_folders=True)
    if existing:
        return existing[0]["id"]
    created = client.create_folder("Backups")
    logger.info("Created OMNIME/Backups folder in Drive: %s", created.get("id"))
    return created["id"]


def _push_s3(archive: Path) -> None:
    if not (settings.s3_bucket and settings.s3_access_key and settings.s3_secret_key):
        logger.warning("S3 backup target missing required credentials")
        return
    try:
        import boto3
    except ImportError:
        logger.warning("boto3 not installed; skipping S3 push (pip install boto3)")
        return

    client_kwargs: dict[str, Any] = {
        "aws_access_key_id": settings.s3_access_key,
        "aws_secret_access_key": settings.s3_secret_key,
        "region_name": settings.s3_region or "auto",
    }
    if settings.s3_endpoint:
        client_kwargs["endpoint_url"] = settings.s3_endpoint
    s3 = boto3.client("s3", **client_kwargs)
    key = f"{settings.s3_prefix.rstrip('/')}/{archive.name}"
    s3.upload_file(str(archive), settings.s3_bucket, key)
    logger.info("Backup uploaded: s3://%s/%s", settings.s3_bucket, key)


def _push_scp(archive: Path) -> None:
    if not settings.scp_target:
        logger.warning("SCP_TARGET not configured")
        return
    cmd = ["scp"]
    if settings.scp_key_path:
        cmd.extend(["-i", settings.scp_key_path])
    cmd.extend([str(archive), settings.scp_target])
    subprocess.run(cmd, check=True, timeout=300)
    logger.info("Backup uploaded via SCP to %s", settings.scp_target)


def _push_rclone(archive: Path) -> None:
    if not settings.rclone_remote:
        logger.warning("RCLONE_REMOTE not configured")
        return
    if shutil.which("rclone") is None:
        logger.warning("rclone binary not installed in container")
        return
    subprocess.run(
        ["rclone", "copy", str(archive), settings.rclone_remote],
        check=True, timeout=300,
    )
    logger.info("Backup uploaded via rclone to %s", settings.rclone_remote)


def main() -> None:
    logging.basicConfig(level=settings.log_level)
    path = run_backup()
    print(f"Backup written: {path}")


if __name__ == "__main__":
    main()
