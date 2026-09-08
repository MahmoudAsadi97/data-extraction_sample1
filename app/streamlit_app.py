"""Browser dashboard for DataHarvest: pick a project, run it, review the results, download the files.

    pip install streamlit pandas
    dataharvest ui            (or: streamlit run app/streamlit_app.py)
"""

from __future__ import annotations

import sys
from pathlib import Path

import pandas as pd
import streamlit as st

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from dataharvest import __version__  # noqa: E402
from dataharvest.config import load_project  # noqa: E402
from dataharvest.export.columns import build_columns, record_row  # noqa: E402
from dataharvest.models import RecordStatus  # noqa: E402
from dataharvest.pipeline import Pipeline  # noqa: E402
from dataharvest.sources import SourceError, all_sources  # noqa: E402

st.set_page_config(page_title="DataHarvest", page_icon="📊", layout="wide")
PROJECT_DIR = ROOT / "projects"
STATUS_COLOURS = {
    RecordStatus.VERIFIED.value: "#C6EFCE",
    RecordStatus.PARTIALLY_VERIFIED.value: "#DDEBF7",
    RecordStatus.UNVERIFIED.value: "#EDEDED",
    RecordStatus.NEEDS_REVIEW.value: "#FFC7CE",
    RecordStatus.EXCLUDED.value: "#FFEB9C",
}


def project_files() -> list[Path]:
    return sorted(PROJECT_DIR.glob("*.yaml")) if PROJECT_DIR.exists() else []


def colour_status(value: str) -> str:
    return f"background-color: {STATUS_COLOURS.get(value, '')}"


st.title("DataHarvest - extract, verify, deliver")
st.caption(f"v{__version__} · projects in `{PROJECT_DIR.relative_to(ROOT)}/`")

with st.sidebar:
    st.header("Project")
    files = project_files()
    if not files:
        st.error("No project files found in projects/. Create one with `dataharvest init NAME`.")
        st.stop()
    chosen = st.selectbox("Project file", files, format_func=lambda p: p.name)
    cfg = load_project(chosen)
    st.markdown(f"**{cfg.project.title}**")
    if cfg.project.instructions:
        st.caption(cfg.project.instructions.strip())
    st.markdown("**Sources:** " + ", ".join(s.label for s in cfg.active_sources))
    st.divider()
    st.header("Run options")
    limit = st.number_input("Record limit (0 = all)", min_value=0, value=0, step=10)
    offline = st.checkbox("Offline (skip web enrichment & verification)", value=False)
    no_cache = st.checkbox("Ignore cached pages", value=False)
    website_enrich = st.checkbox("Website enrichment", value=cfg.enrichment.website.enabled)
    search_missing = st.checkbox("Search for missing websites", value=cfg.enrichment.find_missing_websites.enabled)
    vies = st.checkbox("VIES VAT check", value=cfg.enrichment.vies.enabled)
    gsheets = st.checkbox("Push to Google Sheets (needs credentials)", value=False)
    run_clicked = st.button("Run extraction", type="primary", use_container_width=True)
    st.divider()
    with st.expander("Available sources"):
        for name, cls in all_sources().items():
            ok, reason = cls.availability()
            st.markdown(f"{'✅' if ok else '⚠️'} `{name}` - {reason}")

if run_clicked:
    cfg.enrichment.website.enabled = website_enrich
    cfg.enrichment.find_missing_websites.enabled = search_missing
    cfg.enrichment.vies.enabled = vies
    cfg.output.google_sheets.enabled = gsheets
    bar = st.progress(0, text="starting…")
    status_box = st.empty()

    def on_progress(stage: str, done: int, total: int) -> None:
        pct = int(100 * done / max(total, 1))
        bar.progress(min(pct, 100), text=f"{stage}: {done}/{total}")

    try:
        with st.spinner("Running pipeline…"):
            pipeline = Pipeline(cfg, limit=int(limit) or None, offline=offline, no_cache=no_cache, progress=on_progress)
            result = pipeline.run(export=True)
    except SourceError as exc:
        st.error(f"Extraction failed: {exc}")
        st.stop()
    bar.progress(100, text="done")
    st.session_state["result"] = result
    st.session_state["cfg"] = cfg

result = st.session_state.get("result")
if result is None:
    st.info("Choose a project in the sidebar and click **Run extraction**. Results, review flags and download links appear here.")
    st.stop()

cfg = st.session_state["cfg"]
rep = result.report
schema = cfg.schema_def
columns = build_columns(schema)

c1, c2, c3, c4, c5 = st.columns(5)
c1.metric("Extracted", rep.records_total)
c2.metric("Delivered", rep.records_delivered)
c3.metric("Verified", rep.status_counts.get("VERIFIED", 0))
c4.metric("Needs review", rep.status_counts.get("NEEDS_REVIEW", 0))
c5.metric("Duplicate groups", rep.duplicate_groups)

if rep.warnings:
    with st.expander(f"Warnings ({len(rep.warnings)})", expanded=False):
        for w in rep.warnings:
            st.warning(w)

tab_data, tab_review, tab_dups, tab_report, tab_files = st.tabs(["Data", "Needs review", "Duplicates", "Report", "Files"])

with tab_data:
    delivered = [r for r in result.records if r.status != RecordStatus.EXCLUDED]
    df = pd.DataFrame([["" if v is None else v for v in record_row(r, columns, country=cfg.project.country)] for r in delivered],
                      columns=[c.header for c in columns])
    statuses = st.multiselect("Filter by status", [s.value for s in RecordStatus if s != RecordStatus.EXCLUDED],
                              default=[s.value for s in RecordStatus if s != RecordStatus.EXCLUDED])
    query = st.text_input("Search (name, e-mail, website…)")
    view = df[df["Verification status"].isin(statuses)]
    if query:
        mask = view.astype(str).apply(lambda col: col.str.contains(query, case=False, na=False)).any(axis=1)
        view = view[mask]
    st.caption(f"{len(view)} of {len(df)} delivered records")
    st.dataframe(view.style.map(colour_status, subset=["Verification status"]), use_container_width=True, height=520)

with tab_review:
    review = [r for r in result.records if r.status in (RecordStatus.NEEDS_REVIEW, RecordStatus.EXCLUDED) and not r.duplicate_of]
    if not review:
        st.success("Nothing to review - every record passed the automatic checks.")
    name_field = schema.name_field or "company_name"
    for r in review:
        with st.expander(f"{r.record_id} · {r.get(name_field) or '(no name)'} · {r.status.value}"):
            for f in r.flags:
                (st.info if f.startswith("note:") else st.warning)(f)
            st.json({k: fv.as_dict() for k, fv in r.fields.items() if not fv.is_empty}, expanded=False)
            if r.source_url:
                st.markdown(f"Source: {r.source_url}")

with tab_dups:
    if not result.groups:
        st.success("No duplicates detected.")
    else:
        by_id = {r.record_id: r for r in result.records}
        rows = []
        for g in result.groups:
            master = by_id.get(g.master_id)
            rows.append({
                "Group": g.group_id,
                "Action": "merged" if g.merged else "possible - decide",
                "Kept": f"{g.master_id} {master.get(name_field) if master else ''}",
                "Others": ", ".join(f"{m} {by_id[m].get(name_field) or ''}" for m in g.member_ids if m != g.master_id and m in by_id),
                "Reason": g.reason,
                "Similarity": round(g.similarity),
            })
        st.dataframe(pd.DataFrame(rows), use_container_width=True)

with tab_report:
    st.markdown(rep.to_markdown())

with tab_files:
    for kind, path in result.outputs.items():
        p = Path(path)
        if p.exists() and p.is_file():
            mime = {"xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet", "csv": "text/csv", "json": "application/json"}.get(kind, "text/plain")
            st.download_button(f"Download {p.name}", data=p.read_bytes(), file_name=p.name, mime=mime, key=f"dl-{kind}")
        else:
            st.markdown(f"**{kind}**: {path}")
