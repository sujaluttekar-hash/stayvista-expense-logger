# GitHub Actions setup — Expense Sheet Runner

The workflow is manual-only. It does not run on a cron schedule.

## 1. Add the Google service-account secret

In the repository, open **Settings → Secrets and variables → Actions → New repository secret**.

Create:

- **Name:** \`GOOGLE_SERVICE_ACCOUNT_JSON\`
- **Value:** the complete JSON contents of the Google service-account key file.

The workflow writes this secret to a temporary \`credentials.json\` file during the job and removes it at the end. The file is ignored by Git.

The service-account email must have access to:
- The spreadsheet configured by \`EXPENSE_SHEET_ID\`.
- The Drive folder configured by \`EXPENSE_DRIVE_FOLDER_ID\` and the bill files inside it.

## 2. Check the credential sheet

The runner reads these values from cells B1:B4 of the \`id\` tab in the configured spreadsheet:

| Cell | Value |
|---|---|
| B1 | StayVista admin username |
| B2 | StayVista admin password |
| B3 | Gmail address |
| B4 | Gmail app password |

Use a Gmail app password where the account supports it. IMAP must be enabled for the Gmail account. The runner can still submit expenses if Gmail confirmation polling is unavailable, but the log row will use the fallback Expense ID value \`SUBMITTED\`.

## 3. Run manually

1. Open the repository's **Actions** tab.
2. Select **StayVista Expense Sheet Runner**.
3. Click **Run workflow**.
4. Keep the branch set to \`main\`.
5. Click **Run workflow** again to confirm.
6. Open the run to watch the Selenium logs.

There is no scheduled trigger. Each run processes the rows currently present in the \`Main\` tab.

## 4. Important behaviour

- Chrome runs headlessly on an Ubuntu GitHub-hosted runner.
- The script processes source rows from bottom to top because successful rows are deleted from the Main tab.
- A successful submission is written to the Logs tab and the corresponding Main row is deleted.
- Failed rows remain in the Main tab for review.
- The runner fails the GitHub Actions job if one or more rows fail.
- A bill is only downloaded when the Drive cell contains a matching Drive file URL or exact file name. It will not select an arbitrary file from the folder.

## 5. Security

Never commit \`credentials.json\`, Gmail app passwords, admin passwords, or service-account keys. The repository's \`.gitignore\` excludes credential files.

If any real password or key was previously committed to a public repository, rotate it. Removing a secret from the latest version does not remove it from earlier Git history.
