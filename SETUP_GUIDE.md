# Spike Scanner — Setup Guide

## What You're Setting Up

A free, fully automated daily stock scanner that:
- Runs every weekday at 5:30 PM ET (after market close) on GitHub's servers
- Scans all NASDAQ stocks for 30%+ daily spikes
- Scores each with the Fade Score (0-14) using 8 predictive parameters
- Updates a live web dashboard you can access from any device
- Sends you an email alert with the top fade candidates

**Total cost: $0** (GitHub Free plan includes Actions + Pages)

---

## Step 1: Create a GitHub Account

1. Go to https://github.com/signup
2. Create a free account
3. Verify your email

## Step 2: Create a New Repository

1. Click the **+** button (top right) → **New repository**
2. Repository name: `spike-scanner`
3. Set to **Public** (required for free GitHub Pages)
4. Check **"Add a README file"**
5. Click **Create repository**

## Step 3: Upload the Scanner Files

1. In your new repo, click **"Add file"** → **"Upload files"**
2. From your computer, open the `spike-scanner` folder
3. Drag and drop ALL these files/folders into GitHub:
   - `scanner.py`
   - `index.html`
   - `requirements.txt`
   - `data/` folder (with today.json and history.json)
   - `.github/` folder (with workflows/daily-scan.yml)

   **Important:** The `.github` folder might be hidden on Windows.
   To see it: Open File Explorer → View tab → check "Hidden items"

4. Click **"Commit changes"**

## Step 4: Enable GitHub Pages (Your Dashboard)

1. Go to your repo → **Settings** tab
2. Left sidebar → **Pages**
3. Under "Source", select **"Deploy from a branch"**
4. Branch: **main**, Folder: **/ (root)**
5. Click **Save**
6. Wait 1-2 minutes, then visit: `https://YOUR-USERNAME.github.io/spike-scanner/`

That's your live dashboard URL — bookmark it!

## Step 5: Set Up Email Alerts

### Create a Gmail App Password (required)

1. Go to https://myaccount.google.com/apppasswords
   - You need 2-Step Verification enabled first: https://myaccount.google.com/signinoptions/two-step-verification
2. App name: `Spike Scanner`
3. Click **Create**
4. Copy the 16-character password (e.g., `abcd efgh ijkl mnop`)

### Add Secrets to GitHub

1. Go to your repo → **Settings** tab
2. Left sidebar → **Secrets and variables** → **Actions**
3. Click **"New repository secret"** and add these three:

   | Name | Value |
   |------|-------|
   | `EMAIL_USER` | Your Gmail address (e.g., `vishwasndn@gmail.com`) |
   | `EMAIL_PASS` | The 16-character app password from above |
   | `EMAIL_TO` | Email to receive alerts (can be same as EMAIL_USER) |

## Step 6: Test It!

1. Go to your repo → **Actions** tab
2. Click **"Daily Spike Scanner"** on the left
3. Click **"Run workflow"** → **"Run workflow"** (green button)
4. Wait 5-10 minutes for it to complete
5. Check your email for the alert
6. Refresh your dashboard URL to see the results

---

## How It Works (Automatic After Setup)

- **Every weekday at 5:30 PM ET**, GitHub Actions runs `scanner.py`
- The script downloads all NASDAQ stock data, finds 30%+ gainers
- Each gainer is scored with the Fade Score
- Results are saved to `data/today.json` and committed to the repo
- The dashboard (`index.html`) loads this JSON and displays it
- An email is sent with the top fade candidates
- Over time, `data/history.json` builds a track record showing actual returns

---

## Troubleshooting

**Dashboard shows "No scan data available"**
→ The scanner hasn't run yet. Go to Actions and trigger it manually.

**GitHub Actions shows a red X**
→ Click on the failed run to see the error log. Common fixes:
- Make sure all files are uploaded correctly
- Check that the `.github/workflows/daily-scan.yml` file exists

**No email received**
→ Check spam folder. If not there:
- Verify the secrets are set correctly (Settings → Secrets)
- Make sure 2-Step Verification is on and the App Password is correct
- Try running the workflow again

**Want to change the scan time?**
→ Edit `.github/workflows/daily-scan.yml`, change the cron line.
Format: `minute hour * * 1-5` (UTC time, Mon-Fri)
