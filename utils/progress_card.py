from dotenv import load_dotenv
load_dotenv()


import threading
import time

import logging

from slack_sdk import WebClient


logging.basicConfig(level=logging.DEBUG)
from typing import Literal, Callable

class ProgressCard:
    """Professional card-style progress using Slack blocks."""
    def __init__(self, client: WebClient, channel: str, thread_ts: str, title="Analyzing thread"):
        self.client = client
        self.channel = channel
        self.thread_ts = thread_ts
        self.title = title
        self.ts = None
        self._pct = 0
        self._start = time.time()

    @staticmethod
    def _bar_line(pct: int, width: int = 24) -> str:
        pct = max(0, min(100, int(pct)))
        fill = (pct * width) // 100
        return f"{'█'*fill}{'░'*(width-fill)} {pct:>3d}%"

    def _blocks(self, subtitle: str) -> list[dict]:
        bar = self._bar_line(self._pct)
        return [
            {"type": "header", "text": {"type": "plain_text", "text": self.title, "emoji": False}},
            {"type": "section", "text": {"type": "mrkdwn", "text": f"*Status*\n{bar}"}},
            {"type": "context", "elements": [{"type": "mrkdwn", "text": subtitle}]},
            {"type": "divider"}
        ]

    def start(self, subtitle="Starting…"):
        self._pct = 0
        resp = self.client.chat_postMessage(
            channel=self.channel,
            thread_ts=self.thread_ts,
            text=f"{self.title}…",             # fallback text
            blocks=self._blocks(subtitle)
        )
        self.ts = resp["ts"]

    def set(self, pct: int, subtitle: str):
        self._pct = max(0, min(100, int(pct)))
        if not self.ts:
            return
        self.client.chat_update(
            channel=self.channel,
            ts=self.ts,
            text=f"{self.title}: {self._pct}%",
            blocks=self._blocks(subtitle)
        )

    def maybe_time_bumps(self):
        elapsed = time.time() - self._start
        if 50 <= self._pct < 75 and elapsed >= 8:
            self.set(75, "Running analysis…")
        if 75 <= self._pct < 90 and elapsed >= 15:
            self.set(90, "Finalizing…")

    def finish(self, ok=True, note: str | None = None):
        self._pct = 100
        subtitle = note or ("Completed successfully." if ok else "Completed with errors.")
        if self.ts:
            self.client.chat_update(
                channel=self.channel,
                ts=self.ts,
                text=f"{self.title}: 100%",
                blocks=self._blocks(subtitle)
            )
