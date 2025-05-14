import sys, os
# Ensure project root on PYTHONPATH
sys.path.insert(0, os.path.abspath(os.path.dirname(os.path.dirname(__file__))))

import pytest
import chains.chat_chain_mcp as cm
from langchain.memory import ConversationBufferMemory

@pytest.fixture(autouse=True)
def clear_memories():
    cm._memories.clear()
    yield
    cm._memories.clear()


def test_get_memory_creates_and_reuses():
    m1 = cm._get_memory("thread1")
    assert isinstance(m1, ConversationBufferMemory)
    m2 = cm._get_memory("thread1")
    assert m1 is m2
    m3 = cm._get_memory("thread2")
    assert m3 is not m1


def test_process_message_mcp_success(monkeypatch):
    # Stub LLMChain to return a padded reply
    class DummyChain:
        def __init__(self, llm, prompt, memory):
            pass
        def run(self, chat_history, human_input):
            return " reply "

    monkeypatch.setattr(cm, "LLMChain", DummyChain)
    reply = cm.process_message_mcp("hi there", thread_ts="ts1")
    assert reply == "reply"


def test_process_message_mcp_exception(monkeypatch):
    # Stub LLMChain.run to raise
    class DummyChain2:
        def __init__(self, llm, prompt, memory):
            pass
        def run(self, chat_history, human_input):
            raise ValueError("oops")

    monkeypatch.setattr(cm, "LLMChain", DummyChain2)
    reply = cm.process_message_mcp("hi there", thread_ts="tsX")
    assert reply.startswith("❌ Sorry, I encountered an error")
