"""Command-line interface: ``dataharvest --help``."""

from __future__ import annotations

import logging
import subprocess
import sys
from pathlib import Path

import typer
from rich.console import Console
from rich.logging import RichHandler
from rich.progress import (
    BarColumn,
    MofNCompleteColumn,
    Progress,
    SpinnerColumn,
    TextColumn,
    TimeElapsedColumn,
)
from rich.table import Table

from . import __version__
from .config import ProjectConfig, load_project
from .models import RecordStatus

app = typer.Typer(
    name="dataharvest",
    help="Extract, verify and deliver structured data from websites and online databases.",
    no_args_is_help=True,
    add_completion=False,
    rich_markup_mode="rich",
)
console = Console()
err_console = Console(stderr=True)

STATUS_STYLE = {
    RecordStatus.VERIFIED: "green",
    RecordStatus.PARTIALLY_VERIFIED: "cyan",
    RecordStatus.UNVERIFIED: "white",
    RecordStatus.NEEDS_REVIEW: "red",
    RecordStatus.EXCLUDED: "yellow",
}


def _setup_logging(verbose: bool) -> None:
    level = logging.DEBUG if verbose else logging.WARNING
    logging.basicConfig(level=level, format="%(message)s", datefmt="[%X]",
                        handlers=[RichHandler(console=err_console, show_path=False, rich_tracebacks=verbose)], force=True)
    logging.getLogger("urllib3").setLevel(logging.DEBUG if verbose else logging.ERROR)  # retries are reported in the results
    logging.getLogger("requests_cache").setLevel(logging.WARNING)


def _version_callback(value: bool) -> None:
    if value:
        console.print(f"dataharvest {__version__}")
        raise typer.Exit()


@app.callback()
def main(
    version: bool = typer.Option(False, "--version", "-V", callback=_version_callback, is_eager=True, help="Show the version and exit."),
) -> None:
    """DataHarvest - web data extraction, verification and spreadsheet delivery."""


def _resolve_project(project: str) -> Path:
    path = Path(project)
    if path.exists():
        return path
    for candidate in (Path("projects") / project, Path("projects") / f"{project}.yaml", Path(f"{project}.yaml")):
        if candidate.exists():
            return candidate
    raise typer.BadParameter(f"project file not found: {project} (try 'dataharvest projects')")


# ----------------------------------------------------------------------------- run
@app.command()
def run(
    project: str = typer.Argument(..., help="Project YAML file (or its name in ./projects)."),
    limit: int = typer.Option(0, "--limit", "-n", help="Stop after N records (quick test runs)."),
    offline: bool = typer.Option(False, "--offline", help="Skip all enrichment/verification that needs the network."),
    no_cache: bool = typer.Option(False, "--no-cache", help="Ignore the HTTP cache (always fetch fresh pages)."),
    output_dir: Path | None = typer.Option(None, "--out", "-o", help="Override the output directory."),
    no_export: bool = typer.Option(False, "--no-export", help="Run the pipeline but write no files (dry run)."),
    google_sheets: bool = typer.Option(False, "--gsheets", help="Also push the result to Google Sheets (needs credentials)."),
    verbose: bool = typer.Option(False, "--verbose", "-v", help="Show debug logging."),
) -> None:
    """Run an extraction project end-to-end and deliver Excel/CSV/JSON (+ report)."""
    _setup_logging(verbose)
    from .pipeline import Pipeline
    from .sources import SourceError

    path = _resolve_project(project)
    try:
        cfg: ProjectConfig = load_project(path)
    except Exception as exc:
        err_console.print(f"[red]Invalid project file {path}:[/red] {exc}")
        raise typer.Exit(2) from None
    if output_dir:
        cfg.output.directory = str(output_dir)
    if google_sheets:
        cfg.output.google_sheets.enabled = True

    console.rule(f"[bold]{cfg.project.title}[/bold]")
    console.print(f"Project file: {path}  |  schema: {cfg.schema_def.name}  |  country rules: {cfg.project.country}")
    console.print("Sources: " + ", ".join(f"{s.label}" for s in cfg.active_sources))
    if cfg.project.instructions:
        console.print(f"[dim]{cfg.project.instructions.strip()}[/dim]")

    progress = Progress(SpinnerColumn(), TextColumn("{task.description:<34}"), BarColumn(), MofNCompleteColumn(), TimeElapsedColumn(),
                        console=console, transient=False)
    tasks: dict[str, int] = {}

    def on_progress(stage: str, done: int, total: int) -> None:
        if stage not in tasks:
            tasks[stage] = progress.add_task(stage, total=max(total, 1))
        progress.update(tasks[stage], completed=done, total=max(total, 1))

    pipeline = Pipeline(cfg, limit=limit or None, offline=offline, no_cache=no_cache, progress=on_progress)
    with progress:
        try:
            result = pipeline.run(export=not no_export)
        except SourceError as exc:
            err_console.print(f"[red]Extraction failed:[/red] {exc}")
            for w in pipeline.report.warnings:
                err_console.print(f"  - {w}")
            raise typer.Exit(1) from None
        except KeyboardInterrupt:
            err_console.print("[yellow]Interrupted - no files were written.[/yellow]")
            raise typer.Exit(130) from None

    _print_summary(result)


def _print_summary(result) -> None:
    report = result.report
    table = Table(title="Result", show_header=True, header_style="bold")
    table.add_column("Status")
    table.add_column("Records", justify="right")
    for status in RecordStatus:
        n = report.status_counts.get(status.value, 0)
        table.add_row(f"[{STATUS_STYLE[status]}]{status.value}[/{STATUS_STYLE[status]}]", str(n))
    table.add_row("[bold]delivered[/bold]", f"[bold]{report.records_delivered}[/bold]")
    console.print(table)
    if report.verification_counts:
        vt = Table(title="Verification of key fields (delivered records)", header_style="bold")
        vt.add_column("Field")
        for s in ("verified", "unverified", "conflict", "invalid", "missing"):
            vt.add_column(s, justify="right")
        for name, counts in report.verification_counts.items():
            vt.add_row(name, *[str(counts.get(s, 0)) for s in ("verified", "unverified", "conflict", "invalid", "missing")])
        console.print(vt)
    if report.warnings:
        console.print("[yellow]Warnings:[/yellow]")
        for w in report.warnings:
            console.print(f"  - {w}")
    if result.outputs:
        console.print("[bold green]Output files:[/bold green]")
        for kind, p in result.outputs.items():
            console.print(f"  {kind:<12} {p}")


# ----------------------------------------------------------------------------- validate
@app.command()
def validate(
    file: Path = typer.Argument(..., exists=True, readable=True, help="CSV or XLSX file to audit."),
    schema: str = typer.Option("leads", "--schema", "-s", help="Built-in schema name or a schema YAML file."),
    country: str = typer.Option("BE", "--country", "-c", help="Country whose phone/postcode/VAT rules apply."),
    output: Path | None = typer.Option(None, "--out", "-o", help="Where to write the audit workbook (.xlsx)."),
    similarity: int = typer.Option(90, "--similarity", help="Name similarity (50-100) for duplicate detection."),
    mapping: list[str] = typer.Option([], "--map", "-m", help='Column mapping "Column name=field", repeatable (e.g. -m "Tel=phone").'),
    optional: list[str] = typer.Option([], "--optional", help="Treat a required schema field as optional for this audit (repeatable)."),
) -> None:
    """Audit an existing spreadsheet: missing, invalid, inconsistent and duplicate records."""
    _setup_logging(False)
    from .export import export_excel
    from .qa import audit_file

    column_map: dict[str, str] = {}
    for item in mapping:
        if "=" not in item:
            raise typer.BadParameter(f"--map expects 'Column=field', got {item!r}")
        col, field_name = item.split("=", 1)
        column_map[col.strip()] = field_name.strip()
    try:
        result = audit_file(file, schema, country=country, mapping=column_map or None, name_similarity=similarity,
                            optional_fields=list(optional) or None)
    except ValueError as exc:
        err_console.print(f"[red]{exc}[/red]")
        raise typer.Exit(2) from None
    out = output or file.with_name(f"{file.stem}_audit.xlsx")
    export_excel(out, result.records, result.groups, result.report, result.schema,
                 {"name": file.stem, "title": result.report.project_title, "country": country}, include_excluded=True)
    console.rule(f"[bold]Audit of {file.name}[/bold]")
    console.print(f"Rows: {len(result.records)}  |  rows with problems: {result.rows_with_problems}  |  "
                  f"problems: {result.problems}  |  duplicate groups: {len(result.groups)}")
    console.print("Columns mapped: " + ", ".join(f"{k} -> {v}" for k, v in result.column_map.items()))
    if result.ignored_columns:
        console.print("[yellow]Ignored columns:[/yellow] " + ", ".join(result.ignored_columns) + "  (map them with -m \"Column=field\")")
    for w in result.report.warnings:
        if not w.startswith("columns not part of the schema"):
            console.print(f"[yellow]Warning:[/yellow] {w}")
    flagged = [r for r in result.records if any(not f.startswith("note:") for f in r.flags)]
    if flagged:
        t = Table(title="Findings (first 25)", header_style="bold")
        t.add_column("Row")
        t.add_column("Name")
        t.add_column("Problems")
        name_field = result.schema.name_field or "company_name"
        for r in flagged[:25]:
            t.add_row(r.source_id, str(r.get(name_field) or ""), "\n".join(f for f in r.flags if not f.startswith("note:")))
        console.print(t)
    console.print(f"[bold green]Audit workbook:[/bold green] {out}")


# ----------------------------------------------------------------------------- info commands
@app.command()
def sources() -> None:
    """List the available extraction sources and whether their credentials are configured."""
    from .sources import all_sources

    t = Table(title="Extraction sources", header_style="bold")
    t.add_column("type")
    t.add_column("status")
    t.add_column("produces")
    t.add_column("description")
    for name, cls in all_sources().items():
        ok, reason = cls.availability()
        t.add_row(name, f"[green]{reason}[/green]" if ok else f"[yellow]{reason}[/yellow]", cls.produces, cls.description)
    console.print(t)
    console.print("[dim]Key-less alternatives: osm_overpass (Google Maps), wikidata, html_list, csv_import; web search falls back to DuckDuckGo.[/dim]")


@app.command()
def schemas(name: str | None = typer.Argument(None, help="Show the fields of one schema.")) -> None:
    """List built-in schemas, or show the fields of one."""
    from .schema import builtin_schema_names, load_schema

    if name:
        s = load_schema(name)
        t = Table(title=f"Schema '{s.name}' ({s.label})", header_style="bold")
        for col in ("field", "type", "required", "key", "verification"):
            t.add_column(col)
        for f in s.fields:
            t.add_row(f.name, f.type, "yes" if f.required else "", "yes" if f.key else "", f.verify)
        console.print(t)
        return
    for n in builtin_schema_names():
        s = load_schema(n)
        console.print(f"[bold]{n}[/bold] - {len(s.fields)} fields, one row per {s.entity}")


@app.command()
def projects(directory: Path = typer.Option(Path("projects"), "--dir", help="Folder with project YAML files.")) -> None:
    """List the project files in ./projects."""
    files = sorted(directory.glob("*.yaml")) if directory.exists() else []
    if not files:
        console.print(f"No project files found in {directory}/")
        return
    t = Table(title=f"Projects in {directory}/", header_style="bold", expand=True)
    t.add_column("file", min_width=30, overflow="fold")
    t.add_column("title", ratio=2)
    t.add_column("schema", min_width=9)
    t.add_column("sources", ratio=1)
    for f in files:
        try:
            cfg = load_project(f)
            t.add_row(f.name, cfg.project.title, cfg.schema_def.name, ", ".join(s.label for s in cfg.active_sources))
        except Exception as exc:
            t.add_row(f.name, f"[red]invalid: {exc}[/red]", "", "")
    console.print(t)


TEMPLATES = {
    "leads": """project:
  name: {name}
  title: "Business leads - {name}"
  country: BE
  instructions: |
    Collect every business of the requested category in the area, with name, address, phone,
    e-mail, website, social profiles and VAT number. Verify before delivery; flag what cannot be verified.
schema: leads
sources:
  - type: osm_overpass
    area: Kortrijk
    country: BE
    tags: ["shop=bakery"]
enrichment:
  website: {{enabled: true, max_pages_per_site: 3, workers: 6}}
  find_missing_websites: {{enabled: true, provider: auto, max_queries: 30}}
  vies: {{enabled: true}}
verification: {{website_liveness: true, email_mx: true, phone_format: true}}
dedupe: {{enabled: true, name_similarity: 90, merge: true}}
output:
  directory: data/output
  formats: [xlsx, csv, json]
  google_sheets: {{enabled: false, title: "{name}"}}
""",
    "companies": """project:
  name: {name}
  title: "Company register - {name}"
  country: BE
schema: companies
sources:
  - type: wikidata
    headquarters_in: Q12995     # Kortrijk (municipality). Q1113 = West Flanders, Q31 = Belgium
enrichment:
  website: {{enabled: true}}
  find_missing_websites: {{enabled: true, max_queries: 20}}
  vies: {{enabled: true}}
output:
  directory: data/output
""",
    "products": """project:
  name: {name}
  title: "Product catalogue - {name}"
  country: GB
schema: products
sources:
  - type: html_list
    start_url: https://books.toscrape.com/catalogue/page-1.html
    item_selector: article.product_pod
    fields:
      title: {{selector: "h3 a", attr: title}}
      price: {{selector: "p.price_color"}}
      currency: {{selector: "p.price_color", regex: "([£$€])"}}
      rating: {{selector: "p.star-rating", attr: class, regex: "star-rating (\\\\w+)", map: {{One: 1, Two: 2, Three: 3, Four: 4, Five: 5}}}}
      availability: {{selector: "p.instock.availability"}}
      product_url: {{selector: "h3 a", attr: href, absolute: true}}
    next_page_selector: "li.next a"
    max_pages: 2
enrichment:
  website: {{enabled: false}}
  find_missing_websites: {{enabled: false}}
  vies: {{enabled: false}}
verification: {{email_mx: false}}
output:
  directory: data/output
""",
    "csv": """project:
  name: {name}
  title: "Seed list enrichment - {name}"
  country: BE
schema: leads
sources:
  - type: csv_import
    path: data/input/seed_companies.csv
    mapping: {{}}          # column -> field; identical names and common aliases map automatically
enrichment:
  website: {{enabled: true}}
  find_missing_websites: {{enabled: true, max_queries: 30}}
  vies: {{enabled: true}}
output:
  directory: data/output
""",
}


@app.command()
def init(
    name: str = typer.Argument(..., help="Project name (file name without .yaml)."),
    template: str = typer.Option("leads", "--template", "-t", help="leads | companies | products | csv"),
    directory: Path = typer.Option(Path("projects"), "--dir"),
) -> None:
    """Create a new project file from a template."""
    if template not in TEMPLATES:
        raise typer.BadParameter(f"unknown template '{template}'. Choose from: {', '.join(TEMPLATES)}")
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / f"{name}.yaml"
    if path.exists():
        raise typer.BadParameter(f"{path} already exists")
    path.write_text(TEMPLATES[template].format(name=name), encoding="utf-8")
    console.print(f"[green]Created[/green] {path} - edit it, then run: dataharvest run {path}")


@app.command()
def gsheets(
    file: Path = typer.Argument(..., exists=True, help="A CSV/XLSX export to push to Google Sheets."),
    title: str | None = typer.Option(None, "--title", help="Spreadsheet title (default: file name)."),
    share: list[str] = typer.Option([], "--share", help="E-mail address(es) to share the sheet with."),
) -> None:
    """Push an existing spreadsheet file to Google Sheets (needs GOOGLE_SERVICE_ACCOUNT_JSON)."""
    from .export.gsheets import GoogleSheetsUnavailable, _client
    from .sources.csv_import import read_rows

    try:
        gc = _client()
    except GoogleSheetsUnavailable as exc:
        err_console.print(f"[red]{exc}[/red]")
        raise typer.Exit(2) from None
    rows = read_rows(file)
    headers = list(rows[0].keys()) if rows else []
    values = [headers] + [["" if r.get(h) is None else r.get(h) for h in headers] for r in rows]
    sh = gc.create(title or file.stem)
    ws = sh.sheet1
    ws.resize(rows=max(len(values), 2), cols=max(len(headers), 1))
    ws.update(values, value_input_option="RAW")
    ws.format("1:1", {"textFormat": {"bold": True}})
    ws.freeze(rows=1)
    for email in share:
        sh.share(email, perm_type="user", role="writer", notify=False)
    console.print(f"[green]Uploaded[/green] {len(rows)} rows -> {sh.url}")


@app.command()
def ui(port: int = typer.Option(8501, "--port")) -> None:
    """Open the browser dashboard (Streamlit) to run projects and download results."""
    app_path = Path(__file__).resolve().parents[2] / "app" / "streamlit_app.py"
    if not app_path.exists():
        err_console.print(f"[red]dashboard not found at {app_path}[/red]")
        raise typer.Exit(2)
    try:
        import streamlit  # noqa: F401
    except ImportError:
        err_console.print("[red]Streamlit is not installed:[/red] pip install streamlit pandas")
        raise typer.Exit(2) from None
    cmd = [sys.executable, "-m", "streamlit", "run", str(app_path), "--server.port", str(port), "--browser.gatherUsageStats", "false"]
    raise typer.Exit(subprocess.call(cmd))


if __name__ == "__main__":  # pragma: no cover
    app()
