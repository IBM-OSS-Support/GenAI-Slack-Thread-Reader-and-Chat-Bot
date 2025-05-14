import sys, os
# Ensure project root is on PYTHONPATH
sys.path.insert(0, os.path.abspath(os.path.dirname(os.path.dirname(__file__))))

import pytest
import chains.analyze_thread as at

@pytest.fixture(autouse=True)
def clear_state(monkeypatch):
    # Clear any previous monkeypatch state
    # Reset summarizer and custom_chain to original
    yield


def test_analyze_slack_thread_without_instructions(monkeypatch):
    # Prepare dummy fetch_slack_thread
    dummy_msgs = [
        {"ts": "2.0", "user": "U2", "text": "second"},
        {"ts": "1.0", "user": "U1", "text": "first"}
    ]
    monkeypatch.setattr(at, "fetch_slack_thread", lambda c, t: dummy_msgs)

    # Stub summarizer chain at module level
    class FakeSummarizer:
        def run(self, **kwargs):
            # Expect messages sorted
            assert kwargs.get("messages") == "first\nsecond"
            return "dummy summary"
    monkeypatch.setattr(at, "summarizer", FakeSummarizer())

    result = at.analyze_slack_thread("C1", "123456")
    assert result == "dummy summary"


def test_analyze_slack_thread_with_instructions(monkeypatch):
    dummy_msgs = [{"ts": "1.0", "user": "U1", "text": "hello"}]
    monkeypatch.setattr(at, "fetch_slack_thread", lambda c, t: dummy_msgs)

    # Stub custom_chain
    class FakeCustom:
        def run(self, **kwargs):
            assert kwargs.get("messages") == "hello"
            assert kwargs.get("instructions") == "Do X"
            return "custom response"
    monkeypatch.setattr(at, "custom_chain", FakeCustom())

    result = at.analyze_slack_thread("C1", "123456", instructions="Do X")
    assert result == "custom response"


def test_retry_logic_on_transient_error(monkeypatch):
    dummy_msgs = [{"ts": "1.0", "user": "U1", "text": "retry"}]
    monkeypatch.setattr(at, "fetch_slack_thread", lambda c, t: dummy_msgs)

    calls = {"count": 0}
    class FlakySummary:
        def run(self, **kwargs):
            calls["count"] += 1
            if calls["count"] == 1:
                raise RuntimeError("transient error")
            return "recovered"
    monkeypatch.setattr(at, "summarizer", FlakySummary())

    result = at.analyze_slack_thread("C1", "123456")
    assert result == "recovered"
    assert calls["count"] == 2


def test_retry_exhaustion_raises(monkeypatch):
    dummy_msgs = [{"ts": "1.0", "user": "U1", "text": "fail"}]
    monkeypatch.setattr(at, "fetch_slack_thread", lambda c, t: dummy_msgs)

    class FailAlways:
        def run(self, **kwargs):
            raise RuntimeError("fatal")
    monkeypatch.setattr(at, "summarizer", FailAlways())

    with pytest.raises(RuntimeError):
        at.analyze_slack_thread("C1", "123456")
