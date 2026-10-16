# Setup Guide: GitHub Profile Auto-Updating Automation

This automation keeps your GitHub profile README fresh with your most recently worked-on public projects and creates natural, activity-adaptive commits that reflect on your GitHub contribution graph.

---

## 1. Verify Repository Setup

1. Your repository must be named **exactly** like your GitHub username (e.g., `divyanshshrivastava/divyanshshrivastava`).
2. The repository must be **Public**.
3. Ensure the default branch is `main`.

---

## 2. Configure Your GitHub Noreply Email

Commits authored by the default `github-actions[bot]` do **not** count toward your GitHub contribution graph. To have commits credited to you, the workflow must use your real GitHub-linked email.

1. Go to **GitHub Settings** $\rightarrow$ [**Emails**](https://github.com/settings/emails).
2. Look for the section: **"Keep my email addresses private"**.
3. You will see an email in the format:
   ```text
   <ID>+<USERNAME>@users.noreply.github.com
   ```
   *(Example: `12345678+divyanshshrivastava@users.noreply.github.com`)*
4. Open [`.github/workflows/update-readme.yml`](.github/workflows/update-readme.yml) in your editor and replace `<ID>+divyanshshrivastava@users.noreply.github.com` with your exact noreply email.

---

## 3. Enable Workflow Permissions in GitHub

By default, GitHub Actions may have read-only permissions in your repository. You must allow the workflow to write/push changes:

1. In your GitHub repository, click on the **Settings** tab.
2. In the left sidebar, click **Actions** $\rightarrow$ **General**.
3. Scroll down to **Workflow permissions**.
4. Select **"Read and write permissions"**.
5. Check the box **"Allow GitHub Actions to create and approve pull requests"** (optional, but good practice).
6. Click **Save**.

---

## 4. Push Files to GitHub

Since you have cloned the repository locally, review the changes and push them to your repository:

```bash
git status
git add .
git commit -m "feat: add auto-updating readme workflow and commit generator"
git push origin main
```

---

## 5. Test the Workflow Manually

1. Go to your repository on GitHub.
2. Click the **Actions** tab.
3. In the left sidebar, click **"Update Profile README & Commits"**.
4. Click the **"Run workflow"** dropdown button on the right, leave the branch as `main`, and click **"Run workflow"**.
5. Click on the workflow run to view the logs and verify that it executed cleanly.

---

## How It Works

### A. Real-Activity Adaptation
Every day at 18:00 UTC (or when triggered manually), the bot inspects your real GitHub activity:
- **0 real commits today:** The bot uses normal weights (0 to 5 commits: `0: 20%, 1: 25%, 2: 25%, 3: 15%, 4: 10%, 5: 5%`).
- **1–2 real commits today:** The bot caps itself at 0 to 2 commits (`0: 40%, 1: 40%, 2: 20%`).
- **3 or more real commits today:** The bot makes **0 commits**, stepping out of the way completely.
- **Weekend Reduction:** On Saturdays and Sundays (UTC), normal commit probabilities are automatically shifted down toward 0–2.

### B. Natural Quiet Stretches (Stateless)
- To avoid looking like an artificial machine running 365 days a year, the bot features natural quiet stretches.
- On eligible days, there is a **6% chance** of entering a **2 to 4 consecutive day** quiet stretch where 0 commits are made.
- The bot enforces a **mandatory cooldown of at least 5 days** after any quiet stretch ends before another can start.
- This is completely **stateless and deterministic** (computed in-memory via deterministic replay from Jan 1, 2026), requiring no hidden database or state files.

### C. Natural Timestamps & Double-Run Protection
- If $N$ commits are scheduled, their timestamps are randomly generated between `00:00:00 UTC` and the current run time, sorted in ascending order. No future dates are ever generated.
- The double-run guard checks how many commits have already been authored today in the repository. If you run the workflow multiple times on the same day, it will only make up to the remaining count $N - \text{existing}$, preventing commit flooding.

### D. Safe README Markers
- The bot **only** modifies the text between:
  ```markdown
  <!-- AUTO-UPDATE:START -->
  <!-- AUTO-UPDATE:END -->
  ```
- All other content in your profile README remains untouched.

---

## Important Notes on Contribution Tracking

1. **Default Branch:** Commits only count on your contribution graph if they are made on the **default branch** (`main`) of a standalone (non-fork) repository.
2. **Graph Delay:** GitHub's contribution graph can take anywhere from **5 to 15 minutes** to refresh and reflect new commits.
3. **Scheduled Runs:** GitHub Actions scheduled cron jobs run on shared infrastructure and can sometimes experience delays during peak hours.
4. **Scheduled Inactivity:** If a repository has no code or user activity for 60 days, GitHub may automatically pause scheduled workflows. Running the workflow manually once reactivates it.
