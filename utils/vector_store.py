# utils/vector_store.py

import os
import pickle
from typing import List
import time
import faiss
import numpy as np
from langchain.schema import Document
from langchain_ollama.embeddings import OllamaEmbeddings

class FaissVectorStore:
    def __init__(
        self,
        *,
        index_path: str = None,
        docstore_path: str = None,
        embedding_model=None
    ):
        """
        - index_path: where this thread's FAISS index will be saved (default: ./data/faiss.index)
        - docstore_path: where this thread’s pickled docs will be saved (default: ./data/docstore.pkl)
        """
        # Default to a local ./data folder if not provided via env
        default_index = os.getenv("VECTOR_INDEX_PATH", "data/faiss.index")
        default_doc   = os.getenv("DOCSTORE_PATH",  "data/docstore.pkl")

        self.index_path    = index_path    or default_index
        self.docstore_path = docstore_path or default_doc

        OLLAMA_BASE_URL = os.getenv("OLLAMA_BASE_URL", "http://ollama:11434")
        OLLAMA_MODEL_NAME = os.getenv("OLLAMA_MODEL_NAME", "granite3.3:8b")
        self.embeddings = embedding_model or OllamaEmbeddings(
            model=OLLAMA_MODEL_NAME, 
            base_url=OLLAMA_BASE_URL,
            temperature=0.0,          # low temp → more deterministic
            top_p=0.9,                # nucleus sampling
            top_k=40,                 # restrict to the 40 highest-prob tokens
            repeat_penalty=1.1,   # discourage repeats
            num_ctx=32768,
        )

        self.index = None
        self.docstore: List[Document] = []

        # If both files already exist, try to load them
        if os.path.exists(self.index_path) and os.path.exists(self.docstore_path):
            try:
                self._load_index()
            except Exception:
                # Corrupt or unreadable → start fresh
                self.index = None
                self.docstore = []

    def _load_index(self):
        # Load FAISS index
        self.index = faiss.read_index(self.index_path)
        # Load Python list of Document objects
        with open(self.docstore_path, "rb") as f:
            self.docstore = pickle.load(f)

    def _save_index(self):
        # Ensure parent directory exists
        idx_dir = os.path.dirname(self.index_path)
        if idx_dir and not os.path.exists(idx_dir):
            os.makedirs(idx_dir, exist_ok=True)

        faiss.write_index(self.index, self.index_path)

        ds_dir = os.path.dirname(self.docstore_path)
        if ds_dir and not os.path.exists(ds_dir):
            os.makedirs(ds_dir, exist_ok=True)

        with open(self.docstore_path, "wb") as f:
            pickle.dump(self.docstore, f)

    def add_documents(self, docs: List[Document]):
        texts = [doc.page_content for doc in docs]
        embeddings = []

        # Rather than embed_documents(texts) in one shot, do it chunk by chunk
        for i, chunk in enumerate(texts):
            try:
                emb = self.embeddings.embed_query(chunk)  # or embed_documents([chunk])
                embeddings.append(emb)
            except Exception as e:
                print(f"⚠️ Embedding chunk {i} failed: {e}")
                embeddings.append([0.0]*768)  # dummy vector to keep dimensions consistent

            if i % 20 == 0:
                print(f"↳ Embedded {i}/{len(texts)} chunks so far…")
                time.sleep(0.1)  # give your CPU a tiny breather

        # Now continue as before:
        if self.index is None:
            dim = len(embeddings[0])
            self.index = faiss.IndexFlatL2(dim)

        vectors = np.array(embeddings).astype("float32")
        self.index.add(vectors)
        self.docstore.extend(docs)
        self._save_index()

    def query(self, query_text: str, k: int = 5) -> List[Document]:
        if self.index is None or not self.docstore:
            return []

        q_emb: List[float] = self.embeddings.embed_query(query_text)
        q_vec = np.array(q_emb).reshape(1, -1).astype("float32")
        D, I = self.index.search(q_vec, k)

        results: List[Document] = []
        for idx in I[0]:
            if idx < len(self.docstore):
                results.append(self.docstore[idx])
        return results
