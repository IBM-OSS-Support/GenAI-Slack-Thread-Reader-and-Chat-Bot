# utils/thread_store.py

from typing import Dict
from utils.vector_store import FaissVectorStore

# one store per Slack‐thread, shared everywhere
THREAD_VECTOR_STORES: Dict[str, FaissVectorStore] = {}
