"""Look up each Res 35 network in the ITU Space Explorer and save its notice IDs.

Why: the Space Explorer network page is
    https://www.itu.int/itu-r/space/apps/public/spaceexplorer/networks-explorer/
        space-stations/dashboard/non-plans/<network name>/<notice ID>
and the number at the end is an ITU notice ID (ntc_id). Every submission for a
network has its own notice ID: advance publication (API), coordination,
notification, and the Res 49 / Res 35 / Res 8 special-section submissions.
The page opens for any of them, but the one the Space Explorer itself shows for
a network is its current notification (e.g. MCSAT LEO -> 124500052, received
25.03.2024). The br6a_br6b_id_no in the Res 35 PDFs is the notification that
was current when that PDF was submitted, so for some networks it is an older
notice (MCSAT LEO M0 -> 119500392).

Which notice we link to (pick_dashboard_notice):
    1. the latest notification (Art. 11) by date of receipt; if none,
    2. the latest coordination request (Art. 9.6); if none,
    3. the latest advance publication (API); if none,
    4. the latest other notice (e.g. the Res 35 submission).

The lookup uses the same JSON endpoint the Space Explorer page calls:
    .../ep/spaceexplorer/v1/dashboard/space-stations/relatedNotices/
        non-plans/<network name>/null/relatedNoticesInformation

Usage:
    python fetch_space_explorer.py                # look up every network in output/res35_main.xlsx
    python fetch_space_explorer.py --from-json f  # load a lookup saved from the browser instead

Writes data/space_explorer_notices.csv (one row per notice). If a network can't
be fetched this run, its rows from the previous run are kept.
"""
import argparse
import json
import time
from datetime import date, datetime
from pathlib import Path
from urllib.parse import quote

import pandas as pd
import requests

HERE = Path(__file__).parent
OUT_FILE = HERE / "data" / "space_explorer_notices.csv"
MAIN_WORKBOOK = HERE / "output" / "res35_main.xlsx"

API = ("https://www.itu.int/itu-r/space/apps/ep/spaceexplorer/v1/dashboard/space-stations/"
       "relatedNotices/non-plans/{name}/null/relatedNoticesInformation")
DASHBOARD = ("https://www.itu.int/itu-r/space/apps/public/spaceexplorer/networks-explorer/"
             "space-stations/dashboard/non-plans/{name}/{ntc_id}")

CATEGORIES = {"api": "API", "coordination": "Coordination",
              "notification": "Notification", "other": "Other"}
SHORT = {"a": "API", "c": "Coordination", "n": "Notification", "o": "Other"}
COLUMNS = ["network_name", "adm", "category", "ntc_id", "date_of_receipt",
           "provision", "brific_number", "fetched_date"]


def dashboard_url(network_name, ntc_id):
    """Link to a network's Space Explorer page for one notice ID."""
    return DASHBOARD.format(name=quote(str(network_name), safe=""), ntc_id=int(ntc_id))


def to_iso(dmy):
    """'25.03.2024' -> '2024-03-25' (blank stays blank)."""
    try:
        return datetime.strptime(str(dmy), "%d.%m.%Y").date().isoformat()
    except ValueError:
        return ""


def fetch_one(name, timeout=30):
    """All notices the Space Explorer lists for one network name."""
    r = requests.get(API.format(name=quote(name, safe="")), timeout=timeout,
                     headers={"User-Agent": "Mozilla/5.0 (research script)",
                              "Accept": "application/json"})
    r.raise_for_status()
    info = r.json().get("relatedNoticesInformation") or {}
    rows = []
    for key, label in CATEGORIES.items():
        for n in info.get(key) or []:
            if n.get("isSupAndRem"):          # suppressed and removed notices
                continue
            rows.append({"network_name": name, "adm": n.get("adm"), "category": label,
                         "ntc_id": n.get("ntc_id"), "date_of_receipt": to_iso(n.get("d_rcv")),
                         "provision": n.get("prov"), "brific_number": n.get("wic_no")})
    return rows


def rows_from_browser_json(path):
    """Load {name: [[a/c/n/o, ntc_id, adm, dd.mm.yyyy, provision, suppressed, wic_no], ...]}."""
    data = json.loads(Path(path).read_text())
    rows = []
    for name, notices in data.items():
        for cat, ntc_id, adm, rcv, prov, sup, wic in notices:
            if sup:
                continue
            rows.append({"network_name": name, "adm": adm, "category": SHORT[cat],
                         "ntc_id": ntc_id, "date_of_receipt": to_iso(rcv),
                         "provision": prov, "brific_number": wic})
    return rows, list(data)


def pick_dashboard_notice(notices):
    """Choose the notice ID to link to from one network's notices (a DataFrame).

    Returns (ntc_id, category, date_of_receipt) or None if there are no notices."""
    if notices is None or notices.empty:
        return None
    for cat in ["Notification", "Coordination", "API", "Other"]:
        sub = notices[notices["category"] == cat]
        if len(sub):
            best = sub.sort_values(["date_of_receipt", "ntc_id"]).iloc[-1]
            return int(best["ntc_id"]), cat, best["date_of_receipt"]
    return None


def load_notices(path=OUT_FILE):
    if not Path(path).exists():
        return pd.DataFrame(columns=COLUMNS)
    return pd.read_csv(path, dtype={"adm": str, "provision": str})


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawTextHelpFormatter)
    ap.add_argument("--from-json", help="lookup saved from the browser (see rows_from_browser_json)")
    args = ap.parse_args()
    today = date.today().isoformat()
    old = load_notices()

    if args.from_json:
        rows, names = rows_from_browser_json(args.from_json)
        failed = []
    else:
        names = sorted(pd.read_excel(MAIN_WORKBOOK, sheet_name="networks")["network_name"].dropna().unique())
        rows, failed = [], []
        for name in names:
            try:
                rows += fetch_one(name)
            except Exception as e:                      # network error, ITU site change, ...
                print(f"  could not look up {name}: {e}")
                failed.append(name)
            time.sleep(0.5)

    new = pd.DataFrame(rows, columns=COLUMNS)
    new["fetched_date"] = today
    if failed and len(old):                             # keep last run's rows for failures
        new = pd.concat([new, old[old["network_name"].isin(failed)]], ignore_index=True)
    OUT_FILE.parent.mkdir(exist_ok=True)
    new.sort_values(["network_name", "category", "date_of_receipt"]).to_csv(OUT_FILE, index=False)

    missing = [n for n in names if n not in set(new["network_name"])]
    print(f"Wrote {OUT_FILE}: {len(new)} notices for {new['network_name'].nunique()} networks")
    if missing:
        print(f"  not found in the Space Explorer: {', '.join(missing)}")


if __name__ == "__main__":
    main()
