import os
import tempfile
import requests
import re

from slack_sdk import WebClient
from typing import List

# For text extraction
from PyPDF2 import PdfReader
import docx
import openpyxl  # Added for .xlsx support
import xlrd      # Added for .xls support
from langchain.schema import Document

def sanitize_filename(fn: str) -> str:
    """
    Replace any character that is not alphanumeric, dot, hyphen, or underscore 
    with an underscore. This avoids slugify/Unicode issues.
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
    Basic text extraction: PDF, DOCX, Excel (.xlsx/.xls), or plain text.
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
    elif ext == "xlsx":
        wb = openpyxl.load_workbook(path, read_only=True)
        text = []
        for sheet in wb:
            for row in sheet.iter_rows():
                row_text = [cell.value for cell in row if cell.value is not None]
                if row_text:
                    text.append(" ".join(map(str, row_text)))
        return "\n".join(text)
    elif ext == "xls":
        wb = xlrd.open_workbook(path)
        text = []
        for sheet in wb.sheets():
            for row_idx in range(sheet.nrows):
                row_text = [str(cell.value) for cell in sheet.row(row_idx) if cell.value]
                if row_text:
                    text.append(" ".join(row_text))
        return "\n".join(text)
    else:
        # Try reading as plain text
        try:
            with open(path, "r", encoding="utf-8", errors="ignore") as f:
                return f.read()
        except Exception:
            return ""


def extract_excel_rows(path: str) -> List[Document]:
    """
    Read each sheet in the workbook and return a Document per row,
    with metadata['row_index'] and ['sheet_name'] for exact lookups.
    """
    wb = openpyxl.load_workbook(path, read_only=True)
    docs: List[Document] = []
    for sheet in wb.worksheets:
        # Read header values from the first row
        header_values = []
        header_row = next(sheet.iter_rows(min_row=1, max_row=1, values_only=True), None)
        if header_row:
            header_values = [str(h) for h in header_row]

        # Iterate over data rows starting from the second row
        for i, row in enumerate(sheet.iter_rows(min_row=2, values_only=True), start=0):
            # Skip completely empty rows
            if not any(cell is not None for cell in row):
                continue

            # Pair with headers if they match length, else just stringify values
            if header_values and len(header_values) == len(row):
                pieces = [f"{header_values[j]}: {row[j]}" for j in range(len(row))]
            else:
                pieces = [str(c) for c in row if c is not None]

            text = " | ".join(pieces)
            docs.append(Document(
                page_content=text,
                metadata={
                    "file_name": os.path.basename(path),
                    "sheet_name": sheet.title,
                    "row_index": i
                }
            ))
    return docs