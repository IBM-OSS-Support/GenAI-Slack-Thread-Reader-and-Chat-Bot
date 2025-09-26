# chains/llm_provider.py
from __future__ import annotations
from typing import Any
from langchain_ollama import ChatOllama
import httpx
import os

def is_chat_model(llm: Any) -> bool:
    return llm.__class__.__name__.lower().startswith("chat")

def get_llm():
    base = os.getenv("OLLAMA_BASE_URL", "http://ollama:11434")
    model = os.getenv("OLLAMA_MODEL_NAME", "granite3.3:8b")
    return ChatOllama(
        base_url=base,
        model=model,
        temperature=0.2,
        disable_streaming=True,       # single, complete message
        num_ctx=128000,               # 128K context
        num_predict=-1,               # no early cut-offs
        repeat_penalty=1.05,
        top_p=0.9,

        # ✅ Turn OFF reasoning/thinking
        reasoning=False,              # do not emit thinking content
        # OR equivalently, just omit this argument entirely
        # (default is usually False for non-reasoning models)

        client_kwargs={
            "timeout": httpx.Timeout(connect=60.0, read=600.0, write=600.0, pool=60.0)
        },
        async_client_kwargs={
            "timeout": httpx.Timeout(connect=60.0, read=600.0, write=600.0, pool=60.0)
        },
        keep_alive="30m",
    )
