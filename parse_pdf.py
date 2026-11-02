"""
parse_pdf.py
============
Reads ONE ITU Resolution 35 milestone PDF (a "Special Section") and turns it
into clean tables (pandas DataFrames).

Why read the PDF as text instead of using a "table extractor"?
    These PDFs repeat their column headers on every page in six languages,
    so automatic table extractors produce very messy output. Instead we:
      1. pull the text out of every page with pdfplumber (layout preserved),
      2. work out which table we are in from its ENGLISH TITLE
         (e.g. "Launch information for the deployed space stations"),
      3. keep only the lines that look like data rows (regular expressions).
    The table NUMBERS (Table I, II, ...) changed between the older and newer
    PDF layouts, but the English titles did not, so we never rely on numbers.

Usage from the command line (prints a summary):
    python parse_pdf.py path/to/RES350016M1.pdf

Usage from other code:
    from parse_pdf import parse_pdf
    result = parse_pdf("RES350016M1.pdf")
    result["header"]      # dict with network name, BR IFIC, dates, ...
    result["bands"]       # DataFrame, one row per frequency band
    result["satellites"]  # DataFrame, one row per deployed satellite
"""

import re
import sys
from datetime import datetime
from pathlib import Path

import pandas as pd
import pdfplumber


# ---------------------------------------------------------------------------
# Small helpers
# ---------------------------------------------------------------------------

DATE = r"\d{2}\.\d{2}\.\d{4}"          # ITU dates are day.month.year
NUM = r"-?\d+(?:\.\d+)?"                 # an integer or decimal number


def to_date(text):
    """'27.06.2021' -> datetime.date(2021, 6, 27). Returns None if empty."""
    if not text:
        return None
    return datetime.strptime(text, "%d.%m.%Y").date()


def to_num(text):
    """'1205.66' -> 1205.66, '49' -> 49. Returns None if empty."""
    if text is None or text == "":
        return None
    value = float(text)
    return int(value) if value.is_integer() and "." not in text else value


def read_pdf(pdf_path):
    """
    Return (texts, words) for every page:
      texts  page text with the layout preserved (used to find rows)
      words  each word with its x position (used to split launch columns
             that are only one space apart)
    """
    texts, words = [], []
    with pdfplumber.open(pdf_path) as pdf:
        for page in pdf.pages:
            texts.append(page.extract_text(layout=True) or "")
            words.append(page.extract_words())
    return texts, words


# ---------------------------------------------------------------------------
# 1. Header (first page + the line repeated at the top of every data page)
# ---------------------------------------------------------------------------

def parse_header(pages):
    first = pages[0]
    everything = "\n".join(pages)
    header = {}

    # Special section, e.g. "RES35/16" or "RES35/16 MOD-1"
    m = re.search(r"RES35/(\d+)(?:\s*MOD-(\d+))?", first)
    header["special_section"] = m.group(0).replace("  ", " ") if m else None
    header["ssn_no"] = int(m.group(1)) if m else None
    header["mod_no"] = int(m.group(2)) if m and m.group(2) else None

    # BR IFIC number and date, e.g. "3008 / 31.10.2023"
    m = re.search(rf"\b(\d{{4}})\s*/\s*({DATE})", first)
    header["brific_number"] = int(m.group(1)) if m else None
    header["brific_date"] = to_date(m.group(2)) if m else None

    # Milestone (M0-M3): the only standalone "M0".."M3" on page 1
    m = re.search(r"(?<![A-Za-z0-9])M([0-3])(?![A-Za-z0-9:])", first)
    header["milestone"] = f"M{m.group(1)}" if m else None

    # Page-1 "IDENTIFICATION NUMBER", kept exactly as printed: usually two 9-digit
    # numbers "121935017 / 123535017". It identifies this Res 35 submission; it is a
    # different number from the "BR6a/BR6b Id no." column inside the tables (stored as
    # br6a_br6b_id_no in planes/groups). The oldest M0 PDFs (2021-22) show only one number.
    m = re.search(r"\b(\d{9})\s*/\s*(\d{9})\b", first)
    if m:
        header["identification_number"] = f"{m.group(1)} / {m.group(2)}"
    else:
        single = re.findall(r"(?<!\d)(\d{9})(?!\d)", first)
        header["identification_number"] = single[0] if len(single) == 1 else None

    # Date the Bureau received the information
    m = re.search(rf"BUREAU ON.*?({DATE})", first, flags=re.S)
    header["received_date"] = to_date(m.group(1)) if m else None

    # The line repeated on every data page has the cleanest name/adm/IGSO.
    # Old layout: "A1a Sat. Network: MCSAT LEO A1f1 Notif. Adm:. F A1f3 Inter. sat. org: BR1 ..."
    # New layout: "A1a Sat. Network THEO A1f1 Notif. adm. G A1f3 Inter. sat. org. BR1 ..."
    m = re.search(
        r"A1a Sat\. Network:?\s+(.+?)\s+A1f1 Notif\. [Aa]dm[:.]*\.?\s+(\S+)\s+"
        r"A1f3 Inter\. sat\. org[:.]?\s*(.*?)\s*BR1",
        everything,
    )
    header_line_name = m.group(1).strip() if m else None
    header["adm"] = m.group(2).strip() if m else None
    header["igso"] = m.group(3).strip() if m else None

    # The network name on page 1 is the authoritative one. The repeated line has at
    # least one ITU typo: RES350008M2 is L5's submission but its data pages say STEAM-2.
    m = re.search(r"SATELLITE NETWORK\s+(.+?)\s+SPECIAL SECTION", first)
    cover_name = " ".join(m.group(1).split()) if m else None      # "MCSAT   LEO" -> "MCSAT LEO"
    header_line_name = " ".join(header_line_name.split()) if header_line_name else None
    header["network_name"] = cover_name or header_line_name
    header["source_note"] = (f"data pages name the network '{header_line_name}'"
                             if cover_name and header_line_name and cover_name != header_line_name
                             else None)

    # Nominal longitude: "NGSO" for every system on this list so far
    header["nominal_longitude"] = "NGSO" if "NGSO" in first else None

    # Totals at the end of the orbital-plane table
    m = re.search(r"BR113 Total number of space stations deployed\s+(\d+)\s+(\d+)", everything)
    header["total_notified"] = int(m.group(1)) if m else None
    header["total_deployed"] = int(m.group(2)) if m else None

    # Which layout? Newer PDFs have the BR134 unique satellite id column.
    header["pdf_layout"] = "new (BR134)" if "BR134" in everything else "old"
    return header


# ---------------------------------------------------------------------------
# 1b. Notes from the Bureau (kept in English)
# ---------------------------------------------------------------------------

NON_LATIN = re.compile(r"[Ѐ-ӿ؀-ۿ一-鿿]")   # Cyrillic, Arabic, Chinese


def bureau_note_block(words):
    """
    A 'Radiocommunication Bureau Note' box (3ECOM-1 M1, 3ECOM-3 M1) is printed as
    French | English | Spanish side by side, with Chinese / Russian / Arabic below.
    Keep only the English column: find where the three columns start on the first
    line of text (the two widest gaps), then collect the words in the middle column.
    """
    heading = [w for i, w in enumerate(words[:-1])
               if w["text"] == "NOTE" and words[i + 1]["text"] == "DU"]
    if not heading:
        return None
    body_start = heading[0]["top"] + 20                 # skip the two-line heading
    below = [w for w in words if w["top"] > body_start]
    other_scripts = [w["top"] for w in below if NON_LATIN.search(w["text"])]
    body_end = min(other_scripts) if other_scripts else float("inf")
    body = [w for w in below if w["top"] < body_end]
    if not body:
        return None
    # The heading row holds three widely separated headings:
    # 'NOTE DU BUREAU DES' | 'RADIOCOMMUNICATION BUREAU' | 'NOTA DE LA OFICINA DE'.
    # Split that row at its two widest gaps and take each heading's centre.
    head_row = sorted((w for w in words if abs(w["top"] - heading[0]["top"]) < 2),
                      key=lambda w: w["x0"])
    gaps = sorted(range(len(head_row) - 1),
                  key=lambda i: head_row[i + 1]["x0"] - head_row[i]["x1"], reverse=True)[:2]
    if len(gaps) < 2:
        return None
    a, b = sorted(gaps)
    groups = [head_row[:a + 1], head_row[a + 1:b + 1], head_row[b + 1:]]
    centres = [(g[0]["x0"] + g[-1]["x1"]) / 2 for g in groups]
    left, right = (centres[0] + centres[1]) / 2, (centres[1] + centres[2]) / 2
    centre = lambda w: (w["x0"] + w["x1"]) / 2
    english = sorted((w for w in body if left <= centre(w) < right),
                     key=lambda w: (round(w["top"]), w["x0"]))
    return " ".join(w["text"] for w in english) or None


def extract_notes(pages, page_words):
    """Return the English text of every Bureau note in the PDF, joined by ' | '."""
    notes = []
    for text, words in zip(pages, page_words):
        if "NOTE DU BUREAU" in text:
            block = bureau_note_block(words)
            if block:
                notes.append(block)
        # One-line notes under a table, e.g. L5: "Note: This publication concerned only ..."
        lines = [l.strip() for l in text.splitlines()]
        for i, line in enumerate(lines):
            m = re.match(r"Note:\s+(.*)", line)              # English only ("Note :" is French)
            if not m:
                continue
            parts = [m.group(1)]
            for nxt in lines[i + 1:]:
                if not nxt or nxt.startswith("Nota") or NON_LATIN.search(nxt):
                    break
                parts.append(nxt)
            notes.append(" ".join(parts))
    return " | ".join(dict.fromkeys(notes)) or None          # drop repeats, keep order


# ---------------------------------------------------------------------------
# 2. Data tables
# ---------------------------------------------------------------------------

# English titles that start each table (same in both layouts)
SECTION_TITLES = {
    "groups": "List of groups in the notification",
    "planes": "List of the orbital planes in the notification",
    "bands": "deployment by frequency band",
    "launches": "Launch information for the deployed space stations",
    "satellites": "Characteristics of the deployed space stations",
}

# One regular expression per table describing what a data row looks like.
ROW_PATTERNS = {
    # M 121500130 UC263Y R 121766085 14000 - 14500 27.12.2019 M1 27.06.2023 Y 27.06.2026
    "groups": re.compile(
        rf"^\s*(?P<action_code>[A-Z])\s+(?P<br6a_br6b_id_no>\d{{9}})\s+(?P<beam_designation>\S+)\s+"
        rf"(?P<emi_rcp>[ER])\s+(?P<group_id>\d+)\s+"
        rf"(?P<freq_min_mhz>{NUM})\s*-\s*(?P<freq_max_mhz>{NUM})\s+"
        rf"(?P<date_of_bringing_into_use>{DATE})\s+(?P<current_milestone>M\d)\s+"
        rf"(?P<milestone_period_expiry>{DATE})\s+(?P<milestone_criteria_met>[YN])"
        rf"(?:\s+(?P<next_milestone_period_expiry>{DATE}))?\s*$"
    ),
    # 125500135  1  87.9  1200  1200  0  49  7
    "planes": re.compile(
        rf"^\s*(?P<br6a_br6b_id_no>\d{{9}})\s+(?P<orbital_plane_id>\d+)\s+(?P<inclination_deg>{NUM})\s+"
        rf"(?P<apogee_altitude_km>{NUM})\s+(?P<perigee_altitude_km>{NUM})\s+(?P<arg_perigee_deg>{NUM})\s+"
        rf"(?P<stations_notified>\d+)\s+(?P<stations_deployed>\d+)\s*$"
    ),
    # Usual:   1  10700  12700  882  88  88  M1/YES          (minimum to meet + milestone/met)
    # Notes:   1  17702.5  17827.5  288  1  28  M1 /NO (See Note)
    # Oldest:  1  17800  18600  24  1  4.17%  Y              (percentage deployed + Y/N)
    "bands": re.compile(
        rf"^\s*(?P<freq_band_id>\d+)\s+(?P<freq_min_mhz>{NUM})\s+(?P<freq_max_mhz>{NUM})\s+"
        rf"(?P<stations_notified>\d+)\s+(?P<stations_deployed>\d+)\s+"
        rf"(?:(?P<min_to_meet>\d+)\s+(?P<milestone>M\d)\s*/\s*(?P<met>YES|NO)"
        rf"(?:\s*\((?P<note>[^)]*)\))?"
        rf"|(?P<pct_deployed>{NUM})\s*%\s+(?P<met_yn>[YN]))\s*$"
    ),
    # 1  ARIANESPACE  SOYOUZ  BAIKONUR  KAZ  063E15 36  45N37 48  06.02.2020
    # (provider / vehicle / facility are split afterwards, see parse_launch_middle)
    "launches": re.compile(
        rf"^\s*(?P<launch_id>\d+)\s+(?P<middle>.+?)\s+(?P<launch_country>[A-Z]{{2,3}})\s+"
        rf"(?P<launch_longitude>\d{{3}}[EW]\d{{2}} \d{{2}})\s+(?P<launch_latitude>\d{{2,3}}[NS]\d{{2}} \d{{2}})\s+"
        rf"(?P<launch_date>{DATE})\s*$"
    ),
    # old: 1  1  Starlink-1007  86  550  550  53.00  0  1,2
    # new: 1  1  SL0013  123500094_18_1  18  1227.56  1210.06  88.56  97  1,2,...,12
    "satellites": re.compile(
        rf"^\s*(?P<space_station_id>\d+)\s+(?P<launch_id>\d+)\s+(?P<satellite_name>.+?)\s+"
        rf"(?:(?P<itu_sat_id>\d+_\d+_\d+)\s+)?(?P<orbital_plane_id>\d+)\s+"
        rf"(?P<apogee_altitude_km>{NUM})\s+(?P<perigee_altitude_km>{NUM})\s+(?P<inclination_deg>{NUM})\s+"
        rf"(?P<arg_perigee_deg>{NUM})\s+(?P<freq_band_ids>\d+(?:\s*,\s*\d+)*)\s*$"
    ),
}

DATE_COLUMNS = {"date_of_bringing_into_use", "milestone_period_expiry",
                "next_milestone_period_expiry", "launch_date"}
TEXT_COLUMNS = {"action_code", "br6a_br6b_id_no", "beam_designation", "emi_rcp", "group_id",
                "current_milestone", "milestone_criteria_met", "milestone", "met", "met_yn", "note",
                "launch_country", "launch_longitude", "launch_latitude", "satellite_name",
                "itu_sat_id", "freq_band_ids", "middle"}

LAUNCH_HEADINGS = ("BR34", "BR35", "BR38", "BR39")   # provider, vehicle, facility, country


def split_launch_by_position(page_words, launch_id, launch_date):
    """
    Split provider / vehicle / facility using where each word sits on the page.
    Each word belongs to the column heading (BR34, BR35, BR38) whose centre is
    nearest. Handles rows where two names are only one space apart
    ('SPACEX FALCON 9 V1.1 CAPE CANAVERAL') and rows with a blank vehicle.
    """
    date_text = launch_date.strftime("%d.%m.%Y")
    centre = lambda w: (w["x0"] + w["x1"]) / 2
    # The row: the launch-date word, with the launch id at the start of the same line
    for date_word in (w for w in page_words if w["text"] == date_text):
        row_top = date_word["top"]
        same_line = [w for w in page_words if abs(w["top"] - row_top) < 2]
        first = min(same_line, key=lambda w: w["x0"])
        if first["text"] != str(launch_id):
            continue
        # Nearest heading of each kind above the row (BR39 can sit a few points lower)
        heads = {}
        for name in LAUNCH_HEADINGS:
            above = [w for w in page_words if w["text"] == name and w["top"] < row_top]
            if not above:
                return None
            heads[name] = centre(max(above, key=lambda w: w["top"]))
        cuts = [(heads["BR34"] + heads["BR35"]) / 2, (heads["BR35"] + heads["BR38"]) / 2,
                (heads["BR38"] + heads["BR39"]) / 2]
        # Words belonging to this row: a cell can wrap onto a line just above or
        # below it ('US GOVERNMENT' / 'VEHICL'), so take everything within 8 points.
        cell_words = sorted((w for w in page_words
                             if abs(w["top"] - row_top) <= 8 and first["x1"] < centre(w) < cuts[2]),
                            key=lambda w: (round(w["top"]), w["x0"]))
        cols = {"launch_vehicle_provider": [], "launch_vehicle": [], "launch_facility": []}
        for w in cell_words:
            c = centre(w)
            key = "launch_vehicle_provider" if c < cuts[0] else "launch_vehicle" if c < cuts[1] else "launch_facility"
            cols[key].append(w["text"])
        return {k: " ".join(v) or None for k, v in cols.items()}
    return None


def parse_launch_middle(text, page_words, launch_id, launch_date):
    """
    Split 'ARIANESPACE     SOYOUZ          BAIKONUR' into its three columns.
    First try the 2+ space gaps; if that doesn't give three pieces, use the
    word positions on the page. If neither works, keep the raw text and flag it.
    """
    parts = re.split(r"\s{2,}", text.strip())
    if len(parts) == 3:
        return {"launch_vehicle_provider": parts[0], "launch_vehicle": parts[1], "launch_facility": parts[2],
                "parse_warning": None}
    by_position = split_launch_by_position(page_words, launch_id, launch_date)
    if by_position:
        return {**by_position, "parse_warning": None}
    return {"launch_vehicle_provider": None, "launch_vehicle": None, "launch_facility": None,
            "parse_warning": f"could not split provider/vehicle/facility: '{text.strip()}'"}


def clean_row(table, match, page_words=None):
    row = {}
    for key, value in match.groupdict().items():
        if value is None:
            row[key] = None
        elif key in DATE_COLUMNS:
            row[key] = to_date(value)
        elif key in TEXT_COLUMNS:
            row[key] = value.strip()
        else:
            row[key] = to_num(value)
    if table == "launches":
        row.update(parse_launch_middle(row.pop("middle"), page_words or [],
                                       row["launch_id"], row["launch_date"]))
    if table == "satellites":
        row["freq_band_ids"] = row["freq_band_ids"].replace(" ", "")      # "1, 2, 3" -> "1,2,3"
    if table == "bands" and row.get("met_yn"):                  # oldest layout: Y / N
        row["met"] = "YES" if row["met_yn"] == "Y" else "NO"
    if table == "bands":
        row.pop("met_yn", None)
    return row


def parse_tables(pages, page_words):
    rows = {name: [] for name in SECTION_TITLES}
    current = None
    for page, words in zip(pages, page_words):
        for line in page.splitlines():
            # Did a new table start on this line?
            for name, title in SECTION_TITLES.items():
                if title in line:
                    current = name
            if current is None:
                continue
            match = ROW_PATTERNS[current].match(line)
            if match:
                rows[current].append(clean_row(current, match, words))
    return {name: pd.DataFrame(r) for name, r in rows.items()}


# ---------------------------------------------------------------------------
# 3. Checks: does what we extracted add up?
# ---------------------------------------------------------------------------

def run_checks(header, tables):
    """Return a list of human-readable problems (empty list = all good)."""
    problems = []
    planes, sats, bands = tables["planes"], tables["satellites"], tables["bands"]

    for key in ["network_name", "adm", "special_section", "brific_number",
                "milestone", "received_date"]:
        if header.get(key) in (None, ""):
            problems.append(f"header field '{key}' not found")
    if not header.get("identification_number"):
        problems.append("no identification number found")

    if header["total_notified"] is not None and len(planes):
        if planes["stations_notified"].sum() != header["total_notified"]:
            problems.append(
                f"planes: notified sum {planes['stations_notified'].sum()} "
                f"!= total {header['total_notified']}")
        if planes["stations_deployed"].sum() != header["total_deployed"]:
            problems.append(
                f"planes: deployed sum {planes['stations_deployed'].sum()} "
                f"!= total {header['total_deployed']}")

    if header["total_deployed"] is not None and len(sats) != header["total_deployed"]:
        problems.append(f"satellites: {len(sats)} rows but total deployed is "
                        f"{header['total_deployed']}")

    if len(sats) and len(tables["launches"]):
        missing = set(sats["launch_id"]) - set(tables["launches"]["launch_id"])
        if missing:
            problems.append(f"satellites refer to launch ids not in launch table: {sorted(missing)}")

    if len(bands) == 0:
        problems.append("no frequency-band rows found")

    if len(tables["launches"]) and tables["launches"]["parse_warning"].notna().any():
        problems.append("some launch rows could not be split (see parse_warning column)")
    return problems


# ---------------------------------------------------------------------------
# 4. Main entry point
# ---------------------------------------------------------------------------

def parse_pdf(pdf_path, pdf_url=None):
    """
    Parse one Res 35 PDF. Returns a dict:
        header      dict of single values (one row in the PDFS table)
        groups, planes, bands, launches, satellites   DataFrames
        problems    list of check failures
    Every table gets a 'pdf_id' column (file name without .pdf, e.g.
    'RES350016M1') so rows from different PDFs never get mixed up.
    """
    pdf_path = Path(pdf_path)
    pages, page_words = read_pdf(pdf_path)
    header = parse_header(pages)
    header["bureau_notes"] = extract_notes(pages, page_words)
    header["pdf_id"] = pdf_path.stem
    header["pdf_url"] = pdf_url
    header["pages"] = len(pages)

    tables = parse_tables(pages, page_words)
    for name, df in tables.items():
        df.insert(0, "pdf_id", pdf_path.stem)
        tables[name] = df
    bands = tables["bands"]
    if len(bands):
        # The oldest layout has no milestone column; it is the PDF's own milestone.
        if "milestone" in bands:
            bands["milestone"] = bands["milestone"].fillna(header["milestone"])
        for col in ["min_to_meet", "milestone", "met", "note", "pct_deployed"]:
            if col not in bands:
                bands[col] = None

    problems = run_checks(header, tables)
    header["parse_problems"] = "; ".join(problems) if problems else None
    return {"header": header, **tables, "problems": problems}


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: python parse_pdf.py <file.pdf> [<file2.pdf> ...]")
        sys.exit(1)
    for path in sys.argv[1:]:
        result = parse_pdf(path)
        h = result["header"]
        print(f"\n=== {path}")
        for key in ["network_name", "adm", "special_section", "milestone", "brific_number",
                    "brific_date", "received_date", "identification_number",
                    "total_notified", "total_deployed", "pdf_layout"]:
            print(f"  {key:18} {h[key]}")
        for name in SECTION_TITLES:
            print(f"  {name:18} {len(result[name])} rows")
        print("  checks:", "all passed" if not result["problems"] else result["problems"])
