"""
fetch_snl.py
============
Gets the Resolution 35 Space Network List (SNL) and turns it into two tables:

    networks    one row per network (filing) on the SNL page
    milestones  four rows per network (M0, M1, M2, M3) with deadline, status
                and the link to that milestone's PDF (when one is published)

Where the data comes from
-------------------------
The SNL web page does not load its table from a separate data feed. The whole
list is written inside the page's JavaScript file:
    https://www.itu.int/net/ITU-R/space/res35/build/bundle.js
Each network is a JavaScript object like
    {sat_name:"STEAM-1",adm:"NOR",ntwk_org:"",M:"27.06.2021",M0:"Met",...}
We find those objects, convert them to JSON, and load them.

bundle.js has MORE than the "Download" CSV: it also has the BR IFIC number
(wic_no_*), special section number (ssn_no_*) and revision (ssn_rev_*) for
each milestone, which is what lets us build every PDF link:

    .../documents/RES35/{wic_no}/RES35/RES35{ssn_no:04d}{ssn_rev}{ssn_rev_no}.pdf
    e.g. M0  -> .../RES35/3005/RES35/RES350016.pdf
         M1  -> .../RES35/3008/RES35/RES350016M1.pdf

Fallback: read_snl_csv() reads the site's "Download" CSV instead. It has the
same rows but no PDF links.
"""

import json
import re
from datetime import date, datetime
from pathlib import Path

import pandas as pd
import requests

BUNDLE_URL = "https://www.itu.int/net/ITU-R/space/res35/build/bundle.js"
PDF_BASE = "https://www.itu.int/net/ITU-R/space/documents/RES35"
MILESTONES = ["M0", "M1", "M2", "M3"]
YEARS_AFTER_CLOCK_START = {"M0": 0, "M1": 2, "M2": 5, "M3": 7}
DATE_RE = re.compile(r"^\d{2}\.\d{2}\.\d{4}$")


# ---------------------------------------------------------------------------
# 1. Getting the raw records
# ---------------------------------------------------------------------------

def download_bundle(url=BUNDLE_URL, timeout=60):
    """Download bundle.js and return its text."""
    response = requests.get(url, timeout=timeout,
                            headers={"User-Agent": "Mozilla/5.0 (research script)"})
    response.raise_for_status()
    return response.text


def js_object_to_json(js):
    """
    Turn a simple JavaScript object literal into JSON text.
    JavaScript allows unquoted keys (sat_name:"X"); JSON needs "sat_name":"X".
    Minified code may also write undefined as 'void 0' and true/false as !0/!1.
    """
    out = re.sub(r'([{,]\s*)([A-Za-z_$][\w$]*)\s*:', r'\1"\2":', js)
    out = re.sub(r':\s*void 0', ':null', out)
    out = re.sub(r':\s*!0', ':true', out)
    out = re.sub(r':\s*!1', ':false', out)
    return out


def extract_records(js_text):
    """
    Find every {sat_name:"...", ...} data record in bundle.js and return a list of dicts.

    bundle.js also contains program code that mentions sat_name, e.g.
    {sat_name:i,adm:o}. Those are skipped: a data record always has the
    network name written out in quotes right after sat_name.
    Raises an error if no records are found (the ITU changed the page).
    """
    objects = re.findall(r'\{\s*sat_name\s*:\s*"[^{}]*\}', js_text)
    records, skipped, seen = [], [], set()
    for obj in objects:
        try:
            rec = json.loads(js_object_to_json(obj))
        except json.JSONDecodeError:
            skipped.append(obj[:120])
            continue
        # bundle.js also holds the table's column headings in the same shape
        # ({sat_name:"Satellite Name", M:"7 year limit / 01.01.2021", ...}).
        # A real network always has a date as its clock start, so skip anything else.
        if not DATE_RE.match(str(rec.get("M", ""))):
            skipped.append(obj[:120])
            continue
        fingerprint = json.dumps(rec, sort_keys=True)
        if fingerprint in seen:          # the exact same record written twice
            continue
        seen.add(fingerprint)
        records.append(rec)

    if not records:
        raise ValueError("No network records found in bundle.js. "
                         "The ITU may have changed how the page stores its data.")
    if skipped:
        print(f"  note: skipped {len(skipped)} object(s) that are not networks "
              f"(e.g. the column headings): {skipped[0][:60]}...")
    print(f"  found {len(records)} network records in bundle.js")
    return records


def read_snl_csv(path):
    """
    Fallback: read the CSV from the SNL page's Download button and rename its
    columns to match bundle.js. No PDF links (those fields don't exist in it).
    """
    df = pd.read_csv(path, skiprows=1, dtype=str, keep_default_na=False)
    df = df.rename(columns={
        "Satellite Name": "sat_name", "ADM": "adm", "IGSO": "ntwk_org",
        "7 year limit / 01.01.2021": "M", "Resolves 19": "POST",
        "No. of notified space stations": "nbr_notif",
        "No. of deployed space stations": "nbr_deploy",
    })
    for col in ["nbr_notif", "nbr_deploy"]:
        df[col] = pd.to_numeric(df[col], errors="coerce")
    return df.to_dict("records")


# ---------------------------------------------------------------------------
# 2. Building the networks and milestones tables
# ---------------------------------------------------------------------------

def to_date(text):
    if text and DATE_RE.match(str(text)):
        return datetime.strptime(text, "%d.%m.%Y").date()
    return None


COUNTRY_FILE = Path(__file__).parent / "itu_country_codes.csv"


def load_country_names(path=COUNTRY_FILE):
    """ITU symbol -> short English name, e.g. 'D' -> 'Germany', 'G' -> 'United Kingdom'."""
    df = pd.read_csv(path, comment="#", dtype=str, keep_default_na=False)
    return dict(zip(df["code"], df["country"]))


def country_name(code, names):
    """Translate one ITU symbol; unknown codes are labelled so they get noticed."""
    if code in (None, ""):
        return ""
    return names.get(code, f"unknown code: {code}")


def to_int(value):
    """4408, '4408' or 4408.0 -> 4408; '' or None -> None."""
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return None


def add_years(d, years):
    """Add whole years to a date (29 Feb -> 28 Feb if needed)."""
    try:
        return d.replace(year=d.year + years)
    except ValueError:
        return d.replace(year=d.year + years, day=28)


def build_pdf_url(wic_no, ssn_no, rev=None, rev_no=None):
    """Build a milestone PDF link from bundle.js fields (None if unpublished)."""
    if wic_no in (None, "") or ssn_no in (None, ""):
        return None
    # The suffix counts republications of the special section (MOD-1 -> M1),
    # not the milestone: SLEIPNER-1's first PDF is at M3 and has no suffix.
    suffix = f"{rev}{int(rev_no)}" if rev and rev_no not in (None, "") else ""
    return f"{PDF_BASE}/{int(wic_no)}/RES35/RES35{int(ssn_no):04d}{suffix}.pdf"


def network_key(rec):
    """Name + ADM is NOT unique (L5, HIBLEO-2FL2, USASAT-NGSO-9C appear twice),
    so the key also includes the clock-start date."""
    return f"{rec['sat_name']} | {rec['adm']} | {rec['M']}"


def clean_status(raw, deadline, later_raw_values, today):
    """
    Turn what the page shows into one value from a fixed list.
    raw               what the SNL page shows for this milestone
    later_raw_values  what it shows for the LATER milestones of the same network
    """
    raw = (raw or "").strip()
    if raw == "":
        # Blank, but a later milestone is Met -> system was fully deployed early
        if any(v.strip().lower() == "met" for v in later_raw_values if v):
            return "Skipped (completed early)"
        return "Blank"
    if DATE_RE.match(raw):
        return "Pending" if deadline and deadline >= today else "Deadline passed, no status shown"
    known = {"met": "Met", "not met": "Not Met", "partially met": "Partially Met",
             "as received": "As Received",
             "not met, resolves 12 applies": "Not Met, resolves 12 applies"}
    return known.get(raw.lower(), f"Other: {raw}")


def build_tables(records, today=None):
    """Return (networks, milestones) DataFrames from SNL records."""
    today = today or date.today()
    names = load_country_names()
    networks, milestones = [], []

    for rec in records:
        key = network_key(rec)
        clock_start = to_date(rec.get("M"))
        raw = {m: (rec.get(m) or "") for m in MILESTONES}

        rows = []
        for i, m in enumerate(MILESTONES):
            deadline = add_years(clock_start, YEARS_AFTER_CLOCK_START[m]) if clock_start else None
            url = build_pdf_url(rec.get(f"wic_no_{m}"), rec.get(f"ssn_no_{m}"),
                                rec.get(f"ssn_rev_{m}"), rec.get(f"ssn_rev_no_{m}"))
            rows.append({
                "network_key": key,
                "network_name": rec["sat_name"],
                "adm": rec["adm"],
                "milestone": m,
                "deadline": deadline,
                "status_raw": raw[m],
                "status_clean": clean_status(raw[m], deadline,
                                             [raw[x] for x in MILESTONES[i + 1:]], today),
                "brific_number": rec.get(f"wic_no_{m}"),
                "special_section_no": rec.get(f"ssn_no_{m}"),
                "revision_type": rec.get(f"ssn_rev_{m}"),
                "revision_no": rec.get(f"ssn_rev_no_{m}"),
                "pdf_url": url,
                "pdf_id": url.rsplit("/", 1)[-1][:-4] if url else None,
            })
        milestones.extend(rows)

        cleaned = [r["status_clean"] for r in rows]
        reached = [r["milestone"] for r in rows
                   if r["status_clean"] in ("Met", "Partially Met")]
        notified, deployed = to_int(rec.get("nbr_notif")), to_int(rec.get("nbr_deploy"))
        networks.append({
            "network_key": key,
            "network_name": rec["sat_name"],
            "adm": rec["adm"],
            "adm_country": country_name(rec["adm"], names),
            "igso": rec.get("ntwk_org") or "",
            "clock_start": clock_start,
            "resolves_19": rec.get("POST") or "",
            "notified": notified,
            "deployed": deployed,
            "deployed_pct": round(100 * deployed / notified, 1) if notified and deployed is not None else None,
            "deployed_exceeds_notified": bool(notified and deployed and deployed > notified),
            "completion_type": ("early" if "Skipped (completed early)" in cleaned
                                else "staged" if reached else "not started"),
            "highest_milestone_met": reached[-1] if reached else None,
            "new_sat_name": rec.get("new_sat_name"),
            "type": rec.get("type"),
        })

    return pd.DataFrame(networks), pd.DataFrame(milestones)


# ---------------------------------------------------------------------------
# 3. Change log: compare this run with the previous snapshot
# ---------------------------------------------------------------------------

def compare_snapshots(old_networks, old_milestones, new_networks, new_milestones, run_date):
    """Return a DataFrame of changes between two snapshots (empty if none)."""
    changes = []

    def add(key, what, old, new):
        changes.append({"run_date": run_date, "network_key": key,
                        "change": what, "old_value": old, "new_value": new})

    old_keys, new_keys = set(old_networks["network_key"]), set(new_networks["network_key"])
    for key in sorted(new_keys - old_keys):
        add(key, "network added to SNL", None, None)
    for key in sorted(old_keys - new_keys):
        add(key, "network removed from SNL", None, None)

    on = old_networks.set_index("network_key")
    nn = new_networks.set_index("network_key")
    for key in sorted(old_keys & new_keys):
        for col in ["notified", "deployed", "resolves_19", "igso"]:
            a, b = on.at[key, col], nn.at[key, col]
            if str(a) != str(b):
                add(key, f"{col} changed", a, b)

    om = old_milestones.set_index(["network_key", "milestone"])
    nm = new_milestones.set_index(["network_key", "milestone"])
    for idx in sorted(set(om.index) & set(nm.index)):
        for col in ["status_raw", "pdf_url"]:
            a, b = om.at[idx, col], nm.at[idx, col]
            if str(a) != str(b) and not (pd.isna(a) and pd.isna(b)):
                add(idx[0], f"{idx[1]} {col} changed", a, b)
    return pd.DataFrame(changes, columns=["run_date", "network_key", "change",
                                          "old_value", "new_value"])


if __name__ == "__main__":
    text = download_bundle()
    recs = extract_records(text)
    nets, ms = build_tables(recs)
    print(f"{len(nets)} networks, {ms['pdf_url'].notna().sum()} published milestone PDFs")
    print(nets[["network_name", "adm", "clock_start", "notified", "deployed"]].to_string())
