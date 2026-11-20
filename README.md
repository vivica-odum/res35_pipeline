# Res 35 pipeline

> **Just want the data?** Download [`output/res35_main.xlsx`](output/res35_main.xlsx).
> It is refreshed automatically every Wednesday. A copy you download does not
> update itself, so come back here for the latest one. You only need to run the
> code if you are changing it.

Pulls the ITU Resolution 35 Space Network List (SNL) and every published
milestone PDF, and turns them into linked tables in Excel workbooks. Each run
saves a dated snapshot and logs what changed since the last run.

## What you get

| File | What's in it |
|---|---|
| `output/res35_main.xlsx` | Main workbook: README, Overview, Satellites view, Shared satellites, Change log, Checks, plus every core table |
| `output/res35_frequency_addon.xlsx` | Optional frequency work: beam groups (transmit / receive) and satellite-to-band links |
| `output/res35_dashboard.html` | Interactive dashboard of the same tables, published at https://vivica-odum.github.io/res35_pipeline/output/res35_dashboard.html. Hover a column name or milestone status for its definition from the ESPL glossary; hover a network for its reports and ITU filings, or a chart for the network's frequency bands and orbits |
| `data/as_received.csv` | Submissions on the ITU's "As Received" list (sent to the ITU but not yet published), used to link "As Received" milestones to their files |
| `data/espl_glossary.csv` | Copy of the ESPL glossary, used when the live glossary can't be read |
| `docs/espl_glossary_drafts.csv` | Every word the dashboard defines, with a draft definition, ready to paste into the ESPL glossary sheet |
| `data/space_explorer_notices.csv` | Every ITU notice ID listed for each network in the ITU Space Explorer (used for the dashboard's links) |
| `data/change_log.csv` | Every change detected between runs |
| `data/snapshots/` | The networks and milestones tables as they were on each run date |
| `data/pdfs/` | Every downloaded milestone PDF |

## Running it on your own computer

1. **Install Python.** Either [Python](https://www.python.org/downloads/) (on Windows,
   tick "Add Python to PATH" in the installer) or Anaconda, which also includes Jupyter.
2. **Put this folder somewhere easy**, e.g. `Documents/res35_pipeline`.
3. **Open a terminal in the folder.**
   - Windows: open the folder in File Explorer, click the address bar, type `cmd`, press Enter.
     (With Anaconda, open "Anaconda Prompt" and type `cd ` followed by the folder path.)
   - Mac: right-click the folder in Finder > Services > New Terminal at Folder.
4. **Install the libraries** (once):
   ```
   pip install -r requirements.txt
   ```
5. **Check the code** (should end with `ALL PASSED`):
   ```
   python tests/check_examples.py
   ```
6. **Run it:**
   ```
   python run_pipeline.py
   python fetch_space_explorer.py   # optional: refresh the Space Explorer links
   python fetch_as_received.py      # optional: refresh the As Received links
   python fetch_espl_glossary.py    # optional: refresh the copy of the ESPL glossary
   python build_dashboard.py        # rebuild output/res35_dashboard.html
   ```
   The first run downloads every published PDF, about one per second, and parses them.
   Expect 10 to 20 minutes. Later runs only handle what is new.
7. **Open `output/res35_main.xlsx`** and look at the Checks sheet. A blank
   `parse_problems` column means every PDF's numbers added up.

On a Mac, if `python` or `pip` isn't found, use `python3` and `pip3`.

**Prefer Jupyter?** Open `explore_results.ipynb` (in this folder) and run the cells
top to bottom. It does steps 4 to 6 and then shows the results with pandas.

Other options:

```
python run_pipeline.py --no-download        # use only PDFs already in data/pdfs
python run_pipeline.py --bundle bundle.js   # use a bundle.js saved from the browser
python run_pipeline.py --csv RES35_Space_Network_List.csv   # fallback: the site's Download CSV
```

## Weekly automatic runs (GitHub)

`.github/workflows/weekly_update.yml` tells GitHub to run the pipeline every
**Wednesday at 12:00 UTC** and save the results back into the repository. The BR IFIC
comes out every other Tuesday, so Wednesday picks up each new one the next day,
and the in-between Wednesday catches SNL changes such as new "As Received" statuses.

- Run it by hand: repository page > **Actions** tab > "Weekly Res 35 update" > **Run workflow**.
- Each run is listed on the Actions tab. A failed run shows a red X and GitHub emails the owner.
- After the pipeline, it refreshes the Space Explorer and As Received links and the copy of the
  ESPL glossary, then rebuilds the dashboard. If one of those sites can't be reached, that step
  shows a warning and last week's data is kept.
- If the "Save results" step fails with a permission error: Settings > Actions > General >
  Workflow permissions > "Read and write permissions".

## Files

| File | Job |
|---|---|
| `fetch_snl.py` | Reads the SNL (from `bundle.js`), builds the networks and milestones tables, builds each PDF link, compares snapshots |
| `parse_pdf.py` | Reads one milestone PDF into tables. `python parse_pdf.py some.pdf` prints a summary |
| `run_pipeline.py` | Runs everything and writes the workbooks |
| `fetch_space_explorer.py` | Looks up each network's ITU notice IDs in the ITU Space Explorer and saves them to `data/space_explorer_notices.csv` |
| `fetch_as_received.py` | Looks up each network on the ITU "As Received" list and saves `data/as_received.csv` |
| `fetch_espl_glossary.py` | Saves a copy of the ESPL glossary. Its `PROJECT` setting is the glossary Project name whose terms the dashboard uses |
| `build_dashboard.py` | Builds `output/res35_dashboard.html` from the main workbook, the column definitions and the glossary. The page's layout and styling are in `templates/` |
| `docs/ITU_glossary_Res35.xlsx` | Earlier ITU glossary for this project (now replaced by the ESPL glossary) |
| `explore_results.ipynb` | Runs the pipeline from Jupyter and opens the results |
| `docs/column_definitions.csv` | What every column means, with its ITU label, data item code and source. Shown in the workbook as the Column definitions sheet. Edit this file to change a definition |
| `itu_country_codes.csv` | ITU country symbols to English names (D = Germany, G = United Kingdom, GUF = French Guiana). Add a row if the pipeline warns about an unknown code |
| `tests/check_examples.py` | Known answers from STEAM-1, MCSAT LEO and THEO. Run after any code change |
| `.github/workflows/weekly_update.yml` | The weekly schedule |

## How it works

- The SNL page stores its whole list inside `bundle.js` as JavaScript objects
  (`{sat_name:"STEAM-1",adm:"NOR",...}`). `fetch_snl.py` finds them and converts them to JSON.
- PDF links are built from bundle fields:
  `.../RES35/{wic_no}/RES35/RES35{ssn_no:04d}{ssn_rev}{ssn_rev_no}.pdf`
  (STEAM-1 M0 → `3005/RES35/RES350016.pdf`, M1 → `3008/RES35/RES350016M1.pdf`).
- `parse_pdf.py` finds each table by its English title, not its number, because
  the numbering changed between the older and newer PDF layouts.
- Every PDF's numbers are checked: plane totals must add up to the PDF's stated
  totals, and the satellite count must equal the deployed total.

## ITU Space Explorer links

A network's page in the ITU Space Explorer is
`https://www.itu.int/itu-r/space/apps/public/spaceexplorer/networks-explorer/space-stations/dashboard/non-plans/<network name>/<notice ID>`.
The notice ID is the ITU's number for one submission. Each network has several: advance
publication (API), coordination, notification, and its Res 49, Res 35 and Res 8 submissions.
The page opens for any of them. The dashboard links to the network's latest notification
(or its latest coordination request if it has none), which is the notice the Space Explorer
itself shows for the network. `br6a_br6b_id_no` in the PDFs is the notification that applied
when that PDF was submitted, so for some networks it is an older notice.


## Definitions on the dashboard (ESPL glossary)

Definitions come from the lab's ESPL glossary sheet, read live when the page opens. A row is
used when its **Project** is `ITU Resolution 35` (set in `fetch_espl_glossary.py`) and its
**Term** or **Acronym** matches the word, ignoring case, spaces, underscores and hyphens. So a
Term "Total notified", or any Term with Acronym `total_notified`, defines the total_notified
column. Words the glossary doesn't have yet use the draft wording in
`docs/column_definitions.csv` and `build_dashboard.py` (STATUS_DEFS), without a glossary link.
`docs/espl_glossary_drafts.csv` lists every word with its draft, ready to copy into the sheet.
