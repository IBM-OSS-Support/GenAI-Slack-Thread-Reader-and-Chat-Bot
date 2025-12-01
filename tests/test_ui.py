#!/usr/bin/env python
"""
Rich TUI wrapper for test_bot.py.

Features:
- Runs test_bot.py as a subprocess (so you don't have to rewrite your tests)
- Parses its stdout in real time
- Treats any "=== ... ===" line as a logical test section
- Tracks each section's status (Pending / Running / Passed / Failed)
- Tracks elapsed time per section
- Tracks "summary latency" per section (for Analyze Thread / Analyze Channel)
- Shows a live table of test progress (including Duration & Latency columns)
- Shows a scrolling log of recent output
"""

import argparse
import subprocess
import sys
import re
import time
from typing import Dict, List, Optional

from rich.console import Console, Group
from rich.live import Live
from rich.table import Table
from rich.panel import Panel
from rich.text import Text

console = Console()

# Matches lines like: "=== Thread analysis + follow-up (DM) ==="
# or "=== PDF upload + Q&A (FU-03, channel @mention) ==="
TEST_LINE_RE = re.compile(r"^===\s*(.+?)\s*===$")

# Matches lines like: "[DM] ⏱ Thread analysis summary latency: 12.34 seconds"
LATENCY_RE = re.compile(r"summary latency:\s*([\d\.]+)\s*seconds", re.IGNORECASE)

STATUS_PENDING = "pending"
STATUS_RUNNING = "running"
STATUS_PASSED = "passed"
STATUS_FAILED = "failed"


class TestState:
    def __init__(self, name: str):
        self.name = name
        self.status = STATUS_PENDING
        self.details: List[str] = []
        self.had_pass = False
        self.had_fail = False

        # Timing for whole section
        self.start_time: Optional[float] = None
        self.end_time: Optional[float] = None
        self.duration: Optional[float] = None  # seconds

        # Command -> summary latency (from test_bot.py logs)
        self.latency: Optional[float] = None  # seconds


class RunState:
    def __init__(self):
        self.tests: Dict[int, TestState] = {}
        self.current_test: Optional[int] = None
        self.logs: List[str] = []
        self.exit_code: Optional[int] = None
        self.next_index: int = 1  # auto-increment for each new "===" section

    def ensure_test(self, index: int, name: str):
        if index not in self.tests:
            self.tests[index] = TestState(name)

    def start_test(self, index: int, name: str):
        # Finalize previous test, if any
        if self.current_test is not None:
            self.finalize_test(self.current_test)

        self.ensure_test(index, name)
        self.current_test = index
        t = self.tests[index]
        t.status = STATUS_RUNNING
        t.had_pass = False
        t.had_fail = False
        t.start_time = time.time()
        t.end_time = None
        t.duration = None
        # Keep latency as-is (in case logs appear later in test)

    def mark_pass_marker(self):
        if self.current_test is None:
            return
        t = self.tests[self.current_test]
        t.had_pass = True

    def mark_fail_marker(self):
        if self.current_test is None:
            return
        t = self.tests[self.current_test]
        t.had_fail = True
        t.status = STATUS_FAILED

    def set_latency_for_current(self, latency_seconds: float):
        if self.current_test is None:
            return
        t = self.tests[self.current_test]
        t.latency = latency_seconds

    def finalize_test(self, index: int):
        t = self.tests.get(index)
        if not t:
            return
        # capture timing
        if t.start_time is not None and t.end_time is None:
            t.end_time = time.time()
            t.duration = t.end_time - t.start_time

        if t.had_fail:
            t.status = STATUS_FAILED
        else:
            if t.had_pass:
                t.status = STATUS_PASSED
            else:
                # section ran but no ✅ or ❌ markers => treat as failed/unknown
                t.status = STATUS_FAILED

    def finalize_all(self):
        if self.current_test is not None:
            self.finalize_test(self.current_test)


def parse_forwarded_args() -> List[str]:
    """
    Capture all CLI args (including --bot-user-id, --thread-url, --pdf-path, etc.)
    and pass them straight through to test_bot.py.
    """
    parser = argparse.ArgumentParser(add_help=False)
    _, unknown = parser.parse_known_args()
    return unknown


def format_duration(seconds: Optional[float]) -> str:
    if seconds is None:
        return ""
    if seconds < 1:
        # show ms if under 1 second
        return f"{seconds * 1000:.0f} ms"
    if seconds < 60:
        return f"{seconds:.1f} s"
    mins = int(seconds // 60)
    rem = seconds - mins * 60
    if mins >= 60:
        hours = mins // 60
        mins = mins % 60
        return f"{hours}h {mins}m {rem:.0f}s"
    return f"{mins}m {rem:.0f}s"


def format_latency(seconds: Optional[float]) -> str:
    if seconds is None:
        return ""
    if seconds < 1:
        return f"{seconds * 1000:.0f} ms"
    if seconds < 60:
        return f"{seconds:.2f} s"
    mins = int(seconds // 60)
    rem = seconds - mins * 60
    return f"{mins}m {rem:.0f}s"


def make_status_table(state: RunState) -> Table:
    table = Table(title="Slack Bot Test Progress")
    table.add_column("#", style="bold", justify="right")
    table.add_column("Test Section")
    table.add_column("Duration")
    table.add_column("Latency (cmd→summary)")
    table.add_column("Status")

    if not state.tests:
        table.add_row("-", "Waiting for tests to start…", "", "", "")
        return table

    now = time.time()

    for idx in sorted(state.tests.keys()):
        t = self_test = state.tests[idx]

        # Determine duration to display (live for running, final for completed)
        if t.start_time is not None and t.end_time is None:
            duration = now - t.start_time
        else:
            duration = t.duration

        duration_text = format_duration(duration)
        latency_text = format_latency(t.latency)

        if t.status == STATUS_PENDING:
            status_text = Text("⏳ Pending", style="yellow")
        elif t.status == STATUS_RUNNING:
            status_text = Text("▶️ Running", style="cyan")
        elif t.status == STATUS_PASSED:
            status_text = Text("✅ Passed", style="green")
        elif t.status == STATUS_FAILED:
            status_text = Text("❌ Failed", style="red")
        else:
            status_text = Text(t.status)

        table.add_row(str(idx), t.name, duration_text, latency_text, status_text)

    return table


def make_log_panel(state: RunState, max_lines: int = 40) -> Panel:
    recent = state.logs[-max_lines:]
    if not recent:
        body = Text("No output yet…", style="dim")
    else:
        body = Text("\n".join(recent))

    title = "Live Output"
    if state.exit_code is not None:
        title += f" (exit code: {state.exit_code})"

    return Panel(body, title=title, border_style="blue")


def render(state: RunState):
    table = make_status_table(state)
    log_panel = make_log_panel(state)
    return Group(table, log_panel)


def update_state_from_line(state: RunState, line: str):
    stripped = line.strip()

    # Section header line, e.g. "=== Thread analysis + follow-up (DM) ==="
    m = TEST_LINE_RE.match(stripped)
    if m:
        name = m.group(1)
        index = state.next_index
        state.next_index += 1
        state.start_test(index, name)
        return

    # Latency line, e.g. "[DM] ⏱ Thread analysis summary latency: 12.34 seconds"
    m_lat = LATENCY_RE.search(stripped)
    if m_lat:
        try:
            latency_value = float(m_lat.group(1))
            state.set_latency_for_current(latency_value)
        except ValueError:
            pass  # ignore parse errors
        # Don't return; the same line may also contain ✅/❌ markers below

    # Success / failure markers – any line containing ✅ or ❌
    if "❌" in stripped:
        state.mark_fail_marker()
    elif "✅" in stripped:
        state.mark_pass_marker()


def main():
    forwarded_args = parse_forwarded_args()

    # Run test_bot.py in UNBUFFERED mode so we see prints live (-u)
    cmd = [sys.executable, "-u", "tests/test_bot.py"] + forwarded_args

    state = RunState()

    console.print(f"[bold]Running:[/bold] {' '.join(cmd)}\n")

    proc = subprocess.Popen(
        cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        bufsize=1,  # line-buffered
    )

    if not proc.stdout:
        console.print("[red]Failed to capture stdout from test_bot.py[/red]")
        sys.exit(1)

    with Live(render(state), refresh_per_second=10, console=console) as live:
        for line in proc.stdout:
            line = line.rstrip("\n")
            state.logs.append(line)
            update_state_from_line(state, line)
            live.update(render(state))

        proc.wait()
        state.exit_code = proc.returncode
        # finalize last section
        state.finalize_all()
        live.update(render(state))

    console.print("\n[bold]Final Test Results:[/bold]")
    for idx in sorted(state.tests.keys()):
        t = state.tests[idx]
        dur = format_duration(t.duration)
        lat = format_latency(t.latency)
        console.print(f"{idx}. {t.name} -> {t.status} (Duration: {dur}, Latency: {lat})")

    console.print()

    if proc.returncode == 0:
        console.print("[bold green]🎉 All tests passed (exit code 0).[/bold green]")
    else:
        console.print(
            f"[bold red]Some tests failed or script errored (exit code {proc.returncode}).[/bold red]"
        )

    sys.exit(proc.returncode)


if __name__ == "__main__":
    main()
