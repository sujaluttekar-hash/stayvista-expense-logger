# StayVista Expense Logger — Handoff Document

**Version:** 2.0.0
**Date:** June 2026
**Status:** Production-ready (Railway deployment pending)

---

## What This Does

Automates logging F&B expenses on `https://admin.vistarooms.com/expenses/log`.

Instead of manually filling the form for every booking, your team opens a browser tab, picks the expense type, and clicks Run. The tool reads from Google Sheets, downloads the bill PDF from Drive, fills the entire form, and submits — one booking at a time, with live logs streaming to the screen.

**Rows already processed (with an Expense ID) are automatically skipped.** You can run it daily without worry.

---

## Architecture

```
Browser (team's laptop)
    │
    ▼
Flask + SocketIO server   ←── Railway (cloud host, always on)
    │
    ├── inhouse_logger.py   (0% GST)
    ├── foodtown_logger.py  (18% GST)
    └── clover_logger.py    (18% GST, 2 Drive folders)
         │
         ├── core.py              (shared Selenium + Google logic)
         ├── Google Sheets API    (reads rows, writes back Expense ID)
         └── Google Drive API     (downloads bill PDFs)
```

---

## File Structure

```
stayvista-expense-logger/
├── app.py                  Flask server — runs the web UI + streams logs
├── core.py                 All shared logic (login, form helpers, Drive, Sheets)
├── inhouse_logger.py       Inhouse expenses
├── foodtown_logger.py      Foodtown expenses
├── clover_logger.py        Clover expenses
├── templates/
│   └── index.html          Web UI
├── credentials.json        ← NOT in GitHub. Add manually after cloning.
├── Dockerfile              Railway build instructions
├── railway.toml            Railway deployment config
├── requirements.txt        Python dependencies
└── .gitignore              Keeps credentials.json out of version control
```

---

## Google Sheet + Drive Reference

| Type     | GST | Sheet ID                                    | Tab          | Drive Folder(s)                                                       |
|----------|-----|---------------------------------------------|--------------|-----------------------------------------------------------------------|
| Inhouse  | 0%  | 1P-f7olmGlkL7OYO1AxlPV2x6AVkw6Vhz8KUUi_RmPOA | Vista Logs   | 1jx9rVP0V9n0-I7Raq_YcasX0q4tQXQDO                                    |
| Foodtown | 18% | 1xu6nMyKupPlxpnAKSzhlRn0-tdJmaEAYx50-7dJDTyM | Vista Export | 1fkf22SeQDtCF_hnXtXhrKfSwhuJ7rdFf                                    |
| Clover   | 18% | 1CQwVe3Z96AuHa1IcYdn48PMFX-FMYHksEYLBzOYjUWI | Vista Export | 1O0pPzhdrqQ-dQZYM1v1D346UrBO9Rgnb + 1PJ2UfATRhfipzX3zlHubH7USAHT8P30b |

### Column mapping

**Inhouse (Vista Logs)**
| Sheet Column | Used As      |
|--------------|--------------|
| booking_id   | Booking ID   |
| vendor_name  | Vendor       |
| property_name| Property     |
| amount       | Amount       |
| Sub Expense  | Comment      |
| invoice_number | Invoice #  |
| Expense ID   | Written back after submit (skip if filled) |

**Foodtown (Vista Export)**
| Sheet Column  | Used As    |
|---------------|------------|
| Booking_id    | Booking ID |
| Property      | Property   |
| Base amount   | Amount     |
| Vendor        | Vendor     |
| invoice_number| Invoice #  |
| sub           | Comment    |
| Expense ID    | Written back |

**Clover (Vista Export)**
| Sheet Column | Used As    |
|--------------|------------|
| Booking_id   | Booking ID |
| Vendor       | Vendor     |
| property name| Property   |
| base amount  | Amount     |
| invoice no   | Invoice #  |
| subexpense   | Comment    |
| Expense ID   | Written back |

---

## Google Service Account

| Field   | Value                                                        |
|---------|--------------------------------------------------------------|
| Email   | billsheetbot@boxwood-axon-466612-t3.iam.gserviceaccount.com  |
| File    | credentials.json                                             |
| Project | boxwood-axon-466612-t3                                       |

All Sheets and Drive folders must be shared with the service account email.

---

## Admin Portal Credentials

| Field    | Value                          |
|----------|--------------------------------|
| URL      | https://admin.vistarooms.com   |
| Email    | sujal.uttekar@stayvista.com    |
| Password | Sujal@2025                     |

---

## Deployment — Step-by-Step (do this once)

### Step 1: Push to GitHub

```bash
# In your terminal, navigate to the project folder
cd stayvista-expense-logger

git init
git add .
git commit -m "Initial commit — StayVista Expense Logger v2.0"

# Create a new repo on github.com first, then:
git remote add origin https://github.com/YOUR_USERNAME/stayvista-expense-logger.git
git push -u origin main
```

> credentials.json is in .gitignore — it will NOT be pushed. Good.

### Step 2: Deploy to Railway

1. Go to https://railway.app — sign up / log in with GitHub
2. Click **New Project** → **Deploy from GitHub Repo**
3. Select your `stayvista-expense-logger` repo
4. Railway detects the Dockerfile automatically — no config needed
5. Click **Deploy**

Wait ~3 minutes for the build. Railway will give you a URL like:
`https://stayvista-expense-logger-production.up.railway.app`

### Step 3: Add credentials.json to Railway

Railway can't read your local file. Add it as an environment variable:

1. In Railway → your project → **Variables** tab
2. Add variable: `GOOGLE_CREDENTIALS_JSON` = (paste the entire contents of credentials.json)
3. In `core.py`, replace this line:
   ```python
   CREDENTIALS_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "credentials.json")
   ```
   With:
   ```python
   import json, tempfile
   creds_json = os.environ.get("GOOGLE_CREDENTIALS_JSON")
   if creds_json:
       tmp = tempfile.NamedTemporaryFile(delete=False, suffix=".json", mode="w")
       tmp.write(creds_json); tmp.flush()
       CREDENTIALS_FILE = tmp.name
   else:
       CREDENTIALS_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "credentials.json")
   ```
4. Commit + push — Railway auto-redeploys

### Step 4: Share URL with your team

Send the Railway URL to your 2–5 team members. No login needed (Railway has basic auth you can add later if needed). Open, pick a script, click Run.

---

## Running Locally (no Railway)

```bash
pip install -r requirements.txt
# Make sure credentials.json is in the same folder
python app.py
# Open http://localhost:5000
```

---

## How "Skip Already Processed" Works

Each sheet has (or will get, on first run) an `Expense ID` column. Before processing any row, the script checks if that cell is filled. If yes → skip. If no → process.

This means you can run the script every day safely — it will only pick up new rows.

---

## Known Issues + Next Steps

| # | Issue | Priority | Notes |
|---|-------|----------|-------|
| 1 | Gmail Expense ID capture not built | Medium | Needs OAuth2 for sujal@stayvista.com. Service account can't read personal Gmail. Add when ready. |
| 2 | Clover non-Nashik bills | Medium | Folders 1O0pP... and 1PJ2U... must be shared with service account. Verify sharing for all cities. |
| 3 | credentials.json env var (Railway) | High | Do Step 3 of deployment or the app won't connect to Google on Railway. |
| 4 | Basic auth for team URL | Low | Railway doesn't add a password by default. Add HTTP Basic Auth via Railway's built-in feature if needed. |

---

## Bugs Fixed vs v1

| Bug | Old behaviour | Fixed behaviour |
|-----|---------------|-----------------|
| Wrong sheet write-back | Foodtown + Clover both wrote Expense ID to Inhouse sheet | Each logger writes to its own sheet |
| Typo in select2 ID | `select2-expenshead-container` (missing 'e') | Fixed in all loggers |
| All rows reprocessed on every run | No skip logic | Skips rows where Expense ID column is filled |
| gmail_service passed everywhere | Would crash (service account can't use Gmail) | Gmail removed from core; clean placeholder for later |

---

## Version History

| Version | Date      | Changes |
|---------|-----------|---------|
| 1.0.0   | May 2026  | Initial build — 3 loggers, Flask UI, Dockerfile |
| 2.0.0   | Jun 2026  | Full rewrite: core.py extracted, bugs fixed, skip-processed logic, redesigned UI, Railway deployment config, this handoff doc |

---

## Handing Off to the Next Developer

1. Download all files in this folder
2. Open a new chat with Claude
3. Paste this entire handoff document
4. Attach all files (no credentials.json needed — it's secret)
5. Say what you want to build next (e.g. "Add Gmail Expense ID capture")
