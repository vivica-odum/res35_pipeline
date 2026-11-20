"""Save a copy of the ESPL glossary (the lab's published Google Sheet) for the dashboard.

The dashboard reads the glossary live when it opens, the same way the SBI model does, so new
definitions show up without a rebuild. This copy is the fallback for when the live sheet
can't be reached (for example inside a claude.ai artifact, which blocks outside requests).

Rows are matched to dashboard words by the glossary's Term or Acronym column, ignoring case,
spaces, underscores and hyphens: a Term "Total notified" or an Acronym "total_notified" both
match the total_notified column. Only rows whose Project is PROJECT are used.

Usage:
    python fetch_espl_glossary.py    # writes data/espl_glossary.csv
"""
from pathlib import Path

import requests

HERE = Path(__file__).parent
OUT_FILE = HERE / "data" / "espl_glossary.csv"

# The lab glossary, as published to the web (File > Share > Publish to web > CSV).
CSV_URL = ("https://docs.google.com/spreadsheets/d/e/2PACX-1vQ4UkGvjUAItDaS6sdxJDEqu6JoAI95k3EkCKf9QXAIA"
           "mnzZXEocTeMwFo1U0CldZNkrIHIelx6288p/pub?output=csv")
# The glossary's own page. Adding ?term=<Term> opens it on that term.
PAGE_URL = ("https://script.google.com/macros/s/AKfycbx2N0uVxtBO0JtLjWch8s-EnTQ8jlmhX-E6qdvxLXXwh6QpjrCyz"
            "GjDi4ZSgW5moBNw_g/exec")
# The glossary's Project value for this dashboard's terms.
PROJECT = "ITU Resolution 35"


def main():
    r = requests.get(CSV_URL, timeout=60)
    r.raise_for_status()
    r.encoding = "utf-8"
    OUT_FILE.parent.mkdir(exist_ok=True)
    OUT_FILE.write_text(r.text, encoding="utf-8")
    ours = sum(1 for line in r.text.splitlines() if f",{PROJECT}," in line)
    print(f"Wrote {OUT_FILE} ({len(r.text.splitlines()) - 1} rows, about {ours} for {PROJECT})")


if __name__ == "__main__":
    main()
