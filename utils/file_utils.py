# utils/file_utils.py

import os
import tempfile
import requests
import re

from slack_sdk import WebClient
from typing import List

# For text extraction
from PyPDF2 import PdfReader
import docx

def sanitize_filename(fn: str) -> str:
    """
    Replace any character that is not alphanumeric, dot, hyphen, or underscore 
    with an underscore. This avoids the slugify/Unicode issues.
    """
    return re.sub(r'[^A-Za-z0-9_.-]', '_', fn)

def download_slack_file(client: WebClient, file_info: dict) -> str:
    """
    Given a Slack file_info dict (from the file_shared event),
    download the file to a temporary location and return local path.
    """
    url = file_info.get("url_private_download")
    if not url:
        raise RuntimeError("No url_private_download on file_info")

    # Slack requires auth token to download private files
    headers = {"Authorization": f"Bearer {client.token}"}
    response = requests.get(url, headers=headers)
    if not response.ok:
        raise RuntimeError(f"Failed to download file: HTTP {response.status_code}")

    # Derive a safe filename without using slugify
    original_name = file_info.get("name") or "uploaded_file"
    safe_base = sanitize_filename(original_name)
    suffix = os.path.splitext(original_name)[1] or ""
    tmp_path = os.path.join(tempfile.gettempdir(), safe_base + suffix)

    with open(tmp_path, "wb") as f:
        f.write(response.content)

    return tmp_path

def extract_text_from_file(path: str) -> str:
    """
    Basic text extraction: PDF or DOCX or plain text.
    """
    ext = path.lower().split(".")[-1]
    if ext == "pdf":
        reader = PdfReader(path)
        text = []
        for page in reader.pages:
            text.append(page.extract_text() or "")
        return "\n".join(text)
    elif ext in ("docx", "doc"):
        doc = docx.Document(path)
        paragraphs = [p.text for p in doc.paragraphs]
        return "\n".join(paragraphs)
    else:
        # Try reading as plain text
        try:
            with open(path, "r", encoding="utf-8", errors="ignore") as f:
                return f.read()
        except Exception as e:
            # If it’s not text, return empty or raise
            return ""
