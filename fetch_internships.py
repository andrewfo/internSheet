#!/usr/bin/env python3
"""Pull internship positions posted in the last 7 days from three GitHub listing
repos and append them as new rows to a Google Sheet.

Sources:
  - SimplifyJobs/Summer2026-Internships  (dev branch, listings.json)
  - vanshb03/Summer2027-Internships       (dev branch, listings.json)
  - speedyapply/2027-AI-College-Jobs      (main branch, README.md markdown tables)

Sheet columns (in order):
  Company | Position | Date Applied | Application Status | Details | Applicant Portal

Date Applied and Application Status are left blank so you fill them in as you apply.

Usage:
  python fetch_internships.py --dry-run   # preview rows, no Google calls
  python fetch_internships.py             # authorize (first run) and append rows
"""

import argparse
import re
import sys
import time

import requests

# --- Config ---------------------------------------------------------------

SPREADSHEET_ID = "1S5d7ZyV57iVV4ZxI3JUgnBmsSNy23lvFxP0RmUy9RcU"
WORKSHEET_INDEX = 0          # first tab (the one in the screenshot)
DAYS = 7                     # "last week"
SCOPES = ["https://www.googleapis.com/auth/spreadsheets"]

SIMPLIFY_JSON = "https://raw.githubusercontent.com/SimplifyJobs/Summer2026-Internships/dev/.github/scripts/listings.json"
VANSH_JSON = "https://raw.githubusercontent.com/vanshb03/Summer2027-Internships/dev/.github/scripts/listings.json"
SPEEDYAPPLY_README = "https://raw.githubusercontent.com/speedyapply/2027-AI-College-Jobs/main/README.md"

HTTP_TIMEOUT = 60
USER_AGENT = "internSheet-scraper/1.0"


# --- Fetching / parsing ---------------------------------------------------

def _get(url):
    resp = requests.get(url, headers={"User-Agent": USER_AGENT}, timeout=HTTP_TIMEOUT)
    resp.raise_for_status()
    return resp


US_STATES = {
    "AL", "AK", "AZ", "AR", "CA", "CO", "CT", "DE", "FL", "GA", "HI", "ID", "IL",
    "IN", "IA", "KS", "KY", "LA", "ME", "MD", "MA", "MI", "MN", "MS", "MO", "MT",
    "NE", "NV", "NH", "NJ", "NM", "NY", "NC", "ND", "OH", "OK", "OR", "PA", "RI",
    "SC", "SD", "TN", "TX", "UT", "VT", "VA", "WA", "WV", "WI", "WY", "DC", "PR",
}
_STATE_SUFFIX_RE = re.compile(r",\s*([A-Z]{2})$")


def _is_us_location(loc):
    loc = loc.strip()
    m = _STATE_SUFFIX_RE.search(loc)
    if m and m.group(1) in US_STATES:
        return True
    low = loc.lower()
    return "united states" in low or "usa" in low or low == "remote"


def _is_us(locations):
    return any(_is_us_location(l) for l in (locations or []))


def load_listings_json(url, source):
    """Load a SimplifyJobs-style listings.json and return recent, active US internships."""
    cutoff = time.time() - DAYS * 86400
    items = _get(url).json()
    out = []
    for it in items:
        if not (it.get("active") and it.get("is_visible", True)):
            continue
        if (it.get("date_posted") or 0) < cutoff:
            continue
        if not _is_us(it.get("locations")):
            continue
        company = (it.get("company_name") or "").strip()
        position = (it.get("title") or "").strip()
        details = (it.get("url") or "").strip()
        if not company or not position:
            continue
        out.append({
            "company": company,
            "position": position,
            "details": details,
            "portal": "",
            "source": source,
        })
    return out


_TAG_RE = re.compile(r"<[^>]+>")
_HREF_RE = re.compile(r'href="([^"]+)"')
_AGE_RE = re.compile(r"^(\d+)d$")


def _strip_tags(cell):
    return _TAG_RE.sub("", cell).strip()


def load_speedyapply():
    """Parse the speedyapply USA-internships README markdown tables."""
    text = _get(SPEEDYAPPLY_README).text
    out = []
    for line in text.splitlines():
        line = line.strip()
        if not line.startswith("|"):
            continue
        cells = [c.strip() for c in line.strip("|").split("|")]
        if len(cells) != 6:
            continue
        company_cell, position_cell, _loc, _salary, posting_cell, age_cell = cells
        m = _AGE_RE.match(age_cell)
        if not m or int(m.group(1)) > DAYS:      # skips header/separator rows too
            continue
        company = _strip_tags(company_cell)
        position = _strip_tags(position_cell)
        if not company or not position:
            continue
        href = _HREF_RE.search(posting_cell)
        out.append({
            "company": company,
            "position": position,
            "details": href.group(1) if href else "",
            "portal": "",
            "source": "speedyapply",
        })
    return out


def gather():
    """Fetch all sources and dedup by (company, position). First source wins."""
    rows = []
    loaders = [
        ("SimplifyJobs", lambda: load_listings_json(SIMPLIFY_JSON, "simplify")),
        ("vanshb03", lambda: load_listings_json(VANSH_JSON, "vansh")),
        ("speedyapply", load_speedyapply),
    ]
    for name, loader in loaders:
        try:
            found = loader()
            print(f"  {name}: {len(found)} recent listing(s)")
            rows.extend(found)
        except Exception as exc:                 # noqa: BLE001 - keep going if one source fails
            print(f"  {name}: FAILED ({exc})", file=sys.stderr)

    seen = set()
    deduped = []
    for r in rows:
        key = (r["company"].lower().strip(), r["position"].lower().strip())
        if key in seen:
            continue
        seen.add(key)
        deduped.append(r)
    return deduped


# --- Google Sheets --------------------------------------------------------

def get_worksheet():
    import gspread
    from google.auth.transport.requests import Request
    from google.oauth2.credentials import Credentials
    from google_auth_oauthlib.flow import InstalledAppFlow
    import os

    creds = None
    if os.path.exists("token.json"):
        creds = Credentials.from_authorized_user_file("token.json", SCOPES)
    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            creds.refresh(Request())
        else:
            if not os.path.exists("credentials.json"):
                sys.exit(
                    "Missing credentials.json. Create an OAuth client ID (Desktop app) in Google "
                    "Cloud, enable the Google Sheets API, and download it next to this script. "
                    "See README.md."
                )
            flow = InstalledAppFlow.from_client_secrets_file("credentials.json", SCOPES)
            creds = flow.run_local_server(port=0)
        with open("token.json", "w", encoding="utf-8") as fh:
            fh.write(creds.to_json())

    client = gspread.authorize(creds)
    return client.open_by_key(SPREADSHEET_ID).get_worksheet(WORKSHEET_INDEX)


def existing_keys(worksheet):
    """Set of (company, position) already present in the sheet (skips header row)."""
    companies = worksheet.col_values(1)[1:]
    positions = worksheet.col_values(2)[1:]
    keys = set()
    for c, p in zip(companies, positions):
        keys.add((c.lower().strip(), p.lower().strip()))
    return keys


def to_row(item):
    # Company | Position | Date Applied | Application Status | Details | Applicant Portal
    return [item["company"], item["position"], "", "", item["details"], item["portal"]]


# --- Main -----------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dry-run", action="store_true",
                        help="Print rows that would be added; do not touch Google Sheets.")
    args = parser.parse_args()

    print(f"Fetching listings posted in the last {DAYS} days...")
    items = gather()
    print(f"{len(items)} unique listing(s) after dedup.\n")

    if args.dry_run:
        for it in items:
            print(f"  {it['company']:<28} | {it['position'][:60]:<60} | {it['details']}")
        print(f"\n[dry-run] Would append {len(items)} row(s). No changes made.")
        return

    worksheet = get_worksheet()
    have = existing_keys(worksheet)
    new_items = [it for it in items
                 if (it["company"].lower().strip(), it["position"].lower().strip()) not in have]

    if not new_items:
        print("Nothing new to add - all fetched listings are already in the sheet.")
        return

    worksheet.append_rows([to_row(it) for it in new_items], value_input_option="USER_ENTERED")
    print(f"Appended {len(new_items)} new row(s) to the sheet "
          f"({len(items) - len(new_items)} already present, skipped).")


if __name__ == "__main__":
    main()
