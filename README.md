# OSS Support Bot 🤖

[![Slack](https://img.shields.io/badge/Slack-Compatible-blue?logo=slack)]

An AI-powered Slack bot that summarizes and analyzes threads, handles conversational context, and delivers actionable insights—right inside your Slack workspace! 🚀

---

## 🔍 Features

- **Thread Analysis**: Paste a Slack thread URL + keywords (`analyze`, `summarize`, `explain`) to get a neatly formatted summary. 📋
- **Contextual Chat**: DM or mention the bot to ask questions—keeps thread context for follow-ups. 💬
- **Memory Recall**: Remembers past interactions within the same thread for seamless conversation. 🧠
- **Robust Error Handling**: Notifies you if permissions are missing or a thread can’t be accessed. ⚠️

---

## 🛠️ Installation

1. **Clone the repository**
   ```bash
   git clone https://github.com/your-org/oss-support-bot.git
   cd oss-support-bot
   ```
2. **Set up a Python virtual environment**
   ```bash
   python3 -m venv venv
   source venv/bin/activate
   ```
3. **Install dependencies**
   ```bash
   pip install -r requirements.txt
   ```

---

## ⚙️ Configuration

Create a `.env` file in the project root with the following:

```dotenv
SLACK_BOT_TOKEN=xoxb-your-bot-token
SLACK_APP_TOKEN=xapp-your-app-token\BOT_USER_ID=U01234567
```

> 🔒  Ensure `.env` is in `.gitignore` to keep tokens secure.

---

## 🚀 Usage

### 1. Invite the bot
```slack
/invite @OSS-Support-Bot
```

### 2. Thread Summaries
Mention the bot with a thread URL and keyword:
```slack
@OSS-Support-Bot analyze https://workspace.slack.com/archives/C12345678/p1234567890123456
```
Bot will respond with:
- **Summary**
- **Business Impact**
- **Key Points Discussed**
- **Decisions Made**
- **Action Items**

### 3. Conversational Chat
- **DMs**: Chat directly with the bot.  
- **Channel Mentions**: Mention the bot to ask questions or continue threads.

---

## 📂 Project Structure

```
.
├── app.py                # Main Slack event handlers
├── chains/               # LLM chains for chat & thread analysis
│   ├── chat_chain_mcp.py
│   └── analyze_thread.py
├── utils/                # Helper modules
│   ├── slack_api.py      # send_message wrapper
│   └── slack_tools.py    # fetch_slack_thread & utilities
└── requirements.txt      # Python dependencies
```

---