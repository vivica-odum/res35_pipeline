"""
run_pipeline.py
===============
Runs the whole Res 35 pipeline and writes the Excel workbooks.

    1. Get the Space Network List (SNL)        -> networks, milestones
    2. Save a dated snapshot + update the change log
    3. Download any milestone PDFs we don't have yet
    4. Parse every PDF                         -> pdfs, planes, launches, bands, satellites
    5. Build the views and write:
         output/res35_main.xlsx              (compliance + orbits: the main deliverable)
         output/res35_frequency_addon.xlsx   (beam groups + satellite-band links)

How to run (from this folder):
    python run_pipeline.py                     # normal run: download everything new
    python run_pipeline.py --no-download       # only use PDFs already in data/pdfs
    python run_pipeline.py --bundle bundle.js  # use a bundle.js you saved yourself
    python run_pipeline.py --csv RES35_Space_Network_List.csv
                                               # fallback: the site's Download CSV
                                               # (no PDF links; local PDFs are matched by name)
"""

import argparse
import hashlib
import pickle
import time
from datetime import date
from pathlib import Path

import pandas as pd
import requests
from openpyxl.styles import Alignment, Font, PatternFill
from openpyxl.utils import get_column_letter

import fetch_snl
from parse_pdf import parse_pdf

HERE = Path(__file__).parent
DATA = HERE / "data"
PDF_DIR = DATA / "pdfs"            # downloaded PDFs (never edited)
PARSED_DIR = DATA / "parsed"       # cached parse results, so PDFs are read once
SNAP_DIR = DATA / "snapshots"      # one networks + milestones CSV per run
RAW_DIR = DATA / "raw"             # dated copies of bundle.js
OUT_DIR = HERE / "output"
CHANGE_LOG = DATA / "change_log.csv"
PARSER_CODE = (HERE / "parse_pdf.py").read_bytes()   # a parser fix re-parses every PDF


# ---------------------------------------------------------------------------
# Steps 1-2: SNL, snapshot, change log
# ---------------------------------------------------------------------------

def get_records(args, today):
    if args.csv:
        print(f"Reading SNL from CSV {args.csv} (no PDF links in this source)")
        return fetch_snl.read_snl_csv(args.csv), "csv"
    if args.bundle:
        print(f"Reading SNL from saved {args.bundle}")
        text = Path(args.bundle).read_text(encoding="utf-8")
    else:
        print(f"Downloading {fetch_snl.BUNDLE_URL}")
        text = fetch_snl.download_bundle()
        RAW_DIR.mkdir(parents=True, exist_ok=True)
        (RAW_DIR / f"bundle_{today}.js").write_text(text, encoding="utf-8")
    return fetch_snl.extract_records(text), "bundle.js"


def snapshot_and_log(networks, milestones, today):
    """Save today's snapshot and append any changes since the previous one."""
    SNAP_DIR.mkdir(parents=True, exist_ok=True)
    previous = sorted(p for p in SNAP_DIR.glob("*_networks.csv")
                      if not p.name.startswith(str(today)))
    networks.to_csv(SNAP_DIR / f"{today}_networks.csv", index=False)
    milestones.to_csv(SNAP_DIR / f"{today}_milestones.csv", index=False)

    read = lambda p: pd.read_csv(p, dtype=str, keep_default_na=False)
    if previous:
        last = previous[-1].name.replace("_networks.csv", "")
        changes = fetch_snl.compare_snapshots(
            read(SNAP_DIR / f"{last}_networks.csv"), read(SNAP_DIR / f"{last}_milestones.csv"),
            read(SNAP_DIR / f"{today}_networks.csv"), read(SNAP_DIR / f"{today}_milestones.csv"),
            run_date=str(today))
        print(f"{len(changes)} changes since snapshot {last}")
        if len(changes):
            changes.to_csv(CHANGE_LOG, mode="a", index=False, header=not CHANGE_LOG.exists())
    else:
        print("First snapshot saved; changes will be logged from the next run.")
    return read(CHANGE_LOG) if CHANGE_LOG.exists() else pd.DataFrame(
        columns=["run_date", "network_key", "change", "old_value", "new_value"])


# ---------------------------------------------------------------------------
# Steps 3-4: download and parse PDFs
# ---------------------------------------------------------------------------

def download_new_pdfs(milestones):
    PDF_DIR.mkdir(parents=True, exist_ok=True)
    todo = milestones.dropna(subset=["pdf_url"])
    for _, row in todo.iterrows():
        target = PDF_DIR / f"{row['pdf_id']}.pdf"
        if target.exists():
            continue
        print(f"  downloading {row['pdf_url']}")
        try:
            r = requests.get(row["pdf_url"], timeout=120,
                             headers={"User-Agent": "Mozilla/5.0 (research script)"})
            r.raise_for_status()
            target.write_bytes(r.content)
            time.sleep(1)                     # be polite to the ITU server
        except requests.RequestException as err:
            print(f"  !! could not download {row['pdf_url']}: {err}")


def parse_all_pdfs(url_by_id):
    """Parse every PDF in data/pdfs (re-using cached results when unchanged)."""
    PARSED_DIR.mkdir(parents=True, exist_ok=True)
    results = []
    for pdf in sorted(PDF_DIR.glob("*.pdf")):
        cache = PARSED_DIR / f"{pdf.stem}.pkl"
        # Fingerprint of the PDF AND the parser code: re-parse if either changed
        stamp = hashlib.sha256(pdf.read_bytes() + PARSER_CODE).hexdigest()
        if cache.exists():
            try:
                cached = pickle.loads(cache.read_bytes())
                if cached.get("stamp") == stamp:
                    results.append(cached["result"])
                    continue
            except Exception:
                pass                          # unreadable cache: just parse again
        print(f"  parsing {pdf.name}")
        result = parse_pdf(pdf, pdf_url=url_by_id.get(pdf.stem))
        cache.write_bytes(pickle.dumps({"stamp": stamp, "result": result}))
        results.append(result)
    return results


def link_pdfs_to_milestones(pdfs, milestones):
    """
    Each PDF row gets the network_key of the milestone it belongs to.
    Normal case: the milestone's pdf_id (built from bundle.js) matches.
    Fallback (CSV mode): match on network name + ADM + milestone, but only when
    exactly one milestone fits (L5 etc. appear twice and stay unlinked).
    """
    pdfs = pdfs.copy()
    pdfs["network_key"], pdfs["link_method"] = None, None
    # One PDF can belong to two SNL rows: HIBLEO-2FL2 is listed twice (two clock
    # starts) and both rows point to RES350011. Those PDFs list every row's key.
    keys_by_id = milestones.dropna(subset=["pdf_id"]).groupby("pdf_id")["network_key"] \
        .agg(lambda s: "; ".join(sorted(set(s))))
    rows_by_id = milestones.dropna(subset=["pdf_id"]).groupby("pdf_id")["network_key"].nunique()
    for i, row in pdfs.iterrows():
        if row["pdf_id"] in keys_by_id.index:
            shared = rows_by_id[row["pdf_id"]]
            pdfs.at[i, "network_key"] = keys_by_id[row["pdf_id"]]
            pdfs.at[i, "link_method"] = "pdf link" + (f" (shared by {shared} SNL rows)" if shared > 1 else "")
            continue
        hits = milestones[(milestones.network_name == row["network_name"]) &
                          (milestones.adm == row["adm"]) &
                          (milestones.milestone == row["milestone"])]
        if len(hits) == 1:
            j = hits.index[0]
            pdfs.at[i, "network_key"], pdfs.at[i, "link_method"] = hits.at[j, "network_key"], "matched by name"
            milestones.at[j, "pdf_id"] = row["pdf_id"]
            if pd.isna(milestones.at[j, "pdf_url"]) and row.get("pdf_url"):
                milestones.at[j, "pdf_url"] = row["pdf_url"]
        else:
            pdfs.at[i, "link_method"] = f"UNLINKED ({len(hits)} possible milestones)"
    return pdfs, milestones


# ---------------------------------------------------------------------------
# Step 5: views
# ---------------------------------------------------------------------------

def build_overview(networks, milestones, pdfs):
    status = milestones.pivot(index="network_key", columns="milestone", values="status_clean")
    links = milestones.dropna(subset=["pdf_url"]).sort_values("milestone") \
        .groupby("network_key")["pdf_url"].last().rename("latest_pdf_url")
    view = networks.set_index("network_key").join(status).join(links)
    if "bureau_notes" in pdfs:
        # A shared PDF lists several network keys ("A; B"): give its notes to each of them
        notes = pdfs.dropna(subset=["bureau_notes", "network_key"]).assign(
            network_key=lambda d: d["network_key"].str.split("; ")).explode("network_key")
        view = view.join(notes.groupby("network_key")["bureau_notes"]
                         .agg(lambda s: " | ".join(dict.fromkeys(s))))
    view = view.reset_index()
    if "bureau_notes" not in view:
        view["bureau_notes"] = None
    cols = ["network_name", "adm", "adm_country", "igso", "clock_start", "M0", "M1", "M2", "M3",
            "resolves_19", "notified", "deployed", "deployed_pct", "completion_type",
            "highest_milestone_met", "bureau_notes", "latest_pdf_url", "network_key"]
    return view[cols].sort_values(["network_name", "clock_start"])


def latest_pdf_per_network(pdfs):
    linked = pdfs.dropna(subset=["network_key"])
    return linked.sort_values(["network_key", "milestone", "mod_no"]) \
        .groupby("network_key").tail(1)["pdf_id"]


def arrange_satellites(satellites, pdfs):
    """Satellite name first, then the PDF it was reported in and that PDF's
    BR IFIC date and date the Bureau received the information."""
    if satellites.empty:
        return satellites
    dates = [c for c in ["brific_date", "received_date"] if c in pdfs]
    s = satellites.merge(pdfs[["pdf_id"] + dates], on="pdf_id", how="left")
    first = ["satellite_name", "pdf_id"] + dates
    return s[first + [c for c in s.columns if c not in first]]


def build_satellite_view(pdfs, satellites, launches, planes):
    """One row per satellite per PDF, with its launch and its FILED plane next to it,
    plus the difference between reported and filed orbit (for the Res 8 work)."""
    if satellites.empty:
        return satellites
    sv = satellites.merge(pdfs[["pdf_id", "network_key", "network_name", "adm", "milestone"]],
                          on="pdf_id", how="left")
    sv = sv.merge(launches.drop(columns=["parse_warning"]).rename(columns={
                      "country": "launch_country", "country_name": "launch_country_name",
                      "provider": "launch_provider",
                      "vehicle": "launch_vehicle", "facility": "launch_facility",
                      "longitude": "launch_longitude", "latitude": "launch_latitude"}),
                  on=["pdf_id", "launch_id"], how="left")
    filed = planes[["pdf_id", "plane_id", "inclination_deg", "apogee_km", "perigee_km"]] \
        .rename(columns={"inclination_deg": "filed_inclination_deg",
                         "apogee_km": "filed_apogee_km", "perigee_km": "filed_perigee_km"})
    sv = sv.merge(filed, on=["pdf_id", "plane_id"], how="left")
    sv["apogee_minus_filed_km"] = (sv["apogee_km"] - sv["filed_apogee_km"]).round(2)
    sv["perigee_minus_filed_km"] = (sv["perigee_km"] - sv["filed_perigee_km"]).round(2)
    sv["inclination_minus_filed_deg"] = (sv["inclination_deg"] - sv["filed_inclination_deg"]).round(3)
    sv["in_latest_pdf"] = sv["pdf_id"].isin(set(latest_pdf_per_network(pdfs)))
    first = ["satellite_name", "network_name", "adm", "milestone", "brific_date", "received_date",
             "itu_sat_id", "launch_date", "launch_provider", "launch_vehicle", "launch_facility",
             "launch_country", "launch_country_name", "plane_id", "apogee_km", "filed_apogee_km",
             "apogee_minus_filed_km", "perigee_km", "filed_perigee_km",
             "perigee_minus_filed_km", "inclination_deg", "filed_inclination_deg",
             "inclination_minus_filed_deg", "arg_perigee_deg", "band_ids", "in_latest_pdf"]
    return sv[first + [c for c in sv.columns if c not in first]]


def build_shared_satellites(sat_view):
    """Satellite names that appear under MORE THAN ONE network (latest PDFs only)."""
    if sat_view.empty:
        return pd.DataFrame(columns=["satellite_name", "networks", "count"])
    latest = sat_view[sat_view["in_latest_pdf"]]
    g = latest.groupby("satellite_name")["network_name"].agg(lambda s: sorted(set(s)))
    shared = g[g.apply(len) > 1]
    return pd.DataFrame({"satellite_name": shared.index,
                         "networks": shared.apply(", ".join).values,
                         "count": shared.apply(len).values})


def split_band_links(satellites):
    """'1,2,3' in one cell -> three rows (long format), for the frequency add-on."""
    if satellites.empty:
        return satellites
    s = satellites[["pdf_id", "launch_id", "station_id", "satellite_name", "band_ids"]].copy()
    s["band_id"] = s["band_ids"].str.split(",")
    s = s.explode("band_id").drop(columns="band_ids")
    s["band_id"] = s["band_id"].astype(int)
    return s


# ---------------------------------------------------------------------------
# Writing Excel
# ---------------------------------------------------------------------------

README_ROWS = [
    ("What this is", "Data from the ITU Resolution 35 Space Network List (SNL) and its milestone PDFs, "
                     "rebuilt automatically by run_pipeline.py. Do not edit sheets by hand: they are "
                     "overwritten on every run."),
    ("Last run", None),
    ("SNL source", None),
    ("", ""),
    ("SHEETS", ""),
    ("Overview", "One row per network: the SNL page, cleaned. Start here."),
    ("Satellites view", "One row per deployed satellite per PDF, with launch and filed orbital plane "
                        "joined in, plus reported-minus-filed orbit differences. "
                        "Filter in_latest_pdf = TRUE for one row per satellite per network."),
    ("Shared satellites", "Satellite names listed under more than one network (latest PDFs)."),
    ("Change log", "Every change detected between runs (status changes, new PDFs, counts)."),
    ("Checks", "Parsing problems per PDF. Empty problems = the PDF's totals all add up."),
    ("networks ... satellites", "The core tables the views are built from (one fact stored once)."),
    ("", ""),
    ("KEYS", ""),
    ("network_key", "network name | ADM | clock start. Name + ADM alone is not unique "
                    "(e.g. L5 and HIBLEO-2FL2 appear twice)."),
    ("pdf_id", "PDF file name without .pdf, e.g. RES350016M1. Every PDF-derived row carries it."),
    ("Satellite row", "pdf_id + launch_id + station_id (station ids restart at 1 for each launch)."),
    ("", ""),
    ("COLUMNS", ""),
    ("clock_start", "'7 year limit / 01.01.2021' on the SNL page: end of the 7-year bringing-into-use "
                    "period, or 1 Jan 2021 if earlier. M0/M1/M2/M3 deadlines are +0/2/5/7 years."),
    ("adm / adm_country", "ITU symbol of the responsible administration and its English name. ITU symbols "
                          "are not ISO codes: D = Germany, G = United Kingdom, F = France, HOL = Netherlands, "
                          "E = Spain. Translations come from itu_country_codes.csv."),
    ("igso", "Intergovernmental satellite organization that filed (field ntwk_org), usually empty."),
    ("resolves_19", "SNL 'Resolves 19' column (field POST): post-milestone deployment reports under "
                    "resolves 19 of Resolution 35. The first are due 1 Feb 2032, so it is empty for now."),
    ("identification_number (pdfs)", "'Identification number' exactly as printed on page 1 of the PDF "
                                     "(e.g. 121935017 / 123535017). The oldest PDFs show one number."),
    ("brific_date / received_date", "Date of the BR IFIC that published the PDF, and the date the Bureau "
                                    "received the information. Repeated on the satellites sheets so each "
                                    "satellite row shows when it was reported."),
    ("br6a_br6b_id_no (planes, groups)", "The tables' own 'BR6a/BR6b Id no.' column (newer PDFs: 'BR6a Id. no.'), e.g. 121500130. A different number "
                              "from the page-1 IDs: it points to the filing whose orbits are listed, and "
                              "newer PDFs' itu_sat_id values start with it. Likely the ITU notice ID "
                              "(to be confirmed). Can change between milestones."),
    ("min_to_meet (bands)", "Bureau's minimum deployed stations for the milestone; 10% / 50% / 100% "
                            "of notified, rounded DOWN (STEAM-1 M1: 4408 -> 440)."),
    ("bureau_notes", "English text of any note the Bureau printed in a PDF. Two kinds so far: a Radio "
                     "Regulations Board waiver under resolves 12 (3ECOM-1 and 3ECOM-3 at M1; the band table "
                     "says 'See Note'), and L5's note that a publication covered only some frequency bands. "
                     "Shown on the pdfs, Checks and Overview sheets."),
    ("note (bands)", "'See Note' exactly as printed in the band table; the note itself is in bureau_notes."),
    ("Dates", "All dates converted from the ITU's day.month.year to real dates."),
    ("", ""),
    ("STATUS LABELS (status_clean)", ""),
    ("Met / Not Met / Partially Met", "The Bureau's finding. Partially Met = some frequency bands met "
                                      "the milestone and some did not (see bands sheet)."),
    ("Not Met, resolves 12 applies", "As shown on the SNL page."),
    ("As Received", "Submitted to the Bureau but not yet published; no regulatory status, no PDF yet."),
    ("Pending", "The page shows a deadline that is still in the future."),
    ("Deadline passed, no status shown", "The page still shows the deadline date although it has passed."),
    ("Skipped (completed early)", "Blank on the page because a later milestone was already Met "
                                  "(system fully deployed early). Not an ITU finding."),
    ("Blank", "Blank on the page for any other reason."),
]


def write_workbook(path, sheets, readme_rows=None):
    path.parent.mkdir(parents=True, exist_ok=True)
    with pd.ExcelWriter(path, engine="openpyxl") as xl:
        if readme_rows:
            pd.DataFrame(readme_rows, columns=["Item", "Description"]).to_excel(
                xl, sheet_name="README", index=False)
        for name, df in sheets.items():
            df.to_excel(xl, sheet_name=name[:31], index=False)

        header_fill = PatternFill("solid", fgColor="DDE3EA")
        for ws in xl.book.worksheets:
            ws.freeze_panes = "A2"
            for cell in ws[1]:
                cell.font, cell.fill = Font(bold=True), header_fill
            if ws.title == "README":
                ws.column_dimensions["A"].width = 32
                ws.column_dimensions["B"].width = 110
                for row in ws.iter_rows(min_row=2):
                    row[1].alignment = Alignment(wrap_text=True, vertical="top")
                    if row[0].value and row[0].value.isupper():
                        row[0].font = Font(bold=True)
                continue
            if ws.max_row > 1:
                ws.auto_filter.ref = ws.dimensions
            for col_idx, cell in enumerate(ws[1], start=1):
                letter = get_column_letter(col_idx)
                sample = [len(str(c.value)) for c in ws[letter][:200] if c.value is not None]
                ws.column_dimensions[letter].width = min(max(sample + [8]) + 2, 60)
                if cell.value and str(cell.value).endswith("pdf_url"):
                    for c in ws[letter][1:]:
                        if c.value:
                            c.hyperlink, c.style = c.value, "Hyperlink"
                if cell.value and any(k in str(cell.value) for k in ("date", "deadline", "clock_start")):
                    for c in ws[letter][1:]:
                        c.number_format = "yyyy-mm-dd"


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawTextHelpFormatter)
    ap.add_argument("--bundle", help="use a saved bundle.js instead of downloading it")
    ap.add_argument("--csv", help="fallback: use the SNL Download CSV (no PDF links)")
    ap.add_argument("--no-download", action="store_true", help="don't download PDFs")
    args = ap.parse_args()
    today = date.today()

    # 1-2. SNL + snapshot + change log
    records, source = get_records(args, today)
    networks, milestones = fetch_snl.build_tables(records, today=today)
    print(f"{len(networks)} networks, {milestones['pdf_url'].notna().sum()} published milestone PDFs listed")
    change_log = snapshot_and_log(networks, milestones, today)

    # 3. PDFs
    if not args.no_download:
        download_new_pdfs(milestones)
    url_by_id = milestones.dropna(subset=["pdf_id"]).set_index("pdf_id")["pdf_url"].to_dict()
    results = parse_all_pdfs(url_by_id)
    print(f"{len(results)} PDFs parsed")

    # 4. Core tables
    pdfs = pd.DataFrame([r["header"] for r in results])
    if pdfs.empty:
        pdfs = pd.DataFrame(columns=["pdf_id", "network_name", "adm", "milestone", "mod_no", "pdf_url"])
    pdfs, milestones = link_pdfs_to_milestones(pdfs, milestones)
    def stack(name):
        frames = [r[name] for r in results if len(r[name])]   # skip empty tables (avoids a pandas warning)
        return pd.concat(frames, ignore_index=True) if frames else pd.DataFrame()
    planes, launches, bands, satellites, groups = (stack(n) for n in
                                                   ["planes", "launches", "bands", "satellites", "groups"])
    satellites = arrange_satellites(satellites, pdfs)

    # 5. Views + workbooks
    # English country names next to the ITU symbols
    names = fetch_snl.load_country_names()
    if "adm" in pdfs:
        pdfs.insert(pdfs.columns.get_loc("adm") + 1, "adm_country",
                    pdfs["adm"].map(lambda c: fetch_snl.country_name(c, names)))
    if "country" in launches:
        launches.insert(launches.columns.get_loc("country") + 1, "country_name",
                        launches["country"].map(lambda c: fetch_snl.country_name(c, names)))
    unknown = sorted({v.split(": ")[1] for df, col in [(networks, "adm_country"), (pdfs, "adm_country"),
                                                       (launches, "country_name")] if col in df
                      for v in df[col] if str(v).startswith("unknown code")})
    if unknown:
        print(f"  note: unknown ITU country code(s) {unknown}: add them to itu_country_codes.csv")
    overview = build_overview(networks, milestones, pdfs)
    sat_view = build_satellite_view(pdfs, satellites, launches, planes)
    shared = build_shared_satellites(sat_view)
    checks = pdfs[[c for c in ["pdf_id", "network_name", "milestone", "layout", "link_method",
                               "parse_problems", "source_note", "bureau_notes"] if c in pdfs]]

    readme = [(a, b) for a, b in README_ROWS]
    readme[1] = ("Last run", str(today))
    readme[2] = ("SNL source", source + (" (no PDF links; PDFs matched by name)" if source == "csv" else ""))

    main_path = OUT_DIR / "res35_main.xlsx"
    write_workbook(main_path, {
        "Overview": overview, "Satellites view": sat_view, "Shared satellites": shared,
        "Change log": change_log, "Checks": checks,
        "networks": networks, "milestones": milestones, "pdfs": pdfs, "planes": planes,
        "launches": launches, "bands": bands, "satellites": satellites,
    }, readme)
    addon_path = OUT_DIR / "res35_frequency_addon.xlsx"
    write_workbook(addon_path, {"groups": groups, "satellite_bands": split_band_links(satellites),
                                "bands": bands})
    print(f"Wrote {main_path}\nWrote {addon_path}")
    problems = pdfs["parse_problems"].dropna() if "parse_problems" in pdfs else []
    print("All PDF checks passed." if len(problems) == 0 else f"{len(problems)} PDFs have problems: see Checks sheet")


if __name__ == "__main__":
    main()
