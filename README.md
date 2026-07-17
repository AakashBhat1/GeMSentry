# 🛰️ GeMSentry

![GeMSentry Banner](gemsentry_banner.png)

<div align="center">

**Smart RFP Acquisition & Scraper Dashboard for India's Government e-Marketplace (GeM)**

[![License: MIT](https://img.shields.io/badge/License-MIT-purple.svg?style=for-the-badge)](LICENSE)
[![Python](https://img.shields.io/badge/Python-3.8%2B-blue.svg?style=for-the-badge&logo=python)](https://www.python.org/)
[![Playwright](https://img.shields.io/badge/Playwright-1.40%2B-green.svg?style=for-the-badge&logo=playwright)](https://playwright.dev/python/)
[![Flask](https://img.shields.io/badge/Flask-3.0%2B-black.svg?style=for-the-badge&logo=flask)](https://flask.palletsprojects.com/)

</div>

---

## 📌 Project Overview
**GeMSentry** is an automated pipeline and visual intelligence tool that monitors, extracts, parses, and scores RFP (Request for Proposal) documents from the India Government e-Marketplace (GeM) portal (`bidplus.gem.gov.in/all-bids`).

By leveraging headful/headless browser automation, PDF text analysis, and metadata scoring, GeMSentry takes the manual overhead out of bid hunting. It presents opportunities in a gorgeous, glassmorphic review dashboard with automated scoring and exemption analysis.

---

## 🛠️ Architecture Workflow

The following diagram illustrates how GeMSentry operates from keyword search to local dashboard rendering:

```mermaid
graph TD
    A[Start Scraper / API Trigger] --> B[Launch Playwright with Stealth Config]
    B --> C[Search GeM Portal using Keywords]
    C --> D[Parse Search Result Cards]
    D --> E{Match Date Policy?}
    E -- No --> F[Mark Rejected & Save Metadata]
    E -- Yes --> G[Check Download Cache]
    G -- Not Cached --> H[Download RFP PDF Document]
    G -- Cached --> I[Skip Download]
    H --> J[PyPDF Reader scans first 3 pages]
    I --> J
    J --> K[Regex Scoring Engine processes exemptions]
    K --> L[Save details to metadata.json & metadata.js]
    L --> M[Serve local Flask Backend server]
    M --> N[Load Interactive Dashboard on localhost:5000]
    F --> L
```

---

## ✨ Features

- **🛡️ Stealth Automation:** Uses Playwright with custom user agents, locale configurations, and anti-detection evasion scripts to bypass aggressive web application firewalls (WAF).
- **📋 Keyword Scouting:** Automatically queries keywords defined dynamically in your `config/keywords.csv` file.
- **📅 Dynamic Date Gates:** Automatically rejects tenders that are old or don't match the current month, ensuring you only focus on active bids.
- **🧠 Automated RFP Analyzer:** Automatically parses downloaded PDFs for critical details:
  - **EMD Amount:** Detects EMD presence and triggers warnings if it exceeds 10 Lakhs.
  - **Startup Exemption:** Identifies turnover/experience exemptions for startups.
  - **MSE Exemption:** Identifies micro & small enterprise exemptions.
  - **Pre-Bid Details:** Detects pre-bid meeting necessity and pulls scheduling dates.
  - **ePBG Details:** Identifies performance bank guarantee requirement percentages.
- **🖥️ Glassmorphic Triage Dashboard:** Sleek, responsive, local web interface featuring:
  - Global searching and keyword filtering.
  - Live console stream of scraping activities.
  - Quick buttons to override and toggle status (Shortlisted, Pending, Rejected).
  - One-click viewing of downloaded RFP documents.

---

## 🚀 Getting Started

### Quick Start (Windows)
We provide a PowerShell script that configures your virtual environment, updates dependencies, downloads Playwright chromium binaries, and starts the system:

```powershell
powershell -ExecutionPolicy Bypass -File .\run_search.ps1
```

### Manual Installation (Any OS)

1. **Clone the Repository:**
   ```bash
   git clone https://github.com/<your-username>/GeMSentry.git
   cd GeMSentry
   ```

2. **Install Dependencies:**
   ```bash
   pip install -r requirements.txt
   ```

3. **Install Browser Binaries:**
   ```bash
   playwright install chromium
   ```

4. **Run the Application:**
   ```bash
   python run.py
   ```
   *The local server will spin up, and your default web browser will automatically open to `http://localhost:5000`.*

---

## 📁 Project layout

| Path | Purpose |
|------|---------|
| `config/` | Tunable knobs: `keywords.csv`, `scoring_config.json`, `company_profile.json` |
| `data/` | Runtime / imported state (`history.json`, optional `source/` inputs) |
| `logs/` | App log (`gemsentry.log`) + per-scrape session files under `logs/scrapes/` |
| `tenders/` | Tender metadata + downloaded RFP PDFs (`tenders/downloads/`) |
| `paths.py` | Single path map used by app, scraper, and tools |
| `run.py` | Primary entrypoint |

---

## ⚙️ Configuration

### 🔍 Keywords Setup
Modify `config/keywords.csv` to add or remove search terms. GeMSentry will automatically pick these up on the next scrape.
```csv
POWER SUPPLY
RADAR
SOFTWARE
MILITARY GRADE
```

### 🗓️ Date Policy Engine
The system uses strict filters inside `scraper.py` to triage tenders:
* **Start Date Rule:** Must match the current calendar month and year.
* **Duration Rule:** The bid duration (End Date - Start Date) must be at least 7 days.
* **Remaining Time Rule:** The time left to submit must be at least 7 days.

---

## 📚 References & Third-Party Libraries

GeMSentry uses several open-source libraries to deliver automation:
* **[Playwright Python API](https://playwright.dev/python/):** Drives headless browser operations and manages secure PDF downloading.
* **[Flask](https://flask.palletsprojects.com/):** Powers the local API server and serves the dashboard interface.
* **[BeautifulSoup 4](https://www.crummy.com/software/BeautifulSoup/):** Parses bid card structures from GeM search page results.
* **[PyPDF](https://pypdf.readthedocs.io/):** Handles local PDF text extraction to scan for exemptions and penalties.
* **[Government e-Marketplace (GeM)](https://gem.gov.in/):** The public procurement portal of India from which bids are aggregated.

---

## 📄 License

Distributed under the MIT License. See [LICENSE](LICENSE) for details.
