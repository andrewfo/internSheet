# internSheet

Pulls internship positions **posted in the last 7 days** from three GitHub listing repos
and appends them as new rows to a Google Sheet:

- [SimplifyJobs/Summer2026-Internships](https://github.com/SimplifyJobs/Summer2026-Internships)
- [vanshb03/Summer2027-Internships](https://github.com/vanshb03/Summer2027-Internships)
- [speedyapply/2027-AI-College-Jobs](https://github.com/speedyapply/2027-AI-College-Jobs)

Only **software/tech roles** are included — product/product-management, hardware, and other
non-software listings are skipped, as are **grad-only roles** (PhD, Master's, or
graduate-candidate positions). Roles open to undergrads (e.g. "BS/MS") are kept. Each repo exposes these fields
differently, so the filter checks every signal available: Simplify's `category` and `degrees`
fields plus the position title text (the only signal vanshb03 and speedyapply provide).

New rows are written as: `Company | Position | Date Applied | Application Status | Details | Applicant Portal`.
`Date Applied` and `Application Status` are left blank so you fill them in as you apply.
`Details` holds the application link. Rows already in the sheet (matched by Company + Position)
are skipped, so the script is safe to re-run every week. Duplicate listings across the three
repos are also deduped.

## Setup

1. Install dependencies:

   ```
   pip install -r requirements.txt
   ```

2. Create Google OAuth credentials (one time):
   - Go to <https://console.cloud.google.com/>, create/select a project.
   - Enable the **Google Sheets API**
     (APIs & Services → Library → "Google Sheets API" → Enable).
   - APIs & Services → **Credentials** → *Create credentials* → **OAuth client ID**.
     - If prompted, configure the OAuth consent screen (External, add your own email as a test user).
     - Application type: **Desktop app**.
   - Download the client secret JSON and save it as `credentials.json` next to `fetch_internships.py`.

## Run

Preview what would be added (no Google access needed):

```
python fetch_internships.py --dry-run
```

Fetch and append to the sheet:

```
python fetch_internships.py
```

On the first real run a browser window opens to authorize your Google account. The token is
cached in `token.json` so later runs are non-interactive.

The target spreadsheet and the 7-day window are set at the top of `fetch_internships.py`
(`SPREADSHEET_ID`, `WORKSHEET_INDEX`, `DAYS`).

> **Note:** `credentials.json` and `token.json` are secrets — keep them out of version control
> (see `.gitignore`).
