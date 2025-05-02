import os
import time
import hmac
import hashlib
import logging

logger = logging.getLogger(__name__)

SIGNING_SECRET = os.getenv("SLACK_SIGNING_SECRET")
if not SIGNING_SECRET:
    logger.error("🚨 SLACK_SIGNING_SECRET is missing or empty!")

def verify_slack_request(request) -> bool:
    timestamp = request.headers.get("X-Slack-Request-Timestamp")
    signature = request.headers.get("X-Slack-Signature")
    if not timestamp or not signature:
        logger.warning("Missing Slack signature headers")
        return False

    try:
        req_ts = int(timestamp)
    except ValueError:
        logger.warning("Invalid Slack request timestamp")
        return False

    # Reject if older than 5 minutes
    if abs(time.time() - req_ts) > 60 * 5:
        logger.warning("Slack request timestamp skew too large")
        return False

    body = request.get_data(as_text=True)
    basestring = f"v0:{timestamp}:{body}"
    computed_sig = "v0=" + hmac.new(
        SIGNING_SECRET.encode(),
        basestring.encode(),
        hashlib.sha256
    ).hexdigest()

    valid = hmac.compare_digest(computed_sig, signature)
    if not valid:
        logger.warning("Slack request signature mismatch")
    return valid
