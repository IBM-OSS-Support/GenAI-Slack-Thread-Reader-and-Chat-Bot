# GenAI-Slack-Thread-Reader-and-Chat-Bot 🤖

[![Slack](https://img.shields.io/badge/Slack-Compatible-blue?logo=slack)]

An AI-powered Slack bot that summarizes and analyzes threads, handles conversational context, and delivers actionable insights directly in your Slack workspace! 🚀

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

Create a `.env` file in the project root with these variables:

```dotenv
SLACK_BOT_TOKEN=your-bot-token
SLACK_SIGNING_SECRET=your-signing-secret
SLACK_APP_TOKEN=your-app-token
BOT_USER_ID=your-bot-user-id
OLLAMA_BASE_URL=http://localhost:11434
```

> 🔒 Make sure `.env` is added to `.gitignore`!

---

## 🚀 Usage

1. **Invite the bot** to your channel:
   ```slack
   /invite @GenAI-Slack-Thread-and-Chat-Bot
   ```
2. **Thread Summaries**:
   ```slack
   @GenAI-Slack-Thread-and-Chat-Bot analyze https://workspace.slack.com/archives/C12345678/p1234567890123456
   ```
   Bot will respond with:  
   • *Summary*  
   • *Business Impact*  
   • *Key Points Discussed*  
   • *Decisions Made*  
   • *Action Items*

3. **Conversational Chat**:
   - **DMs**: Chat directly with the bot for follow-up questions.  
   - **Channel Mentions**: Mention the bot to ask questions or continue threads.

---

## 📂 Project Structure

```text
.
├── app.py                 # Main Slack event handlers
├── chains/               
│   ├── chat_chain_mcp.py  # Chat logic & memory management
│   └── analyze_thread.py  # Thread fetching + LLM summarization
├── utils/                
│   ├── slack_api.py       # send_message wrapper
│   └── slack_tools.py     # fetch_slack_thread & helpers
└── requirements.txt       # Python dependencies
```

---

## 🔧 Slack App Setup (Socket Mode)

1. **Create a new Slack App** at https://api.slack.com/apps ➡️ *Create New App* ➡️ *From scratch*.
2. **Enable Socket Mode** under *Settings → Socket Mode*:
   - Toggle *Enable Socket Mode* **On**.
   - Copy the **App Token** (begins with `xapp-`).
3. **Add Bot Token Scopes** under *OAuth & Permissions*:
   - `channels:history`
   - `channels:read`
   - `chat:write`
   - `conversations.replies`
   - `groups:history`
   - `im:history`
   - `im:read`
   - `im:write`
   - `commands`
4. **Install the App** to your workspace (click *Install App*).
5. **Populate your `.env`** with:
   ```dotenv
   SLACK_BOT_TOKEN=xoxb-your-bot-token
   SLACK_APP_TOKEN=xapp-your-app-token
   SLACK_SIGNING_SECRET=your-signing-secret
   BOT_USER_ID=your-bot-user-id
   OLLAMA_BASE_URL=http://localhost:11434
   ```

No public endpoint is required—your bot communicates over Socket Mode! 🚀
