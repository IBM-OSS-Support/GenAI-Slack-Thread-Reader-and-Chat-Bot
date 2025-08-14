import logging
import re
import os
from langchain_community.llms import Ollama
from langchain.prompts import PromptTemplate
from langchain.chains import LLMChain
from langchain.memory import ConversationBufferMemory

from chains.llm_provider import get_llm

logger = logging.getLogger(__name__)
_TOKEN_RE = re.compile(r"<\|im_start\|>|\<\|im_sep\|>")

OLLAMA_BASE_URL = os.getenv("OLLAMA_BASE_URL", "http://localhost:11434")
OLLAMA_MODEL_NAME = os.getenv("OLLAMA_MODEL_NAME", "granite3.3:8b")

# Initialize local Granite model
llm = get_llm()

prompt = PromptTemplate(
    input_variables=["chat_history", "human_input"],
    template="""
You are a helpful AI assistant that uses conversation history to inform your replies.

Conversation History:
{chat_history}

User Input: {human_input}

Respond in plain text only, without any JSON or code formatting.
"""
)

# In-process per-thread memory store
_memories: dict[str, ConversationBufferMemory] = {}

def _get_memory(thread_ts: str) -> ConversationBufferMemory:
    if thread_ts not in _memories:
        _memories[thread_ts] = ConversationBufferMemory(memory_key="chat_history")
    return _memories[thread_ts]

def process_message_mcp(human_input: str, thread_ts: str = "global") -> str:
    # pull in memory
    memory = _get_memory(thread_ts)
    vars = memory.load_memory_variables({})
    chat_history = vars.get("chat_history", "")

    # sanitize both history and incoming prompt
    human_input   = _TOKEN_RE.sub("", human_input)
    chat_history  = _TOKEN_RE.sub("", chat_history)

    try:
        chain = LLMChain(llm=llm, prompt=prompt, memory=memory)
        reply = chain.run(human_input=human_input)
        return reply.strip()
    except Exception:
        logger.exception("Error processing message")
        return "❌ Sorry, I encountered an error and couldn't process your message."
