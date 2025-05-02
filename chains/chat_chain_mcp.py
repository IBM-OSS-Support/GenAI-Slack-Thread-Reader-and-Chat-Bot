import logging
import os
from langchain_community.llms import Ollama
from langchain.prompts import PromptTemplate
from langchain.chains import LLMChain
from langchain.memory import ConversationBufferMemory

logger = logging.getLogger(__name__)
OLLAMA_BASE_URL = os.getenv("OLLAMA_BASE_URL", "http://ollama:11434")
# Initialize local Granite model
llm = Ollama(
    model="granite3.3:8b",
    base_url=OLLAMA_BASE_URL
)

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
    memory = _get_memory(thread_ts)
    chat_history = memory.load_memory_variables({}).get("chat_history", "")
    try:
        chain = LLMChain(llm=llm, prompt=prompt, memory=memory)
        reply = chain.run(chat_history=chat_history, human_input=human_input)
        return reply.strip()
    except Exception:
        logger.exception("Error processing message")
        return "❌ Sorry, I encountered an error and couldn't process your message."
