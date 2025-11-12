from dotenv import load_dotenv
load_dotenv()

import time
import logging
from slack_sdk import WebClient
from slack_sdk.errors import SlackApiError

logging.basicConfig(level=logging.DEBUG)


class ProgressCard:
    """Professional card-style progress using Slack blocks, resilient across workspaces."""

    def __init__(self, client: WebClient, channel: str, thread_ts: str, title="Analyzing thread"):
        self.client = client
        self.channel = channel  # may be a DM channel id "D..." or a user id "U..."
        self.thread_ts = thread_ts
        self.title = title
        self.ts = None
        self._pct = 0
        self._start = time.time()

        # Capture team info for diagnostics
        try:
            auth = self.client.auth_test()
            self.team_id = auth.get("team_id")
            self.team_name = auth.get("team")
        except Exception:
            self.team_id = None
            self.team_name = None

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
            {"type": "divider"},
        ]

    def _resolve_dm_if_needed(self):
        """
        If `self.channel` looks like a user id (starts with 'U'), open a DM with that user
        using the *same client* and replace self.channel with the DM channel id.
        """
        try:
            if isinstance(self.channel, str) and self.channel.startswith("U"):
                resp = self.client.conversations_open(users=[self.channel])
                dm = resp.get("channel", {}).get("id")
                if dm:
                    logging.debug("[ProgressCard] resolved user %s -> dm %s", self.channel, dm)
                    self.channel = dm
        except SlackApiError as e:
            logging.warning("[ProgressCard] conversations_open failed for user %s: %s", self.channel, e.response.get("error"))

    def start(self, subtitle="Starting…"):
        """Post initial progress message."""
        self._pct = 0

        # If caller passed a user id, open a DM first (important for cross-workspace cases)
        self._resolve_dm_if_needed()

        try:
            resp = self.client.chat_postMessage(
                channel=self.channel,
                thread_ts=self.thread_ts,
                text=f"{self.title}…",  # fallback text
                blocks=self._blocks(subtitle),
            )
            self.ts = resp.get("ts")
            logging.info(
                "[ProgressCard] started: team=%s(%s) channel=%s ts=%s",
                self.team_name, self.team_id, self.channel, self.ts,
            )
        except SlackApiError as e:
            logging.exception(
                "[ProgressCard] Failed to post initial message: team=%s channel=%s error=%s",
                self.team_id, self.channel, e.response.get("error"),
            )
            self.ts = None

    def set(self, pct: int, subtitle: str):
        """Update progress bar percentage and subtitle."""
        self._pct = max(0, min(100, int(pct)))

        # If the original `start()` didn't resolve a DM because it had a user id,
        # make sure to resolve now (covers callers who passed a raw user id).
        self._resolve_dm_if_needed()

        if not self.ts:
            # nothing to update — create a new message (start() will set self.ts)
            logging.warning("[ProgressCard] No ts; creating new progress message.")
            return self.start(subtitle)

        try:
            self.client.chat_update(
                channel=self.channel,
                ts=self.ts,
                text=f"{self.title}: {self._pct}%",
                blocks=self._blocks(subtitle),
            )
        except SlackApiError as e:
            err = e.response.get("error", str(e))
            logging.warning(
                "[ProgressCard] chat.update failed (team=%s, channel=%s, ts=%s): %s",
                self.team_id, self.channel, self.ts, err,
            )
            if err == "message_not_found":
                # The message was removed or token can’t see it — re-post once
                logging.info(
                    "[ProgressCard] message_not_found; re-posting progress message in team=%s(%s)",
                    self.team_name, self.team_id,
                )
                try:
                    resp = self.client.chat_postMessage(
                        channel=self.channel,
                        thread_ts=self.thread_ts,
                        text=f"{self.title}: {self._pct}%",
                        blocks=self._blocks(subtitle),
                    )
                    self.ts = resp.get("ts")
                    logging.info(
                        "[ProgressCard] re-posted progress card: team=%s channel=%s ts=%s",
                        self.team_name, self.channel, self.ts,
                    )
                except SlackApiError as inner:
                    logging.exception(
                        "[ProgressCard] Failed to re-post after message_not_found: %s",
                        inner.response.get("error"),
                    )
            else:
                logging.exception("[ProgressCard] chat.update error: %s", err)

    def maybe_time_bumps(self):
        """Auto-advance progress visually if analysis takes long."""
        elapsed = time.time() - self._start
        if 50 <= self._pct < 75 and elapsed >= 8:
            self.set(75, "Running analysis…")
        if 75 <= self._pct < 90 and elapsed >= 15:
            self.set(90, "Finalizing…")

    def finish(self, ok=True, note: str | None = None):
        """Mark analysis finished successfully or with error."""
        self._pct = 100
        subtitle = note or ("Completed successfully." if ok else "Completed with errors.")

        # Ensure DM resolved (if starting with a user id)
        self._resolve_dm_if_needed()

        if not self.ts:
            return self.start(subtitle)
        try:
            self.client.chat_update(
                channel=self.channel,
                ts=self.ts,
                text=f"{self.title}: 100%",
                blocks=self._blocks(subtitle),
            )
        except SlackApiError as e:
            err = e.response.get("error", str(e))
            logging.warning(
                "[ProgressCard] finish() chat.update failed: team=%s channel=%s ts=%s err=%s",
                self.team_id, self.channel, self.ts, err,
            )
            if err == "message_not_found":
                # fall back to posting a new final message
                self.client.chat_postMessage(
                    channel=self.channel,
                    thread_ts=self.thread_ts,
                    text=f"{self.title}: done",
                    blocks=self._blocks(subtitle),
                )
