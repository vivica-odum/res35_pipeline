"""Build the interactive Res 35 dashboard (one self-contained HTML file).

Reads   output/res35_main.xlsx              (the pipeline's main workbook)
        docs/column_definitions.csv         (header tooltips)
        data/space_explorer_notices.csv     (Space Explorer links; see fetch_space_explorer.py)
        templates/dashboard_template.html   (the page layout and code)
Writes  output/res35_dashboard.html         (open it in any web browser)

Usage:
    python build_dashboard.py
    python build_dashboard.py --glossary path/to/ITU_glossary_Res35.xlsx   # adds a Glossary tab

Hovering a column name shows what the column means and where it comes from.
Hovering a network name, PDF ID, milestone or notice ID shows links to the
milestone PDFs and to the network's ITU Space Explorer page.
"""
import argparse
import base64
import json
import math
from datetime import date, datetime
from pathlib import Path

import pandas as pd

import fetch_space_explorer as se

HERE = Path(__file__).parent
MAIN = HERE / "output" / "res35_main.xlsx"
DEFS = HERE / "docs" / "column_definitions.csv"
TEMPLATE = HERE / "templates" / "dashboard_template.html"
LOGO = HERE / "templates" / "espl_logo.png"           # ESPL logo, black on transparent
OUT = HERE / "output" / "res35_dashboard.html"
SNL_URL = "https://www.itu.int/net/ITU-R/space/res35/index.html"

# (tab id, tab label, workbook sheet, columns hidden until switched on, one-line description)
TABLES = [
    ("overview", "Networks", "Overview",
     ["network_key", "bureau_notes", "igso", "resolves_19"],
     "One row per Space Network List entry: milestone statuses and deployment totals."),
    ("satellites", "Satellites", "Satellites view",
     ["received_date", "launch_vehicle_provider", "launch_facility", "launch_country",
      "launch_country_name", "arg_perigee_deg", "freq_band_ids", "space_station_id",
      "launch_id", "network_key", "launch_longitude", "launch_latitude"],
     "Every deployed satellite reported in a milestone PDF, with its reported orbit next to the notified orbit."),
    ("milestones", "Milestones", "milestones", ["network_key", "status_raw"],
     "One row per network and milestone (M0 to M3), with the PDF that reported it."),
    ("pdfs", "PDFs", "pdfs",
     ["network_key", "pages", "parse_problems", "link_method", "source_note", "nominal_longitude",
      "pdf_layout", "bureau_notes", "igso"],
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
        return v.strip() or None
    return v


def table_rows(df):
    return [[clean(v) for v in row] for row in df.itertuples(index=False, name=None)]


def split(text):
    return [p.strip() for p in str(text).split(",") if p.strip()]


def definitions_for(sheet, columns, defs):
    """Column -> {definition, itu, source_name, source_url} for one sheet."""
    out = {}
    for col in columns:
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
            out[col] = {k: (None if pd.isna(best[c]) or best[c] == "" else str(best[c]))
                        for k, c in [("definition", "definition"), ("itu", "itu_label_and_code"),
                                     ("source_name", "name_of_source"), ("source_url", "source")]}
    return out


def network_links(networks, pdfs, notices):
    """'NAME|ADM' -> Space Explorer page, notice list and Res 35 PDFs for that network."""
    out = {}
    for name, adm in networks[["network_name", "adm"]].drop_duplicates().itertuples(index=False):
        mine = notices[(notices["network_name"] == name) &
                       ((notices["adm"] == adm) | notices["adm"].isna())]
        pick = se.pick_dashboard_notice(mine)
        page = None
        if pick:
            page = {"url": se.dashboard_url(name, pick[0]), "ntc_id": pick[0],
                    "category": pick[1], "received": pick[2]}
        notice_list = [{"ntc_id": int(r.ntc_id), "category": r.category, "provision": r.provision,
                        "received": r.date_of_receipt, "url": se.dashboard_url(name, r.ntc_id)}
                       for r in mine.sort_values("date_of_receipt", ascending=False).itertuples()]
        mine_pdfs = pdfs[(pdfs["network_name"] == name) & (pdfs["adm"] == adm)]
        pdf_list = [{"pdf_id": r.pdf_id, "milestone": r.milestone, "special_section": r.special_section,
                     "brific_date": clean(r.brific_date), "url": r.pdf_url}
                    for r in mine_pdfs.sort_values("brific_date").itertuples()]
        out[f"{name}|{adm}"] = {"name": name, "adm": adm, "page": page,
                                "notices": notice_list, "pdfs": pdf_list}
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
    row_notified, row_deployed = int(overview["total_notified"].fillna(0).sum()), int(overview["total_deployed"].fillna(0).sum())
    rep = ", ".join(sorted(repeats))
    return [
        {"n": len(per_net), "label": "Satellite networks",
         "note": f"{len(per_net)} networks on {len(overview)} Space Network List rows."
                 + (f" {rep} each appear more than once (one row per 7-year limit date)." if rep else "")},
        {"n": notified, "label": "Satellites notified",
         "note": "Satellites in each network's latest notified filing, counted once per network. "
                 "This is the number the 10% / 50% / 100% milestones are measured against."
                 + (f" Adding up every Space Network List row instead would give {row_notified:,}." if row_notified != notified else "")},
        {"n": deployed, "label": "Satellites reported deployed",
         "note": "Satellites each network reported as deployed in its latest Res 35 report, counted once per network. "
                 "A satellite reported under two networks is counted in both."
                 + (f" Adding up every Space Network List row instead would give {row_deployed:,}." if row_deployed != deployed else "")},
        {"n": int(by_name.size), "label": "Distinct satellites reported",
         "note": f"Different satellite names in the networks' latest milestone PDFs. Fewer than the deployed total "
                 f"because {n_shared:,} satellites are reported under more than one network (see the Shared satellites tab)."},
        {"n": int(len(pdfs)), "label": "Milestone PDFs read",
         "note": "Res 35 milestone reports published on the Space Network List and read by the pipeline."},
    ]


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawTextHelpFormatter)
    ap.add_argument("--glossary", help="ITU glossary workbook (Term, Acronym, Alternate Names, "
                                       "Definition, Name of Source, Source) to add as a tab")
    ap.add_argument("--out", default=str(OUT))
    ap.add_argument("--bare", action="store_true",
                    help="leave out the <html>/<head> wrapper (for publishing as a claude.ai artifact)")
    args = ap.parse_args()

    book = pd.read_excel(MAIN, sheet_name=None)
    defs = pd.read_csv(DEFS, dtype=str).fillna("")
    notices = se.load_notices()
    readme = dict(zip(book["README"]["Item"], book["README"]["Description"]))
    pdfs = book["pdfs"]
    networks = book["networks"]

    tables = []
    for tab_id, label, sheet, hidden, blurb in TABLES:
        df = book[sheet]
        cols = list(df.columns)
        tables.append({"id": tab_id, "label": label, "sheet": sheet, "blurb": blurb,
                       "columns": cols, "hidden": [c for c in hidden if c in cols],
                       "rows": table_rows(df), "defs": definitions_for(sheet, cols, defs)})

    if args.glossary:
        g = pd.read_excel(args.glossary)
        g.columns = [str(c).strip() for c in g.columns]
        gdefs = {"Term": "ITU or Res 35 term.", "Acronym": "Short form of the term.",
                 "Alternate Names": "Other names for the same thing.",
                 "Definition": "What the term means.", "Name of Source": "Where the definition comes from.",
                 "Source": "Link to that source."}
        tables.append({"id": "glossary", "label": "Glossary", "sheet": "Glossary",
                       "blurb": "ITU terms used in these tables, with a source for each.",
                       "columns": list(g.columns), "hidden": [], "rows": table_rows(g),
                       "defs": {c: {"definition": d} for c, d in gdefs.items()}})

    pdf_map = {r.pdf_id: {"url": r.pdf_url, "name": r.network_name, "adm": r.adm,
                          "milestone": r.milestone, "special_section": r.special_section,
                          "brific_number": clean(r.brific_number), "brific_date": clean(r.brific_date)}
               for r in pdfs.itertuples()}
    ms = book["milestones"].dropna(subset=["pdf_id"])
    milestone_pdf = {f"{r.network_key}|{r.milestone}": r.pdf_id for r in ms.itertuples()}

    overview = book["Overview"]
    sats = book["Satellites view"]
    data = {
        "meta": {"last_run": str(readme.get("Last run", "")), "built": date.today().isoformat(),
                 "snl_url": SNL_URL,
                 "notices_checked": (notices["fetched_date"].max() if len(notices) else None),
                 "stats": summary_stats(overview, sats, pdfs)},
        "tables": tables,
        "networks": network_links(networks, pdfs, notices),
        "pdfs": pdf_map,
        "milestone_pdf": milestone_pdf,
    }

    payload = json.dumps(data, ensure_ascii=False, separators=(",", ":")).replace("</", "<\\/")
    logo = "data:image/png;base64," + base64.b64encode(LOGO.read_bytes()).decode() if LOGO.exists() else ""
    html = (TEMPLATE.read_text(encoding="utf-8")
            .replace("/*__DATA__*/null", payload).replace("__ESPL_LOGO__", logo))
    if not args.bare:      # a complete page that opens in any browser
        html = ('<!doctype html>\n<html lang="en">\n<head>\n<meta charset="utf-8">\n'
                '<meta name="viewport" content="width=device-width, initial-scale=1, viewport-fit=cover">\n'
                '</head>\n<body>\n' + html + '\n</body>\n</html>\n')
    Path(args.out).write_text(html, encoding="utf-8")
    missing = [v["name"] for v in data["networks"].values() if not v["page"]]
    print(f"Wrote {args.out} ({Path(args.out).stat().st_size / 1e6:.1f} MB)")
    if missing:
        print(f"  no Space Explorer link for: {', '.join(missing)}")


if __name__ == "__main__":
    main()
