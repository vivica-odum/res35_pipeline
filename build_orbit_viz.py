"""Build the 3D planned-orbit / Resolution 8 tolerance visualization (one HTML file).

Reads   output/res35_main.xlsx              (planes, pdfs, Satellites view, Overview)
        templates/orbit_viz_template.html   (the page layout and 3D code)
Writes  output/res35_orbits.html            (open it in any web browser)

Usage:
    python build_orbit_viz.py

For each network it uses the latest milestone PDF only:
  * planes   -> the notified ("planned") orbital planes: inclination, apogee and perigee
                altitude, argument of perigee, satellites notified / deployed per plane
  * satellites view (in_latest_pdf) -> each reported satellite with its reported orbit
The page draws planned satellites as boxes and reported satellites as spheres.
The page loads three.js from cdn.jsdelivr.net, so it needs an internet connection.
"""
import argparse
import base64
import json
import math
from datetime import date
from pathlib import Path

import pandas as pd

HERE = Path(__file__).parent
MAIN = HERE / "output" / "res35_main.xlsx"
TEMPLATE = HERE / "templates" / "orbit_viz_template.html"
LOGO = HERE / "templates" / "espl_logo.png"           # ESPL logo, black on transparent
OUT = HERE / "output" / "res35_orbits.html"


def num(v, nd=3):
    if v is None or (isinstance(v, float) and math.isnan(v)):
        return None
    v = float(v)
    return int(v) if v.is_integer() else round(v, nd)


def build_data(book):
    pdfs = book["pdfs"].copy()
    planes = book["planes"]
    sats = book["Satellites view"]
    overview = book["Overview"]
    readme = dict(zip(book["README"]["Item"], book["README"]["Description"]))

    # latest PDF per network name (a few networks have two SNL rows; they share PDFs)
    pdfs = pdfs.sort_values(["brific_date", "pdf_id"])
    latest = pdfs.groupby("network_name").tail(1)

    ov = overview.drop_duplicates("network_name").set_index("network_name")
    networks = []
    for p in latest.itertuples():
        pl = planes[planes.pdf_id == p.pdf_id]
        st = sats[(sats.pdf_id == p.pdf_id)]
        st = st.drop_duplicates(["satellite_name", "orbital_plane_id", "space_station_id"])
        o = ov.loc[p.network_name] if p.network_name in ov.index else None
        networks.append({
            "name": p.network_name,
            "adm": p.adm,
            "country": p.adm_country,
            "pdf_id": p.pdf_id,
            "pdf_url": p.pdf_url,
            "milestone": p.milestone,
            "brific_date": p.brific_date.date().isoformat() if pd.notna(p.brific_date) else None,
            "total_notified": num(p.total_notified),
            "total_deployed": num(p.total_deployed),
            "highest_milestone_met": (None if o is None or pd.isna(o.highest_milestone_met)
                                      else o.highest_milestone_met),
            # [plane id, inclination deg, apogee km, perigee km, arg perigee deg, notified, deployed]
            "planes": [[num(r.orbital_plane_id), num(r.inclination_deg), num(r.apogee_altitude_km),
                        num(r.perigee_altitude_km), num(r.arg_perigee_deg),
                        num(r.stations_notified), num(r.stations_deployed)]
                       for r in pl.itertuples()],
            # [name, plane id, apogee km, perigee km, inclination deg, arg perigee deg, launch date]
            "sats": [[r.satellite_name, num(r.orbital_plane_id), num(r.apogee_altitude_km),
                      num(r.perigee_altitude_km), num(r.inclination_deg), num(r.arg_perigee_deg),
                      r.launch_date.date().isoformat() if pd.notna(r.launch_date) else None]
                     for r in st.itertuples()],
        })
    networks.sort(key=lambda n: n["name"])
    return {"meta": {"last_run": str(readme.get("Last run", "")), "built": date.today().isoformat()},
            "networks": networks}


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawTextHelpFormatter)
    ap.add_argument("--out", default=str(OUT))
    args = ap.parse_args()

    book = pd.read_excel(MAIN, sheet_name=None)
    data = build_data(book)
    payload = json.dumps(data, ensure_ascii=False, separators=(",", ":")).replace("</", "<\\/")
    logo = "data:image/png;base64," + base64.b64encode(LOGO.read_bytes()).decode() if LOGO.exists() else ""
    html = (TEMPLATE.read_text(encoding="utf-8")
            .replace("/*__DATA__*/null", payload).replace("__ESPL_LOGO__", logo))
    Path(args.out).write_text(html, encoding="utf-8")
    n_pl = sum(len(n["planes"]) for n in data["networks"])
    n_st = sum(len(n["sats"]) for n in data["networks"])
    print(f"Wrote {args.out} ({Path(args.out).stat().st_size / 1e6:.2f} MB): "
          f"{len(data['networks'])} networks, {n_pl} planes, {n_st} reported satellites")


if __name__ == "__main__":
    main()
