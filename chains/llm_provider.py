# chains/llm_provider.py
import os
from langchain_community.llms import Ollama

def get_llm():
    base = os.getenv("OLLAMA_BASE_URL", "http://ollama:11434")  # one default
    model = os.getenv("OLLAMA_MODEL_NAME", "granite3.3:8b")
    # keep params consistent across all chains
    return Ollama(
        model=model,
        base_url=base,
        temperature=0.0,
        top_p=0.9,
        top_k=40,
        repeat_penalty=1.1,
        num_predict=int(os.getenv("OLLAMA_NUM_PREDICT", "4096")),
        num_ctx=int(os.getenv("OLLAMA_NUM_CTX", "131072")),
    )
