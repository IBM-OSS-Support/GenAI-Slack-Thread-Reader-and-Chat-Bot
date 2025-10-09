# chains/llm_provider.py
from typing import Any, Optional, Dict
from langchain_ollama import ChatOllama
import httpx
import os

def is_chat_model(llm: Any) -> bool:
    return llm.__class__.__name__.lower().startswith("chat")

def get_llm(overrides: Optional[Dict[str, Any]] = None):
    base = os.getenv("OLLAMA_BASE_URL", "http://ollama:11434")
    model = os.getenv("OLLAMA_MODEL_NAME", "granite3.3:8b")
    # Turn streaming on only when we want to debug/aggregate chunks
    stream_debug = os.getenv("OLLAMA_STREAM_DEBUG", "0") == "1"

    kwargs = dict(
        base_url=base,
        model=model,
        temperature=0,
        # If stream_debug -> allow streaming; else force single complete response
        disable_streaming=not stream_debug,
        num_ctx=int(os.getenv("OLLAMA_NUM_CTX", "128000")),  # full context if model supports it
        num_predict=-1,  # unlimited output generation
        repeat_penalty=1.05,
        max_retries=3,
        top_k=40,
        top_p=0.9,
        reasoning=True,
        client_kwargs={
            # httpx requires all four values when not providing a default
            "timeout": httpx.Timeout(connect=60.0, read=600.0, write=600.0, pool=60.0)
        },
    )

    if overrides:
        # shallow merge; nested dicts (like client_kwargs) can be replaced fully if supplied
        kwargs.update(overrides)

    return ChatOllama(**kwargs)
