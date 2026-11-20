"""Build the interactive Res 35 dashboard (one self-contained HTML file).

Reads   output/res35_main.xlsx              (the pipeline's main workbook)
        docs/column_definitions.csv         (fallback definitions for column names)
        data/space_explorer_notices.csv     (Space Explorer links; see fetch_space_explorer.py)
        data/as_received.csv                (As Received links; see fetch_as_received.py)
        data/espl_glossary.csv              (copy of the ESPL glossary; see fetch_espl_glossary.py)
        templates/dashboard_template.html   (the page layout and code)
Writes  output/res35_dashboard.html         (open it in any web browser)

Usage:
    python build_dashboard.py
    python build_dashboard.py --bare --out page.html   # without <html>/<head>, for a claude.ai artifact

Definitions: hovering a column name or a milestone status shows its definition from the ESPL
glossary, with a link that opens the glossary on that term. The page reads the glossary live
when it opens; words the glossary doesn't have yet fall back to docs/column_definitions.csv
and the STATUS_DEFS below.
"""
import argparse
import base64
import csv
import io
import json
import math
import re
from datetime import date, datetime
from pathlib import Path

import pandas as pd

import fetch_as_received as asr
import fetch_espl_glossary as eg
import fetch_space_explorer as se

HERE = Path(__file__).parent
MAIN = HERE / "output" / "res35_main.xlsx"
DEFS = HERE / "docs" / "column_definitions.csv"
GLOSSARY_COPY = HERE / "data" / "espl_glossary.csv"
TEMPLATE = HERE / "templates" / "dashboard_template.html"
LOGO = HERE / "templates" / "espl_logo.png"           # ESPL logo, black on transparent
OUT = HERE / "output" / "res35_dashboard.html"
SNL_URL = "https://www.itu.int/net/ITU-R/space/res35/index.html"
DRAFTS = HERE / "docs" / "espl_glossary_drafts.csv"

# (tab id, tab label, workbook sheet, columns hidden until switched on, one-line description)
# Columns with no values at all are also hidden until switched on.
TABLES = [
    ("overview", "Networks", "Overview",
     ["adm", "latest_pdf_url", "network_key", "bureau_notes"],
     "One row per Space Network List entry: deployment, milestone statuses, frequency bands and orbits."),
    ("satellites", "Satellites", "Satellites view",
     ["received_date", "launch_vehicle_provider", "launch_facility", "launch_country",
      "launch_country_name", "arg_perigee_deg", "freq_band_ids", "space_station_id",
      "launch_id", "network_key", "launch_longitude", "launch_latitude", "adm"],
     "Every deployed satellite reported in a milestone PDF, with its reported orbit next to the notified orbit."),
    ("milestones", "Milestones", "milestones", ["network_key", "status_raw", "pdf_url"],
     "One row per network and milestone (M0 to M3), with the PDF that reported it."),
    ("pdfs", "PDFs", "pdfs",
     ["network_key", "pages", "parse_problems", "link_method", "source_note", "nominal_longitude",
      "pdf_layout", "bureau_notes", "igso", "adm"],
     "Every published Res 35 milestone PDF and the header details printed on it."),
    ("shared", "Shared satellites", "Shared satellites", [],
     "Satellite names listed in the latest PDF of more than one network."),
    ("planes", "Orbital planes", "planes", [],
     "Notified orbital planes from each PDF, with stations notified and deployed per plane."),
    ("launches", "Launches", "launches", ["parse_warning", "launch_longitude", "launch_latitude"],
     "Launches listed in each PDF."),
    ("bands", "Frequency bands", "bands", ["note", "pct_deployed"],
     "Frequency bands examined in each PDF and whether each met the milestone."),
]

MILESTONES = ["M0", "M1", "M2", "M3"]
RATIO = "deployed / notified"

# Shown on the dashboard instead of the pipeline's wording (the workbook keeps the original,
# so the change log isn't filled with relabelled statuses).
RELABEL = {"Skipped (completed early)": "Completed early"}

# Fallback definitions until the ESPL glossary has its own (Project = fetch_espl_glossary.PROJECT).
STATUS_DEFS = {
    "Met": "The administration reported deploying at least the required share of the notified "
           "satellites (10% at M1, 50% at M2, 100% at M3) by this milestone, in every frequency band.",
    "Partially Met": "The required share was reached in some of the network's frequency bands but not in others.",
    "Not Met": "The reported deployment was below the required share for this milestone.",
    "Not Met, resolves 12 applies": "The required share was not reached. Under resolves 12 of Resolution 35, "
           "the number of satellites recorded for the network is to be reduced in line with what was "
           "actually deployed.",
    "As Received": "The administration has sent its milestone report and the ITU has posted it as received, "
           "but the Radiocommunication Bureau has not yet examined and published it.",
    "Pending": "This milestone's deadline has not been reached yet.",
    "Deadline passed, no status shown": "This milestone's deadline has passed, but the Space Network List "
           "does not show a status for it yet.",
    "Completed early": "There is no separate report for this milestone: a later milestone was already met "
           "(for example, 100% of the satellites were deployed at the first report).",
}
EXTRA_DEFS = {
    RATIO: {"definition": "Satellites the administration reported as deployed in the network's latest Res 35 "
                          "report, out of the total number of satellites in its notified filing.",
            "source_name": "ITU Space Network List", "source_url": SNL_URL},
    "frequency_bands": {"definition": "The frequency bands in the network's latest milestone report. Each bar "
                        "spans one band, from its lowest to its highest frequency, on a common scale from "
                        "10 to 52 GHz. Hover a chart for the full list.",
                        "source_name": "Res 35 milestone PDF", "source_url": SNL_URL},
    "orbits": {"definition": "A small sketch of the notified orbits in the network's latest milestone report "
               "(altitude, inclination and number of planes). Hover a globe for the details.",
               "source_name": "Res 35 milestone PDF", "source_url": SNL_URL},
}

# ITU administration symbol -> ISO 3166 two-letter code, for the flag shown next to the country.
ISO2_BY_ADM = {"CHN": "CN", "F": "FR", "D": "DE", "ISR": "IL", "LIE": "LI", "LUX": "LU", "NOR": "NO",
               "PNG": "PG", "RUS": "RU", "G": "GB", "USA": "US", "J": "JP", "IND": "IN", "CAN": "CA",
               "AUS": "AU", "I": "IT", "E": "ES", "HOL": "NL", "B": "BE", "S": "SE", "FIN": "FI",
               "DNK": "DK", "SUI": "CH", "AUT": "AT", "KOR": "KR", "UAE": "AE", "ARS": "SA", "BRA": "BR",
               "MEX": "MX", "ARG": "AR", "TUR": "TR", "IRN": "IR", "INS": "ID", "MLA": "MY", "SNG": "SG",
               "THA": "TH", "VTN": "VN", "PHL": "PH", "NZL": "NZ", "AFS": "ZA", "EGY": "EG", "NIG": "NG",
               "KAZ": "KZ", "UKR": "UA", "POL": "PL", "CZE": "CZ", "GRC": "GR", "POR": "PT", "IRL": "IE",
               "MCO": "MC", "CYP": "CY", "MLT": "MT", "QAT": "QA", "BHR": "BH", "OMA": "OM", "PAK": "PK",
               "BGD": "BD", "LAO": "LA", "TON": "TO", "BLR": "BY", "AZE": "AZ", "ARM": "AM", "GEO": "GE"}


def flag(adm):
    iso = ISO2_BY_ADM.get(str(adm))
    return "".join(chr(0x1F1E6 + ord(c) - 65) for c in iso) if iso else ""


def clean(v):
    """Make a cell JSON-friendly: dates as YYYY-MM-DD, whole floats as ints, blanks as None."""
    if v is None:
        return None
    if isinstance(v, float):
        if math.isnan(v):
            return None
        return int(v) if v.is_integer() else round(v, 4)
    if isinstance(v, (pd.Timestamp, datetime)):
        return None if pd.isna(v) else v.strftime("%Y-%m-%d")
    if isinstance(v, date):
        return v.isoformat()
    if hasattr(v, "item"):            # numpy scalars
        return clean(v.item())
    if isinstance(v, str):
        return RELABEL.get(v.strip(), v.strip()) or None
    return v


def table_rows(df):
    return [[clean(v) for v in row] for row in df.itertuples(index=False, name=None)]


def split(text):
    return [p.strip() for p in str(text).split(",") if p.strip()]


def definitions_for(sheet, columns, defs):
    """Column -> {definition, itu, source_name, source_url} for one sheet."""
    out = {}
    for col in columns:
        if col in EXTRA_DEFS:
            out[col] = EXTRA_DEFS[col]
            continue
        best = None
        for _, r in defs.iterrows():
            if col not in split(r["column"]):
                continue
            sheets = split(r["sheets"])
            exact = sheet in sheets or (r["sheets"] == "all PDF-based sheets" and sheet not in
                                        ("Overview", "networks", "Shared satellites"))
            if exact:
                best = r
                break
            best = best if best is not None else r     # same column on another sheet
        if best is not None:
            d = {k: (None if pd.isna(best[c]) or best[c] == "" else str(best[c]))
                 for k, c in [("definition", "definition"), ("itu", "itu_label_and_code"),
                              ("source_name", "name_of_source"), ("source_url", "source")]}
            if d["definition"]:
                d["definition"] = d["definition"].replace("Skipped (completed early)", "Completed early")
            out[col] = d
    return out


def add_country_flags(df):
    if "adm_country" in df.columns and "adm" in df.columns:
        df = df.copy()
        df["adm_country"] = [f"{c} {flag(a)}".strip() if isinstance(c, str) else c
                             for c, a in zip(df["adm_country"], df["adm"])]
    return df


def latest_pdf_ids(pdfs):
    """network_key -> pdf_id of that SNL row's latest milestone PDF."""
    p = pdfs.dropna(subset=["network_key"]).sort_values(["brific_date", "pdf_id"])
    return p.groupby("network_key")["pdf_id"].last().to_dict()


def overview_table(overview, latest):
    """The Networks tab: deployed / notified and % deployed before M0; chart columns after M3."""
    o = overview.copy()
    o[RATIO] = [f"{int(d):,} / {int(n):,}" if pd.notna(d) and pd.notna(n) else None
                for d, n in zip(o["total_deployed"], o["total_notified"])]
    o["frequency_bands"] = [latest.get(k) for k in o["network_key"]]
    o["orbits"] = o["frequency_bands"]
    o = o.drop(columns=["total_notified", "total_deployed"])
    cols = [c for c in o.columns if c not in (RATIO, "pct_deployed", "frequency_bands", "orbits")]
    at = cols.index("M0")
    cols[at:at] = [RATIO, "pct_deployed"]
    at = cols.index("M3") + 1
    cols[at:at] = ["frequency_bands", "orbits"]
    return o[cols]


def viz_data(pdfs, planes, bands, latest):
    """pdf_id -> bands and orbital shells for the mini charts (latest PDFs only)."""
    out = {}
    for pid in set(latest.values()):
        p = pdfs[pdfs["pdf_id"] == pid].iloc[0]
        b = bands[bands["pdf_id"] == pid].sort_values(["freq_min_mhz", "freq_max_mhz"])
        pl = planes[planes["pdf_id"] == pid]
        shells = []
        if len(pl):
            g = pl.assign(arg_perigee_deg=pl["arg_perigee_deg"].fillna(0)).groupby(
                ["inclination_deg", "apogee_altitude_km", "perigee_altitude_km", "arg_perigee_deg"], dropna=False)
            for (inc, apo, peri, argp), s in g:
                shells.append({"inc": clean(inc), "apo": clean(apo), "peri": clean(peri), "argp": clean(argp),
                               "planes": int(len(s)), "notified": clean(s["stations_notified"].sum()),
                               "deployed": clean(s["stations_deployed"].sum())})
        out[pid] = {"milestone": p["milestone"], "brific_date": clean(p["brific_date"]),
                    "bands": [[clean(r.freq_band_id), clean(r.freq_min_mhz), clean(r.freq_max_mhz),
                               clean(r.met), clean(r.stations_notified), clean(r.stations_deployed)]
                              for r in b.itertuples()],
                    "shells": shells}
    return out


def network_links(networks, pdfs, notices, received):
    """'NAME|ADM' -> Space Explorer page, filings, Res 35 PDFs and As Received reports."""
    out = {}
    for name, adm, country in networks[["network_name", "adm", "adm_country"]].drop_duplicates(
            ["network_name", "adm"]).itertuples(index=False):
        mine = notices[(notices["network_name"] == name) &
                       ((notices["adm"] == adm) | notices["adm"].isna())]
        pick = se.pick_dashboard_notice(mine)
        page = None
        if pick:
            page = {"url": se.dashboard_url(name, pick[0]), "ntc_id": pick[0], "category": pick[1]}
        filings = [{"ntc_id": int(r.ntc_id), "provision": r.provision, "category": r.category,
                    "received": r.date_of_receipt}
                   for r in mine.sort_values("date_of_receipt", ascending=False).itertuples()]
        mine_pdfs = pdfs[(pdfs["network_name"] == name) & (pdfs["adm"] == adm)]
        pdf_list = [{"pdf_id": r.pdf_id, "milestone": r.milestone, "brific_date": clean(r.brific_date),
                     "url": r.pdf_url}
                    for r in mine_pdfs.sort_values("brific_date").itertuples()]
        rec = received[(received["network_name"] == name) &
                       received["submission_type"].str.contains("Res 35", na=False)]
        as_received = [{"url": asr.submission_url(r.submission_id), "received": r.received,
                        "ntc_id": str(r.ntc_id)} for r in rec.sort_values("received", ascending=False).itertuples()]
        out[f"{name}|{adm}"] = {"name": name, "adm": adm, "country": country, "flag": flag(adm),
                                "page": page, "filings": filings, "pdfs": pdf_list, "as_received": as_received}
    return out


def summary_stats(overview, sats, pdfs):
    """Numbers for the summary strip, each with a note shown on hover.

    Some networks appear on the Space Network List more than once (e.g. L5), with the same
    totals on each row, so totals are taken once per network (name + ADM), not per row."""
    per_net = overview.groupby(["network_name", "adm"], as_index=False)[["total_notified", "total_deployed"]].max()
    repeats = overview[overview.duplicated(["network_name", "adm"], keep=False)]["network_name"].unique()
    latest = sats[sats["in_latest_pdf"].fillna(False).astype(bool)]
    by_name = latest.groupby("satellite_name")["network_name"].nunique()
    n_shared = int((by_name > 1).sum())
    notified, deployed = int(per_net["total_notified"].fillna(0).sum()), int(per_net["total_deployed"].fillna(0).sum())
    row_notified = int(overview["total_notified"].fillna(0).sum())
    rep = ", ".join(sorted(repeats))
    return [
        {"n": len(per_net), "label": "Satellite networks",
         "note": f"{len(per_net)} networks on {len(overview)} Space Network List rows."
                 + (f" {rep} each appear more than once (one row per 7-year limit date)." if rep else "")},
        {"n": notified, "label": "Satellites notified",
         "note": "Satellites in each network's latest notified filing, counted once per network. "
                 "This is the number the 10% / 50% / 100% milestones are measured against."
                 + (f" Adding up every Space Network List row instead would give {row_notified:,}." if row_notified != notified else "")},
        {"n": int(by_name.size), "label": "Distinct satellites reported",
         "note": f"Different satellite names in the networks' latest milestone reports. The networks report "
                 f"{deployed:,} satellites as deployed in total, but {n_shared:,} satellites are counted under "
                 f"more than one network, so there are {int(by_name.size):,} different satellites "
                 f"(see the Shared satellites tab)."},
        {"n": int(len(pdfs)), "label": "Milestone PDFs read",
         "note": "Res 35 milestone reports published on the Space Network List and read by the pipeline."},
    ]


def glossary_entries(path=GLOSSARY_COPY, project=eg.PROJECT):
    """This project's rows from the saved ESPL glossary (the page also reads it live)."""
    if not Path(path).exists():
        return []
    rows = list(csv.DictReader(io.StringIO(Path(path).read_text(encoding="utf-8"))))
    out = []
    for r in rows:
        if (r.get("Project") or "").strip() != project or not r.get("Term") or not r.get("Definition"):
            continue
        sources = []
        for i in range(1, 6):
            label, url = (r.get(f"Source {i} display") or "").strip(), (r.get(f"Source {i} link") or "").strip()
            if label or url:
                sources.append({"label": label or url, "url": url if url.startswith("https://") else ""})
        out.append({"term": r["Term"].strip(), "acronym": (r.get("Acronym") or "").strip(),
                    "definition": r["Definition"].strip(), "sources": sources})
    return out


def write_glossary_drafts(tables):
    """docs/espl_glossary_drafts.csv: one row per word the dashboard explains, ready to paste
    into the ESPL glossary sheet. Acronym holds the column name so the dashboard can match it."""
    rows, seen = [], set()

    def add(term, acronym, definition, src_name, src_url):
        key = re.sub(r"[\s_\-]+", " ", (acronym or term).lower()).strip()
        if key in seen:
            return
        seen.add(key)
        rows.append({"Term": term, "Acronym": acronym, "Definition": definition or "", "Project": eg.PROJECT,
                     "Source 1 display": src_name or "", "Source 1 link": src_url or ""})

    for status, text in STATUS_DEFS.items():
        add(status, "", text, "Resolution 35 (Rev. WRC-23)", "")
    for t in tables:
        for col in t["columns"]:
            d = t["defs"].get(col, {})
            human = col.replace("_", " ").strip()
            human = human[:1].upper() + human[1:]
            add(human, "" if human.lower() == col.replace("_", " ") else col, d.get("definition"),
                d.get("source_name"), d.get("source_url"))
    pd.DataFrame(rows).to_csv(DRAFTS, index=False)
    return len(rows)


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawTextHelpFormatter)
    ap.add_argument("--out", default=str(OUT))
    ap.add_argument("--bare", action="store_true",
                    help="leave out the <html>/<head> wrapper (for publishing as a claude.ai artifact)")
    args = ap.parse_args()

    book = pd.read_excel(MAIN, sheet_name=None)
    defs = pd.read_csv(DEFS, dtype=str).fillna("")
    notices = se.load_notices()
    received = asr.load()
    readme = dict(zip(book["README"]["Item"], book["README"]["Description"]))
    pdfs = book["pdfs"]
    networks = book["networks"]
    overview = book["Overview"]
    latest = latest_pdf_ids(pdfs)

    tables = []
    for tab_id, label, sheet, hidden, blurb in TABLES:
        df = overview_table(overview, latest) if tab_id == "overview" else book[sheet]
        df = add_country_flags(df)
        cols = list(df.columns)
        empty = [c for c in cols if df[c].isna().all()]
        tables.append({"id": tab_id, "label": label, "sheet": sheet, "blurb": blurb, "columns": cols,
                       "hidden": [c for c in cols if c in hidden or c in empty],
                       "rows": table_rows(df), "defs": definitions_for(sheet, cols, defs)})

    pdf_map = {r.pdf_id: {"url": r.pdf_url, "name": r.network_name, "adm": r.adm,
                          "milestone": r.milestone, "special_section": r.special_section,
                          "brific_number": clean(r.brific_number), "brific_date": clean(r.brific_date)}
               for r in pdfs.itertuples()}
    ms = book["milestones"].dropna(subset=["pdf_id"])
    milestone_pdf = {f"{r.network_key}|{r.milestone}": r.pdf_id for r in ms.itertuples()}

    data = {
        "meta": {"last_run": str(readme.get("Last run", "")), "built": date.today().isoformat(),
                 "snl_url": SNL_URL,
                 "notices_checked": (notices["fetched_date"].max() if len(notices) else None),
                 "stats": summary_stats(overview, book["Satellites view"], pdfs)},
        "glossary": {"page": eg.PAGE_URL, "csv": eg.CSV_URL, "project": eg.PROJECT,
                     "entries": glossary_entries(), "fallback": STATUS_DEFS},
        "tables": tables,
        "networks": network_links(networks, pdfs, notices, received),
        "pdfs": pdf_map,
        "milestone_pdf": milestone_pdf,
        "viz": viz_data(pdfs, book["planes"], book["bands"], latest),
    }
    n_drafts = write_glossary_drafts(tables)

    payload = json.dumps(data, ensure_ascii=False, separators=(",", ":")).replace("</", "<\\/")
    logo = "data:image/png;base64," + base64.b64encode(LOGO.read_bytes()).decode() if LOGO.exists() else ""
    html = (TEMPLATE.read_text(encoding="utf-8")
            .replace("/*__DATA__*/null", payload).replace("__ESPL_LOGO__", logo))
    if not args.bare:      # a complete page that opens in any browser
        html = ('<!doctype html>\n<html lang="en">\n<head>\n<meta charset="utf-8">\n'
                '<meta name="viewport" content="width=device-width, initial-scale=1, viewport-fit=cover">\n'
                '<meta name="color-scheme" content="light">\n'
                '</head>\n<body>\n' + html + '\n</body>\n</html>\n')
    Path(args.out).write_text(html, encoding="utf-8")
    missing = [v["name"] for v in data["networks"].values() if not v["page"]]
    print(f"Wrote {args.out} ({Path(args.out).stat().st_size / 1e6:.1f} MB); "
          f"{len(data['glossary']['entries'])} ESPL glossary entries; {n_drafts} draft terms in {DRAFTS.name}")
    if missing:
        print(f"  no Space Explorer link for: {', '.join(missing)}")
    unknown = sorted({str(a) for a in overview["adm"].dropna() if str(a) not in ISO2_BY_ADM})
    if unknown:
        print(f"  no flag for ADM code(s): {', '.join(unknown)} (add to ISO2_BY_ADM)")


if __name__ == "__main__":
    main()
