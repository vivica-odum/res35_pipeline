"""Daily download of tracked (actual) orbits from CelesTrak, matched to the Res 35 satellites.

CelesTrak (https://celestrak.org) publishes the latest orbit of every tracked satellite with
no login. This script runs once a day (GitHub Actions, "Daily CelesTrak orbits"):

  1. downloads CelesTrak's current orbits (GP data for active satellites, plus the
     operator-based "SupGP" orbits where they exist) and the satellite catalogue (SATCAT),
  2. matches each satellite named in the Res 35 milestone PDFs to its NORAD catalog number,
  3. saves that day's orbit of every matched satellite in data/celestrak/history/,
  4. writes output/res35_tracked_orbits.xlsx: tracked vs notified vs reported orbit.

Usage:
    python fetch_celestrak.py                 # download, match, save history, write workbook
    python fetch_celestrak.py --no-download   # reuse the files already in data/celestrak/raw/
    python fetch_celestrak.py --download-only # only download (used for testing)

CelesTrak usage policy (https://celestrak.org/usage-policy.php): use the CSV formats, don't
query more often than needed, and stop immediately on any answer other than HTTP 200 and let a
person look into it. This script downloads each file once per run and stops on the first
non-200 answer (the GitHub run then fails and GitHub emails the repository owner).
"""
import argparse
import sys
import time
from pathlib import Path

import requests

HERE = Path(__file__).parent
CT = HERE / "data" / "celestrak"
RAW = CT / "raw"                     # today's downloads (not kept in the repository)

GP = "https://celestrak.org/NORAD/elements/gp.php"
SUP = "https://celestrak.org/NORAD/elements/supplemental/sup-gp.php"
# (file name, URL, required). Optional files may legitimately hold no data.
DOWNLOADS = [
    ("gp_active.csv", f"{GP}?GROUP=active&FORMAT=csv", True),
    ("satcat.csv", "https://celestrak.org/pub/satcat.csv", True),
    ("supgp_starlink.csv", f"{SUP}?FILE=starlink&FORMAT=csv", False),
    ("supgp_oneweb.csv", f"{SUP}?FILE=oneweb&FORMAT=csv", False),
    ("supgp_kuiper.csv", f"{SUP}?SOURCE=Kuiper-E&FORMAT=csv", False),
    ("supgp_iridium.csv", f"{SUP}?SOURCE=Iridium-E&FORMAT=csv", False),
    ("supgp_ses.csv", f"{SUP}?SOURCE=SES-E&FORMAT=csv", False),
]
USER_AGENT = ("res35_pipeline/1.0 (Georgia Tech Engineering Space Policy Lab research; "
              "https://github.com/vivica-odum/res35_pipeline)")


class StopQuerying(SystemExit):
    pass


def download(raw_dir=RAW):
    raw_dir.mkdir(parents=True, exist_ok=True)
    s = requests.Session()
    s.headers["User-Agent"] = USER_AGENT
    for name, url, required in DOWNLOADS:
        r = s.get(url, timeout=180)
        if r.status_code != 200:
            # CelesTrak asks automated software to stop at once on any non-200 answer.
            raise StopQuerying(
                f"CelesTrak answered HTTP {r.status_code} for {url}.\n"
                "Stopped all further queries, as CelesTrak's usage policy asks. Look at "
                "https://celestrak.org/usage-policy.php and the response before running again.")
        text = r.text
        has_data = text.lstrip().upper().startswith(("OBJECT_NAME", "OBJECT_ID", "INTLDES", "\"OBJECT"))
        if not has_data:
            msg = text.strip().splitlines()[0][:200] if text.strip() else "(empty)"
            if required:
                raise StopQuerying(f"CelesTrak returned no data for {url}: {msg}")
            print(f"  {name}: no data ({msg})")
            (raw_dir / name).write_text("", encoding="utf-8")
        else:
            (raw_dir / name).write_text(text, encoding="utf-8")
            print(f"  {name}: {len(text.splitlines()) - 1:,} rows")
        time.sleep(3)


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawTextHelpFormatter)
    ap.add_argument("--no-download", action="store_true")
    ap.add_argument("--download-only", action="store_true")
    args = ap.parse_args()
    if not args.no_download:
        print("Downloading from CelesTrak")
        download()
    if args.download_only:
        return
    import celestrak_match            # matching + outputs
    celestrak_match.run()


if __name__ == "__main__":
    sys.exit(main())
