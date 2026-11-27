"""Match Res 35 satellites to CelesTrak orbits and write the tracked-orbit outputs.

Called by fetch_celestrak.py after the day's download (data/celestrak/raw/). Writes:

  data/celestrak/satellite_matches.csv      one row per satellite name in the latest PDFs:
                                            its NORAD catalog number and how it was matched
  data/celestrak/history/YYYY/YYYY-MM-DD.csv.gz
                                            that day's orbit of every matched satellite
                                            (GP and SupGP), kept so a history builds up
  output/res35_tracked_orbits.xlsx          tracked vs notified vs reported orbit, per
                                            network and satellite, with Res 8 status

How names are matched (PDF name -> CelesTrak OBJECT_NAME), ignoring case, spaces and hyphens:
  same name                    Starlink-1007 -> STARLINK-1007, KUIPER-00037, ASBM-1, SKIF-D
  OneWeb                       SL0012        -> ONEWEB-0012
  Iridium NEXT                 IR-102        -> IRIDIUM 102
  O3B / O3B mPOWER (SES)       FM5 -> O3B FM5, MPOWER F5 -> O3B MPOWER F5
Each match is then checked against the launch date in the PDF (CelesTrak SATCAT launch
date within 3 days). Only confirmed matches (name AND launch date) are used. A name match
whose launch date differs or cannot be checked is marked "needs review" and left out of the
history and the tracked values until someone checks it. Names that give no match or several
matches are listed, not guessed.

Orbit numbers: CelesTrak element sets are SGP4 mean elements. The semi-major axis is taken
from the mean motion after SGP4's own correction (Brouwer mean motion, WGS-72 constants),
then apogee / perigee altitude = a(1 +/- e) - 6378.137 km, the same Earth radius as the
3D orbit page. Mean elements smooth out short-period wobble of a few km, so small
differences from operator-reported values are expected.
"""
import math
import re
from datetime import date, datetime, timezone
from pathlib import Path

import pandas as pd

HERE = Path(__file__).parent
CT = HERE / "data" / "celestrak"
RAW = CT / "raw"
HIST = CT / "history"
MATCHES = CT / "satellite_matches.csv"
MAIN = HERE / "output" / "res35_main.xlsx"
OUT = HERE / "output" / "res35_tracked_orbits.xlsx"

RE_KM = 6378.137                      # Earth radius for altitudes (as the 3D page)
# SGP4 (WGS-72) constants for recovering the Brouwer mean motion
XKE = 60.0 / math.sqrt(6378.135 ** 3 / 398600.8)      # per minute, Earth radii units
K2 = 0.5 * 0.001082616
SUP_FILES = ["supgp_starlink.csv", "supgp_oneweb.csv", "supgp_kuiper.csv", "supgp_iridium.csv", "supgp_ses.csv"]
ORBIT_COLS = ["OBJECT_NAME", "OBJECT_ID", "NORAD_CAT_ID", "EPOCH", "MEAN_MOTION", "ECCENTRICITY",
              "INCLINATION", "RA_OF_ASC_NODE", "ARG_OF_PERICENTER", "MEAN_ANOMALY", "BSTAR"]


# ---------------------------------------------------------------------------
# Orbit maths
# ---------------------------------------------------------------------------

def semi_major_axis_km(mean_motion_rev_day, ecc, inc_deg):
    """Brouwer mean semi-major axis from an SGP4 (Kozai) mean motion, as SGP4 itself does."""
    n0 = mean_motion_rev_day * 2 * math.pi / 1440.0          # rad / min
    cosi = math.cos(math.radians(inc_deg))
    x3thm1 = 3 * cosi * cosi - 1
    beta0 = (1 - ecc * ecc) ** 1.5
    a1 = (XKE / n0) ** (2 / 3)
    d1 = 1.5 * K2 * x3thm1 / (a1 * a1 * beta0)
    a0 = a1 * (1 - d1 / 3 - d1 * d1 - 134 / 81 * d1 ** 3)
    d0 = 1.5 * K2 * x3thm1 / (a0 * a0 * beta0)
    a0dp = a0 / (1 - d0)                                       # Earth radii (WGS-72)
    return a0dp * 6378.135


def add_altitudes(df):
    a = [semi_major_axis_km(n, e, i) for n, e, i in zip(df.MEAN_MOTION, df.ECCENTRICITY, df.INCLINATION)]
    df = df.copy()
    df["semi_major_axis_km"] = [round(x, 2) for x in a]
    df["apogee_altitude_km"] = [round(x * (1 + e) - RE_KM, 1) for x, e in zip(a, df.ECCENTRICITY)]
    df["perigee_altitude_km"] = [round(x * (1 - e) - RE_KM, 1) for x, e in zip(a, df.ECCENTRICITY)]
    return df


# ---------------------------------------------------------------------------
# Loading
# ---------------------------------------------------------------------------

def read_orbits():
    gp = pd.read_csv(RAW / "gp_active.csv", usecols=ORBIT_COLS)
    gp["source"] = "GP"
    sups = []
    for f in SUP_FILES:
        p = RAW / f
        if p.exists() and p.stat().st_size > 0:
            d = pd.read_csv(p)
            sups.append(d[ORBIT_COLS + (["DATA_SOURCE"] if "DATA_SOURCE" in d else [])])
    sup = pd.concat(sups, ignore_index=True) if sups else pd.DataFrame(columns=ORBIT_COLS)
    sup["source"] = "SupGP"
    # SupGP files can hold several epochs per satellite: keep the newest
    sup = sup.sort_values("EPOCH").drop_duplicates("NORAD_CAT_ID", keep="last")
    orbits = pd.concat([gp, sup], ignore_index=True)
    orbits["NORAD_CAT_ID"] = orbits["NORAD_CAT_ID"].astype(int)
    return add_altitudes(orbits)


def latest_pdf_satellites():
    """Satellites in each network's latest PDF (one PDF per network name, as on the 3D page)."""
    sv = pd.read_excel(MAIN, sheet_name="Satellites view")
    sv = sv[sv["in_latest_pdf"] == True].copy()                      # noqa: E712
    latest = (sv.sort_values(["brific_date", "pdf_id"]).groupby("network_name")["pdf_id"].last())
    return sv[sv["pdf_id"] == sv["network_name"].map(latest)].copy()


# ---------------------------------------------------------------------------
# Matching
# ---------------------------------------------------------------------------

def key(name):
    return re.sub(r"[\s\-_]", "", str(name).upper())


def candidate_keys(name):
    """CelesTrak name keys a PDF satellite name may correspond to, with the rule used."""
    k = key(name)
    out = [(k, "same name")]
    if m := re.fullmatch(r"SL(\d{4})", k):
        out.append((f"ONEWEB{m.group(1)}", "SL#### = ONEWEB-####"))
    if m := re.fullmatch(r"IR(\d{2,3})", k):
        out.append((f"IRIDIUM{m.group(1)}", "IR-### = IRIDIUM ###"))
    if re.fullmatch(r"(FM\d+|MPOWERF\d+)", k):
        out.append((f"O3B{k}", "O3B prefix"))
    return out


def match_names(sats, satcat):
    satcat = satcat.copy()
    satcat["key"] = satcat["OBJECT_NAME"].map(key)
    by_key = satcat.groupby("key")
    pdf_launch = (sats.groupby("satellite_name")["launch_date"].first()
                  .pipe(pd.to_datetime, errors="coerce"))
    rows = []
    for name in sorted(sats["satellite_name"].dropna().unique(), key=str):
        nets = sorted(sats.loc[sats.satellite_name == name, "network_name"].unique())
        per_pdf = int(sats[sats.satellite_name == name].groupby("pdf_id").size().max())
        row = {"satellite_name": name, "networks": "; ".join(nets), "norad_cat_id": None,
               "celestrak_name": None, "object_id": None, "match_rule": None, "match_status": None,
               "pdf_launch_date": pdf_launch.get(name), "satcat_launch_date": None,
               "decay_date": None, "note": None}
        if per_pdf > 1:
            row.update(match_status="not matched",
                       note=f"The PDF uses this name for {per_pdf} different satellites, so it cannot identify one object.")
            rows.append(row)
            continue
        found = None
        for k, rule in candidate_keys(name):
            if k in by_key.groups:
                found = (by_key.get_group(k), rule)
                break
        if found is None:
            row.update(match_status="not matched", note="No CelesTrak catalogue name matches.")
            rows.append(row)
            continue
        cands, rule = found
        if len(cands) > 1 and pd.notna(row["pdf_launch_date"]):
            ld = pd.to_datetime(cands["LAUNCH_DATE"], errors="coerce")
            cands = cands[(ld - row["pdf_launch_date"]).abs() <= pd.Timedelta(days=3)]
        if len(cands) != 1:
            row.update(match_status="not matched",
                       note=f"{len(found[0])} catalogue objects share this name: "
                            + ", ".join(f"{r.OBJECT_NAME} ({r.NORAD_CAT_ID})" for r in found[0].itertuples()))
            rows.append(row)
            continue
        c = cands.iloc[0]
        ld = pd.to_datetime(c["LAUNCH_DATE"], errors="coerce")
        checkable = pd.notna(ld) and pd.notna(row["pdf_launch_date"])
        ok = checkable and abs((ld - row["pdf_launch_date"]).days) <= 3
        row.update(norad_cat_id=int(c["NORAD_CAT_ID"]), celestrak_name=c["OBJECT_NAME"], object_id=c["OBJECT_ID"],
                   match_rule=rule, satcat_launch_date=ld, decay_date=pd.to_datetime(c["DECAY_DATE"], errors="coerce"),
                   match_status="matched" if ok else "needs review",
                   note=None if ok else ("Name matches but the CelesTrak launch date is more than 3 days from the PDF's. "
                                         if checkable else "Name matches but there is no launch date to check it against. ")
                                        + "Not used until checked by hand.")
        rows.append(row)
    return pd.DataFrame(rows)


# ---------------------------------------------------------------------------
# Res 8 status (same rules as the 3D page)
# ---------------------------------------------------------------------------

def res8_status(apo_n, per_n, inc_n, d_apo, d_per, d_inc):
    if any(pd.isna(v) for v in (apo_n, per_n, inc_n, d_apo, d_per, d_inc)):
        return "not comparable"
    apo_n, per_n = float(apo_n), float(per_n)
    if apo_n >= 15000:
        return "Res 8 does not apply"
    leo = apo_n <= 2000 and per_n <= 2000

    def limits(tier):                    # (apogee km, perigee km, inclination deg)
        if tier == "r11":
            return 30, 30, 2 if leo else 3
        fixed, pct, i_leo, i_high = (70, 0.05, 2, 3) if tier == "r6" else (100, 0.10, 3, 4)
        return (fixed if apo_n <= 2000 else pct * apo_n, fixed if per_n <= 2000 else pct * per_n,
                i_leo if leo else i_high)

    def inside(tier):
        la, lp, li = limits(tier)
        return abs(d_apo) <= la and abs(d_per) <= lp and abs(d_inc) <= li
    if inside("r11"):
        return "inside maintenance limit (30 km)"
    if inside("r6"):
        return "inside deployment limit"
    if inside("r7"):
        return "between deployment and upper limits"
    return "beyond upper deployment limit"


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

COLUMN_DEFS = [
    ("satellite_name", "Satellite name as written in the Res 35 milestone PDF."),
    ("network_name", "ITU satellite network whose latest milestone PDF lists the satellite."),
    ("norad_cat_id", "NORAD catalog number of the matched object (CelesTrak SATCAT)."),
    ("celestrak_name", "Object name in CelesTrak's catalogue."),
    ("match_status", "matched (name and launch date confirmed; the only ones used) / needs review (name matches, launch date differs or is missing; not used yet) / not matched."),
    ("orbit_source", "SupGP = CelesTrak element set derived from operator-supplied orbit data (preferred when present); GP = standard element set from the US Space Force catalogue, as published by CelesTrak."),
    ("epoch", "Time (UTC) the tracked orbit refers to."),
    ("tracked_*", "Apogee / perigee altitude (km above a 6,378.137 km Earth) and inclination (deg) from CelesTrak's mean elements."),
    ("notified_*", "Values notified for the satellite's orbital plane (PDF orbital-plane table)."),
    ("reported_*", "Values the operator reported for the satellite in the PDF (deployed space stations table)."),
    ("tracked_minus_notified_*", "Tracked minus notified: the actual deviation from the filing."),
    ("res8_status_tracked", "Which Resolution 8 limit the tracked orbit is inside: maintenance (±30 km), deployment (±70 km, ±2°), between deployment and upper (±100 km, ±3°), or beyond. Above 2,000 km: ±5% / ±10% and ±3° / ±4°."),
    ("decay_date", "Re-entry date from CelesTrak SATCAT, if the satellite has decayed."),
]


def run(today=None):
    today = today or datetime.now(timezone.utc).date()
    satcat = pd.read_csv(RAW / "satcat.csv", dtype={"NORAD_CAT_ID": int})
    orbits = read_orbits()
    sats = latest_pdf_satellites()

    matches = match_names(sats, satcat)
    CT.mkdir(parents=True, exist_ok=True)
    matches.to_csv(MATCHES, index=False, date_format="%Y-%m-%d")
    confirmed = matches[matches["match_status"] == "matched"]
    ids = set(confirmed["norad_cat_id"].astype(int))

    # today's orbits of the matched satellites, kept as history
    snap = orbits[orbits["NORAD_CAT_ID"].isin(ids)].copy()
    snap.insert(0, "download_date", today.isoformat())
    hist_path = HIST / f"{today:%Y}" / f"{today.isoformat()}.csv.gz"
    hist_path.parent.mkdir(parents=True, exist_ok=True)
    snap.sort_values(["NORAD_CAT_ID", "source"]).to_csv(hist_path, index=False, compression="gzip")

    # one orbit per satellite: SupGP when CelesTrak has it, else GP
    best = snap.sort_values("source", key=lambda s: s.map({"SupGP": 0, "GP": 1})).drop_duplicates("NORAD_CAT_ID")
    best = best.set_index("NORAD_CAT_ID")

    m = matches.set_index("satellite_name")
    rows = []
    for r in sats.itertuples():
        mm = m.loc[r.satellite_name] if r.satellite_name in m.index else None
        nid = None if mm is None or mm["match_status"] != "matched" else int(mm["norad_cat_id"])
        o = best.loc[nid] if nid is not None and nid in best.index else None
        row = {
            "satellite_name": r.satellite_name, "network_name": r.network_name, "orbital_plane_id": r.orbital_plane_id,
            "norad_cat_id": nid, "celestrak_name": None if nid is None else mm["celestrak_name"],
            "match_status": None if mm is None else mm["match_status"],
            "orbit_source": None if o is None else o["source"], "epoch": None if o is None else o["EPOCH"],
            "tracked_apogee_altitude_km": None if o is None else o["apogee_altitude_km"],
            "tracked_perigee_altitude_km": None if o is None else o["perigee_altitude_km"],
            "tracked_inclination_deg": None if o is None else round(float(o["INCLINATION"]), 4),
            "notified_apogee_altitude_km": r.notified_apogee_altitude_km,
            "notified_perigee_altitude_km": r.notified_perigee_altitude_km,
            "notified_inclination_deg": r.notified_inclination_deg,
            "reported_apogee_altitude_km": r.apogee_altitude_km,
            "reported_perigee_altitude_km": r.perigee_altitude_km,
            "reported_inclination_deg": r.inclination_deg,
            "decay_date": None if nid is None else mm["decay_date"],
        }
        for p, t, n in (("apogee_altitude_km", "tracked_apogee_altitude_km", "notified_apogee_altitude_km"),
                        ("perigee_altitude_km", "tracked_perigee_altitude_km", "notified_perigee_altitude_km"),
                        ("inclination_deg", "tracked_inclination_deg", "notified_inclination_deg")):
            row[f"tracked_minus_notified_{p}"] = (None if row[t] is None or pd.isna(row[n])
                                                  else round(row[t] - row[n], 4 if "incl" in p else 1))
            row[f"reported_minus_notified_{p}"] = (None if pd.isna(getattr(r, p)) or pd.isna(row[n])
                                                   else round(getattr(r, p) - row[n], 4 if "incl" in p else 1))
        row["res8_status_tracked"] = res8_status(
            row["notified_apogee_altitude_km"], row["notified_perigee_altitude_km"], row["notified_inclination_deg"],
            row["tracked_minus_notified_apogee_altitude_km"], row["tracked_minus_notified_perigee_altitude_km"],
            row["tracked_minus_notified_inclination_deg"])
        if o is None:
            row["res8_status_tracked"] = ("decayed" if nid is not None and pd.notna(mm["decay_date"])
                                          else "no tracked orbit" if nid is not None
                                          else "needs review (not used)" if mm is not None and mm["match_status"] == "needs review"
                                          else "not matched")
        rows.append(row)
    tracked = pd.DataFrame(rows)

    # summary
    n_names = len(matches)
    n_match = int((matches["match_status"] == "matched").sum())
    n_review = int((matches["match_status"] == "needs review").sum())
    n_orbit = int(tracked["orbit_source"].notna().sum())
    status_counts = tracked["res8_status_tracked"].value_counts()
    readme = [
        ("WHAT THIS IS", "Tracked (actual) orbits of the satellites named in each network's latest Res 35 milestone PDF, "
                         "compared with the notified orbital plane and with the orbit the operator reported in the PDF."),
        ("Source", "CelesTrak (https://celestrak.org), downloaded daily without a login: GP data for active satellites, "
                   "Supplemental GP (operator-derived) data where available, and the SATCAT catalogue. Please credit CelesTrak."),
        ("Downloaded", today.isoformat()),
        ("Satellite names in latest PDFs", f"{n_names:,}: {n_match:,} confirmed matches (name and launch date) are used; "
                                            f"{n_review:,} need review and {n_names - n_match - n_review:,} have no match, and are left out"),
        ("Rows with a tracked orbit today", f"{n_orbit:,} of {len(tracked):,} network-satellite rows"),
        ("Res 8 status (tracked orbit)", "; ".join(f"{k}: {v:,}" for k, v in status_counts.items())),
        ("History", "data/celestrak/history/YYYY/YYYY-MM-DD.csv.gz keeps each day's orbit of every matched satellite "
                    "(both GP and SupGP when present), so deviations can be studied over time."),
        ("Method", "SGP4 mean elements; semi-major axis from the Brouwer mean motion (WGS-72), altitude = a(1 ± e) − 6,378.137 km. "
                   "Mean elements smooth out short-period variations of a few km."),
        ("Matching", "Names compared ignoring case, spaces and hyphens, with SL#### = ONEWEB-####, IR-### = IRIDIUM ###, "
                     "FM# / MPOWER F# = O3B FM# / O3B MPOWER F#; checked against the PDF launch date (±3 days). "
                     "Unmatched names are listed in the Matches sheet with the reason."),
        ("Not yet on the 3D page", "These tracked orbits are not drawn on the orbit page yet, until the matching has been reviewed."),
    ]
    import run_pipeline
    run_pipeline.write_workbook(OUT, {
        "Tracked vs notified": tracked,
        "Matches": matches,
        "Column definitions": pd.DataFrame(COLUMN_DEFS, columns=["column", "meaning"]),
    }, readme)
    print(f"Matched {n_match:,} of {n_names:,} satellite names; {n_orbit:,} rows with a tracked orbit")
    print("Res 8 status of tracked orbits:", dict(status_counts))
    print(f"Wrote {OUT.relative_to(HERE)}, {MATCHES.relative_to(HERE)}, {hist_path.relative_to(HERE)}")


if __name__ == "__main__":
    run()
