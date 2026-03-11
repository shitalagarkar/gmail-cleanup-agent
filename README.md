# 📧 Gmail Cleanup Agent

An AI-powered Gmail inbox management agent that helps you 
scan, categorize, and clean up your inbox intelligently.

![Python](https://img.shields.io/badge/Python-3.13-blue)
![Flask](https://img.shields.io/badge/Flask-3.0-green)
![OpenAI](https://img.shields.io/badge/OpenAI-GPT4o_Mini-orange)
![License](https://img.shields.io/badge/License-MIT-yellow)

---

## ✨ Features
## ✨ Features

### 🆓 Basic Mode — No API Key Needed!
- 🔍 **Smart Inbox Scanning** — Scan by days, months or years
- 📁 **Folder Selection** — Choose which Gmail folders to scan
- 🗑️ **Smart Delete** — Delete within period, all ever, or keep recent
- 📭 **Never Read Detection** — Find emails you've never opened
- 🔗 **Auto Unsubscribe** — Find and send unsubscribe requests

### 🤖 AI Mode — Optional OpenAI Key
- 🧠 **AI Categorization** — Auto-categorize emails using GPT-4o Mini
- 🛡️ **Safety Scoring** — Know what's safe to delete vs keep
- 💬 **Inbox Chat** — Ask questions in natural language
- 📊 **AI Summary** — Get intelligent inbox health report
- 🔒 **Privacy Mode** — Control what data is sent to AI

> 💡 **No OpenAI key? No problem!** Basic Mode works perfectly
> without any API key. Add your key anytime to unlock AI features.
---

## 🚀 Quick Start

### Prerequisites
- Python 3.9+
- Gmail account
- Google Cloud account ([free](https://console.cloud.google.com))
- OpenAI API key — **optional** ([get one here](https://platform.openai.com))
---

### Step 1 — Clone the Repository
```bash
git clone https://github.com/YOUR_USERNAME/gmail-cleanup-agent.git
cd gmail-cleanup-agent
```

### Step 2 — Create Virtual Environment
```bash
python3 -m venv venv
source venv/bin/activate  # Mac/Linux
venv\Scripts\activate     # Windows
```

### Step 3 — Install Dependencies
```bash
python -m pip install -r requirements.txt
```

### Step 4 — Set Up Google Cloud & Gmail API
1. Go to [Google Cloud Console](https://console.cloud.google.com)
2. Create a new project
3. Enable Gmail API
4. Create OAuth 2.0 credentials (Desktop app)
5. Download credentials as `credentials.json`
6. Place `credentials.json` in project root

### Step 5 — Set Up Environment Variables
Create a `.env` file in project root:
```
OPENAI_API_KEY=sk-your-openai-key-here
```

### Step 6 — Run the App
```bash
python app.py
```

Open your browser at:
👉 http://127.0.0.1:5000

---

## 💰 Cost

### Basic Mode
- **Completely FREE** — no API key needed!
- Scan, delete and unsubscribe at zero cost

### AI Mode
- Uses OpenAI GPT-4o Mini — extremely cheap!
- Cost per full scan with AI: ~$0.01 (1 cent!)
- $5 in credits = 500+ full AI scans
- Each user brings their own OpenAI key
- No key? Just use Basic Mode for free!

---

## 🔒 Privacy & Security

- Your emails never leave your computer
- Only sender names and subjects are sent to OpenAI
- Enable Privacy Mode to send names only
- OAuth token stored locally
- No data stored on any server

---

## 🛠️ Tech Stack

| Component | Technology |
|---|---|
| Backend | Python + Flask |
| AI | OpenAI GPT-4o Mini |
| Gmail | Google Gmail API |
| Auth | OAuth 2.0 |
| Frontend | HTML + CSS + JavaScript |

---

## 📁 Project Structure
```
gmail-cleanup-agent/
├── app.py              # Main application
├── templates/
│   └── index.html      # Web interface
├── requirements.txt    # Python dependencies
├── .env               # Your API keys (never commit!)
├── .gitignore         # Files to ignore
└── README.md          # This file
```

---

## 🤝 Contributing

Contributions are welcome! Feel free to:
- Report bugs
- Suggest features
- Submit pull requests

---

## 📄 License

MIT License — feel free to use and modify!

---

## ⭐ If you find this useful, please star the repo!