# chains/analyze_thread.py
from __future__ import annotations

import logging
import os
import random
import time
from datetime import datetime
from typing import Optional

from tenacity import retry, wait_random_exponential, stop_after_attempt

from slack_sdk import WebClient
from slack_sdk.errors import SlackApiError

# LangChain
from langchain_core.prompts import ChatPromptTemplate, PromptTemplate
from langchain_core.output_parsers import StrOutputParser
from langchain_core.runnables import Runnable

# LLM provider (now supports chat/text and exposes is_chat_model)
from chains.llm_provider import get_llm
from chains.llm_provider import is_chat_model

from utils.resolve_user_mentions import resolve_user_mentions
from utils.slack_tools import fetch_slack_thread, get_user_name

logger = logging.getLogger(__name__)

# -----------------------------------------------------------------------------
# Prompts (content unchanged)
# -----------------------------------------------------------------------------

SUMMARY_SYSTEM = (
    "You are a Slack assistant that must produce output with exactly five sections "
    "in Slack markdown. Never add commentary, headers, or code fences outside the sections. "
    "Never invent timestamps or facts. Keep body text unbolded. If a section has no facts, "
    "omit its bullets rather than inventing content."
)

SUMMARY_HUMAN = """
You are a Slack assistant summarizing internal support or escalation threads. Below is the full message thread with speakers and timestamps:

{messages}

Your output must contain *exactly these five sections*, using *Slack markdown formatting* (asterisks for bold section titles, no bold in body). Do not add anything outside these sections. Do not add explanations. 

*Summary*  
- Write *one clear sentence* summarizing the entire thread. Be specific about what triggered the thread (e.g., escalation, request, incident).

*Business Impact*  
- Only include bullets for impacts *explicitly mentioned in the thread*.  
- Use the following bullet format:
  - *Revenue at risk*: Describe risk to Watson or IBM revenue.
  - *Operational impact*: Describe what is blocked or degraded.
  - *Customer impact*: Describe how the customer is affected, including any leadership mention (e.g., CIO-level).
  - *Team impact*: Mention any IBM team concerns, delays, or credibility issues.
  - *Other impacts*: List any escalation triggers (e.g., Duty Manager contacted, credibility risk, etc.)

*Key Points Discussed*  
- List 3–6 bullets summarizing specific events, facts, or updates.  
- Focus on what was done, requested, stated, or observed.  
- Use speaker names *only* if it clarifies the point.  
- Do not add any information not present in the thread.

*Decisions Made*  
- List *all concrete decisions*, even logistical ones (like scheduling a call or taking ownership).  
- Use this format:
  - *@username* decided to ___ [DD/MM/YYYY HH:MM UTC]

*Action Items*  
- List only *clearly stated follow-up actions* assigned to specific people.  
- Use this format:
  - *@username* to ___ [DD/MM/YYYY HH:MM UTC]  
- Include due-dates only if explicitly mentioned. Do not guess or infer.

---

Strictly follow the format. Do *not invent* any bullet or timestamp. If something is missing, leave it out—do not assume. Format all of your output using Slack’s markup.
"""

CUSTOM_SYSTEM = """You are a helpful assistant working on Slack threads."""
CUSTOM_HUMAN = """Here is a Slack thread:

{messages}

User instructions:
{instructions}

Follow the instructions exactly and respond in plain text."""

TRANSLATE_SYSTEM = """You translate Slack markdown preserving formatting."""
TRANSLATE_HUMAN = """Translate the following Slack message to {language}, preserving all markdown:

```{text}```"""

# -----------------------------------------------------------------------------
# Build both chat and text prompts (content kept the same)
# -----------------------------------------------------------------------------

# Chat prompts
summary_chat_prompt = ChatPromptTemplate.from_messages(
    [("system", SUMMARY_SYSTEM), ("human", SUMMARY_HUMAN)]
)
custom_chat_prompt = ChatPromptTemplate.from_messages(
    [("system", CUSTOM_SYSTEM), ("human", CUSTOM_HUMAN)]
)
translate_chat_prompt = ChatPromptTemplate.from_messages(
    [("system", TRANSLATE_SYSTEM), ("human", TRANSLATE_HUMAN)]
)

# Text prompts (system baked into single template)
summary_text_prompt = PromptTemplate.from_template(
    "SYSTEM:\n" + SUMMARY_SYSTEM + "\n\nUSER:\n" + SUMMARY_HUMAN
)
custom_text_prompt = PromptTemplate.from_template(
    "SYSTEM:\n" + CUSTOM_SYSTEM + "\n\nUSER:\n" + CUSTOM_HUMAN
)
translate_text_prompt = PromptTemplate.from_template(
    "SYSTEM:\n" + TRANSLATE_SYSTEM + "\n\nUSER:\n" + TRANSLATE_HUMAN
)

# -----------------------------------------------------------------------------
# Model + parser
# -----------------------------------------------------------------------------

llm = get_llm()  # may be ChatOllama (chat) or Ollama (text)
parser = StrOutputParser()

# Choose chat vs text chains at runtime (only change needed for web/LLM side)
if is_chat_model(llm):
    summary_chain: Runnable = summary_chat_prompt | llm | parser
    custom_chain: Runnable = custom_chat_prompt | llm | parser
    translation_chain: Runnable = translate_chat_prompt | llm | parser
else:
    summary_chain: Runnable = summary_text_prompt | llm | parser
    custom_chain: Runnable = custom_text_prompt | llm | parser
    translation_chain: Runnable = translate_text_prompt | llm | parser

# -----------------------------------------------------------------------------
# Main functions (unchanged behavior)
# -----------------------------------------------------------------------------

def _build_thread_blob(client: WebClient, messages: list[dict]) -> str:
    lines = []
    for m in sorted(messages, key=lambda x: float(x.get("ts", 0))):
        ts = float(m.get("ts", 0))
        human_ts = datetime.fromtimestamp(ts).strftime("%Y-%m-%d %H:%M:%S")
        uid = m.get("user") or m.get("bot_id") or "system"
        try:
            speaker = f"@{get_user_name(client, uid)}"
        except Exception:
            speaker = uid
        text = (m.get("text", "") or "").replace("\n", " ")
        lines.append(f"{human_ts} {speaker}: {text}")
    return resolve_user_mentions(client, "\n".join(lines))

from tenacity import retry, wait_random_exponential, stop_after_attempt, retry_if_exception_type

class EmptyLLMOutput(RuntimeError):
    pass

def _trim_messages_blob(s: str, max_chars: int = 6000) -> str:
    """Keep the tail of the thread (often the most relevant)."""
    if not isinstance(s, str):
        return s
    if len(s) <= max_chars:
        return s
    # Try to avoid cutting a line mid-way
    tail = s[-max_chars:]
    nl = tail.find("\n")
    return tail[nl+1:] if nl != -1 else tail

@retry(
    wait=wait_random_exponential(min=0.7, max=2.5),
    stop=stop_after_attempt(3),
    retry=retry_if_exception_type(EmptyLLMOutput),
)
def _invoke_chain(chain: Runnable, /, **inputs) -> str:
    """
    Invoke the chain; if the model returns an empty string, raise to trigger a retry.
    On the 2nd attempt we trim the messages blob (to dodge ctx/decoding edge-cases).
    """
    attempt = getattr(_invoke_chain, "_attempt", 1)

    # 1) First try with original inputs
    out = chain.invoke(inputs)
    text = (out or "").strip()
    if text:
        _invoke_chain._attempt = 1  # reset
        return text

    # 2) Empty → try again with a trimmed blob (only once per call stack)
    msg_key = "messages" if "messages" in inputs else ("text" if "text" in inputs else None)
    if msg_key and attempt == 1 and isinstance(inputs[msg_key], str):
        logger.warning("LLM returned empty output; retrying with trimmed messages blob.")
        trimmed = _trim_messages_blob(inputs[msg_key], max_chars=6000)
        new_inputs = dict(inputs)
        new_inputs[msg_key] = trimmed
        _invoke_chain._attempt = 2
        out2 = chain.invoke(new_inputs)
        text2 = (out2 or "").strip()
        if text2:
            _invoke_chain._attempt = 1
            return text2

    # 3) Still empty → raise to trigger tenacity retry (or bubble up)
    _invoke_chain._attempt = attempt + 1
    raise EmptyLLMOutput("Model returned empty output")


def analyze_slack_thread(
    client: WebClient,
    channel_id: str,
    thread_ts: str,
    instructions: Optional[str] = None,
    default: bool = True,
) -> str:
    """
    Fetch the Slack thread, build a chat-friendly blob, and run the appropriate chain.
    default=True  -> use the STRICT summary formatter
    default=False -> use the custom instructions formatter
    """
    try:
        raw = fetch_slack_thread(client, channel_id, thread_ts)
    except Exception as e:
        raise RuntimeError(f"Error fetching thread: {e}")

    blob = _build_thread_blob(client, raw)

    try:
        if default:
            # STRICT summary (the 5-section format)
            return _invoke_chain(summary_chain, messages=blob)
        else:
            # Custom instructions
            return _invoke_chain(custom_chain, messages=blob, instructions=instructions or "")
    except Exception as e:
        logger.exception("Summarization failed")
        return "❌ Sorry, I couldn't process your request."


def translate_slack_markdown(text: str, language: str) -> str:
    try:
        return _invoke_chain(translation_chain, text=text, language=language)
    except Exception:
        logger.exception("Translation failed")
        return text  # graceful fallback
