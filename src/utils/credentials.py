"""Encrypted credential vault — stores site logins with Fernet at rest.

Requires `ENCRYPTION_KEY` to be set; without it, refuses to write secrets.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Optional

from sqlalchemy import select

from src.memory import models as m
from src.memory.db import session_scope
from src.utils.crypto import decrypt, encrypt, is_enabled


logger = logging.getLogger(__name__)


@dataclass
class SiteCredentials:
    site: str
    login_url: Optional[str]
    username: Optional[str]
    password: Optional[str]
    notes: Optional[str] = None


class CredentialVault:
    @staticmethod
    def store(
        user_id: int,
        site: str,
        username: str | None = None,
        password: str | None = None,
        login_url: str | None = None,
        notes: str | None = None,
    ) -> None:
        if (username or password) and not is_enabled():
            raise RuntimeError(
                "ENCRYPTION_KEY is not set; refusing to store credentials in plaintext."
            )

        with session_scope() as s:
            existing = s.scalar(
                select(m.Credential).where(
                    m.Credential.user_id == user_id, m.Credential.site == site
                )
            )
            payload = {
                "user_id": user_id,
                "site": site,
                "login_url": login_url,
                "username_enc": encrypt(username) if username else None,
                "password_enc": encrypt(password) if password else None,
                "notes": notes,
            }
            if existing:
                for k, v in payload.items():
                    if v is not None:
                        setattr(existing, k, v)
            else:
                row = m.Credential(**payload)
                s.add(row)

    @staticmethod
    def get(user_id: int, site: str) -> Optional[SiteCredentials]:
        with session_scope() as s:
            row = s.scalar(
                select(m.Credential).where(
                    m.Credential.user_id == user_id, m.Credential.site == site
                )
            )
            if not row:
                return None
            return SiteCredentials(
                site=row.site,
                login_url=row.login_url,
                username=decrypt(row.username_enc) if row.username_enc else None,
                password=decrypt(row.password_enc) if row.password_enc else None,
                notes=row.notes,
            )

    @staticmethod
    def list_sites(user_id: int) -> list[str]:
        with session_scope() as s:
            return [
                r.site
                for r in s.scalars(
                    select(m.Credential).where(m.Credential.user_id == user_id)
                )
            ]

    @staticmethod
    def delete(user_id: int, site: str) -> bool:
        with session_scope() as s:
            row = s.scalar(
                select(m.Credential).where(
                    m.Credential.user_id == user_id, m.Credential.site == site
                )
            )
            if not row:
                return False
            s.delete(row)
            return True
