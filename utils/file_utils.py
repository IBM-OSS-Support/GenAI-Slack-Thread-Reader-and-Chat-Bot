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
import openpyxl  # Added for .xlsx support
import xlrd      # Added for .xls support
import pandas as pd
import difflib
from langchain.schema import Document

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
        except Exception as e:
            # If it’s not text, return empty or raise
            return ""

def dataframe_to_documents(df, file_name):
    docs = []
    for i, row in df.iterrows():
        content = "; ".join(f"{col}: {row[col]}" for col in df.columns)
        docs.append(Document(
            page_content=content,
            metadata={"row_index": i, "file_name": file_name}
        ))
    return docs

def extract_excel_as_table(path: str):
    ext = path.lower().split(".")[-1]
    if ext not in ("xlsx", "xls"):
        raise ValueError("Not an Excel file")
    # Read all rows as raw data
    df_raw = pd.read_excel(path, header=None, engine="openpyxl" if ext == "xlsx" else "xlrd")
    # Find the first row with at least 2 non-empty cells (likely the header)
    for i, row in df_raw.iterrows():
        non_empty = sum([bool(str(cell).strip()) for cell in row])
        if non_empty >= 2:
            header_row = i
            break
    # Read again, using that row as header
    df = pd.read_excel(path, header=header_row, engine="openpyxl" if ext == "xlsx" else "xlrd")
    df = df.loc[:, ~df.columns.str.contains('^Unnamed')]  # Remove unnamed columns
    df = df.dropna(how='all')  # Drop fully empty rows
    return df

def _clean_text(s: str) -> str:
    import re as _re
    x = str(s).lower()
    # normalize smart quotes
    x = x.replace("’", "'").replace("‘", "'").replace("“", '"').replace("”", '"')
    # treat -, _, / as word separators
    x = _re.sub(r"[-_/]+", " ", x)
    # collapse whitespace
    x = _re.sub(r"\s+", " ", x).strip()
    return x

def _clean_entity(entity: str) -> str:
    e = _clean_text(entity)
    e = re.sub(r"^(the\s+)?(product|person|employee|user)\s+", "", e)
    return e.strip(" '\"")

def _two_way_contains(series: pd.Series, needle: str) -> pd.Series:
    s = series.astype(str).str.lower().fillna("")
    # cell contains needle  OR  needle contains cell (for cases where user typed more words)
    return s.str.contains(re.escape(needle), regex=True) | s.apply(lambda x: x and x in needle)
def _role_tokens(text: str) -> set:
    """Tokenize role text with aliases like 2nd/L2/level 2 → second line."""
    import re as _re
    norm = _clean_text(text)
    toks = set(norm.split())

    # Expand common aliases for "second line"
    if _re.search(r"\b(2nd|l2|level 2|second level)\b", norm):
        toks.update({"second", "line"})

    # Expand "second-line" already handled by _clean_text (hyphen→space)
    return toks

def resolve_role_column(columns, col_query: str) -> str:
    """Choose the best role column for a query like 'second-line owner'."""
    q_tokens = _role_tokens(col_query)
    best_col, best_score = None, -1.0

    for c in columns:
        c_norm   = _clean_text(c)
        c_tokens = _role_tokens(c_norm)

        # Base overlap score (how much of the query is covered)
        common   = len(q_tokens & c_tokens)
        coverage = common / max(1, len(q_tokens))

        score = 70.0 * coverage

        # Strong signals
        if "owner" in q_tokens and "owner" in c_tokens:
            score += 20.0
        if {"second", "line"}.issubset(q_tokens) and {"second", "line"}.issubset(c_tokens):
            score += 15.0

        # Penalize mismatched planner/lead if not asked
        if "planner" in c_tokens and "planner" not in q_tokens:
            score -= 20.0
        if "lead" in c_tokens and "lead" not in q_tokens and "owner" in q_tokens:
            score -= 10.0

        # Exact-ish match bonus
        if c_norm == _clean_text(col_query):
            score += 50.0

        if score > best_score:
            best_score, best_col = score, c

    return best_col
def answer_from_excel_super_dynamic(df, question):
    q = question.lower().strip()

    # 1. Show last N rows
    m = re.search(r"last\s+(\d+)\s+(rows|data|entries|records|activities|tasks|items)", q)
    if m:
        n = int(m.group(1))
        last_rows = df.tail(n)
        if len(last_rows) == 1:
            row = last_rows.iloc[0]
            return "\n".join(f"{col}: {row[col]}" for col in df.columns)
        return last_rows.to_markdown(index=False)

    # 2. "What is the X (and Y) of Z?" or "Which X does Y belong to?" or "Who is the X of Y?"
    m = re.search(
        r"(?:^|\s)(?:what|which|who)\s+(?:is\s+)?(?:the\s+)?(.+?)\s+(?:of|for|does)\s+(?:the\s+)?(?:product\s+)?[\"']?(.+?)[\"']?(?:\s+belong to)?[\?\.]?$",
        q
    )
    if m:
        col_query = _clean_text(m.group(1))
        entity = _clean_entity(m.group(2))

        def fuzzy_col_match(columns, col_query):
            for c in columns:
                if col_query in c.lower():
                    return c
            for c in columns:
                if all(word in c.lower() for word in col_query.split()):
                    return c
            return max(columns, key=lambda c: difflib.SequenceMatcher(None, c.lower(), col_query).ratio())

        role_col = resolve_role_column(df.columns, col_query)

        # Prefer product-like columns
        product_cols = [c for c in df.columns if "product" in c.lower() or ("name" in c.lower() and "product" in c.lower())]
        candidates = pd.DataFrame()
        search_cols = product_cols or list(df.columns)

        for pc in search_cols:
            mask = _two_way_contains(df[pc], entity)
            if mask.any():
                part = df[mask].copy()
                part["_match_col"] = pc
                candidates = pd.concat([candidates, part], axis=0)

        if not candidates.empty:
            row = candidates.iloc[0]
            match_col = row["_match_col"] if "_match_col" in row else search_cols[0]
            matched_label = row[match_col] if match_col in row.index else "the item"

            if role_col in df.columns:
                return f"The {role_col} of {matched_label} is: {row[role_col]}"

            likely = [c for c in df.columns if any(k in c.lower() for k in ["owner","manager","lead","support","contact","vp","director","planner"])]
            if likely:
                return "\n".join(f"{c}: {row[c]}" for c in likely[:4] if pd.notna(row[c]))
            return "I found the product row, but couldn’t find a matching role column."

        # If still nothing, try entity-like columns with two-way test
        entity_cols = [c for c in df.columns if any(word in c.lower() for word in ["product","name","owner","person","employee","user","activity","item","project"])]
        for entity_col in entity_cols:
            mask = _two_way_contains(df[entity_col], entity)
            matches = df[mask]
            if not matches.empty:
                row = matches.iloc[0]
                if role_col in df.columns:
                    return f"The {role_col} of {entity} is: {row[role_col]}"
                else:
                    likely = [c for c in df.columns if any(k in c.lower() for k in ["owner","manager","lead","support","contact"])]
                    if likely:
                        return "\n".join(f"{c}: {row[c]}" for c in likely[:4] if pd.notna(row[c]))
                    return "I found a matching row, but couldn’t find a matching role column."


    # 3. "Who is X?" or "details of X"
    m = re.search(r"(?:who\s+is|details\s+of)\s+(.+?)[\?\.]?$", q)
    if m:
        entity = m.group(1).strip()
        
        # Search for the entity across all columns
        all_matches = pd.DataFrame()
        for col in df.columns:
            mask = df[col].astype(str).str.lower().str.contains(entity.lower())
            if mask.any():
                all_matches = pd.concat([all_matches, df[mask]])
        
        # Remove duplicates
        all_matches = all_matches.drop_duplicates()
        
        if not all_matches.empty:
            # Analyze what role this person has
            role_info = {}
            
            # Check each column to understand the person's role
            for col in df.columns:
                if any(word in col.lower() for word in ["owner", "manager", "lead", "vp", "director", "planner"]):
                    # Count how many times this person appears in this role column
                    count = (all_matches[col].astype(str).str.lower() == entity.lower()).sum()
                    if count > 0:
                        role_info[col] = count
            
            # Find the most relevant column where this person appears
            if role_info:
                # Person has specific roles
                primary_role = max(role_info.items(), key=lambda x: x[1])[0]
                total_count = role_info[primary_role]
                
                # Get all products/items associated with this person in their primary role
                mask = df[primary_role].astype(str).str.lower() == entity.lower()
                person_rows = df[mask]
                
                # Find the product/item column
                item_col = None
                for col in df.columns:
                    if any(word in col.lower() for word in ["product", "activity", "task", "item", "project"]) and "name" in col.lower():
                        item_col = col
                        break
                if not item_col:  # Fallback to first column that looks like a name
                    for col in df.columns:
                        if "name" in col.lower():
                            item_col = col
                            break
                
                if item_col and item_col in person_rows.columns:
                    items = person_rows[item_col].dropna().tolist()
                    items = [str(item) for item in items if str(item) != 'nan']
                    
                    # Format the response
                    entity_title = entity.title()
                    response = f"{entity_title} is the {primary_role} of {len(items)} Products."
                    
                    if items:
                        response += f" They are: {', '.join(items[:len(items)-1])}"
                        if len(items) > 1:
                            response += f", and {items[-1]}"
                        else:
                            response += f"{items[0]}"
                        response += "."
                    
                    # Show details of latest 3 entries
                    if len(person_rows) > 3:
                        latest_3 = person_rows.tail(3)
                        response += "\n\nFew of the products are given below:"
                    else:
                        latest_3 = person_rows
                        response += f"\n\nDetails of all {len(person_rows)} entries:"
                    
                    for idx, row in latest_3.iterrows():
                        response += f"\n\n{row[item_col] if item_col else f'Entry {idx}'}:"
                        # Show key columns only
                        key_cols = [col for col in df.columns if col != primary_role and pd.notna(row[col]) and str(row[col]) != 'nan'][:4]
                        for col in key_cols:
                            response += f"\n  - {col}: {row[col]}"
                    
                    if len(person_rows) > 3:
                        response += f"\n\nI'm unable to provide full details for all {len(person_rows)} entries in this format. Please refine your query for specific information."
                    
                    return response
            
            # Fallback: person found but not in a clear role column
            # Just show where they appear
            response = f"Found {entity.title()} in {len(all_matches)} entries."
            if len(all_matches) <= 3:
                response += "\n\nDetails:"
                for idx, row in all_matches.iterrows():
                    response += "\n"
                    relevant_cols = [col for col in df.columns if pd.notna(row[col]) and str(row[col]) != 'nan' and entity.lower() in str(row[col]).lower()][:3]
                    for col in relevant_cols:
                        response += f"\n  - {col}: {row[col]}"
            else:
                response += " Please be more specific about what information you need."
            
            return response

    # 4. "Show nth row" - only return specific row requests
    m = re.search(r"show\s+(\d+)(?:st|nd|rd|th)?\s+row", q)
    if m:
        row_num = int(m.group(1)) - 1  # Convert to 0-based index
        if 0 <= row_num < len(df):
            row = df.iloc[row_num]
            return "\n".join(f"{col}: {row[col]}" for col in df.columns if pd.notna(row[col]) and str(row[col]) != 'nan')
        else:
            return f"Row {row_num + 1} does not exist. The data has {len(df)} rows."

    # Check if user is asking for "show all" and decline
    if any(phrase in q for phrase in ["show all", "list all", "display all", "all rows", "all entries", "show everything", "full data", "complete data"]):
        return "I'm not able to retrieve all data. Please refine your query for specific information.[example: 'show me the last 3 rows', 'show me the first row'. etc.]"

    # 5. Fallback: best-match single value
    best_row_idx = None
    best_row_score = 0
    for idx, row in df.iterrows():
        row_text = " ".join(str(cell).lower() for cell in row)
        score = sum(word in row_text for word in q.split() if len(word) > 2)
        if score > best_row_score:
            best_row_score = score
            best_row_idx = idx

    best_col = None
    best_col_score = 0
    for col in df.columns:
        col_score = sum(word in col.lower() for word in q.split() if len(word) > 2)
        if col_score > best_col_score:
            best_col_score = col_score
            best_col = col

    if best_row_idx is not None and best_col is not None:
        value = df.loc[best_row_idx, best_col]
        # Find a good label for the row
        row_label = None
        for col in df.columns:
            if any(word in col.lower() for word in ["product", "name", "activity", "item"]):
                row_label = df.loc[best_row_idx, col]
                break
        if row_label and pd.notna(row_label):
            return f"{best_col} for {row_label}: {value}"
        else:
            return f"{best_col}: {value}"
    
    return "I couldn't find a relevant answer in the file."

