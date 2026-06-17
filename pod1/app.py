"""
Billing Reconciliation Agent — Streamlit Demo UI
Project: PRJ-NS-7421 · Northstar Civic Group · Cycle 2026-04
"""

import sys
from pathlib import Path
import streamlit as st
import pandas as pd

sys.path.insert(0, str(Path(__file__).parent))

from pipeline import (
    run_pipeline, load_results, load_decisions, save_decision, load_audit_trail,
    CLEAN, FLAG, EXEMPT, ORPHAN, UNREADABLE, MISSING_DOC,
)

# ── Config ───────────────────────────────────────────────────────────────────

st.set_page_config(
    page_title="Reconciliation Agent — PRJ-NS-7421",
    layout="wide",
    initial_sidebar_state="collapsed",
)

STATUS_ICON = {
    CLEAN: "✅",
    FLAG: "🚨",
    EXEMPT: "⚡",
    ORPHAN: "👻",
    UNREADABLE: "❓",
    MISSING_DOC: "📄",
}

STATUS_COLOR = {
    CLEAN: "green",
    FLAG: "red",
    EXEMPT: "blue",
    ORPHAN: "orange",
    UNREADABLE: "gray",
    MISSING_DOC: "yellow",
}

ACTION_COLOR = {
    "APPROVE": "success",
    "REJECT": "error",
    "ADJUST": "warning",
    "ESCALATE": "warning",
}


def badge(status: str) -> str:
    return f"{STATUS_ICON.get(status, '')} {status}"


# ── Shared header ─────────────────────────────────────────────────────────────

def render_header():
    c1, c2 = st.columns([4, 1])
    with c1:
        st.title("Billing Reconciliation Agent")
        st.caption("PRJ-NS-7421 · Northstar Civic Group · MSA-NS-2024-0418 · Cycle 2026-04")
    with c2:
        results = load_results()
        if results:
            flagged = sum(1 for r in results if r["classification"] in (FLAG, MISSING_DOC, ORPHAN))
            decided = len({d["transaction_id"] for d in load_decisions()})
            st.metric("Exceptions", flagged)
            st.metric("Reviewed", decided)


# ── Tab 1: Dashboard ─────────────────────────────────────────────────────────

def tab_dashboard():
    results = load_results()

    # Pipeline control
    col_run, col_status = st.columns([2, 3])
    with col_run:
        run_btn = st.button("▶ Run Reconciliation Pipeline", type="primary", use_container_width=True)

    if run_btn:
        progress_bar = col_status.progress(0, text="Starting pipeline…")

        def on_progress(step, total, msg):
            pct = min(int(step / max(total, 1) * 100), 99)
            progress_bar.progress(pct, text=msg)

        try:
            results = run_pipeline(progress_callback=on_progress)
            progress_bar.progress(100, text="Pipeline complete.")
            st.success(f"Processed {len(results)} items.")
            st.rerun()
        except Exception as e:
            st.error(f"Pipeline error: {e}")
        return

    if not results:
        st.info("No results yet. Click **Run Reconciliation Pipeline** to start.")
        return

    # Summary metrics
    st.divider()
    counts = {}
    for r in results:
        c = r["classification"]
        counts[c] = counts.get(c, 0) + 1

    cols = st.columns(len(STATUS_ICON))
    for i, (status, icon) in enumerate(STATUS_ICON.items()):
        with cols[i]:
            n = counts.get(status, 0)
            st.metric(f"{icon} {status}", n)

    # Auto-resolvable KPI
    st.divider()
    flagged_list = [r for r in results if r["classification"] in (FLAG, MISSING_DOC, ORPHAN)]
    auto_res = [r for r in flagged_list if r.get("auto_resolution") or (r.get("triage", {}) or {}).get("auto_resolvable")]
    if flagged_list:
        pct = len(auto_res) / len(flagged_list) * 100
        st.metric(
            "Auto-resolvable exceptions",
            f"{len(auto_res)} / {len(flagged_list)}",
            delta=f"{pct:.0f}% (target ≥55%)",
            delta_color="normal" if pct >= 55 else "inverse",
        )


# ── Tab 2: Exception Queue ────────────────────────────────────────────────────

def render_exception_detail(r: dict, decisions: list[dict]):
    tx = r.get("transaction_data") or {}
    tx_id = r["transaction_id"]
    decided = next((d for d in decisions if d["transaction_id"] == tx_id), None)

    col_info, col_action = st.columns([3, 2])

    with col_info:
        if tx:
            info_rows = {
                "Description": tx.get("description", "—"),
                "SAP Amount": f"{tx.get('amount', '—')} {tx.get('currency', '')}",
                "Employee": tx.get("employee_id", "—"),
                "Date": tx.get("transaction_date", "—"),
                "Backup Ref": r.get("backup_ref") or "None",
                "Classification": badge(r["classification"]),
                "Confidence": f"{r.get('confidence', 0)*100:.0f}%",
            }
            for k, v in info_rows.items():
                st.markdown(f"**{k}:** {v}")

        if r.get("issues"):
            st.markdown("**Issues:**")
            for issue in r["issues"]:
                st.markdown(f"- {issue}")

        if r.get("non_reimbursable_items"):
            st.markdown("**Non-reimbursable items found:**")
            for item in r["non_reimbursable_items"]:
                st.markdown(f"- ⛔ {item}")

        if r.get("doc_total_extracted") is not None:
            sap_amt = float(tx.get("amount", 0)) if tx else 0
            doc_amt = float(r["doc_total_extracted"])
            delta = doc_amt - sap_amt
            color = "green" if abs(delta) < 0.01 else "red"
            st.markdown(
                f"**Amount check:** SAP {sap_amt:.2f} vs Doc {doc_amt:.2f} "
                f"— <span style='color:{color}'>Δ {delta:+.2f}</span>",
                unsafe_allow_html=True,
            )

        if r.get("analyst_note"):
            st.info(f"**Agent note:** {r['analyst_note']}")

        # Show triage result if available
        triage = r.get("triage")
        if triage and not r.get("auto_resolution"):
            st.markdown("**Triage result:**")
            conf = triage.get("confidence", 0)
            st.markdown(
                f"Auto-resolvable: {'Yes' if triage.get('auto_resolvable') else 'No'} "
                f"({conf*100:.0f}% confidence)  \n"
                f"Suggested: **{triage.get('suggested_action')}**  \n"
                f"Reason: {triage.get('reasoning', '—')}"
            )
            if triage.get("based_on_prior_exception"):
                st.caption(f"Based on: {triage['based_on_prior_exception']}")
            if triage.get("requires_pl_approval"):
                st.warning("⚠ Requires Project Lead approval")

    with col_action:
        auto_res = r.get("auto_resolution")
        if auto_res:
            fn = getattr(st, ACTION_COLOR.get(auto_res.get("action", ""), "info"))
            msg = f"**Suggested: {auto_res.get('action')}**\n\n{auto_res.get('reason', '')}"
            if auto_res.get("adjusted_amount") is not None:
                msg += f"\n\nAdjusted: **${auto_res['adjusted_amount']:.2f}**"
            if auto_res.get("based_on_prior"):
                msg += f"\n\nBased on: `{auto_res['based_on_prior']}`"
            fn(msg)

        if decided:
            st.success(
                f"**Decision recorded**\n"
                f"Action: **{decided['action']}**\n"
                f"Reason: {decided['reason']}\n"
                f"By: {decided['analyst']}\n"
                f"At: {decided['decided_at'][:19].replace('T', ' ')} UTC"
                + ("\n\n⚠ Manual override" if decided.get("override") else "")
            )
        else:
            st.markdown("**Analyst Review**")
            with st.form(f"form_{tx_id}"):
                analyst_name = st.text_input("Your name *")

                # Pre-select from auto_resolution suggestion if available
                default_action = (auto_res or {}).get("action", "APPROVE")
                action_options = ["APPROVE", "REJECT", "ADJUST", "ESCALATE"]
                default_idx = action_options.index(default_action) if default_action in action_options else 0
                action = st.selectbox("Action *", action_options, index=default_idx)

                adjusted_amount = None
                if action == "ADJUST":
                    default_adj = (auto_res or {}).get("adjusted_amount") or 0.0
                    adjusted_amount = st.number_input("Adjusted amount (USD)", min_value=0.0, value=float(default_adj), step=0.01)

                default_reason = (auto_res or {}).get("reason", "")
                reason = st.text_area("Reason / decision notes *", value=default_reason)
                override = st.checkbox("Mark as manual override (overrides agent suggestion)")

                if st.form_submit_button("💾 Save Decision", type="primary"):
                    if not analyst_name.strip() or not reason.strip():
                        st.error("Name and reason are required.")
                    else:
                        save_decision(tx_id, action, reason, analyst_name.strip(), override, adjusted_amount)
                        st.success("Decision saved to audit trail.")
                        st.rerun()


def tab_exceptions():
    results = load_results()
    if not results:
        st.info("Run the pipeline first (Dashboard tab).")
        return

    decisions = load_decisions()
    decided_ids = {d["transaction_id"] for d in decisions}

    flagged = [r for r in results if r["classification"] in (FLAG, MISSING_DOC, ORPHAN, UNREADABLE)]
    pending = [r for r in flagged if r["transaction_id"] not in decided_ids]
    reviewed = [r for r in flagged if r["transaction_id"] in decided_ids]

    c1, c2, c3 = st.columns(3)
    c1.metric("Total Exceptions", len(flagged))
    c2.metric("Pending Review", len(pending))
    c3.metric("Reviewed", len(reviewed))

    st.divider()

    if pending:
        st.subheader("Pending Review")
        for r in pending:
            tx = r.get("transaction_data") or {}
            desc = tx.get("description", "") if tx else f"Orphan: {r.get('backup_ref', '')}"
            amount_str = f"{tx.get('amount', '—')} {tx.get('currency', '')}" if tx else "—"
            label = f"{badge(r['classification'])} &nbsp; **{r['transaction_id']}** — {desc} &nbsp; `{amount_str}`"
            with st.expander(label, expanded=False):
                render_exception_detail(r, decisions)

    if reviewed:
        st.divider()
        st.subheader("Reviewed")
        for r in reviewed:
            tx = r.get("transaction_data") or {}
            desc = tx.get("description", "") if tx else f"Orphan: {r.get('backup_ref', '')}"
            dec = next(d for d in decisions if d["transaction_id"] == r["transaction_id"])
            st.markdown(
                f"- {badge(r['classification'])} **{r['transaction_id']}** — {desc} "
                f"→ **{dec['action']}** by _{dec['analyst']}_ "
                + ("⚠ override" if dec.get("override") else "")
            )


# ── Tab 3: All Transactions ──────────────────────────────────────────────────

def tab_all_transactions():
    results = load_results()
    if not results:
        st.info("Run the pipeline first (Dashboard tab).")
        return

    decisions = load_decisions()
    decided_ids = {d["transaction_id"] for d in decisions}

    rows = []
    for r in results:
        tx = r.get("transaction_data") or {}
        dec = next((d for d in decisions if d["transaction_id"] == r["transaction_id"]), None)
        rows.append({
            "ID": r["transaction_id"],
            "Description": tx.get("description", f"Orphan doc {r.get('backup_ref', '')}") if tx else f"Orphan: {r.get('backup_ref', '')}",
            "SAP Amount": f"{tx.get('amount', '')} {tx.get('currency', '')}" if tx else "—",
            "Employee": tx.get("employee_id", "—") if tx else "—",
            "Backup Doc": r.get("backup_ref") or "—",
            "Status": badge(r["classification"]),
            "Confidence": f"{r.get('confidence', 0)*100:.0f}%" if r.get("confidence") is not None else "—",
            "Reviewed": f"✓ {dec['action']}" if dec else "",
        })

    df = pd.DataFrame(rows)
    st.dataframe(
        df,
        use_container_width=True,
        hide_index=True,
        column_config={
            "Status": st.column_config.TextColumn(width="medium"),
            "Confidence": st.column_config.TextColumn(width="small"),
            "Reviewed": st.column_config.TextColumn(width="small"),
        },
    )

    # Download results as JSON
    import json as _json
    st.download_button(
        "⬇ Download results JSON",
        data=_json.dumps(results, indent=2),
        file_name="reconciliation-results-2026-04.json",
        mime="application/json",
    )


# ── Tab 4: Audit Trail ───────────────────────────────────────────────────────

def tab_audit():
    trail = load_audit_trail()
    if not trail:
        st.info("No audit events yet. Run the pipeline first.")
        return

    st.caption(f"{len(trail)} events · Immutable append-only log · Retention: 2 years")

    # Reverse-chronological
    for event in reversed(trail):
        ts = event.get("timestamp", "")[:19].replace("T", " ")
        etype = event.get("event_type", "")
        p = event.get("payload", {})

        if etype == "ANALYST_DECISION":
            override_note = " ⚠ OVERRIDE" if p.get("override") else ""
            adj = f" → ${p['adjusted_amount']:.2f}" if p.get("adjusted_amount") is not None else ""
            st.markdown(
                f"`{ts} UTC` **DECISION** &nbsp; {p.get('transaction_id')} "
                f"→ **{p.get('action')}**{adj}{override_note} "
                f"by _{p.get('analyst')}_ — _{p.get('reason', '')}_"
            )
        elif etype == "PIPELINE_RUN":
            st.markdown(
                f"`{ts} UTC` **PIPELINE RUN** &nbsp; "
                f"{p.get('expense_transactions')} transactions · "
                f"{p.get('flagged')} flagged · "
                f"{p.get('orphans')} orphans"
            )
        else:
            st.markdown(f"`{ts} UTC` **{etype}**")


# ── Main ──────────────────────────────────────────────────────────────────────

def main():
    render_header()
    st.divider()

    tabs = st.tabs(["📊 Dashboard", "🚨 Exception Queue", "📋 All Transactions", "📜 Audit Trail"])

    with tabs[0]:
        tab_dashboard()

    with tabs[1]:
        tab_exceptions()

    with tabs[2]:
        tab_all_transactions()

    with tabs[3]:
        tab_audit()


if __name__ == "__main__":
    main()
