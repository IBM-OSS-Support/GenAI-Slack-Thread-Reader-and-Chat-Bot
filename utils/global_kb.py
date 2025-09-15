# utils/global_kb.py
import os
import logging
from typing import List, Tuple, Optional
import pandas as pd

from langchain.schema import Document
from langchain.text_splitter import RecursiveCharacterTextSplitter

from utils.vector_store import FaissVectorStore
from utils.file_utils import (
    extract_text_from_file,
    extract_excel_as_table,
    dataframe_to_documents,
    answer_from_excel_super_dynamic,
)
from chains.chat_chain_mcp import process_message_mcp

# Global, process-wide KB (indexed once at startup)
GLOBAL_VECTOR_STORE: Optional[FaissVectorStore] = None
EXCEL_TABLES_GLOBAL: List[Tuple[str, "pd.DataFrame"]] = []

def _parse_startup_files() -> List[str]:
    raw = os.getenv("STARTUP_FILES", "")
    return [p.strip() for p in raw.split(",") if p.strip()]

def index_startup_files(
    files: Optional[List[str]] = None,
    index_path: str = "data/faiss_global.index",
    docstore_path: str = "data/docstore_global.pkl",
) -> None:
    """
    Indexes the given files (or STARTUP_FILES env) into a single global FAISS index
    and stores Excel DataFrames for deterministic Q&A.
    """
    global GLOBAL_VECTOR_STORE, EXCEL_TABLES_GLOBAL

    paths = files if files is not None else _parse_startup_files()
    if not paths:
        logging.warning("[KB] No STARTUP_FILES provided; global KB will be empty.")
        return

    os.makedirs(os.path.dirname(index_path) or ".", exist_ok=True)

    # Initialize (or reuse) the global FAISS store
    if GLOBAL_VECTOR_STORE is None:
        GLOBAL_VECTOR_STORE = FaissVectorStore(index_path=index_path, docstore_path=docstore_path)

    splitter = RecursiveCharacterTextSplitter(chunk_size=5000, chunk_overlap=500)

    for path in paths:
        try:
            file_name = os.path.basename(path)
            ext = (path.rsplit(".", 1)[-1] if "." in path else "").lower()

            # 1) Excel → keep table + embed each row
            if ext in ("xlsx", "xls"):
                try:
                    df = extract_excel_as_table(path)
                    EXCEL_TABLES_GLOBAL.append((file_name, df))
                    row_docs = dataframe_to_documents(df, file_name)
                    if row_docs:
                        GLOBAL_VECTOR_STORE.add_documents(row_docs)
                    logging.info(f"[KB] Indexed Excel rows from {file_name} (rows={len(df)})")
                except Exception as e:
                    logging.exception(f"[KB] Failed to parse Excel {file_name}: {e}")

            # 2) All files → extract text → chunk → embed
            raw_text = extract_text_from_file(path) or ""
            if raw_text.strip():
                chunks = splitter.split_text(raw_text)
                docs = [
                    Document(
                        page_content=chunk,
                        metadata={"file_name": file_name, "chunk_index": i},
                    )
                    for i, chunk in enumerate(chunks)
                ]
                if docs:
                    GLOBAL_VECTOR_STORE.add_documents(docs)
                logging.info(f"[KB] Indexed text chunks from {file_name} (chunks={len(docs)})")
            else:
                logging.warning(f"[KB] No text extracted from {file_name}")

        except Exception as e:
            logging.exception(f"[KB] Failed indexing {path}: {e}")

    logging.info(f"[KB] Startup indexing complete. Excel tables: {len(EXCEL_TABLES_GLOBAL)}")

def query_global_kb(question: str, thread_id: str) -> str:
    """
    Reuse existing logic:
    1) Try deterministic Excel Q&A (answer_from_excel_super_dynamic) across all startup tables.
    2) Fall back to RAG over the global FAISS store with a grounded prompt.
    """
    # 1) Deterministic Excel Q&A first
    for fname, df in EXCEL_TABLES_GLOBAL:
        try:
            ans = answer_from_excel_super_dynamic(df, question)
            if ans and not ans.strip().lower().startswith("i couldn't find"):
                return ans
        except Exception:
            # keep trying other tables
            pass

    # 2) RAG fallback
    if GLOBAL_VECTOR_STORE is None or GLOBAL_VECTOR_STORE.index is None:
        return "Global knowledge base is not loaded. Ask the admin to set STARTUP_FILES."

    try:
        retrieved = GLOBAL_VECTOR_STORE.query(question, k=30)
    except Exception:
        retrieved = []

    if retrieved:
        context = "\n".join(doc.page_content for doc in retrieved)
        prompt = (
            "You are a helpful data assistant. Use ONLY the context below.\n"
            f"{context}\n\n"
            f"User question: {question}\n"
            "If the answer is not present, say: I can't find any match in the KB."
        )
        return process_message_mcp(prompt, thread_id)

    return "I can't find any match in the KB."
