from dotenv import load_dotenv
load_dotenv()


import threading
import time

import logging
import os
from slack_sdk import WebClient


logging.basicConfig(level=logging.DEBUG)
from typing import Literal, Callable

PROGRESS_STYLE = os.getenv("PROGRESS_STYLE")
ProgressStyle = Literal[
    "blocks", "ascii", "squares", "thermometer", "chevrons",
    "dotted", "meter", "ticks", "steps"
]

def render_blocks(pct: int, width: int = 20) -> str:
    filled = (pct * width) // 100
    return f"[{'█'*filled}{'░'*(width-filled)}] {pct}%"

def render_ascii(pct: int, width: int = 24) -> str:
    filled = (pct * width) // 100
    return f"[{'#'*filled}{'-'*(width-filled)}] {pct}%"

def render_squares(pct: int, width: int = 10) -> str:
    # 🟩 = filled, ⬜ = empty (works nicely in Slack)
    filled = (pct * width) // 100
    return f"{'🟩'*filled}{'⬜'*(width-filled)} {pct}%"

def render_thermometer(pct: int) -> str:
    # segments: 0, 10, 25, 50, 75, 90, 100
    stages = [
        (0,   "🌡️▁"),
        (10,  "🌡️▂"),
        (25,  "🌡️▃"),
        (50,  "🌡️▅"),
        (75,  "🌡️▆"),
        (90,  "🌡️▇"),
        (100, "🌡️█"),
    ]
    seg = next(s for t, s in stages if pct <= t) if pct <= 0 else next((s for t, s in stages if pct <= t), "🌡️█")
    return f"{seg} {pct}%"

def render_chevrons(pct: int, width: int = 12) -> str:
    filled = (pct * width) // 100
    return f"[{'»'*filled}{'·'*(width-filled)}] {pct}%"

def render_dotted(pct: int, width: int = 20) -> str:
    filled = (pct * width) // 100
    return f"[{'.'*filled}{' '*(width-filled)}] {pct}%"

def render_meter(pct: int) -> str:
    # simple gauge needle 0–10
    ticks = 10
    pos = round((pct/100) * ticks)
    scale = "".join("│" if i == pos else "·" for i in range(ticks+1))
    return f"⟨{scale}⟩ {pct}%"

def render_ticks(pct: int, width: int = 10) -> str:
    # ☑/☐ checklist-like meter
    filled = (pct * width) // 100
    return " ".join(["☑"]*filled + ["☐"]*(width-filled)) + f"  {pct}%"

def render_steps(pct: int, labels: list[str] | None = None) -> str:
    """
    Stepper view, e.g., [Fetch]—[Model]—[Reply]
    Completed = ✅, current = 🔄, pending = ⏳
    """
    steps = labels or ["Fetch", "Model", "Reply"]
    n = len(steps)
    step_index = min(n-1, max(0, int((pct/100) * n)))
    pieces = []
    for i, name in enumerate(steps):
        if pct == 100 or i < step_index:
            pieces.append(f"✅ {name}")
        elif i == step_index:
            pieces.append(f"🔄 {name}")
        else:
            pieces.append(f"⏳ {name}")
    return " — ".join(pieces) + f"  ({pct}%)"

STYLE_RENDERERS: dict[ProgressStyle, Callable[..., str]] = {
    "blocks":      render_blocks,
    "ascii":       render_ascii,
    "squares":     render_squares,
    "thermometer": render_thermometer,
    "chevrons":    render_chevrons,
    "dotted":      render_dotted,
    "meter":       render_meter,
    "ticks":       render_ticks,
    "steps":       render_steps,
}

class ProgressBar:
    def __init__(
        self,
        client: WebClient,
        channel: str,
        thread_ts: str,
        title: str = "Analyzing thread…",
        style: ProgressStyle = PROGRESS_STYLE,
    ):
        self.client = client
        self.channel = channel
        self.thread_ts = thread_ts
        self.title = title
        self.message_ts = None
        self._last_percent = -1
        self._done = False
        self._start_time = time.time()
        self.style = style

    def _render(self, percent: int) -> str:
        pct = max(0, min(100, int(percent)))
        renderer = STYLE_RENDERERS.get(self.style, render_blocks)
        # special handling for "steps" to show Fetch/Model/Reply phases
        if renderer is render_steps:
            return renderer(pct, labels=["Fetch", "Model", "Reply"])
        return renderer(pct)

    def _payload(self, percent: int, subtitle: str) -> str:
        bar = self._render(percent)
        return (
            f":hourglass_flowing_sand: *{self.title}*\n"
            f"`{bar}`\n"
            f"_Current step: {subtitle}_"
        )

    def start(self, subtitle="Starting…", initial=0):
        if self.message_ts is not None:
            return
        msg = self._payload(initial, subtitle)
        resp = self.client.chat_postMessage(
            channel=self.channel,
            text=msg,
            thread_ts=self.thread_ts
        )
        self.message_ts = resp["ts"]
        self._last_percent = initial

    def set(self, percent: int, subtitle: str):
        if self._done or self.message_ts is None:
            return
        percent = max(0, min(100, int(percent)))
        if percent == self._last_percent and subtitle:
            return
        self._last_percent = percent
        msg = self._payload(percent, subtitle)
        self.client.chat_update(channel=self.channel, ts=self.message_ts, text=msg)

    def maybe_time_bumps(self):
        if self._done:
            return
        elapsed = time.time() - self._start_time
        if 50 <= self._last_percent < 75 and elapsed >= 5:
            self.set(75, "Crunching with the model…")
        if 75 <= self._last_percent < 90 and elapsed >= 10:
            self.set(90, "Almost there…")

    def finish(self, subtitle="Completed."):
        self._done = True
        self.set(100, subtitle)
