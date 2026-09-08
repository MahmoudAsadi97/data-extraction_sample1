# Google Sheets delivery

Two ways to get the result into Google Sheets.

## Option A - import the CSV (no setup)

1. Open Google Sheets -> *File -> Import -> Upload* and choose `data/output/<project>_<date>.csv`.
2. Separator: comma; the file is UTF-8 with BOM, so accents and special characters import correctly.
3. Format the header row and freeze it (*View -> Freeze -> 1 row*).

## Option B - push directly (`--gsheets`)

Uses a Google Cloud **service account** and the `gspread` library.

1. In the [Google Cloud Console](https://console.cloud.google.com/) create (or pick) a project.
2. *APIs & Services -> Enable APIs*: enable **Google Sheets API** and **Google Drive API**.
3. *IAM & Admin -> Service accounts -> Create service account*. No roles are required.
4. Open the account -> *Keys -> Add key -> JSON*. Download the key file.
5. Install the optional dependencies and point the tool at the key:

   ```bash
   pip install "dataharvest[gsheets]"          # or: pip install gspread google-auth
   ```

   In `.env`:

   ```
   GOOGLE_SERVICE_ACCOUNT_JSON=C:\keys\dataharvest-sa.json     # path, or the JSON content itself
   GOOGLE_SHEETS_ID=                                           # optional: update an existing spreadsheet
   ```

6. Run with `--gsheets`, or set `output.google_sheets.enabled: true` in the project file. Add the e-mail
   addresses that should get access under `output.google_sheets.share_with` (the spreadsheet is owned by
   the service account, so sharing is how you see it in your own Drive):

   ```yaml
   output:
     google_sheets:
       enabled: true
       title: "Kortrijk restaurants - verified leads"
       share_with: ["you@example.com"]
   ```

   The URL of the spreadsheet is printed at the end of the run and stored in the run report.

To push an existing file instead: `dataharvest gsheets data/output/file.xlsx --title "My sheet" --share you@example.com`.

Sheets created: the data sheet (same columns as the Excel workbook), *Needs Review* and *Duplicates*, with
bold coloured headers and a frozen first row. Conditional formatting and drop-downs are Excel-only.
