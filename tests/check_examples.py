"""
check_examples.py
=================
Known answers from the three test PDFs and the STEAM-1 bundle.js record.
Run this after changing any code:

    python tests/check_examples.py

Every line should say PASS. A FAIL means the code no longer reads something
it used to read correctly.
"""

import sys
from datetime import date
from pathlib import Path

HERE = Path(__file__).parent
sys.path.insert(0, str(HERE.parent))

from fetch_snl import build_tables, extract_records, read_snl_csv, to_date  # noqa: E402
from parse_pdf import parse_pdf  # noqa: E402

PDFS = HERE.parent / "data" / "pdfs"
failures = 0


def check(label, actual, expected):
    global failures
    ok = actual == expected
    failures += not ok
    print(f"{'PASS' if ok else 'FAIL'}  {label}: {actual!r}" + ("" if ok else f" (expected {expected!r})"))


# --- STEAM-1 M1 (old layout) --------------------------------------------------
r = parse_pdf(PDFS / "RES350016M1.pdf")
h = r["header"]
check("STEAM-1 name/adm", (h["network_name"], h["adm"]), ("STEAM-1", "NOR"))
check("STEAM-1 BR IFIC", (h["brific_number"], h["brific_date"]), (3008, date(2023, 10, 31)))
check("STEAM-1 received", h["received_date"], date(2023, 7, 26))
check("STEAM-1 page-1 ids", (h["res35_network_id"], h["res35_submission_id"]), ("121935017", "123535017"))
check("STEAM-1 table id (BR6a/BR6b Id no.) is a different number",
      r["planes"]["br6a_br6b_id_no"].unique().tolist(), ["121500130"])
check("STEAM-1 totals", (h["total_notified"], h["total_deployed"]), (4408, 442))
check("STEAM-1 satellites", len(r["satellites"]), 442)
check("STEAM-1 min to meet M1 (rounded down)", r["bands"]["min_to_meet"].tolist(), [440, 440])
first_launch = r["launches"].iloc[0]
check("STEAM-1 launch 1", (first_launch["provider"], first_launch["vehicle"],
                           first_launch["facility"], first_launch["launch_date"]),
      ("SPACEX", "FALCON V9", "CAPE CANAVERAL", date(2019, 11, 11)))
check("STEAM-1 first satellite", r["satellites"].iloc[0]["satellite_name"], "Starlink-1007")
check("STEAM-1 problems", r["problems"], [])

# --- MCSAT LEO M1 (old layout) -------------------------------------------------
r = parse_pdf(PDFS / "RES350002M1.pdf")
check("MCSAT totals", (r["header"]["total_notified"], r["header"]["total_deployed"]), (774, 77))
check("MCSAT bands all met", r["bands"]["met"].unique().tolist(), ["YES"])
check("MCSAT problems", r["problems"], [])
mcsat_names = set(r["satellites"]["satellite_name"])

# --- THEO M1 (new layout) -------------------------------------------------------
r = parse_pdf(PDFS / "RES350019M1.pdf")
check("THEO layout", r["header"]["layout"], "new (BR134)")
check("THEO totals", (r["header"]["total_notified"], r["header"]["total_deployed"]), (882, 88))
check("THEO bands not met", r["bands"].query("met == 'NO'")["band_id"].tolist(), [5, 7, 9, 11])
sl13 = r["satellites"].query("satellite_name == 'SL0013'").iloc[0]
check("THEO SL0013", (sl13["itu_sat_id"], sl13["plane_id"], sl13["apogee_km"], sl13["band_ids"]),
      ("123500094_18_1", 18, 1227.56, "1,2,3,4,5,6,7,8,9,10,11,12"))
check("THEO station ids restart per launch",
      int(r["satellites"].duplicated(["launch_id", "station_id"]).sum()), 0)
check("THEO problems", r["problems"], [])
check("No satellite in both MCSAT LEO and THEO", sorted(mcsat_names & set(r["satellites"]["satellite_name"])), [])

# --- Older / unusual PDFs found on the first live run (skipped if not downloaded yet) ---
def maybe(pdf_id):
    path = PDFS / f"{pdf_id}.pdf"
    if not path.exists():
        print(f"SKIP  {pdf_id} not in data/pdfs yet")
        return None
    return parse_pdf(path)

r = maybe("RES350003")          # oldest layout: % deployed + Y/N, one ID number
if r:
    check("O3B-A M0 bands (oldest layout)", r["bands"][["pct_deployed", "met", "milestone"]].iloc[0].tolist(),
          [4.17, "YES", "M0"])
    check("O3B-A M0 single ID number", (r["header"]["res35_network_id"], r["header"]["res35_submission_id"]), (None, "121535001"))
    check("O3B-A M0 problems", r["problems"], [])
r = maybe("RES350001")          # vehicle cell wraps onto three lines
if r:
    l1 = r["launches"].iloc[0]
    check("USCSID-P launch split by position", (l1["provider"], l1["vehicle"], l1["facility"]),
          ("US GOVERNMENT", "US GOVERNMENT VEHICL", "CAPE CANAVERAL"))
r = maybe("RES350016")          # columns one space apart
if r:
    l1 = r["launches"].iloc[0]
    check("STEAM-1 M0 launch split by position", (l1["provider"], l1["vehicle"], l1["facility"]),
          ("SPACEX", "FALCON 9 V1.1", "CAPE CANAVERAL"))
r = maybe("RES350002")          # band lists written "1, 2, 3"
if r:
    check("MCSAT LEO M0 satellites", (len(r["satellites"]), r["satellites"].iloc[0]["band_ids"]),
          (38, "1,2,3,4,5,6"))
r = maybe("RES350008M2")        # ITU typo: data pages say STEAM-2
if r:
    check("RES350008M2 named from page 1", (r["header"]["network_name"], r["header"]["source_note"]),
          ("L5", "data pages name the network 'STEAM-2'"))
r = maybe("RES350007M1")        # "M1 /NO (See Note)" + the Bureau note box
if r:
    check("3ECOM-1 M1 band note", r["bands"][["met", "note"]].iloc[0].tolist(), ["NO", "See Note"])
    check("3ECOM-1 M1 Bureau note (English column only)", r["header"]["bureau_notes"],
          "The Radio Regulations Board in its 93rd meeting gave a favorable determination under "
          "resolves 12 of Resolution 35 (WRC-19), thus waiving the need to meet the requirements "
          "for the first milestone under resolves 7a)/11a).")
r = maybe("RES350008M3")        # one-line note under a table
if r:
    check("L5 M1 (2nd row) note", r["header"]["bureau_notes"],
          "This publication concerned only the findings for the frequency bands 7 to 10. "
          "The findings for the bands 1 to 6 were published in a previous Special section.")

# --- bundle.js record (as copied from DevTools) -------------------------------------
recs = extract_records((HERE / "steam1_record_snippet.js").read_text(encoding="utf-8"))
check("bundle: column headings skipped, duplicates removed",
      sorted(r["sat_name"] for r in recs), ["HIBLEO-2FL2", "HIBLEO-2FL2", "SLEIPNER-1", "STEAM-1"])
all_nets, all_ms = build_tables(recs, today=date(2026, 10, 8))
check("SLEIPNER-1 M3 PDF (first publication, no suffix)",
      all_ms.query("network_name == 'SLEIPNER-1' and milestone == 'M3'")["pdf_id"].iloc[0], "RES350027")
check("Both HIBLEO-2FL2 rows share one PDF",
      all_ms.query("network_name == 'HIBLEO-2FL2' and milestone == 'M3'")["pdf_id"].tolist(),
      ["RES350011", "RES350011"])
nets, ms = build_tables([r for r in recs if r["sat_name"] == "STEAM-1"], today=date(2026, 10, 8))
check("bundle M0 url", ms.iloc[0]["pdf_url"],
      "https://www.itu.int/net/ITU-R/space/documents/RES35/3005/RES35/RES350016.pdf")
check("bundle M1 url", ms.iloc[1]["pdf_url"],
      "https://www.itu.int/net/ITU-R/space/documents/RES35/3008/RES35/RES350016M1.pdf")
check("bundle statuses", ms["status_clean"].tolist(), ["Met", "Met", "As Received", "Pending"])

# --- ITU country codes ----------------------------------------------------------------
from fetch_snl import country_name, load_country_names  # noqa: E402
names = load_country_names()
check("ITU codes D / G / F / GUF / LIE",
      [country_name(c, names) for c in ["D", "G", "F", "GUF", "LIE"]],
      ["Germany", "United Kingdom", "France", "French Guiana", "Liechtenstein"])
check("Unknown ITU code is flagged", country_name("QQQ", names), "unknown code: QQQ")

# --- SNL CSV ------------------------------------------------------------------------
nets, ms = build_tables(read_snl_csv(HERE / "RES35_Space_Network_List.csv"), today=date(2026, 10, 8))
check("CSV networks / unique keys", (len(nets), nets["network_key"].is_unique), (40, True))
dated = ms[ms["status_raw"].str.match(r"\d\d\.\d\d\.\d{4}")]
check("Every date on the page = computed deadline",
      int((dated["status_raw"].map(to_date) != dated["deadline"]).sum()), 0)
sleip = ms.query("network_name == 'SLEIPNER-1'")["status_clean"].tolist()
check("SLEIPNER-1 statuses", sleip, ["Skipped (completed early)"] * 3 + ["Met"])

print("\nALL PASSED" if failures == 0 else f"\n{failures} FAILED")
sys.exit(1 if failures else 0)
