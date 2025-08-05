# health_check.py
import os

from flask import Flask


# --- 1️⃣ Define the Flask health‐check app ---
health_app = Flask(__name__)

@health_app.route("/health")
def health():
    # You could add extra checks here (DB, cache, etc.)
    return "OK", 200

def run_health_server():
    health_app.run(
        host="0.0.0.0",     # listen on all interfaces
        port=int(os.getenv("HEALTH_PORT", 3001)),
        debug=False,
        use_reloader=False
    )
