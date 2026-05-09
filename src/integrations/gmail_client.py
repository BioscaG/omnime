"""Gmail wrapper. Disabled when OAuth2 credentials are missing."""
from __future__ import annotations

import base64
import logging
from email.mime.text import MIMEText
from typing import Any

from src.config import settings


logger = logging.getLogger(__name__)


class GmailClient:
    SCOPES = [
        "https://www.googleapis.com/auth/gmail.readonly",
        "https://www.googleapis.com/auth/gmail.send",
        "https://www.googleapis.com/auth/gmail.compose",
        "https://www.googleapis.com/auth/gmail.modify",
    ]

    def __init__(self) -> None:
        self._service = None
        self.enabled = bool(
            settings.gmail_client_id
            and settings.gmail_client_secret
            and settings.gmail_refresh_token
        )

    def _build(self):
        if self._service is not None:
            return self._service
        if not self.enabled:
            raise RuntimeError("Gmail not configured")
        from google.oauth2.credentials import Credentials
        from googleapiclient.discovery import build

        creds = Credentials(
            token=None,
            refresh_token=settings.gmail_refresh_token,
            client_id=settings.gmail_client_id,
            client_secret=settings.gmail_client_secret,
            token_uri="https://oauth2.googleapis.com/token",
            scopes=self.SCOPES,
        )
        self._service = build("gmail", "v1", credentials=creds, cache_discovery=False)
        return self._service

    def list_unread(self, max_results: int = 10) -> list[dict[str, Any]]:
        service = self._build()
        resp = service.users().messages().list(
            userId="me", q="is:unread in:inbox", maxResults=max_results
        ).execute()
        msgs = resp.get("messages", [])
        out: list[dict[str, Any]] = []
        for m in msgs:
            data = service.users().messages().get(
                userId="me", id=m["id"], format="metadata",
                metadataHeaders=["From", "Subject", "Date"],
            ).execute()
            headers = {h["name"]: h["value"] for h in data.get("payload", {}).get("headers", [])}
            out.append(
                {
                    "id": m["id"],
                    "thread_id": data.get("threadId"),
                    "from": headers.get("From"),
                    "subject": headers.get("Subject"),
                    "date": headers.get("Date"),
                    "snippet": data.get("snippet"),
                }
            )
        return out

    def search(self, query: str, max_results: int = 10) -> list[dict[str, Any]]:
        service = self._build()
        resp = service.users().messages().list(
            userId="me", q=query, maxResults=max_results
        ).execute()
        return resp.get("messages", [])

    def get_message(self, message_id: str) -> dict[str, Any]:
        service = self._build()
        return service.users().messages().get(
            userId="me", id=message_id, format="full",
        ).execute()

    def create_draft(self, to: str, subject: str, body: str) -> dict[str, Any]:
        service = self._build()
        msg = MIMEText(body)
        msg["to"] = to
        msg["subject"] = subject
        raw = base64.urlsafe_b64encode(msg.as_bytes()).decode()
        return service.users().drafts().create(
            userId="me", body={"message": {"raw": raw}}
        ).execute()

    def send(self, to: str, subject: str, body: str) -> dict[str, Any]:
        service = self._build()
        msg = MIMEText(body)
        msg["to"] = to
        msg["subject"] = subject
        raw = base64.urlsafe_b64encode(msg.as_bytes()).decode()
        return service.users().messages().send(
            userId="me", body={"raw": raw}
        ).execute()
