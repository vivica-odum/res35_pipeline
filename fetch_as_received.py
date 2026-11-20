"""Look up each network's "as received" submissions on the ITU e-Submission site.

When an administration sends its Res 35 milestone report, the ITU posts the raw files on
    https://www.itu.int/ITU-R/space/asreceived/Publication/AsReceived
before the Bureau examines and publishes them. The Space Network List shows these milestones
as "As Received". Each submission has a page with its files:
    https://www.itu.int/ITU-R/space/asreceived/Publication/DisplayPublication/<submission id>

This script searches that list for every network in output/res35_main.xlsx and saves every
submission it finds (Res 35, Res 8, notifications, ...) to data/as_received.csv. Submissions
drop off the list once they are published in the BR IFIC, so the list only ever holds the
ones still waiting.

Usage:
    python fetch_as_received.py                 # look up every network
    python fetch_as_received.py --from-json f   # load a lookup saved from the browser instead

If a network can't be looked up this run, its rows from the previous run are kept.
"""
import argparse
import base64
import html
import json
import re
import time
from datetime import date, datetime
from pathlib import Path

import pandas as pd
import requests

HERE = Path(__file__).parent
OUT_FILE = HERE / "data" / "as_received.csv"
MAIN_WORKBOOK = HERE / "output" / "res35_main.xlsx"

BASE = "https://www.itu.int/ITU-R/space/asreceived"
TABLE = BASE + "/Publication/GetPublicationTable"
PAGE = BASE + "/Publication/DisplayPublication/{id}"
COLUMNS = ["network_name", "adm", "submission_id", "ntc_id", "reference", "received",
           "submission_type", "fetched_date"]


def submission_url(submission_id):
    return PAGE.format(id=int(submission_id))


def _param(obj):
    """The list page takes its filters as base64-encoded JSON."""
    return base64.b64encode(json.dumps(obj).encode()).decode()


def to_iso(dmy):
    try:
        return datetime.strptime(str(dmy).strip(), "%d.%m.%Y").date().isoformat()
    except ValueError:
        return ""


ROW = re.compile(r'<tr[^>]*data-submission-id="(\d+)"[^>]*>(.*?)</tr>', re.S)
CELL = re.compile(r"<td[^>]*>(.*?)</td>", re.S)


def fetch_one(name, timeout=30):
    """Every as-received submission whose satellite name is exactly `name`."""
    params = {"publication-table.ex": _param({}),
              "publication-table.p": _param({"Skip": 0, "Take": 100}),
              "publication-table.f": _param({"Search": name})}
    r = requests.get(TABLE, params=params, timeout=timeout,
                     headers={"User-Agent": "Mozilla/5.0 (research script)"})
    r.raise_for_status()
    rows = []
    for sid, body in ROW.findall(r.text):
        cells = [html.unescape(re.sub(r"<[^>]+>", "", c)).strip() for c in CELL.findall(body)]
        # Reference | NTC ID | Adm. | Network Org. | Station/Satellite Name | Long. | Date | Type | ...
        if len(cells) < 8 or cells[4] != name:
            continue
        rows.append({"network_name": name, "adm": cells[2], "submission_id": int(sid),
                     "ntc_id": cells[1], "reference": cells[0], "received": to_iso(cells[6]),
                     "submission_type": cells[7]})
    return rows


def rows_from_browser_json(path):
    """Load {name: [[submission id, ntc id, adm, dd.mm.yyyy, type], ...]}."""
    data = json.loads(Path(path).read_text())
    rows = [{"network_name": name, "adm": adm, "submission_id": int(sid), "ntc_id": ntc,
             "reference": "", "received": to_iso(rcv), "submission_type": typ}
            for name, subs in data.items() for sid, ntc, adm, rcv, typ in subs]
    return rows, list(data)


def load(path=OUT_FILE):
    if not Path(path).exists():
        return pd.DataFrame(columns=COLUMNS)
    return pd.read_csv(path, dtype={"adm": str, "ntc_id": str, "reference": str})


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawTextHelpFormatter)
    ap.add_argument("--from-json", help="lookup saved from the browser (see rows_from_browser_json)")
    args = ap.parse_args()
    old = load()

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
    new["fetched_date"] = date.today().isoformat()
    keep = old[old["network_name"].isin(failed)] if failed else old.iloc[0:0]
    if args.from_json:                                  # a partial lookup: keep the other networks
        keep = old[~old["network_name"].isin(names)]
    new = pd.concat([new, keep], ignore_index=True)
    OUT_FILE.parent.mkdir(exist_ok=True)
    new.sort_values(["network_name", "received"], ascending=[True, False]).to_csv(OUT_FILE, index=False)
    res35 = new[new["submission_type"].str.contains("Res 35", na=False)]
    print(f"Wrote {OUT_FILE}: {len(new)} submissions waiting to be published "
          f"({len(res35)} Res 35 reports) for {new['network_name'].nunique()} networks")


if __name__ == "__main__":
    main()
