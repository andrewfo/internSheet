#!/usr/bin/env python3
"""Pull internship positions posted in the last 7 days from three GitHub listing
repos and append them as new rows to a Google Sheet.

Sources:
  - SimplifyJobs/Summer2026-Internships  (dev branch, listings.json)
  - vanshb03/Summer2027-Internships       (dev branch, listings.json)
  - speedyapply/2027-AI-College-Jobs      (main branch, README.md markdown tables)

Only technical roles are kept: product/management and other non-engineering roles
are skipped, as are grad-only roles (PhD/Master's/graduate), while roles open to
undergrads (e.g. "BS/MS") are kept. Each repo marks these differently, so the filter
uses whatever it offers -- Simplify's category/degrees fields, and the title text
(the only signal vanshb03 and speedyapply provide). See keep_role().

Sheet columns (in order):
  Company | Position | Date Applied | Status | Link

New rows fill in Company (with the posting date appended, e.g. "Meta (7/2/26)"),
Position, and Link; Date Applied and Status are left blank so you fill them in as
you apply.

Usage:
  python fetch_internships.py --dry-run   # preview rows, no Google calls
  python fetch_internships.py             # authorize (first run) and append rows
"""

import argparse
import datetime
import re
import sys
import time

import requests

# --- Config ---------------------------------------------------------------

SPREADSHEET_ID = "1xDU8Yr1bJbxvAtpYs6RDg61-tEnN2r2ikDxGXulmLH4"
WORKSHEET_TITLE = "tracker"  # the tab to append to (matched case-insensitively)
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


# --- Role filtering: keep technical roles, drop grad-only roles ------------
#
# The three repos expose role type differently, so we check every signal each
# one offers:
#   * Simplify listings.json carry a "category" and a "degrees" array.
#   * vansh listings.json have neither (category is null), so we fall back to
#     the title text.
#   * speedyapply is a markdown table with only a title, so likewise.

# Simplify categories we consider technical. Anything else (e.g. "Product",
# "Product Management", "Hardware") is dropped.
TECH_CATEGORIES = {
    "ai/ml/data",
    "software",
    "software engineering",
    "quant",
    "quantitative finance",
    "data science, ai & machine learning",
}

# Simplify "degrees" values an undergraduate can apply with. If a listing names
# degrees but none of these, it's a grad-only role and we skip it.
UNDERGRAD_DEGREES = {"bachelor's", "associate's", "certificate", "bootcamp", "incomplete"}

# Title fragments marking a non-technical (product/management/business) role.
_NONTECH_TITLE_RE = re.compile(
    r"\b(?:"
    r"product manager|product management|product owner|product marketing|"
    r"program manager|project manager|"
    r"business analyst|business development|"
    r"marketing|sales|recruit|"
    r"ux designer|ui designer|ux/ui|product design(?:er)?"
    r")\b",
    re.IGNORECASE,
)

# Title fragments marking a hardware role. vansh/speedyapply have no category
# field, so titles are the only signal; this also catches hardware roles filed
# under another Simplify category. "Embedded" is intentionally omitted since
# "embedded software" roles are software.
_HARDWARE_TITLE_RE = re.compile(
    r"\b(?:"
    r"hardware|firmware|fpga|asic|vlsi|pcb|rtl|silicon|semiconductor|"
    r"electrical engineer(?:ing)?|analog|circuit|rf engineer"
    r")\b",
    re.IGNORECASE,
)

# Title fragments marking a grad-only (PhD / Master's / graduate) role.
_PHD_TITLE_RE = re.compile(r"\b(?:ph\.?d|doctoral|mba)\b", re.IGNORECASE)
_MASTERS_TITLE_RE = re.compile(r"\b(?:master'?s?|m\.?s\.?|m\.?sc)\b", re.IGNORECASE)
_GRADUATE_TITLE_RE = re.compile(r"(?<!under)\bgraduate\b", re.IGNORECASE)
# Signals the role is also open to undergrads (e.g. "BS/MS"), which keeps it in.
_UNDERGRAD_TITLE_RE = re.compile(r"\b(?:bachelor'?s?|b\.?s\.?|undergrad)", re.IGNORECASE)


def _degrees_allow_undergrad(degrees):
    """True unless a listing names degrees that are all graduate-level."""
    if not degrees:                              # empty/unspecified -> can't exclude
        return True
    return any(d.strip().lower() in UNDERGRAD_DEGREES for d in degrees)


def _is_grad_only_title(title):
    """Title reads as a PhD / Master's / graduate role with no undergrad path."""
    title = title or ""
    marked_grad = (_PHD_TITLE_RE.search(title) or _MASTERS_TITLE_RE.search(title)
                   or _GRADUATE_TITLE_RE.search(title))
    if not marked_grad:
        return False
    return not _UNDERGRAD_TITLE_RE.search(title)  # keep "BS/MS"-style roles


def keep_role(position, category=None, degrees=None):
    """Keep only technical, undergrad-eligible roles.

    Any provided signal can veto a role; missing signals (None) are ignored so a
    repo that omits a field simply relies on the ones it does provide.
    """
    if category is not None and category.strip().lower() not in TECH_CATEGORIES:
        return False
    if _NONTECH_TITLE_RE.search(position or ""):
        return False
    if _HARDWARE_TITLE_RE.search(position or ""):
        return False
    if not _degrees_allow_undergrad(degrees):
        return False
    if _is_grad_only_title(position):
        return False
    return True


def _fmt_date(dt):
    """Format a datetime.date as M/D/YY to match the sheet (e.g. 7/9/26)."""
    return f"{dt.month}/{dt.day}/{dt.year % 100:02d}"


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
        if not keep_role(position, it.get("category"), it.get("degrees")):
            continue
        posted = datetime.date.fromtimestamp(it["date_posted"])
        out.append({
            "company": company,
            "position": position,
            "details": details,
            "portal": "",
            "date": _fmt_date(posted),
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
        if not keep_role(position):             # no category/degree fields here
            continue
        href = _HREF_RE.search(posting_cell)
        # speedyapply gives an age (e.g. "3d"), not an exact date -> derive it.
        posted = datetime.date.today() - datetime.timedelta(days=int(m.group(1)))
        out.append({
            "company": company,
            "position": position,
            "details": href.group(1) if href else "",
            "portal": "",
            "date": _fmt_date(posted),
            "source": "speedyapply",
        })
    return out


# Trailing " (M/D/YY)" date suffix we append to the Company cell for display.
_DATE_SUFFIX_RE = re.compile(r"\s*\(\d{1,2}/\d{1,2}/\d{2,4}\)\s*$")


def _clean_company(company):
    """Strip the display-only date suffix so dedup matches the real company name."""
    return _DATE_SUFFIX_RE.sub("", company or "")


def _key(company, position):
    """Normalized identity used everywhere for dedup: case- and whitespace-insensitive.

    Ignores the display-only date suffix so the same role isn't re-added just
    because its posted date differs slightly between sources or runs.
    """
    return (
        re.sub(r"\s+", " ", _clean_company(company)).strip().lower(),
        re.sub(r"\s+", " ", position or "").strip().lower(),
    )


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
        key = _key(r["company"], r["position"])
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

    from google.auth.exceptions import RefreshError

    creds = None
    if os.path.exists("token.json"):
        creds = Credentials.from_authorized_user_file("token.json", SCOPES)
    if not creds or not creds.valid:
        refreshed = False
        if creds and creds.expired and creds.refresh_token:
            try:
                creds.refresh(Request())
                refreshed = True
            except RefreshError:
                # Refresh tokens for unpublished ("Testing") OAuth apps expire after
                # 7 days, and revoked tokens fail the same way. Fall back to a fresh
                # interactive login rather than crashing.
                creds = None
        if not refreshed:
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
    spreadsheet = client.open_by_key(SPREADSHEET_ID)
    for ws in spreadsheet.worksheets():
        if ws.title.strip().lower() == WORKSHEET_TITLE.lower():
            return ws
    titles = ", ".join(repr(ws.title) for ws in spreadsheet.worksheets())
    sys.exit(f"No worksheet named {WORKSHEET_TITLE!r} found. Tabs present: {titles}")


def existing_keys(worksheet):
    """Set of (company, position) keys already in the sheet (skips header row).

    Reads whole rows so Company/Position stay aligned even when the columns have
    different numbers of trailing blank cells (e.g. dropdown-only rows below the data).
    """
    keys = set()
    for row in worksheet.get_all_values()[1:]:
        company = row[0] if len(row) > 0 else ""
        position = row[1] if len(row) > 1 else ""
        if not company and not position:
            continue
        keys.add(_key(company, position))
    return keys


def company_display(item):
    """Company name with the posting date appended, e.g. 'Meta (7/2/26)'."""
    date = item.get("date")
    return f"{item['company']} ({date})" if date else item["company"]


def to_row(item):
    # Company | Position | Date Applied | Status | Link
    # Company carries the posting date; Position and Link are filled; the rest blank.
    return [company_display(item), item["position"], "", "", item["details"]]


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
            print(f"  {company_display(it):<28} | {it['position'][:60]:<60} | {it['details']}")
        print(f"\n[dry-run] Would append {len(items)} row(s). No changes made.")
        return

    worksheet = get_worksheet()
    have = existing_keys(worksheet)
    new_items = [it for it in items if _key(it["company"], it["position"]) not in have]

    if not new_items:
        print("Nothing new to add - all fetched listings are already in the sheet.")
        return

    worksheet.append_rows([to_row(it) for it in new_items], value_input_option="USER_ENTERED")
    print(f"Appended {len(new_items)} new row(s) to the sheet "
          f"({len(items) - len(new_items)} already present, skipped).")


if __name__ == "__main__":
    main()
