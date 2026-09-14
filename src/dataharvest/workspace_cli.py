"""CLI for persistent customer audits and reviewed delivery."""

from __future__ import annotations

import json
from importlib.resources import files
from pathlib import Path

import typer

from .workspace import Workspace

app = typer.Typer(help="Save audits, review records, compare runs and export approved records.", no_args_is_help=True)
DEFAULT_DB = Path("data/workspace/audits.sqlite3")


def emit(value) -> None:
    typer.echo(json.dumps(value, ensure_ascii=False, indent=2, default=str))


def fail(exc: Exception) -> None:
    typer.echo(str(exc), err=True)
    raise typer.Exit(2) from None


@app.command()
def audit(file: Path = typer.Argument(..., exists=True), project: str = typer.Option(...),
          schema: str = "accounts", country: str = "BE", db: Path = DEFAULT_DB) -> None:
    """Audit up to 5,000 company rows offline and save an immutable snapshot."""
    try:
        workspace = Workspace(db)
        run_id = workspace.audit(file, project=project, schema_ref=schema, country=country)
        run = workspace.load(run_id)
        emit({"run_id": run_id, "records": len(run["records"]), "status_counts": run["report"]["status_counts"]})
    except (ValueError, OSError) as exc:
        fail(exc)


@app.command()
def runs(project: str | None = None, db: Path = DEFAULT_DB) -> None:
    """List saved runs, newest first."""
    emit(Workspace(db).list_runs(project))


@app.command()
def show(run_id: str, db: Path = DEFAULT_DB) -> None:
    """Inspect records, field evidence, reviews and quality findings as JSON."""
    try:
        run = Workspace(db).load(run_id)
        run["records"] = [r.as_dict() for r in run["records"]]
        run["schema"] = run["schema"].model_dump()
        emit(run)
    except ValueError as exc:
        fail(exc)


@app.command()
def review(run_id: str, record_id: str, decision: str, reviewer: str = typer.Option(...),
           note: str = "", revision: int = 0, db: Path = DEFAULT_DB) -> None:
    """Approve, reject or reopen one record. Use the current revision when updating."""
    try:
        updated = Workspace(db).review(run_id, record_id, decision, reviewer=reviewer, note=note, expected_revision=revision)
        emit({"revision": updated, "decision": decision})
    except ValueError as exc:
        fail(exc)


@app.command()
def compare(before: str, after: str, key: str = "account_id", db: Path = DEFAULT_DB) -> None:
    """Compare normalized field values and statuses using a unique, stable key."""
    try:
        emit(Workspace(db).compare(before, after, key=key))
    except ValueError as exc:
        fail(exc)


@app.command()
def export(run_id: str, out: Path = typer.Option(...), db: Path = DEFAULT_DB) -> None:
    """Write only explicitly approved records to a new CSV file."""
    try:
        content = Workspace(db).approved_csv(run_id)
        out.parent.mkdir(parents=True, exist_ok=True)
        with out.open("xb") as destination:
            destination.write(content)
        emit({"output": str(out)})
    except (ValueError, OSError) as exc:
        fail(exc)


@app.command()
def demo(db: Path = DEFAULT_DB) -> None:
    """Load two synthetic company lists and show their changes, without network calls."""
    import tempfile

    workspace = Workspace(db)
    with tempfile.TemporaryDirectory() as directory:
        run_ids = []
        for name in ("accounts_before.csv", "accounts_after.csv"):
            path = Path(directory) / name
            path.write_bytes((files("dataharvest") / "examples" / name).read_bytes())
            run_ids.append(workspace.audit(path, project="Synthetic demo"))
    emit(workspace.compare(*run_ids))
