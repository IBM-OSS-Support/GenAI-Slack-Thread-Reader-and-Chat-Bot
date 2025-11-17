<h3 align="center">
 <img width="200" src="utils/assets/images/logo.svg" alt="IBM GenAI Ask-Support Bot">
 
</h3>
<h1 align="center">IBM GenAI Ask-Support Bot</h1>

## Release Notes (with Timeline View)

**From Initial Commit to Latest (`dev` branch)**  
✅ *Auto‑generated from commit history*  
🔄 *Versioning Logic:*  
- **MAJOR** = Model / architecture change  
- **MINOR** = Feature addition  
- **PATCH** = Bug fix / stability  

---

## 🚀 v1.0.0 — Initial Release
📅 May 2 2025  

### Highlights
- Initial project structure committed  
- `.env` handling + sample env file added  
- Dockerfile setup for deployment  
- Basic Ollama URL configuration  
- `README.md` initialized  
⚙️ Foundation for Slack integration + LLM backend.

---

## 🐞 v1.0.1 — Bug Fixes & Stability
📅 May 13 – 15 2025  

### Highlights  
- Fixed user ID bugs  
- Added usage status tracking  
- Fixed direct DM context handling (x2 commits)  
- Made bot responses more accurate  
- Added feedback thumbs up / down functionality  
- Persistent storage for votes & unique user count  
💬 Improved reliability and user interaction tracking.

---

## ➕ v1.1.0 — Thread Memory & Testing
📅 May 14 – 19 2025  

### Highlights
- Added thread memory expiration (10 minutes)  
- Added test cases  
- Hidden unique user count for privacy  
- Added extra context to business impact prompt  
- Improved prompt engineering for better output  
🧠 Enhanced conversational memory and testing coverage.

---

## ➕ v1.2.0 — Multi‑Workspace Support
📅 May 28 2025  

### Highlights
- Added multi‑workspace support for bot  
- Enabled bot to operate across Slack workspaces  
🌐 Major scalability improvement for enterprise use.

---

## ➕ v1.3.0 — PDF Export & Stats Display
📅 May 29 2025  

### Highlights
- Added PDF export for thread analysis  
- Enhanced stats display with export count  
- Separated PDF utility module for modularity  
📄 Users can now export conversations for reporting.

---

## ➕ v1.4.0 — RAG Implementation & Channel Reading
📅 June 9 – 12 2025  

### Highlights
- Implemented Retrieval‑Augmented Generation (RAG)  
- Added channel reading functionality  
- Enhanced RAG and channel analysis prompts  
- Updated `requirements.txt`  
🤖 Major AI architecture upgrade – contextual awareness from channels/files.

---

## ➕ v1.5.0 — Deployment Automation & Temperature Tuning
📅 June 12 – 13 2025  

### Highlights
- GitHub Actions workflow for automated deployment  
- Fixed build and rollout issues  
- Updated LLM temperature for better response control  
- Prompt enhancements for “analyze channel”  
🛠️ DevOps maturity + improved response consistency.

---

## ➕ v1.6.0 — Cross‑Workspace Analysis & ID Fixes
📅 June 16 – 17 2025  

### Highlights
- Fixed cross‑workspace channel analysis  
- Fixed ID formatting issues  
- Fixed file upload and formatting bugs  
🔗 Seamless operation across teams and workspaces.

---

## ➕ v1.7.0 — Memory Context & User Mention Fixes
📅 June 18 – 19 2025  

### Highlights
- Added missing memory context for channel analysis  
- Fixed user mention issues in production  
- Reverted unstable channel thread enhancements  
🧵 More reliable threading and @mentions in replies.

---

## ➕ v1.8.0 — Translation & Feedback Collection
📅 June 23 2025  

### Highlights
- Added translation support for thread summarization  
- Predefined feedback collection mechanism  
- Adjusted `STATS_FILE` path for consistency  
🌍 Global readiness + structured feedback loop.

---

## ➕ v1.9.0 — OCP Deployment & Image Tagging
📅 June 24 2025  

### Highlights
- OpenShift Container Platform (OCP) deployment support  
- PVC and deployment YAML updates  
- Docker image version tagging automation  
- Added timestamp to logs / responses  
- Improved prompt templates  
☸️ Enterprise‑grade Kubernetes deployment ready.

---

## 🐞 v1.9.1 — Regression & Mobile Fixes
📅 June 25 2025  

### Highlights
- Fixed regression issues from prior PRs  
- Fixed mobile UI display issues  
- Removed “IST” from timestamps for UTC clarity  
- GitHub Actions trigger refined to PR merges only  
📱 Smoother mobile experience.

---

## 🚀 v2.0.0 — Model Architecture Upgrade + Excel RAG
📅 July 21 2025  
🚀 **MAJOR RELEASE – MODEL UPGRADE**

### Highlights
- Added Excel RAG support (analysis of structured data)  
- Fixed multi‑workspace setup issues  
- Tuned model parameters for OllamaEmbedding  
- Added table‑format extraction logic  
📊 Extends bot beyond text – reads, analyzes Excel content.

---

## 🐞 v2.0.1 — Health Check API
📅 Aug 5 2025  

### Highlights
- Added `/health` API endpoint for monitoring  
- GitHub Actions workflow for automatic health‑check  
- Allowed curl to ignore TLS verification (for internal tests)  
❤️‍🩹 Improved service reliability.

---

## ➕ v2.1.0 — Home Page & Async Channel Analysis
📅 Aug 11 – 14 2025  

### Highlights
- Added home page with detailed instructions  
- Enhanced “analyze channel” with async processing  
- Improved prompt templates  
- Verified multi‑workspace functionality  
🏠 Simpler onboarding + faster analysis.

---

## 🐞 v2.1.1 — RAG Inconsistencies Fix
📅 Sep 9 – 17 2025  

### Highlights
- Fixed RAG inconsistencies in file uploads  
- Added persistent Excel file caching  
- Introduced grammar pre‑analysis step  
- Changed keyword `org` ➜ `-org` for parser consistency  
✍️ Smarter, more consistent RAG results.

---

## ➕ v2.2.0 — Product Profile & Full Report Generation
📅 Sep 19 – 26 2025  

### Highlights
- Added full product profile listing support  
- Introduced **“365 Days of Innovation”** report generator  
- Error handling added for empty datasets  
📈 Strategic reporting for leadership and innovation tracking.

---

## 📅 Timeline Summary (Visual Overview)

```text
02 May 2025 → v1.0.0 — Initial Release
15 May 2025 → v1.0.1 — Bug Fixes
19 May 2025 → v1.1.0 — Thread Memory
28 May 2025 → v1.2.0 — Multi‑Workspace
29 May 2025 → v1.3.0 — PDF Export
12 Jun 2025 → v1.4.0 — RAG + Channel Reading
13 Jun 2025 → v1.5.0 — Deployment Automation
17 Jun 2025 → v1.6.0 — Cross Workspace Fixes
19 Jun 2025 → v1.7.0 — Memory + Mentions
23 Jun 2025 → v1.8.0 — Translation + Feedback
24 Jun 2025 → v1.9.0 — OCP Deployment
25 Jun 2025 → v1.9.1 — Mobile + Regression Fixes
21 Jul 2025 → v2.0.0 — Excel RAG (Major Model Upgrade)
05 Aug 2025 → v2.0.1 — Health Check API
14 Aug 2025 → v2.1.0 — Homepage + Async Analysis
17 Sep 2025 → v2.1.1 — RAG Consistency Fixes
26 Sep 2025 → v2.2.0 — 365‑Day Innovation Reports