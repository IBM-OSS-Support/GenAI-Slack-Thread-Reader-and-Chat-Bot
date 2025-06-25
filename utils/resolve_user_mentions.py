import re
from utils.slack_tools import get_user_name
from slack_sdk import WebClient
from slack_sdk.errors import SlackApiError
import logging
def get_channel_name(client: WebClient, channel_id: str) -> str:
    try:
        info = client.conversations_info(channel=channel_id)
        if info.get("ok"):
            return f"#{info['channel']['name']}"
    except SlackApiError:
        logging.exception(f"Failed channel.info for {channel_id}")
    return f"#{channel_id}"



def resolve_user_mentions(client: WebClient, text: str) -> str:
    text = re.sub(r"@<(@?[UW][A-Z0-9]{8,})>", r"<\1>", text)
    text = re.sub(
        r"<@([UW][A-Z0-9]{8,})>",
        lambda m: f"@{get_user_name(client, m.group(1))}",
        text,
    )
    text = re.sub(
        r"\b([UW][A-Z0-9]{8,})\b",
        lambda m: f"@{get_user_name(client, m.group(1))}"
                  if m.group(1).startswith(("U","W")) else m.group(1),
        text,
    )
    text = re.sub(
        r"<#(C[A-Z0-9]{8,})(?:\|[^>]+)?>",
        lambda m: get_channel_name(client, m.group(1)),
        text,
    )
    return text
