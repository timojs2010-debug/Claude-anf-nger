"""Push-Benachrichtigungen über ntfy und/oder Telegram. Fehler werden geloggt, nie geworfen."""
from __future__ import annotations

import logging

import requests

log = logging.getLogger(__name__)


class Notifier:
    def __init__(self, settings, dry_run: bool = False):
        self.s = settings
        self.dry_run = dry_run
        self.sent: list[dict] = []  # für Tests / Ausgabe

    @property
    def configured(self) -> bool:
        return bool(self.s.ntfy_topic or (self.s.telegram_bot_token and self.s.telegram_chat_id))

    def send(self, title: str, body: str, priority: str = "default", tags: str = "") -> bool:
        self.sent.append({"title": title, "body": body, "priority": priority})
        if self.dry_run or not self.configured:
            print(f"\n=== {title} ===\n{body}\n")
            return not self.configured or self.dry_run
        ok = False
        if self.s.ntfy_topic:
            ok |= self._ntfy(title, body, priority, tags)
        if self.s.telegram_bot_token and self.s.telegram_chat_id:
            ok |= self._telegram(title, body)
        return ok

    def _ntfy(self, title: str, body: str, priority: str, tags: str) -> bool:
        headers = {"Title": title.encode("utf-8"), "Priority": priority}
        if tags:
            headers["Tags"] = tags
        if self.s.ntfy_token:
            headers["Authorization"] = f"Bearer {self.s.ntfy_token}"
        try:
            r = requests.post(f"{self.s.ntfy_server}/{self.s.ntfy_topic}", data=body.encode("utf-8"),
                              headers=headers, timeout=15)
            r.raise_for_status()
            return True
        except requests.RequestException as e:
            log.error("ntfy fehlgeschlagen: %s", e)
            return False

    def _telegram(self, title: str, body: str) -> bool:
        try:
            r = requests.post(
                f"https://api.telegram.org/bot{self.s.telegram_bot_token}/sendMessage",
                json={"chat_id": self.s.telegram_chat_id, "text": f"{title}\n\n{body}"[:4000],
                      "disable_web_page_preview": True},
                timeout=15,
            )
            r.raise_for_status()
            return True
        except requests.RequestException as e:
            log.error("Telegram fehlgeschlagen: %s", e)
            return False
