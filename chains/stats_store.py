import json
import os
import logging

# You can change this path if you want (relative to your working dir)
PERSIST_FILE = "/Users/athirakm/data/stats.json"

_vote_up_count = 0
_vote_down_count = 0
_unique_users = set()

def save_stats():
    try:
        os.makedirs(os.path.dirname(PERSIST_FILE), exist_ok=True)
        with open(PERSIST_FILE, "w") as f:
            json.dump({
                "vote_up_count": _vote_up_count,
                "vote_down_count": _vote_down_count,
                "unique_users": list(_unique_users),
            }, f)
    except Exception as e:
        logging.error(f"[Stats] Failed to save stats: {e}")

def load_stats():
    global _vote_up_count, _vote_down_count, _unique_users
    try:
        if os.path.exists(PERSIST_FILE):
            with open(PERSIST_FILE, "r") as f:
                data = json.load(f)
                _vote_up_count = data.get("vote_up_count", 0)
                _vote_down_count = data.get("vote_down_count", 0)
                _unique_users = set(data.get("unique_users", []))
    except Exception as e:
        logging.error(f"[Stats] Failed to load stats: {e}")

def add_vote(vote_type, user_id):
    global _vote_up_count, _vote_down_count, _unique_users
    if vote_type == "up":
        _vote_up_count += 1
    else:
        _vote_down_count += 1
    _unique_users.add(user_id)
    save_stats()

def get_stats():
    return {
        "vote_up_count": _vote_up_count,
        "vote_down_count": _vote_down_count,
        "unique_users_count": len(_unique_users),
    }
