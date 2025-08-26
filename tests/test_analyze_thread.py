import pytest
from unittest.mock import MagicMock, patch
from slack_sdk import WebClient

from chains.analyze_thread import analyze_slack_thread

# Sample Slack thread messages
sample_thread = [
    {
        "ts": "1690000000.000000",
        "user": "U123456",
        "text": "We have a problem with service X"
    },
    {
        "ts": "1690000010.000000",
        "user": "U234567",
        "text": "Looking into it now."
    }
]

@pytest.fixture
def mock_client():
    return MagicMock(spec=WebClient)

@patch("chains.analyze_thread.fetch_slack_thread")
@patch("chains.analyze_thread.get_user_name")
@patch("chains.analyze_thread.resolve_user_mentions")
@patch("chains.analyze_thread.LLMChain.run")
def test_analyze_thread_default_summary(
        mock_chain_run,
        mock_resolve_mentions,
        mock_get_user_name,
        mock_fetch_thread,
        mock_client
):
    # Arrange
    mock_fetch_thread.return_value = sample_thread
    mock_get_user_name.side_effect = lambda client, uid: {
        "U123456": "alice",
        "U234567": "bob"
    }.get(uid, "unknown")

    mock_resolve_mentions.return_value = (
        "2023-07-21 12:00:00 @alice: We have a problem with service X\n"
        "2023-07-21 12:00:10 @bob: Looking into it now."
    )

    mock_chain_run.return_value = "✅ Summary generated"

    # Act
    result = analyze_slack_thread(
        client=mock_client,
        channel_id="C123456",
        thread_ts="1690000000.000000",
        instructions="Summarize the thread",
        default=True
    )

    # Assert
    mock_fetch_thread.assert_called_once()
    mock_get_user_name.assert_called()
    mock_resolve_mentions.assert_called_once()
    mock_chain_run.assert_called_once()
    assert result == "✅ Summary generated"

@patch("chains.analyze_thread.fetch_slack_thread")
@patch("chains.analyze_thread.get_user_name")
@patch("chains.analyze_thread.resolve_user_mentions")
@patch("chains.analyze_thread.LLMChain.run")
def test_analyze_thread_custom_summary(
        mock_chain_run,
        mock_resolve_mentions,
        mock_get_user_name,
        mock_fetch_thread,
        mock_client
):
    # Arrange
    mock_fetch_thread.return_value = sample_thread
    mock_get_user_name.side_effect = lambda client, uid: {
        "U123456": "alice",
        "U234567": "bob"
    }.get(uid, "unknown")

    mock_resolve_mentions.return_value = (
        "2023-07-21 12:00:00 @alice: We have a problem with service X\n"
        "2023-07-21 12:00:10 @bob: Looking into it now."
    )

    mock_chain_run.return_value = "✅ Custom instructions applied"

    # Act
    result = analyze_slack_thread(
        client=mock_client,
        channel_id="C123456",
        thread_ts="1690000000.000000",
        instructions="Translate this to French",
        default=False
    )

    # Assert
    mock_chain_run.assert_called_once()
    assert result == "✅ Custom instructions applied"

@patch("chains.analyze_thread.fetch_slack_thread", side_effect=Exception("API Error"))
def test_fetch_thread_failure(mock_fetch_thread, mock_client):
    with pytest.raises(RuntimeError) as exc:
        analyze_slack_thread(
            client=mock_client,
            channel_id="C123456",
            thread_ts="1690000000.000000"
        )
    assert "Error fetching thread" in str(exc.value)
