# GenAI-Slack-Thread-Reader-and-Chat-Bot 🤖

An AI-powered Slack bot that summarizes and analyzes threads, handles conversational context, and delivers actionable insights directly in your Slack workspace — now with **multi-workspace support**! 🚀

---

## 📋 Table of Contents

- [Features](#-features)
- [Installation](#-installation)
- [Configuration](#-configuration)
- [Usage](#-usage)
- [Environment Variables](#-environment-variables)
- [Slack App Setup (Socket Mode)](#-slack-app-setup-socket-mode)

---

## 🔍 Features

- **Thread Analysis**: Paste a Slack thread URL + keywords (`analyze`, `summarize`, `explain`) to get a neatly formatted summary. 📋
- **Contextual Chat**: DM or mention the bot to ask questions — thread context is retained. 💬
- **Memory Recall**: Seamless follow-up interaction within threads. 🧠
- **PDF Export**: One-click download of thread summaries. 📄
- **Usage Metrics**: `/stats` command shows usage stats, feedback, and call breakdowns. 📊
- **Multi-Workspace Support**: Easily connect and operate across multiple Slack workspaces. 🌐
- **Robust Error Handling**: Clear feedback for missing permissions or broken threads. ⚠️

---

## 🛠️ Installation

```bash
git clone https://github.com/IBM-OSS-Support/GenAI-Slack-Thread-Reader-and-Chat-Bot.git
cd GenAI-Slack-Thread-Reader-and-Chat-Bot

# Create and activate virtual environment
python3 -m venv venv
source venv/bin/activate   # macOS/Linux
# .\venv\Scripts\activate   # Windows

# Install dependencies
pip install -r requirements.txt
```

---

## ⚙️ Configuration

Create a `.env` file at the root with the following structure:

```dotenv
# Core Slack App Config
SLACK_SIGNING_SECRET=""
SLACK_APP_TOKEN=""
BOT_USER_ID="@genai-bot"

# Optional settings
OLLAMA_BASE_URL=http://localhost:11434
SESSION_EXPIRATION_SECONDS=120
STATS_FILE="./data/stats.json"

# Workspace 1
TEAM1_ID="T08PRMC298B"
TEAM1_BOT_TOKEN="xoxb-..."

# Workspace 2
TEAM2_ID="T08S..."
TEAM2_BOT_TOKEN="xoxb-..."

# Add more TEAM<N>_ID and TEAM<N>_BOT_TOKEN pairs as needed
```

> ⚠️ Don’t forget to add `.env` to `.gitignore`.

---

## 🚀 Usage

- **Invite the bot**:
  ```slack
  /invite @GenAI-Slack-Thread-and-Chat-Bot
  ```

- **Analyze a thread**:
  ```slack
  @GenAI-Slack-Thread-and-Chat-Bot analyze https://workspace.slack.com/archives/C12345678/p1234567890123456
  ```

  You’ll get:
  - Thread Summary
  - Business Impact
  - Key Points Discussed
  - Decisions Made
  - Action Items
  - [Export to PDF] button

- **Stats**:
  ```slack
  stats
  ```
  Get usage breakdown:
  - Total calls
  - Analyze vs General calls (with follow-ups)
  - 👍 / 👎 votes

---

## 🔧 Slack App Setup (Socket Mode)

1. **Create a new Slack App** from [https://api.slack.com/apps](https://api.slack.com/apps)
2. **Enable Socket Mode**:
   - Navigate to **Settings → Socket Mode**
   - Toggle "Enable Socket Mode" ON
   - Copy the **App Token** (`xapp-...`)
3. **OAuth Scopes** (add under Bot Token Scopes):
   ```
   channels:history
   channels:read
   chat:write
   conversations.replies
   groups:history
   im:history
   im:read
   im:write
   commands
   ```
4. **Install the App** to each workspace
5. **Configure your .env** with the correct tokens and workspace IDs

No public HTTP server required — it runs entirely over Socket Mode.
