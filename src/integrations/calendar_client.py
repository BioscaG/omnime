"""Google Calendar wrapper."""
from __future__ import annotations

import logging
from datetime import datetime
from typing import Any

from src.config import settings


logger = logging.getLogger(__name__)


class CalendarClient:
    SCOPES = ["https://www.googleapis.com/auth/calendar"]

    def __init__(self, calendar_id: str = "primary") -> None:
        self._service = None
        self.calendar_id = calendar_id
        self.enabled = bool(
            settings.gcal_client_id
            and settings.gcal_client_secret
            and settings.gcal_refresh_token
        )

    def _build(self):
        if self._service is not None:
            return self._service
        if not self.enabled:
            raise RuntimeError("Calendar not configured")
        from google.oauth2.credentials import Credentials
        from googleapiclient.discovery import build

        creds = Credentials(
            token=None,
            refresh_token=settings.gcal_refresh_token,
            client_id=settings.gcal_client_id,
            client_secret=settings.gcal_client_secret,
            token_uri="https://oauth2.googleapis.com/token",
            scopes=self.SCOPES,
        )
        self._service = build("calendar", "v3", credentials=creds, cache_discovery=False)
        return self._service

    def list_events(self, time_min: datetime, time_max: datetime, max_results: int = 20) -> list[dict[str, Any]]:
        service = self._build()
        resp = service.events().list(
            calendarId=self.calendar_id,
            timeMin=time_min.isoformat() + "Z",
            timeMax=time_max.isoformat() + "Z",
            singleEvents=True,
            orderBy="startTime",
            maxResults=max_results,
        ).execute()
        events = resp.get("items", [])
        out = []
        for e in events:
            out.append(
                {
                    "id": e.get("id"),
                    "summary": e.get("summary"),
                    "start": (e.get("start") or {}).get("dateTime") or (e.get("start") or {}).get("date"),
                    "end": (e.get("end") or {}).get("dateTime") or (e.get("end") or {}).get("date"),
                    "location": e.get("location"),
                    "description": e.get("description"),
                    "attendees": [a.get("email") for a in e.get("attendees", [])],
                }
            )
        return out

    def create_event(
        self,
        summary: str,
        start: datetime,
        end: datetime,
        description: str | None = None,
        location: str | None = None,
        attendees: list[str] | None = None,
    ) -> dict[str, Any]:
        service = self._build()
        body: dict[str, Any] = {
            "summary": summary,
            "start": {"dateTime": start.isoformat()},
            "end": {"dateTime": end.isoformat()},
        }
        if description:
            body["description"] = description
        if location:
            body["location"] = location
        if attendees:
            body["attendees"] = [{"email": a} for a in attendees]
        return service.events().insert(calendarId=self.calendar_id, body=body).execute()

    def has_conflict(self, start: datetime, end: datetime) -> bool:
        events = self.list_events(start, end, max_results=5)
        return any(e for e in events if e.get("start") and e.get("end"))
