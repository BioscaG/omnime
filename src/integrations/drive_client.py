"""Google Drive wrapper. Uploads/lists/downloads files in the OMNIME
workspace folder so the user can grab them from any device.

Auto-creates a top-level folder named 'OMNIME' in the user's Drive root
on first use unless ``GDRIVE_WORKSPACE_FOLDER_ID`` overrides the target.
"""
from __future__ import annotations

import io
import logging
import mimetypes
from pathlib import Path
from typing import Any

from src.config import settings


logger = logging.getLogger(__name__)


WORKSPACE_FOLDER_NAME = "OMNIME"


class DriveClient:
    SCOPES = ["https://www.googleapis.com/auth/drive.file"]

    def __init__(self) -> None:
        self._service = None
        self._workspace_id: str | None = None
        self.enabled = bool(
            settings.gdrive_client_id
            and settings.gdrive_client_secret
            and settings.gdrive_refresh_token
        )

    def _build(self):
        if self._service is not None:
            return self._service
        if not self.enabled:
            raise RuntimeError("Drive not configured")
        from google.oauth2.credentials import Credentials
        from googleapiclient.discovery import build

        # Same scope-omission trick as Gmail/Calendar — let the refresh
        # endpoint use whatever scopes the consent originally granted.
        creds = Credentials(
            token=None,
            refresh_token=settings.gdrive_refresh_token,
            client_id=settings.gdrive_client_id,
            client_secret=settings.gdrive_client_secret,
            token_uri="https://oauth2.googleapis.com/token",
        )
        self._service = build("drive", "v3", credentials=creds, cache_discovery=False)
        return self._service

    def _workspace_folder(self) -> str:
        if self._workspace_id is not None:
            return self._workspace_id
        if settings.gdrive_workspace_folder_id:
            self._workspace_id = settings.gdrive_workspace_folder_id
            return self._workspace_id
        service = self._build()
        # Look for an existing OMNIME folder in the user's Drive root.
        resp = service.files().list(
            q=(
                f"name = '{WORKSPACE_FOLDER_NAME}' "
                f"and mimeType = 'application/vnd.google-apps.folder' "
                f"and 'root' in parents and trashed = false"
            ),
            spaces="drive",
            fields="files(id, name)",
            pageSize=1,
        ).execute()
        files = resp.get("files", [])
        if files:
            self._workspace_id = files[0]["id"]
            return self._workspace_id
        # Create it.
        meta = {
            "name": WORKSPACE_FOLDER_NAME,
            "mimeType": "application/vnd.google-apps.folder",
            "parents": ["root"],
        }
        created = service.files().create(body=meta, fields="id").execute()
        self._workspace_id = created["id"]
        logger.info("created OMNIME workspace folder in Drive: %s", self._workspace_id)
        return self._workspace_id

    def upload(
        self,
        local_path: Path | str,
        remote_name: str | None = None,
        description: str | None = None,
        mime_type: str | None = None,
    ) -> dict[str, Any]:
        from googleapiclient.http import MediaFileUpload

        service = self._build()
        path = Path(local_path)
        if not path.exists():
            raise FileNotFoundError(str(path))
        name = remote_name or path.name
        mtype = mime_type or mimetypes.guess_type(path.name)[0] or "application/octet-stream"
        body = {"name": name, "parents": [self._workspace_folder()]}
        if description:
            body["description"] = description
        media = MediaFileUpload(str(path), mimetype=mtype, resumable=False)
        result = service.files().create(
            body=body,
            media_body=media,
            fields="id, name, webViewLink, size, mimeType, createdTime",
        ).execute()
        return result

    def list_files(self, max_results: int = 30, query: str | None = None) -> list[dict[str, Any]]:
        service = self._build()
        folder_id = self._workspace_folder()
        q_parts = [f"'{folder_id}' in parents", "trashed = false"]
        if query:
            safe = query.replace("'", "\\'")
            q_parts.append(f"name contains '{safe}'")
        resp = service.files().list(
            q=" and ".join(q_parts),
            spaces="drive",
            fields="files(id, name, mimeType, webViewLink, size, createdTime, modifiedTime, description)",
            orderBy="modifiedTime desc",
            pageSize=max_results,
        ).execute()
        return resp.get("files", [])

    # MIME-type → export format for Google native files (Docs/Sheets/Slides).
    # Plain binaries (PDF, images, .py, …) skip this and use get_media.
    GOOGLE_NATIVE_EXPORTS = {
        "application/vnd.google-apps.document": ("application/pdf", "pdf"),
        "application/vnd.google-apps.spreadsheet": ("text/csv", "csv"),
        "application/vnd.google-apps.presentation": ("application/pdf", "pdf"),
        "application/vnd.google-apps.drawing": ("image/png", "png"),
    }

    def download(self, file_id: str, local_path: Path | str) -> Path:
        """Download a Drive file. Handles both regular binaries (PDF,
        images, code, etc.) and Google-native formats (Docs / Sheets /
        Slides) — natives are auto-exported (Doc → PDF, Sheet → CSV,
        Slides → PDF, Drawing → PNG)."""
        from googleapiclient.http import MediaIoBaseDownload

        service = self._build()
        path = Path(local_path)
        path.parent.mkdir(parents=True, exist_ok=True)

        meta = self.get_metadata(file_id)
        mime = meta.get("mimeType") or ""
        export = self.GOOGLE_NATIVE_EXPORTS.get(mime)
        if export:
            export_mime, ext = export
            # Append the export extension if the user didn't include one.
            if path.suffix.lower() != f".{ext}":
                path = path.with_suffix(f".{ext}")
            request = service.files().export_media(fileId=file_id, mimeType=export_mime)
        else:
            request = service.files().get_media(fileId=file_id)

        buf = io.BytesIO()
        downloader = MediaIoBaseDownload(buf, request)
        done = False
        while not done:
            _, done = downloader.next_chunk()
        path.write_bytes(buf.getvalue())
        return path

    def get_metadata(self, file_id: str) -> dict[str, Any]:
        service = self._build()
        return service.files().get(
            fileId=file_id,
            fields="id, name, mimeType, webViewLink, size, createdTime, modifiedTime, description",
        ).execute()

    def delete(self, file_id: str) -> None:
        service = self._build()
        service.files().delete(fileId=file_id).execute()

    def create_folder(self, name: str, parent_id: str | None = None) -> dict[str, Any]:
        """Create a folder. If parent_id is None, creates inside the OMNIME
        workspace folder. Returns the new folder's metadata."""
        service = self._build()
        parent = parent_id or self._workspace_folder()
        body = {
            "name": name,
            "mimeType": "application/vnd.google-apps.folder",
            "parents": [parent],
        }
        return service.files().create(
            body=body, fields="id, name, webViewLink, parents",
        ).execute()

    def move(
        self,
        file_id: str,
        new_parent_id: str | None = None,
        new_name: str | None = None,
    ) -> dict[str, Any]:
        """Move a file to a different parent and/or rename it."""
        service = self._build()
        update_body: dict[str, Any] = {}
        if new_name:
            update_body["name"] = new_name
        kwargs: dict[str, Any] = {
            "fileId": file_id,
            "fields": "id, name, parents, webViewLink",
        }
        if new_parent_id:
            current = service.files().get(fileId=file_id, fields="parents").execute()
            old_parents = ",".join(current.get("parents", []))
            kwargs["addParents"] = new_parent_id
            kwargs["removeParents"] = old_parents
        if update_body:
            kwargs["body"] = update_body
        return service.files().update(**kwargs).execute()

    def find_by_name(
        self,
        name: str,
        parent_id: str | None = None,
        only_folders: bool = False,
    ) -> list[dict[str, Any]]:
        """Look up files/folders by exact name in a given parent (defaults
        to the OMNIME workspace folder)."""
        service = self._build()
        parent = parent_id or self._workspace_folder()
        safe_name = name.replace("'", "\\'")
        q_parts = [f"'{parent}' in parents", "trashed = false", f"name = '{safe_name}'"]
        if only_folders:
            q_parts.append("mimeType = 'application/vnd.google-apps.folder'")
        resp = service.files().list(
            q=" and ".join(q_parts),
            spaces="drive",
            fields="files(id, name, mimeType, webViewLink)",
            pageSize=20,
        ).execute()
        return resp.get("files", [])

    def share_link(self, file_id: str) -> str:
        """Create a 'anyone with the link can view' permission and return
        the webViewLink. Use carefully — anyone with the URL can read."""
        service = self._build()
        try:
            service.permissions().create(
                fileId=file_id,
                body={"type": "anyone", "role": "reader"},
                fields="id",
            ).execute()
        except Exception as exc:
            logger.warning("share_link permission create failed: %s", exc)
        meta = self.get_metadata(file_id)
        return meta.get("webViewLink", "")
