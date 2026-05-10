"""Gmail wrapper. Disabled when OAuth2 credentials are missing."""
from __future__ import annotations

import base64
import logging
import re
from email.mime.text import MIMEText
from email.utils import parseaddr
from typing import Any

from src.config import settings


logger = logging.getLogger(__name__)


NOREPLY_RE = re.compile(r"\b(no[-_.]?reply|donotreply|noreply|notifications?)\b", re.I)


def is_noreply(address: str) -> bool:
    if not address:
        return False
    name, email = parseaddr(address)
    return bool(NOREPLY_RE.search(email or "") or NOREPLY_RE.search(name or ""))


def _decode_part(part: dict[str, Any]) -> str:
    body = part.get("body", {})
    data = body.get("data")
    if not data:
        return ""
    try:
        return base64.urlsafe_b64decode(data.encode("utf-8") + b"==").decode(
            "utf-8", errors="replace"
        )
    except Exception:
        return ""


def _walk_for_text(payload: dict[str, Any]) -> str:
    """Walk the MIME tree, prefer text/plain, fall back to text/html stripped."""
    if not payload:
        return ""
    mime = payload.get("mimeType", "")
    if mime == "text/plain":
        return _decode_part(payload)
    if mime == "text/html":
        html = _decode_part(payload)
        return _strip_html(html)

    plain = ""
    html_fallback = ""
    for part in payload.get("parts", []) or []:
        nested = _walk_for_text(part)
        if part.get("mimeType") == "text/plain" and nested:
            plain = plain or nested
        elif part.get("mimeType") == "text/html" and nested:
            html_fallback = html_fallback or nested
        elif nested:
            plain = plain or nested
    return plain or html_fallback


def _strip_html(html: str) -> str:
    if not html:
        return ""
    text = re.sub(r"<style[^>]*>.*?</style>", "", html, flags=re.S | re.I)
    text = re.sub(r"<script[^>]*>.*?</script>", "", text, flags=re.S | re.I)
    text = re.sub(r"<br\s*/?>", "\n", text, flags=re.I)
    text = re.sub(r"</p>", "\n\n", text, flags=re.I)
    text = re.sub(r"<[^>]+>", "", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


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

        # NOTE: don't pass `scopes=self.SCOPES` here — google-auth would forward
        # them to the refresh endpoint, and Google returns `invalid_scope` if
        # the listed scopes don't exactly match what the user originally
        # consented to. The refresh token already encodes the granted scopes,
        # so omitting them lets the refresh succeed with whatever was granted.
        creds = Credentials(
            token=None,
            refresh_token=settings.gmail_refresh_token,
            client_id=settings.gmail_client_id,
            client_secret=settings.gmail_client_secret,
            token_uri="https://oauth2.googleapis.com/token",
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
        """Search inbox using Gmail's query syntax (`from:`, `subject:`,
        `after:YYYY/MM/DD`, etc.). Returns enriched results, not just IDs."""
        service = self._build()
        resp = service.users().messages().list(
            userId="me", q=query, maxResults=max_results
        ).execute()
        msgs = resp.get("messages", [])
        out: list[dict[str, Any]] = []
        for m in msgs:
            data = service.users().messages().get(
                userId="me", id=m["id"], format="metadata",
                metadataHeaders=["From", "Subject", "Date"],
            ).execute()
            headers = {h["name"]: h["value"] for h in data.get("payload", {}).get("headers", [])}
            out.append({
                "id": m["id"],
                "thread_id": data.get("threadId"),
                "from": headers.get("From"),
                "subject": headers.get("Subject"),
                "date": headers.get("Date"),
                "snippet": data.get("snippet"),
            })
        return out

    def get_message(self, message_id: str) -> dict[str, Any]:
        service = self._build()
        return service.users().messages().get(
            userId="me", id=message_id, format="full",
        ).execute()

    def get_message_parsed(self, message_id: str) -> dict[str, Any]:
        """Return a friendly dict with sender/subject/body/headers ready to
        feed back to Claude. ``body`` is plain text (HTML stripped)."""
        raw = self.get_message(message_id)
        payload = raw.get("payload") or {}
        headers = {
            h["name"]: h["value"]
            for h in payload.get("headers", [])
        }
        body = _walk_for_text(payload)
        return {
            "id": raw.get("id"),
            "thread_id": raw.get("threadId"),
            "from": headers.get("From"),
            "to": headers.get("To"),
            "cc": headers.get("Cc"),
            "subject": headers.get("Subject"),
            "date": headers.get("Date"),
            "message_id_header": headers.get("Message-ID") or headers.get("Message-Id"),
            "references": headers.get("References"),
            "body": body,
            "snippet": raw.get("snippet"),
            "label_ids": raw.get("labelIds", []),
        }

    def create_draft(self, to: str, subject: str, body: str) -> dict[str, Any]:
        service = self._build()
        msg = MIMEText(body)
        msg["to"] = to
        msg["subject"] = subject
        raw = base64.urlsafe_b64encode(msg.as_bytes()).decode()
        return service.users().drafts().create(
            userId="me", body={"message": {"raw": raw}}
        ).execute()

    def send(
        self,
        to: str,
        subject: str,
        body: str,
        *,
        thread_id: str | None = None,
        in_reply_to: str | None = None,
        references: str | None = None,
        cc: str | None = None,
    ) -> dict[str, Any]:
        """Send an email. When replying, pass ``thread_id`` and ``in_reply_to``
        (the original Message-ID header) so Gmail threads it correctly and
        the recipient's client recognises the reply."""
        service = self._build()
        msg = MIMEText(body)
        msg["to"] = to
        msg["subject"] = subject
        if cc:
            msg["cc"] = cc
        if in_reply_to:
            msg["In-Reply-To"] = in_reply_to
            msg["References"] = (references + " " + in_reply_to).strip() if references else in_reply_to
        raw = base64.urlsafe_b64encode(msg.as_bytes()).decode()
        request_body: dict[str, Any] = {"raw": raw}
        if thread_id:
            request_body["threadId"] = thread_id
        return service.users().messages().send(userId="me", body=request_body).execute()

    def archive(self, message_id: str) -> None:
        service = self._build()
        service.users().messages().modify(
            userId="me", id=message_id,
            body={"removeLabelIds": ["INBOX"]},
        ).execute()

    def mark_read(self, message_id: str) -> None:
        service = self._build()
        service.users().messages().modify(
            userId="me", id=message_id,
            body={"removeLabelIds": ["UNREAD"]},
        ).execute()
