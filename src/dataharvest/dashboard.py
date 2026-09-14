"""Local company-data workbench. Run through ``dataharvest ui``."""

from __future__ import annotations

import json
import os
import tempfile
from importlib.resources import files
from pathlib import Path

import pandas as pd
import streamlit as st

from dataharvest import __version__
from dataharvest.workspace import Workspace, approval_blockers

STYLE = """
<style>
.stApp {background:#f7fafb;color:#182e3b}
.block-container {max-width:1450px;padding-top:2rem}
h1,h2,h3 {letter-spacing:-.025em}
[data-testid="stSidebar"] {background:#fff;border-right:1px solid #e0e9eb}
[data-testid="stMetric"] {background:white;border:1px solid #dce7ea;border-radius:14px;padding:18px}
[data-testid="stMetricLabel"] {color:#536b78}
[data-testid="stMetricValue"] {color:#087f75}
[data-testid="stBaseButton-primary"] {background:#087f75;border-color:#087f75}
</style>
"""


def demo_runs(workspace: Workspace) -> str:
    with tempfile.TemporaryDirectory() as directory:
        for name in ("accounts_before.csv", "accounts_after.csv"):
            path = Path(directory) / name
            path.write_bytes((files("dataharvest") / "examples" / name).read_bytes())
            run_id = workspace.audit(path, project="Synthetic demo")
    return run_id


def render() -> None:
    st.set_page_config(page_title="DataHarvest | Company data", page_icon="✓", layout="wide")
    st.markdown(STYLE, unsafe_allow_html=True)
    with st.sidebar:
        st.markdown("### DataHarvest")
        st.caption("COMPANY DATA WORKBENCH")
        mode = st.radio("Workspace", ["Company audits", "Extraction projects"], label_visibility="collapsed")
        st.divider()
        st.caption(f"Version {__version__} · Local workspace")
    if mode == "Extraction projects":
        from dataharvest.extraction_ui import render as extraction

        extraction()
        return

    st.title("Company data, ready for review.")
    st.markdown("Audit a client list. Resolve the exceptions. Deliver an approved file with evidence.")
    workspace = Workspace(os.environ.get("DATAHARVEST_WORKSPACE", "data/workspace/audits.sqlite3"))
    with st.expander("Import a company list", expanded=not workspace.list_runs()):
        col_upload, col_context = st.columns([2, 1])
        with col_upload:
            upload = st.file_uploader("Company list", type=["csv", "tsv", "xlsx"],
                                      help="Up to 5,000 rows / 20 MiB. Include a stable Account ID and Company name.")
        with col_context:
            project = st.text_input("Client / project", value="Client accounts", max_chars=120)
            country = st.selectbox("Country rules", ["BE", "NL", "FR", "DE", "GB", "US"])
        mappings = st.text_area("Column mapping (optional)", placeholder="Customer number=account_id\nBusiness=company_name",
                               help="Use one Input column=field name per line when automatic matching is ambiguous.")
        st.caption("Account ID and Company name are required. Import runs locally and makes no website or mailbox requests.")
        left, right = st.columns(2)
        if left.button("Audit company list", type="primary", disabled=upload is None, width="stretch"):
            try:
                mapping = {}
                for line in mappings.splitlines():
                    if not line.strip():
                        continue
                    key, separator, value = line.partition("=")
                    if not separator or not key.strip() or not value.strip():
                        raise ValueError("Use one Input column=field name mapping per line.")
                    mapping[key.strip()] = value.strip()
                with tempfile.TemporaryDirectory() as directory:
                    path = Path(directory) / Path(upload.name.replace("\\", "/")).name
                    path.write_bytes(upload.getvalue())
                    with st.spinner("Checking formats, required fields and duplicate candidates…"):
                        run_id = workspace.audit(path, project=project, country=country, mapping=mapping)
                st.session_state["active_run"] = run_id
                st.rerun()
            except (ValueError, OSError) as exc:
                st.error(str(exc))
        if right.button("Load sample workspace", width="stretch"):
            st.session_state["active_run"] = demo_runs(workspace)
            st.rerun()

    runs = workspace.list_runs()
    if not runs:
        st.info("Import your company list or load the synthetic sample to explore audits, review decisions and changes.")
        a, b, c = st.columns(3)
        a.markdown("#### 01 · Audit\nFind missing fields, invalid formats and duplicate candidates.")
        b.markdown("#### 02 · Review\nRecord a decision while keeping the original evidence.")
        c.markdown("#### 03 · Deliver\nExport approved records and compare the next revision.")
        return
    by_id = {run["run_id"]: run for run in runs}
    selected = st.session_state.get("active_run", runs[0]["run_id"])
    if selected not in by_id:
        selected = runs[0]["run_id"]
    selected = st.selectbox("Saved audit", list(by_id), index=list(by_id).index(selected),
                            format_func=lambda rid: f"{by_id[rid]['project']} · {by_id[rid]['input_name']} · {by_id[rid]['created_at'][:19]} · {rid[:6]}")
    st.session_state["active_run"] = selected
    run = workspace.load(selected)
    records, reviews, schema = run["records"], run["reviews"], run["schema"]
    approved = sum(d["decision"] == "approved" for d in reviews.values())
    blocked = sum(bool(approval_blockers(r, schema)) for r in records)
    a, b, c, d = st.columns(4)
    a.metric("Company records", len(records))
    b.metric("Corrections required", blocked)
    c.metric("Awaiting decision", len(records) - sum(v["decision"] != "pending" for v in reviews.values()))
    d.metric("Approved for export", approved)
    st.caption("Format checks do not confirm mailbox delivery, company ownership or permission to contact. Human approval is recorded separately.")
    overview, review, changes, delivery = st.tabs(["Data quality", "Review queue", "Changes between runs", "Delivery & history"])

    with overview:
        query = st.text_input("Find a company", placeholder="Search by name, account ID, website or email")
        rows = []
        for record in records:
            row = {**{field.display: record.get(field.name, "") for field in schema.fields}, "Status": record.status.value,
                   "Decision": reviews.get(record.record_id, {}).get("decision", "pending"), "Findings": " | ".join(record.flags)}
            if not query or query.casefold() in " ".join(str(v) for v in row.values()).casefold():
                rows.append(row)
        st.dataframe(pd.DataFrame(rows), width="stretch", hide_index=True, height=300)
        st.caption(f"{len(rows)} records shown · {run['report']['duplicate_groups']} duplicate candidate groups · No records silently removed")
        for warning in run["report"]["warnings"]:
            st.warning(warning)
        with st.expander("Import mapping and input fingerprint"):
            st.json({"columns": run["report"].get("column_map"), "input_sha256": run["input_sha256"]})

    with review:
        names = {r.record_id: f"{r.get('account_id', r.record_id)} · {r.get(schema.name_field, '(missing name)')}" for r in records}
        record_id = st.selectbox("Record to review", list(names), format_func=names.get, key=f"record-{selected}")
        record = next(r for r in records if r.record_id == record_id)
        decision = reviews.get(record_id, {})
        blockers = approval_blockers(record, schema)
        detail, action = st.columns([2, 1])
        with detail:
            st.markdown("#### Evidence and findings")
            if blockers:
                st.error("Correct these fields in the source list and import a new audit: " + "; ".join(blockers))
            for flag in record.flags:
                st.warning(flag)
            st.dataframe(pd.DataFrame([{"Field": schema.field(name).display if schema.field(name) else name, "Value": "" if fv.value is None else str(fv.value), "Check": fv.status.value,
                                       "Source": fv.source, "Evidence": fv.note} for name, fv in record.fields.items()]),
                         hide_index=True, width="stretch")
        with action:
            st.markdown("#### Review decision")
            st.caption(f"Current: {decision.get('decision', 'pending')} · revision {decision.get('revision', 0)}")
            revision_key = f"revision-{selected}-{record_id}"
            st.session_state.setdefault(revision_key, decision.get("revision", 0))
            with st.form(f"decision-{selected}-{record_id}"):
                reviewer = st.text_input("Reviewer name", value=decision.get("reviewer", ""), max_chars=120)
                options = ["pending", "rejected"] if blockers else ["pending", "approved", "rejected"]
                value = decision.get("decision", "pending")
                choice = st.selectbox("Decision", options, index=options.index(value) if value in options else 0)
                note = st.text_area("Review note", value=decision.get("note", ""), max_chars=4000,
                                    help="Explain any accepted flags or a rejection. Approval does not change verification evidence.")
                submitted = st.form_submit_button("Save decision", type="primary")
            if submitted:
                try:
                    workspace.review(selected, record_id, choice, reviewer=reviewer, note=note,
                                     expected_revision=st.session_state[revision_key])
                    del st.session_state[revision_key]
                    st.rerun()
                except ValueError as exc:
                    st.error(str(exc))
            if st.button("Refresh record", key=f"refresh-{selected}-{record_id}"):
                st.session_state.pop(revision_key, None)
                st.rerun()

    with changes:
        earlier = [r for r in runs if r["run_id"] != selected and r["project"] == run["project"] and r["created_at"] < run["created_at"]]
        if not earlier:
            st.info("Import another revision under the same client / project to compare changes.")
        else:
            before = st.selectbox("Compare with earlier audit", [r["run_id"] for r in earlier],
                                  format_func=lambda rid: f"{by_id[rid]['input_name']} · {by_id[rid]['created_at'][:19]} · {rid[:6]}")
            try:
                comparison = workspace.compare(before, selected)
                a, b, c, d = st.columns(4)
                a.metric("Added", len(comparison["added"]))
                b.metric("Removed from input", len(comparison["removed"]))
                c.metric("Changed", len(comparison["changed"]))
                d.metric("Unchanged", comparison["unchanged"])
                st.caption("Matching uses Account ID. Removed means absent from this input; it does not establish that a company closed.")
                changes_rows = [{"Account ID": r["key"], **field} for r in comparison["changed"] for field in r["fields"]]
                st.dataframe(pd.DataFrame(changes_rows), hide_index=True, width="stretch")
                st.download_button("Download comparison JSON", json.dumps(comparison, ensure_ascii=False, indent=2),
                                   file_name=f"changes_{selected[:8]}.json", mime="application/json")
            except ValueError as exc:
                st.error(str(exc))

    with delivery:
        st.markdown("#### Approved company list")
        st.write(f"{approved} of {len(records)} records have an explicit approval. Pending and rejected records stay out of this export.")
        st.download_button("Download approved CSV", workspace.approved_csv(selected),
                           file_name=f"approved_{selected[:8]}.csv", mime="text/csv", disabled=not approved, type="primary")
        st.caption("CSV is protected for spreadsheet viewing. Use the evidence JSON when exact original text values are needed.")
        evidence = {"run_id": selected, "input_sha256": run["input_sha256"], "schema": schema.model_dump(),
                    "report": run["report"], "records": [r.as_dict() for r in records], "reviews": reviews,
                    "events": workspace.events(selected)}
        st.download_button("Download full audit evidence", json.dumps(evidence, ensure_ascii=False, indent=2, default=str),
                           file_name=f"audit_{selected[:8]}.json", mime="application/json")
        st.markdown("#### Decision history")
        events = workspace.events(selected)
        if events:
            st.dataframe(pd.DataFrame(events), hide_index=True, width="stretch")
        else:
            st.info("No decisions recorded yet.")
        with st.expander("Remove this saved audit"):
            confirm = st.checkbox("Delete this run and its review history from this local workspace", key=f"delete-{selected}")
            if st.button("Delete saved audit", disabled=not confirm):
                workspace.delete_run(selected)
                st.session_state.pop("active_run", None)
                st.rerun()


if __name__ == "__main__":
    render()
