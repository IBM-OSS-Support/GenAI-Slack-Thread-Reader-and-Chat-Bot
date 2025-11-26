#!/usr/bin/env python
"""
Simple Slack bot test runner.

What it tests (for each mode: DM, and optionally channel @mention):

1. Thread analysis:
   - Sends:  "analyze <thread_url>"
   - Waits for replies in the same conversation/thread
   - Handles bots that first send a progress/status message and then the summary
   - Asserts that the summary reply contains headings:
       - "Summary"
       - "Business Impact"
   - Asserts that the summary reply has action buttons:
       - Export PDF
       - Translate
       - Thumbs Up
       - Thumbs Down
   - Then expects a follow-up "deep-dive prompt" style message
   - Then asks "Explain the timeline in more detail." and validates the deep-dive answer

2. Channel analysis (via channel name):
   - Sends:  "analyze <channel_name> last:1y"
   - Same expectations re: summary headings and buttons
     (with optional progress message first)

3. Channel analysis invalid name (DM only) - CA-04:
   - DM: "analyze #not-a-real-channel"
   - Expects a helpful "channel not found" style error.

4. Channel analysis via channel ID (DM only) - CA-05:
   - DM: "analyze <#C12345678>"
   - Expects analysis to run and return results (same checks as normal channel analysis).

5. Greeting:
   - Sends:  "Hi"
   - Waits for reply
   - Asserts response looks like a greeting (hi/hello/hey/etc.)

6. In-thread memory:
   - Sends: "Hi, my name is John." as a thread root
   - In same thread, sends: "What is my name?"
   - Asserts the bot correctly recalls "John"

7. File uploads (DM and channel @mention):

   FU-03: PDF upload + Q&A
     - Upload a PDF
     - Bot sends:
         ":loadingcircle: Received <file>. Indexing now…"
         ":checked: Finished indexing <file>. What would you like to know?"
     - Ask in same thread: "Summarize the key points."
     - Validate non-trivial answer.

   FU-04: Excel upload
     - Upload .xlsx
     - Expect finish message mentioning sheet name, rows, columns, and querying tips.

   FU-05: Excel Q&A from table
     - In same thread as FU-04, ask a question answerable from the table.
     - Validate non-trivial answer (no obvious fallback).

   FU-06: Excel fallback RAG
     - In same thread as FU-04, ask a question not in the table.
     - Validate that bot gives a sane answer (may mention RAG/memory).

   FU-07: Image-only PDF
     - Upload an image-only PDF.
     - Bot should say it couldn't extract text from the file.

Modes:

- DM mode:
    messages are sent in direct message with the bot.
- Channel @mention mode:
    - messages are posted into a channel
    - root and thread messages are prefixed with "<@bot_user_id> ".
"""

import os
import sys
import time
import argparse
from typing import Optional, List, Dict, Any, Tuple

from slack_sdk import WebClient
from slack_sdk.errors import SlackApiError
from tenacity import retry, stop_after_attempt, wait_exponential


# ------------ Configuration ------------ #

DEFAULT_TIMEOUT_SECONDS = 120
POLL_INTERVAL_SECONDS = 3

REQUIRED_HEADINGS = ["Summary", "Business Impact"]
INVALID_CHANNEL_NAME = "not-a-real-channel"


# ------------ Basic helpers ------------ #

def is_deep_dive_prompt(text: str) -> bool:
    lower = text.lower()

    if "want a deeper dive?" in lower:
        return True

    keywords = [
        "reply in this thread with your question",
        "explain the timeline",
        "why did we escalate",
        "expand business impact",
    ]
    return any(k in lower for k in keywords)


def make_client(token: str) -> WebClient:
    if not token:
        raise RuntimeError("Missing Slack token. Set SLACK_USER_TOKEN in your environment.")
    return WebClient(token=token)


@retry(wait=wait_exponential(min=1, max=15), stop=stop_after_attempt(5))
def post_message(client: WebClient, channel: str, text: str, thread_ts: Optional[str] = None) -> str:
    resp = client.chat_postMessage(channel=channel, text=text, thread_ts=thread_ts)
    return resp["ts"]


def get_current_user_id(client: WebClient) -> str:
    resp = client.auth_test()
    return resp["user_id"]


def find_existing_dm_channel_with_bot(client: WebClient, bot_user_id: str) -> Optional[str]:
    cursor = None
    while True:
        resp = client.conversations_list(types="im", limit=1000, cursor=cursor)
        channels = resp.get("channels", [])
        for ch in channels:
            if ch.get("user") == bot_user_id:
                return ch["id"]

        cursor = resp.get("response_metadata", {}).get("next_cursor")
        if not cursor:
            break
    return None


def open_dm_channel(client: WebClient, bot_user_id: str) -> str:
    try:
        resp = client.conversations_open(users=bot_user_id)
        return resp["channel"]["id"]
    except SlackApiError as e:
        if e.response.get("error") == "cannot_dm_bot":
            print("Slack returned cannot_dm_bot; searching for existing DM channel…")
            dm_id = find_existing_dm_channel_with_bot(client, bot_user_id)
            if dm_id:
                print(f"Found existing DM channel with bot: {dm_id}")
                return dm_id
            raise RuntimeError(
                "Slack says cannot_dm_bot and no existing DM channel was found.\n"
                "Please manually open a DM with the bot in Slack (send it any message once), "
                "then re-run this script."
            ) from e
        raise


def get_channel_id_by_name(client: WebClient, channel_name: str) -> str:
    cursor = None
    while True:
        resp = client.conversations_list(
            types="public_channel,private_channel",
            limit=1000,
            cursor=cursor,
        )
        channels = resp.get("channels", [])
        for ch in channels:
            if ch.get("name") == channel_name:
                return ch["id"]

        cursor = resp.get("response_metadata", {}).get("next_cursor")
        if not cursor:
            break

    raise RuntimeError(
        f"Could not find channel with name '{channel_name}'. "
        f"Make sure the token has access and the name is correct."
    )


def find_bot_reply(
    messages: List[Dict[str, Any]],
    bot_user_id: str,
    sender_user_id: str,
) -> Optional[Dict[str, Any]]:
    for msg in messages:
        if "text" not in msg:
            continue

        user = msg.get("user")
        bot_profile = msg.get("bot_profile") or {}
        bot_profile_user_id = bot_profile.get("user_id")
        is_bot = bool(msg.get("subtype") == "bot_message" or msg.get("bot_id"))

        if user == bot_user_id:
            return msg
        if bot_profile_user_id == bot_user_id:
            return msg
        if is_bot and user != sender_user_id:
            return msg

    return None


def wait_for_bot_reply_raw(
    client: WebClient,
    channel_id: str,
    parent_ts: str,
    bot_user_id: str,
    timeout: int,
    sender_user_id: str,
    after_ts: Optional[str] = None,
) -> Optional[Dict[str, Any]]:
    deadline = time.time() + timeout
    after_ts_float = float(after_ts) if after_ts else None

    while time.time() < deadline:
        try:
            resp = client.conversations_replies(
                channel=channel_id,
                ts=parent_ts,
                inclusive=True,
                limit=50,
            )
        except SlackApiError as e:
            print(f"Error fetching replies: {e.response.get('error')}", file=sys.stderr)
            time.sleep(POLL_INTERVAL_SECONDS)
            continue

        messages = resp.get("messages", [])
        if messages:
            parent = messages[0]
            replies = messages[1:] if parent.get("ts") == parent_ts else messages

            if after_ts_float is not None:
                replies = [m for m in replies if m.get("ts") and float(m["ts"]) > after_ts_float]

            candidate = find_bot_reply(replies, bot_user_id, sender_user_id)
            if candidate:
                return candidate

        time.sleep(POLL_INTERVAL_SECONDS)

    return None


def headings_missing(text: str, headings: List[str]) -> List[str]:
    lower = text.lower()
    return [h for h in headings if h.lower() not in lower]


def is_progress_message(text: str) -> bool:
    lower = text.lower()
    keywords = [
        "status", "progress", "processing", "analyzing", "analysing",
        "working on", "in progress", "please wait", "loading"
    ]
    if any(k in lower for k in keywords):
        return True

    progress_chars = ["▰", "▱", "█", "░", "▓", "▒", "▮", "▯", "[==", "==]", "%"]
    if any(ch in text for ch in progress_chars):
        return True

    return False


# ------------ Button extraction & validation ------------ #

def extract_button_labels_from_message(msg: Dict[str, Any]) -> List[str]:
    labels: List[str] = []

    for block in msg.get("blocks", []) or []:
        btype = block.get("type")
        if btype == "actions":
            for el in block.get("elements", []) or []:
                if el.get("type") == "button":
                    text_obj = el.get("text") or {}
                    text = text_obj.get("text") or ""
                    if text:
                        labels.append(text.strip())
        elif btype == "section":
            accessory = block.get("accessory") or {}
            if accessory.get("type") == "button":
                text_obj = accessory.get("text") or {}
                text = text_obj.get("text") or ""
                if text:
                    labels.append(text.strip())

    for att in msg.get("attachments", []) or []:
        for block in att.get("blocks", []) or []:
            btype = block.get("type")
            if btype == "actions":
                for el in block.get("elements", []) or []:
                    if el.get("type") == "button":
                        text_obj = el.get("text") or {}
                        text = text_obj.get("text") or ""
                        if text:
                            labels.append(text.strip())
            elif btype == "section":
                accessory = block.get("accessory") or {}
                if accessory.get("type") == "button":
                    text_obj = accessory.get("text") or {}
                    text = text_obj.get("text") or ""
                    if text:
                        labels.append(text.strip())

    return labels


def missing_analysis_buttons(msg: Dict[str, Any]) -> List[str]:
    labels = extract_button_labels_from_message(msg)
    labels_lower = [l.lower() for l in labels]
    text_raw = msg.get("text") or ""
    text_lower = text_raw.lower()

    def in_labels_or_text(substr: str) -> bool:
        return any(substr in l for l in labels_lower) or (substr in text_lower)

    has_export_pdf = (
        in_labels_or_text("export to pdf")
        or in_labels_or_text("export pdf")
        or in_labels_or_text("export as pdf")
    )
    has_translate = (
        in_labels_or_text("translate")
        or in_labels_or_text("select language")
        or in_labels_or_text("language")
    )
    has_thumbs_up = (
        any("👍" in l for l in labels)
        or "👍" in text_raw
        or in_labels_or_text("thumbs up")
        or in_labels_or_text(":up")
        or in_labels_or_text(":+1:")
        or in_labels_or_text("like")
    )
    has_thumbs_down = (
        any("👎" in l for l in labels)
        or "👎" in text_raw
        or in_labels_or_text("thumbs down")
        or in_labels_or_text(":down")
        or in_labels_or_text(":-1:")
        or in_labels_or_text("dislike")
    )

    missing: List[str] = []
    if not has_export_pdf:
        missing.append("Export PDF")
    if not has_translate:
        missing.append("Translate")
    if not has_thumbs_up:
        missing.append("Thumbs Up")
    if not has_thumbs_down:
        missing.append("Thumbs Down")

    return missing


# ------------ File upload helper (files_upload_v2 + history) ------------ #

def upload_file_and_get_message_ts(
    client: WebClient,
    channel_id: str,
    sender_user_id: str,
    file_path: str,
    initial_comment: str,
) -> str:
    if not os.path.isfile(file_path):
        raise RuntimeError(f"File not found: {file_path}")

    with open(file_path, "rb") as f:
        resp = client.files_upload_v2(
            channel=channel_id,
            file=f,
            filename=os.path.basename(file_path),
            initial_comment=initial_comment,
        )

    file_obj = resp.get("file")
    if not file_obj:
        files_list = resp.get("files") or []
        if files_list:
            file_obj = files_list[0]

    if not file_obj:
        raise RuntimeError(
            f"files_upload_v2 response did not contain 'file' or 'files'. Response: {resp}"
        )

    file_id = file_obj.get("id")
    if not file_id:
        raise RuntimeError(f"No file id found in files_upload_v2 response: {file_obj}")

    deadline = time.time() + 20
    while time.time() < deadline:
        history = client.conversations_history(channel=channel_id, limit=50)
        for msg in history.get("messages", []):
            if msg.get("user") != sender_user_id:
                continue
            for f in msg.get("files", []) or []:
                if f.get("id") == file_id:
                    ts = msg.get("ts")
                    if ts:
                        return ts
        time.sleep(1)

    raise RuntimeError(
        f"Could not determine message ts for uploaded file in channel {channel_id}. "
        f"Could not find a recent message with file id {file_id}."
    )


# ------------ Conversation Context (DM vs @mention) ------------ #

class ConversationContext:
    def __init__(
        self,
        client: WebClient,
        channel_id: str,
        bot_user_id: str,
        sender_user_id: str,
        timeout: int,
        label: str,
        mention: bool = False,
    ):
        self.client = client
        self.channel_id = channel_id
        self.bot_user_id = bot_user_id
        self.sender_user_id = sender_user_id
        self.timeout = timeout
        self.label = label
        self.mention = mention

    def _format_text(self, text: str) -> str:
        if self.mention:
            return f"<@{self.bot_user_id}> {text}"
        return text

    def send_root(self, text: str) -> str:
        full_text = self._format_text(text)
        return post_message(self.client, self.channel_id, full_text)

    def send_reply(self, parent_ts: str, text: str) -> str:
        full_text = self._format_text(text)
        return post_message(self.client, self.channel_id, full_text, thread_ts=parent_ts)

    def wait_for_bot_reply(self, parent_ts: str, after_ts: Optional[str] = None) -> Optional[Dict[str, Any]]:
        return wait_for_bot_reply_raw(
            client=self.client,
            channel_id=self.channel_id,
            parent_ts=parent_ts,
            bot_user_id=self.bot_user_id,
            timeout=self.timeout,
            sender_user_id=self.sender_user_id,
            after_ts=after_ts,
        )

    def upload_file(self, file_path: str, initial_comment: str) -> str:
        full_comment = self._format_text(initial_comment)
        return upload_file_and_get_message_ts(
            client=self.client,
            channel_id=self.channel_id,
            sender_user_id=self.sender_user_id,
            file_path=file_path,
            initial_comment=full_comment,
        )


# ------------ Thread analysis ------------ #

def run_thread_analysis_test(ctx: ConversationContext, thread_url: str) -> bool:
    print(f"\n=== Thread analysis + follow-up ({ctx.label}) ===")
    cmd = f"analyze {thread_url}"
    print(f"[{ctx.label}] Sending command: {cmd}")

    parent_ts = ctx.send_root(cmd)

    first_reply = ctx.wait_for_bot_reply(parent_ts)
    if not first_reply:
        print(f"❌ No bot reply received for thread analysis ({ctx.label}) within timeout.")
        return False

    first_text = first_reply.get("text", "") or ""
    print(f"[{ctx.label}] First bot reply:\n{first_text}\n")

    missing_in_first = headings_missing(first_text, REQUIRED_HEADINGS)

    if not missing_in_first:
        print(f"✅ First bot reply ({ctx.label}) already contains all required headings (treated as summary).")
        summary_reply = first_reply
    else:
        if is_progress_message(first_text):
            print(f"ℹ️ First bot reply ({ctx.label}) looks like a progress/status message.")
        else:
            print(f"ℹ️ First bot reply ({ctx.label}) missing headings; treating as pre-summary/progress.")

        second_reply = ctx.wait_for_bot_reply(parent_ts, after_ts=first_reply.get("ts"))
        if not second_reply:
            print(f"❌ No second bot reply (summary) received ({ctx.label}) within timeout.")
            return False

        second_text = second_reply.get("text", "") or ""
        print(f"[{ctx.label}] Second bot reply (expected summary):\n{second_text}\n")

        missing_in_second = headings_missing(second_text, REQUIRED_HEADINGS)
        if missing_in_second:
            print(f"❌ Summary reply ({ctx.label}) is missing required headings: {missing_in_second}")
            return False

        print(f"✅ Thread analysis summary reply ({ctx.label}) contains all required headings.")
        summary_reply = second_reply

    missing_btns = missing_analysis_buttons(summary_reply)
    if missing_btns:
        print(
            f"❌ Thread analysis summary reply ({ctx.label}) is missing required buttons: "
            f"{', '.join(missing_btns)}"
        )
        return False

    print(
        f"✅ Thread analysis summary reply ({ctx.label}) has required action buttons "
        "(Export PDF, Translate, Thumbs Up, Thumbs Down)."
    )

    deep_dive_prompt_reply = ctx.wait_for_bot_reply(parent_ts, after_ts=summary_reply.get("ts"))
    if not deep_dive_prompt_reply:
        print(f"❌ No deep-dive prompt received after summary ({ctx.label}) within timeout.")
        return False

    deep_dive_prompt_text = deep_dive_prompt_reply.get("text", "") or ""
    print(f"[{ctx.label}] Deep-dive prompt:\n{deep_dive_prompt_text}\n")

    if not is_deep_dive_prompt(deep_dive_prompt_text):
        print("⚠️ Deep-dive prompt heuristic did not match; but got a follow-up message.")

    follow_up_question = "Explain the timeline in more detail."
    print(f"[{ctx.label}] Sending follow-up: {follow_up_question}")
    follow_up_ts = ctx.send_reply(parent_ts, follow_up_question)

    deep_dive_answer = ctx.wait_for_bot_reply(parent_ts, after_ts=follow_up_ts)
    if not deep_dive_answer:
        print(f"❌ No deep-dive answer received ({ctx.label}) after follow-up question within timeout.")
        return False

    deep_dive_answer_text = deep_dive_answer.get("text", "") or ""
    print(f"[{ctx.label}] Deep-dive answer:\n{deep_dive_answer_text}\n")

    lower = deep_dive_answer_text.lower()
    bad_phrases = [
        "i don't understand", "i do not understand",
        "i'm not sure", "i am not sure",
        "sorry, i can't", "sorry i can't",
        "i cannot answer", "error",
    ]
    if any(p in lower for p in bad_phrases):
        print(f"❌ Deep-dive answer ({ctx.label}) looks like an error or fallback.")
        return False

    if len(deep_dive_answer_text.strip()) < 30:
        print(f"⚠️ Deep-dive answer ({ctx.label}) is short; you may tighten this rule later.")

    print(f"✅ Deep-dive follow-up answer ({ctx.label}) looks good.")
    return True


# ------------ Channel analysis & variants ------------ #

def _run_channel_analysis_with_command(ctx: ConversationContext, cmd: str, heading_label: str) -> bool:
    print(f"\n=== {heading_label} ({ctx.label}) ===")
    print(f"[{ctx.label}] Sending command: {cmd}")

    parent_ts = ctx.send_root(cmd)

    first_reply = ctx.wait_for_bot_reply(parent_ts)
    if not first_reply:
        print(f"❌ No bot reply received for {heading_label.lower()} ({ctx.label}) within timeout.")
        return False

    first_text = first_reply.get("text", "") or ""
    print(f"[{ctx.label}] First bot reply:\n{first_text}\n")

    missing_in_first = headings_missing(first_text, REQUIRED_HEADINGS)

    if not missing_in_first:
        print(f"✅ First bot reply ({ctx.label}) already contains all required headings (treated as summary).")
        summary_reply = first_reply
    else:
        if is_progress_message(first_text):
            print(f"ℹ️ First bot reply ({ctx.label}) looks like progress. Waiting for summary…")
        else:
            print(f"ℹ️ First bot reply ({ctx.label}) missing headings; treating as pre-summary.")

        second_reply = ctx.wait_for_bot_reply(parent_ts, after_ts=first_reply.get("ts"))
        if not second_reply:
            print(f"❌ No second (summary) reply for {heading_label} ({ctx.label}) within timeout.")
            return False

        second_text = second_reply.get("text", "") or ""
        print(f"[{ctx.label}] Second bot reply (expected summary):\n{second_text}\n")

        missing_in_second = headings_missing(second_text, REQUIRED_HEADINGS)
        if missing_in_second:
            print(f"❌ Summary reply ({ctx.label}) is missing expected headings: {missing_in_second}")
            return False

        print(f"✅ {heading_label} summary reply ({ctx.label}) contains all required headings.")
        summary_reply = second_reply

    missing_btns = missing_analysis_buttons(summary_reply)
    if missing_btns:
        print(
            f"❌ {heading_label} summary reply ({ctx.label}) missing required buttons: "
            f"{', '.join(missing_btns)}"
        )
        return False

    print(
        f"✅ {heading_label} summary reply ({ctx.label}) has action buttons "
        "(Export PDF, Translate, Thumbs Up, Thumbs Down)."
    )

    return True


def run_channel_analysis_test(ctx: ConversationContext, channel_name: str) -> bool:
    cmd = f"analyze #{channel_name} last:1y"
    return _run_channel_analysis_with_command(ctx, cmd, "Channel analysis")


def run_channel_id_analysis_test(ctx: ConversationContext, channel_id: str) -> bool:
    cmd = f"analyze #{channel_id} last:1y"
    return _run_channel_analysis_with_command(ctx, cmd, "Channel analysis via channel ID")


def run_channel_invalid_name_test(ctx: ConversationContext, invalid_name: str) -> bool:
    print(f"\n=== Channel analysis invalid channel ({ctx.label}) ===")
    cmd = f"analyze #{invalid_name}"
    print(f"[{ctx.label}] Sending command: {cmd}")

    parent_ts = ctx.send_root(cmd)
    reply = ctx.wait_for_bot_reply(parent_ts)
    if not reply:
        print(f"❌ No bot reply received for invalid channel analysis ({ctx.label}) within timeout.")
        return False

    text = reply.get("text", "") or ""
    lower = text.lower()
    print(f"[{ctx.label}] Invalid channel reply:\n{text}\n")

    not_found_phrases = [
        "no channel named", "no channel called", "no channel matching",
        "could not find channel", "couldn't find channel",
        "unknown channel", "channel not found",
    ]
    mentions_name = (invalid_name in lower) or (f"#{invalid_name}" in lower)

    if any(p in lower for p in not_found_phrases) and mentions_name:
        print("✅ Invalid channel error looks good.")
        return True

    print("❌ Invalid channel reply does not look like a clear 'channel not found' error.")
    return False


# ------------ Greeting & Memory ------------ #

def run_greeting_test(ctx: ConversationContext) -> bool:
    print(f"\n=== Greeting ({ctx.label}) ===")
    cmd = "Hi"
    print(f"[{ctx.label}] Sending greeting: {cmd}")

    parent_ts = ctx.send_root(cmd)
    reply = ctx.wait_for_bot_reply(parent_ts)
    if not reply:
        print(f"❌ No greeting reply ({ctx.label}) within timeout.")
        return False

    text = reply.get("text", "") or ""
    print(f"[{ctx.label}] Greeting reply:\n{text}\n")

    greeting_keywords = ["hi", "hello", "hey", "hiya", "howdy"]
    lower = text.lower()
    if not any(word in lower for word in greeting_keywords):
        print(f"❌ Reply ({ctx.label}) does not look like a greeting.")
        return False

    print(f"✅ Greeting ({ctx.label}) looks good.")
    return True


def run_memory_test(ctx: ConversationContext) -> bool:
    print(f"\n=== In-thread memory (name recall, {ctx.label}) ===")

    intro_text = "Hi, my name is John."
    print(f"[{ctx.label}] Intro: {intro_text}")
    parent_ts = ctx.send_root(intro_text)

    intro_reply = ctx.wait_for_bot_reply(parent_ts)
    if not intro_reply:
        print(f"❌ No reply after introducing name ({ctx.label}) within timeout.")
        return False

    intro_reply_text = intro_reply.get("text", "") or ""
    print(f"[{ctx.label}] Intro reply:\n{intro_reply_text}\n")

    follow_up = "What is my name?"
    print(f"[{ctx.label}] Follow-up: {follow_up}")
    follow_up_ts = ctx.send_reply(parent_ts, follow_up)

    memory_answer = ctx.wait_for_bot_reply(parent_ts, after_ts=follow_up_ts)
    if not memory_answer:
        print(f"❌ No memory answer ({ctx.label}) within timeout.")
        return False

    memory_answer_text = memory_answer.get("text", "") or ""
    print(f"[{ctx.label}] Memory answer:\n{memory_answer_text}\n")

    lower = memory_answer_text.lower()
    if "john" not in lower:
        print(f"❌ Memory answer ({ctx.label}) does not mention 'John'.")
        return False

    bad_phrases = [
        "i don't remember", "i do not remember",
        "i don't know", "i do not know",
        "i'm not sure", "i am not sure",
        "sorry, i can't", "sorry i can't",
        "error",
    ]
    if any(p in lower for p in bad_phrases):
        print(f"❌ Memory answer ({ctx.label}) looks like a fallback.")
        return False

    if len(memory_answer_text.strip()) < 15:
        print(f"⚠️ Memory answer ({ctx.label}) is short; you may tighten this later.")

    print(f"✅ Memory test ({ctx.label}) passed.")
    return True


# ------------ File Upload Tests (FU-03..FU-07) ------------ #

def run_pdf_upload_and_qa_test(ctx: ConversationContext, pdf_path: str) -> bool:
    print(f"\n=== PDF upload + Q&A (FU-03, {ctx.label}) ===")

    try:
        parent_ts = ctx.upload_file(pdf_path, "Uploading PDF for FU-03 test.")
    except Exception as e:
        print(f"❌ Failed to upload PDF for FU-03 ({ctx.label}): {e}")
        return False

    received_msg = ctx.wait_for_bot_reply(parent_ts)
    if not received_msg:
        print(f"❌ No 'received/indexing' message for PDF ({ctx.label}) within timeout.")
        return False

    received_text = received_msg.get("text", "") or ""
    print(f"[{ctx.label}] PDF received/indexing:\n{received_text}\n")

    finished_msg = ctx.wait_for_bot_reply(parent_ts, after_ts=received_msg.get("ts"))
    if not finished_msg:
        print(f"❌ No 'finished indexing' message for PDF ({ctx.label}) within timeout.")
        return False

    finished_text = finished_msg.get("text", "") or ""
    print(f"[{ctx.label}] PDF finished indexing:\n{finished_text}\n")

    question = "Summarize the key points."
    print(f"[{ctx.label}] Asking FU-03 question: {question}")
    q_ts = ctx.send_reply(parent_ts, question)

    answer_msg = ctx.wait_for_bot_reply(parent_ts, after_ts=q_ts)
    if not answer_msg:
        print(f"❌ No FU-03 answer ({ctx.label}) within timeout.")
        return False

    answer_text = answer_msg.get("text", "") or ""
    print(f"[{ctx.label}] FU-03 answer:\n{answer_text}\n")

    lower = answer_text.lower()
    bad_phrases = [
        "i don't understand", "i do not understand",
        "i'm not sure", "i am not sure",
        "sorry, i can't", "sorry i can't",
        "cannot answer", "could not answer",
        "error",
    ]
    if any(p in lower for p in bad_phrases):
        print(f"❌ FU-03 answer looks like error/fallback ({ctx.label}).")
        return False

    if len(answer_text.strip()) < 40:
        print(f"⚠️ FU-03 answer ({ctx.label}) is short; you may tighten this later.")

    print(f"✅ FU-03 PDF upload + Q&A ({ctx.label}) looks good.")
    return True


def run_excel_upload_test(ctx: ConversationContext, excel_path: str) -> Tuple[bool, Optional[str]]:
    print(f"\n=== Excel upload (FU-04, {ctx.label}) ===")

    try:
        parent_ts = ctx.upload_file(excel_path, "")
    except Exception as e:
        print(f"❌ Failed to upload Excel file for FU-04 ({ctx.label}): {e}")
        return False, None

    received_msg = ctx.wait_for_bot_reply(parent_ts)
    if not received_msg:
        print(f"❌ No 'received/indexing' message for Excel ({ctx.label}) within timeout.")
        return False, parent_ts

    received_text = received_msg.get("text", "") or ""
    print(f"[{ctx.label}] Excel received/indexing:\n{received_text}\n")

    finish_msg = ctx.wait_for_bot_reply(parent_ts, after_ts=received_msg.get("ts"))
    if not finish_msg:
        print(f"❌ No 'finished indexing' message for Excel ({ctx.label}) within timeout.")
        return False, parent_ts

    text = finish_msg.get("text", "") or ""
    lower = text.lower()
    print(f"[{ctx.label}] Excel finished indexing:\n{text}\n")

    has_sheet = ("sheet" in lower) or ("worksheet" in lower)
    has_rows = "rows" in lower
    has_cols = ("cols" in lower) or ("columns" in lower)
    has_tips = any(k in lower for k in ["ask", "query", "question"])

    if not (has_sheet and has_rows and has_cols and has_tips):
        missing = []
        if not has_sheet:
            missing.append("sheet name")
        if not has_rows:
            missing.append("rows")
        if not has_cols:
            missing.append("columns")
        if not has_tips:
            missing.append("querying tips")
        print(f"❌ FU-04 Excel finish ({ctx.label}) missing: {', '.join(missing)}")
        return False, parent_ts

    print(f"✅ FU-04 Excel upload finish message looks good ({ctx.label}).")
    return True, parent_ts


def run_excel_qa_direct_table_test(ctx: ConversationContext, parent_ts: str) -> bool:
    print(f"\n=== Excel Q&A from table (FU-05, {ctx.label}) ===")

    question = "Who manages X?"  # customize for your sheet
    print(f"[{ctx.label}] FU-05 question: {question}")
    q_ts = ctx.send_reply(parent_ts, question)

    answer_msg = ctx.wait_for_bot_reply(parent_ts, after_ts=q_ts)
    if not answer_msg:
        print(f"❌ No FU-05 answer ({ctx.label}) within timeout.")
        return False

    text = answer_msg.get("text", "") or ""
    lower = text.lower()
    print(f"[{ctx.label}] FU-05 answer:\n{text}\n")

    bad_phrases = [
        "i don't understand", "i do not understand",
        "i'm not sure", "i am not sure",
        "sorry, i can't", "sorry i can't",
        "cannot answer", "could not answer",
        "error",
    ]
    if any(p in lower for p in bad_phrases):
        print(f"❌ FU-05 answer looks like fallback ({ctx.label}).")
        return False

    if len(text.strip()) < 20:
        print(f"⚠️ FU-05 answer ({ctx.label}) is short; you may tighten checks later.")

    print(f"✅ FU-05 Excel Q&A ({ctx.label}) looks okay at basic level.")
    return True


def run_excel_fallback_rag_test(ctx: ConversationContext, parent_ts: str) -> bool:
    print(f"\n=== Excel fallback RAG (FU-06, {ctx.label}) ===")

    question = "What is the company mission?"
    print(f"[{ctx.label}] FU-06 question: {question}")
    q_ts = ctx.send_reply(parent_ts, question)

    answer_msg = ctx.wait_for_bot_reply(parent_ts, after_ts=q_ts)
    if not answer_msg:
        print(f"❌ No FU-06 answer ({ctx.label}) within timeout.")
        return False

    text = answer_msg.get("text", "") or ""
    lower = text.lower()
    print(f"[{ctx.label}] FU-06 answer:\n{text}\n")

    bad_phrases = [
        "i don't understand", "i do not understand",
        "i'm not sure", "i am not sure",
        "sorry, i can't", "sorry i can't",
        "cannot answer", "could not answer",
        "error",
    ]
    if any(p in lower for p in bad_phrases):
        print(f"❌ FU-06 answer looks like fallback ({ctx.label}).")
        return False

    hints = [
        "not in the sheet", "not in the table",
        "based on other docs", "based on my knowledge",
    ]
    if not any(h in lower for h in hints):
        print("⚠️ FU-06 answer does not explicitly say it's using RAG/memory; "
              "tighten this check if your design expects that wording.")

    print(f"✅ FU-06 Excel fallback RAG ({ctx.label}) looks okay.")
    return True



# ------------Usage/Help Tests (FU-03..FU-07) ------------ #

def run_help_test(ctx: ConversationContext) -> bool:
    print(f"\n=== Help / Usage (HS-01, {ctx.label}) ===")
    cmd = "help"
    print(f"[{ctx.label}] Sending help command: {cmd}")

    parent_ts = ctx.send_root(cmd)

    reply = ctx.wait_for_bot_reply(parent_ts)

    if not reply:
        print(f"❌ No bot reply received for help/usage ({ctx.label}) within timeout.")
        return False

    text = reply.get("text", "") or ""
    print(f"[{ctx.label}] Help reply:\n{text}\n")

    # Required key strings
    required_keywords = [
        "Quick Start Guide",
        "Analyze Thread",
    ]

    lower = text.lower()
    missing = [kw for kw in required_keywords if kw.lower() not in lower]

    if missing:
        print(f"❌ Help reply ({ctx.label}) missing keywords: {missing}")
        return False

    print(f"✅ Help / Usage test ({ctx.label}) passed.")
    return True

# ------------ORG KB TEST ------------ #

def run_kb_org_query_test(ctx: ConversationContext) -> bool:
    """
    KB-01: Org-style KB query

    Q: -org who is Suport Direcor of Business Automtn Manager Open Edition ?

    Expected answer (flexible check):
      - Mentions "Support Director"
      - Mentions "Business Automation Manager Open Edition"
      - Mentions "Rakesh Ranjan"
    """
    print(f"\n=== KB org lookup (KB-01, {ctx.label}) ===")

    question = "-org who is Suport Direcor of Business Automtn Manager Open Edition ?"
    print(f"[{ctx.label}] Sending KB org query: {question}")

    parent_ts = ctx.send_root(question)

    reply = ctx.wait_for_bot_reply(parent_ts)
    if not reply:
        print(f"❌ No reply received for KB org query ({ctx.label}) within timeout.")
        return False

    text = reply.get("text", "") or ""
    lower = text.lower()
    print(f"[{ctx.label}] KB org reply:\n{text}\n")

    # Required content
    required_substrings = [
        "support director",                        # role
        "business automation manager open edition",  # product name
        "rakesh ranjan",                           # person
    ]

    missing = [s for s in required_substrings if s not in lower]

    if missing:
        print(f"❌ KB org reply ({ctx.label}) missing expected content: {missing}")
        return False

    # Optional sanity: ensure it's not an obvious fallback
    bad_phrases = [
        "i don't know", "i do not know",
        "i'm not sure", "i am not sure",
        "sorry", "error",
    ]
    if any(p in lower for p in bad_phrases):
        print(f"❌ KB org reply ({ctx.label}) looks like a fallback/error.")
        return False

    print(f"✅ KB org lookup (KB-01, {ctx.label}) passed.")
    return True


def run_kb_product_query_test(ctx: ConversationContext) -> bool:
    """
    KB-02: Product-style KB query

    Q: -product Business Automation Manager Open Edition

    Expected structured info (flexible check):
      - Product name
      - Product Manager: Phil Simpson
      - Support Director: Rakesh Ranjan
      - Support Owner: Erik Potenza
      - 2nd/1st Line Owner: Kleber Gomes Silva
    """
    print(f"\n=== KB product lookup (KB-02, {ctx.label}) ===")

    question = "-product Business Automation Manager Open Edition"
    print(f"[{ctx.label}] Sending KB product query: {question}")

    parent_ts = ctx.send_root(question)

    reply = ctx.wait_for_bot_reply(parent_ts)
    if not reply:
        print(f"❌ No reply received for KB product query ({ctx.label}) within timeout.")
        return False

    text = reply.get("text", "") or ""
    lower = text.lower()
    print(f"[{ctx.label}] KB product reply:\n{text}\n")

    # Required core fields from your expected answer
    required_pairs = [
        ("business automation manager open edition", "product name"),
        ("product manager", "Product Manager field"),
        ("phil simpson", "Product Manager: Phil Simpson"),
        ("support director", "Support Director field"),
        ("rakesh ranjan", "Support Director: Rakesh Ranjan"),
        ("support owner", "Support Owner field"),
        ("erik potenza", "Support Owner: Erik Potenza"),
        ("2nd/1st line owner", "2nd/1st Line Owner field"),
        ("kleber gomes silva", "2nd/1st Line Owner: Kleber Gomes Silva"),
    ]

    missing_labels = [label for substring, label in required_pairs if substring not in lower]

    if missing_labels:
        print(f"❌ KB product reply ({ctx.label}) missing fields: {missing_labels}")
        return False

    # Optional sanity: ensure it's not an obvious fallback
    bad_phrases = [
        "i don't know", "i do not know",
        "i'm not sure", "i am not sure",
        "sorry", "error",
    ]
    if any(p in lower for p in bad_phrases):
        print(f"❌ KB product reply ({ctx.label}) looks like a fallback/error.")
        return False

    # Optional: basic length check
    if len(text.strip()) < 80:
        print(f"⚠️ KB product reply ({ctx.label}) is quite short; "
              f"you may tighten this rule if you want a richer response.")
        # Not failing for now

    print(f"✅ KB product lookup (KB-02, {ctx.label}) passed.")
    return True

# ------------ Main ------------ #

def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Test workflow of a Slack bot via DM and optionally via @mention in a channel: "
            "thread analysis, channel analysis, error cases, file uploads, greeting, in-thread memory."
        )
    )
    parser.add_argument(
        "--bot-user-id",
        required=True,
        help="User ID of the bot to DM (e.g. U0ABC123DEF).",
    )
    parser.add_argument(
        "--thread-url",
        required=True,
        help="Slack thread URL to ask the bot to analyze.",
    )
    parser.add_argument(
        "--channel-name",
        required=True,
        help="Channel name (without #) for the bot to analyze, e.g. 'mec-test-with-contents'.",
    )
    parser.add_argument(
        "--mention-channel-name",
        required=False,
        default=None,
        help=(
            "Channel name (without #) where the bot should be @mentioned for additional tests. "
            "If omitted, @mention tests are skipped."
        ),
    )
    parser.add_argument(
        "--pdf-path",
        required=False,
        help="Path to a normal text PDF for FU-03.",
    )
    parser.add_argument(
        "--excel-path",
        required=False,
        help="Path to an .xlsx file for FU-04/FU-05/FU-06.",
    )
    parser.add_argument(
        "--image-pdf-path",
        required=False,
        help="Path to an image-only PDF (no text) for FU-07.",
    )
    parser.add_argument(
        "--timeout",
        type=int,
        default=DEFAULT_TIMEOUT_SECONDS,
        help=f"Timeout in seconds to wait for each bot reply (default: {DEFAULT_TIMEOUT_SECONDS}).",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()

    user_token = os.getenv("SLACK_USER_TOKEN")
    if not user_token:
        print(
            "Error: SLACK_USER_TOKEN is not set in the environment.\n"
            "Set it to a token that can open/read DMs with your bot.",
            file=sys.stderr,
        )
        sys.exit(2)

    client = make_client(user_token)

    try:
        sender_user_id = get_current_user_id(client)
    except SlackApiError as e:
        print(f"Error calling auth.test: {e.response.get('error')}", file=sys.stderr)
        sys.exit(1)

    try:
        dm_channel_id = open_dm_channel(client, args.bot_user_id)
    except Exception as e:
        print(f"Error opening DM with bot: {e}", file=sys.stderr)
        sys.exit(1)

    try:
        channel_id_for_name = get_channel_id_by_name(client, args.channel_name)
    except Exception as e:
        print(f"Error resolving channel name '{args.channel_name}': {e}", file=sys.stderr)
        sys.exit(1)

    mention_channel_id: Optional[str] = None
    if args.mention_channel_name:
        try:
            mention_channel_id = get_channel_id_by_name(client, args.mention_channel_name)
        except Exception as e:
            print(f"Error resolving mention channel name '{args.mention_channel_name}': {e}", file=sys.stderr)
            sys.exit(1)

    print(f"Using DM channel: {dm_channel_id}")
    print(f"Sender (this token) user_id: {sender_user_id}")
    print(f"Bot user_id: {args.bot_user_id}")
    print(f"Resolved channel '{args.channel_name}' to ID: {channel_id_for_name}")
    if mention_channel_id:
        print(f"Using mention channel: {mention_channel_id} (name: {args.mention_channel_name})")

    dm_ctx = ConversationContext(
        client=client,
        channel_id=dm_channel_id,
        bot_user_id=args.bot_user_id,
        sender_user_id=sender_user_id,
        timeout=args.timeout,
        label="DM",
        mention=False,
    )

    mention_ctx: Optional[ConversationContext] = None
    if mention_channel_id:
        mention_ctx = ConversationContext(
            client=client,
            channel_id=mention_channel_id,
            bot_user_id=args.bot_user_id,
            sender_user_id=sender_user_id,
            timeout=args.timeout,
            label="channel @mention",
            mention=True,
        )

    all_ok = True

    try:
        # DM tests
        if not run_thread_analysis_test(dm_ctx, args.thread_url):
            all_ok = False
        if not run_channel_analysis_test(dm_ctx, args.channel_name):
            all_ok = False
        if not run_channel_invalid_name_test(dm_ctx, INVALID_CHANNEL_NAME):
            all_ok = False
        if not run_channel_id_analysis_test(dm_ctx, channel_id_for_name):
            all_ok = False

        if args.pdf_path:
            if not run_pdf_upload_and_qa_test(dm_ctx, args.pdf_path):
                all_ok = False
        else:
            print("ℹ️ --pdf-path not provided; skipping FU-03 PDF test (DM).")

        if args.excel_path:
            ok_excel, excel_thread_ts = run_excel_upload_test(dm_ctx, args.excel_path)
            if not ok_excel or not excel_thread_ts:
                all_ok = False
            else:
                if not run_excel_qa_direct_table_test(dm_ctx, excel_thread_ts):
                    all_ok = False
                if not run_excel_fallback_rag_test(dm_ctx, excel_thread_ts):
                    all_ok = False
        else:
            print("ℹ️ --excel-path not provided; skipping FU-04/FU-05/FU-06 Excel tests (DM).")

        # DM KB tests
        if not run_kb_org_query_test(dm_ctx):
            all_ok = False

        if not run_kb_product_query_test(dm_ctx):
            all_ok = False

        if not run_greeting_test(dm_ctx):
            all_ok = False
        if not run_memory_test(dm_ctx):
            all_ok = False
        # DM help test
        if not run_help_test(dm_ctx):
            all_ok = False


        # Channel @mention tests (if configured)
        if mention_ctx:
            if not run_thread_analysis_test(mention_ctx, args.thread_url):
                all_ok = False
            if not run_channel_analysis_test(mention_ctx, args.channel_name):
                all_ok = False

            if args.pdf_path:
                if not run_pdf_upload_and_qa_test(mention_ctx, args.pdf_path):
                    all_ok = False
            else:
                print("ℹ️ --pdf-path not provided; skipping FU-03 PDF test (channel @mention).")

            if args.excel_path:
                ok_excel2, excel_thread_ts2 = run_excel_upload_test(mention_ctx, args.excel_path)
                if not ok_excel2 or not excel_thread_ts2:
                    all_ok = False
                else:
                    if not run_excel_qa_direct_table_test(mention_ctx, excel_thread_ts2):
                        all_ok = False
                    if not run_excel_fallback_rag_test(mention_ctx, excel_thread_ts2):
                        all_ok = False
            else:
                print("ℹ️ --excel-path not provided; skipping FU-04/FU-05/FU-06 Excel tests (channel @mention).")

            if not run_thread_analysis_test(mention_ctx, args.thread_url):
                all_ok = False

            if not run_channel_analysis_test(mention_ctx, args.channel_name):
                all_ok = False

            # 🔹 New KB tests (channel @mention)
            if not run_kb_org_query_test(mention_ctx):
                all_ok = False

            if not run_kb_product_query_test(mention_ctx):
                all_ok = False

            if not run_greeting_test(mention_ctx):
                all_ok = False
            if not run_memory_test(mention_ctx):
                all_ok = False
            if not run_help_test(mention_ctx):
                all_ok = False

    except SlackApiError as e:
        print(f"Slack API error: {e.response.get('error')}\nDetails: {e.response.data}", file=sys.stderr)
        sys.exit(1)
    except Exception as e:
        print(f"Unexpected error: {e}", file=sys.stderr)
        sys.exit(1)

    if all_ok:
        print("\n🎉 All tests passed!")
        sys.exit(0)
    else:
        print("\n❗ Some tests failed.")
        sys.exit(3)


if __name__ == "__main__":
    main()
