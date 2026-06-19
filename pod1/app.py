"""
Billing Reconciliation Agent — Streamlit Demo UI
Project: PRJ-NS-7421 · Northstar Civic Group · Cycle 2026-04
"""

import sys
import time as _time
from pathlib import Path
import streamlit as st
import pandas as pd

sys.path.insert(0, str(Path(__file__).parent))

from pipeline import (
    run_pipeline, load_results, load_decisions, save_decision, clear_decisions, load_audit_trail,
    CLEAN, FLAG, EXEMPT, ORPHAN, UNREADABLE, MISSING_DOC,
)
import ruleset_builder
from data_loader import (
    save_uploaded_transactions, save_uploaded_contract, save_reference_file,
    clear_uploaded_transactions, clear_reference_data,
    get_transactions_source, get_reference_data_summary,
    UPLOADED_TRANSACTIONS_FILE, UPLOADED_CONTRACT_FILE,
    load_pm_instructions, load_restricted_docs,
    CATEGORIES,
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


# ── Tab 0: Setup Ruleset ─────────────────────────────────────────────────────

_CATEGORY_ICON = {
    "exceptions": "📋",
    "document": "🧾",
    "restricted": "🔒",
    "pm_instructions": "📧",
}


def tab_setup_ruleset():
    ruleset = ruleset_builder.load_ruleset()

    # ── Status banner ────────────────────────────────────────────────────────
    if ruleset:
        built_at = ruleset.get("built_at", "")[:19].replace("T", " ")
        st.success(
            f"Ruleset active — built {built_at} UTC  |  "
            f"Contract: `{ruleset.get('contract_id', '—')}`  |  "
            f"Project: `{ruleset.get('project_id', '—')}`"
        )
        col_caps, col_exc = st.columns(2)
        with col_caps:
            caps = ruleset.get("caps", {})
            if caps:
                st.subheader("Extracted Caps")
                st.table(pd.DataFrame(
                    [{"Rule": k.replace("_", " ").title(), "Value": str(v)} for k, v in caps.items()]
                ))
        with col_exc:
            patterns = ruleset.get("prior_exception_patterns", [])
            if patterns:
                st.subheader(f"Exception Patterns ({len(patterns)} recurring)")
                for p in patterns:
                    st.markdown(
                        f"- **{p.get('exception_id', '—')}** · {p.get('exception_type', '')} "
                        f"→ **{p.get('resolution', '')}**"
                    )
            pm_rules = ruleset.get("pm_rules", [])
            if pm_rules:
                st.subheader("PM Rules")
                for r in pm_rules:
                    st.markdown(f"- {r}")

        # Uploaded reference data summary
        ref_summary = get_reference_data_summary()
        if ref_summary:
            st.subheader("Uploaded Reference Data")
            st.table(pd.DataFrame([
                {
                    "File": r["file"],
                    "Type": f"{_CATEGORY_ICON.get(r['category'], '')} {r['label']}",
                }
                for r in ref_summary
            ]))

        st.divider()
        st.caption("Re-upload files below to rebuild with new data.")
    else:
        st.info(
            "No ruleset built yet. Upload the contract and reference data below, "
            "then click **Build Ruleset**."
        )

    # ── Upload section ───────────────────────────────────────────────────────
    st.subheader("1 · Contract")
    contract_file = st.file_uploader(
        "Master Services Agreement",
        type=["md", "txt"],
        key="setup_contract",
        help="MSA with billing caps and reimbursement rules (e.g. contract-001.md)",
    )

    st.subheader("2 · Reference Data")
    st.caption(
        "Upload all files used to determine reconciliation confidence: "
        "prior exceptions (CSV), PM instructions (md/txt), backup documents (RC-*/VI-*/ML-*), "
        "and restricted reference docs. Files are auto-categorised by name."
    )
    ref_files = st.file_uploader(
        "Reference files",
        type=["md", "txt", "csv"],
        accept_multiple_files=True,
        key="setup_refs",
        label_visibility="collapsed",
    )

    # Show live categorisation preview
    if ref_files:
        preview_rows = []
        for f in ref_files:
            from data_loader import categorize_reference_file
            cat = categorize_reference_file(f.name)
            preview_rows.append({
                "File": f.name,
                "Detected Type": f"{_CATEGORY_ICON.get(cat, '')} {CATEGORIES[cat]}",
            })
        st.table(pd.DataFrame(preview_rows))

    # ── Action buttons ───────────────────────────────────────────────────────
    contract_ready = contract_file is not None
    refs_ready = len(ref_files) > 0 if ref_files else False

    col_build, col_reset = st.columns([3, 1])

    with col_build:
        build_btn = st.button(
            "⚙ Build Ruleset",
            type="primary",
            disabled=not contract_ready,
            use_container_width=True,
            help="Contract is required. Reference data is optional but improves confidence.",
        )

    with col_reset:
        if ruleset or refs_ready:
            if st.button("🗑 Reset All", use_container_width=True):
                ruleset_builder.clear_ruleset()
                clear_reference_data()
                st.success("Ruleset and reference data cleared.")
                st.rerun()

    if build_btn and contract_ready:
        contract_text = contract_file.read().decode("utf-8")
        save_uploaded_contract(contract_text)

        exceptions_text = ""
        pm_text = ""
        restricted_text = ""

        if ref_files:
            with st.spinner("Saving reference files…"):
                for f in ref_files:
                    content = f.read().decode("utf-8")
                    save_reference_file(f.name, content)
                    from data_loader import categorize_reference_file
                    cat = categorize_reference_file(f.name)
                    if cat == "exceptions":
                        exceptions_text += content + "\n"
                    elif cat == "pm_instructions":
                        pm_text += f"\n## {f.name}\n\n" + content
                    elif cat == "restricted":
                        restricted_text += f"\n## {f.name}\n\n" + content

        with st.spinner("Extracting rules from uploaded data…"):
            try:
                result = ruleset_builder.build_ruleset(
                    contract_text,
                    exceptions_text,
                    pm_instructions_text=pm_text,
                    restricted_docs_text=restricted_text,
                )
                n_exc = len(result.get("prior_exception_patterns", []))
                n_pm = len(result.get("pm_rules", []))
                st.success(
                    f"Ruleset built — {n_exc} recurring exception patterns, "
                    f"{n_pm} PM rules extracted."
                )
                st.rerun()
            except Exception as e:
                st.error(f"Build failed: {e}")


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

    # SAP transactions upload
    with st.expander("📂 SAP Transactions Data Source", expanded=not bool(UPLOADED_TRANSACTIONS_FILE.exists())):
        source_label = get_transactions_source()
        st.caption(f"Active source: **{source_label}**")
        uploaded = st.file_uploader(
            "Upload SAP transactions CSV",
            type=["csv"],
            key="dashboard_transactions",
            help="e.g. unbilled-2026-04.csv — SAP unbilled expense transactions",
        )
        col_save, col_clear = st.columns([2, 1])
        with col_save:
            if uploaded and st.button("💾 Use this file", use_container_width=True):
                save_uploaded_transactions(uploaded.read().decode("utf-8"))
                st.success(f"Saved: {uploaded.name}")
                st.rerun()
        with col_clear:
            if UPLOADED_TRANSACTIONS_FILE.exists():
                if st.button("↩ Revert to sample", use_container_width=True):
                    clear_uploaded_transactions()
                    st.rerun()

    st.divider()

    run_btn = st.button("▶ Run Reconciliation Pipeline", type="primary")

    if run_btn:
        # ── Progress UI ──────────────────────────────────────────────────────
        col_cls, col_tri = st.columns(2)
        with col_cls:
            st.caption("📊 Classification")
            cls_bar   = st.progress(0.0)
            cls_label = st.empty()
        with col_tri:
            st.caption("🔍 Triage")
            tri_bar   = st.progress(0.0)
            tri_label = st.empty()
        countdown_el = st.empty()

        _s = {
            "cls_total": 0, "cls_done": 0,
            "tri_total": 0, "tri_done": 0,
            "start":       _time.time(),
            "tri_start":   None,
        }

        def _fmt(secs: float) -> str:
            secs = max(0, int(secs))
            m, s = divmod(secs, 60)
            return f"{m}m {s:02d}s" if m else f"{s}s"

        def on_progress(step: int, total: int, _msg: str, phase: str = "classify"):
            now     = _time.time()
            elapsed = now - _s["start"]
            done    = step + 1
            pending = total - done

            if phase == "classify":
                _s["cls_total"] = total
                _s["cls_done"]  = done
                cls_bar.progress(done / max(total, 1))
                cls_label.markdown(
                    f"✅ **{done}** done &nbsp;·&nbsp; "
                    f"⏳ **{pending}** pending &nbsp;·&nbsp; total **{total}**"
                )
                if done > 1:
                    rate      = elapsed / done          # secs per classify step
                    rem_cls   = pending * rate
                    est_tri   = total * 0.35 * rate * 0.7  # ~35% flagged, triage faster
                    countdown_el.info(f"⏱ Est. **{_fmt(rem_cls + est_tri)}** remaining")

            elif phase == "triage":
                if _s["tri_start"] is None:
                    _s["tri_start"] = now
                _s["tri_total"] = total
                _s["tri_done"]  = done
                tri_bar.progress(done / max(total, 1))
                tri_label.markdown(
                    f"✅ **{done}** done &nbsp;·&nbsp; "
                    f"⏳ **{pending}** pending &nbsp;·&nbsp; total **{total}**"
                )
                tri_elapsed = now - _s["tri_start"]
                if done > 0:
                    rate = tri_elapsed / done
                    countdown_el.info(f"⏱ Est. **{_fmt(pending * rate)}** remaining (triage)")

        try:
            results = run_pipeline(progress_callback=on_progress)
            cls_bar.progress(1.0)
            tri_bar.progress(1.0)
            cls_label.markdown(f"✅ **{_s['cls_total']}** classified &nbsp;·&nbsp; 🏁 complete")
            tri_label.markdown(
                f"✅ **{_s['tri_total']}** triaged &nbsp;·&nbsp; 🏁 complete"
                if _s["tri_total"] else "— no exceptions to triage"
            )
            countdown_el.success(f"✅ Pipeline complete — {len(results)} items processed.")
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

    c1, c2, c3, c4 = st.columns([2, 2, 2, 1])
    c1.metric("Total Exceptions", len(flagged))
    c2.metric("Pending Review", len(pending))
    c3.metric("Reviewed", len(reviewed))
    with c4:
        if reviewed and st.button("🗑 Reset Decisions", use_container_width=True, help="Clear all analyst decisions — action is logged to audit trail"):
            clear_decisions()
            st.success("All decisions cleared.")
            st.rerun()

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


# ── Tab 5: KPI & Savings ─────────────────────────────────────────────────────

def tab_kpi():
    results = load_results()
    if not results:
        st.info("Run the pipeline first (Dashboard tab).")
        return

    # ── Metrics from this run ─────────────────────────────────────────────────
    total_run = len(results)
    n_clean   = sum(1 for r in results if r["classification"] == CLEAN)
    n_exempt  = sum(1 for r in results if r["classification"] == EXEMPT)
    n_flag    = sum(1 for r in results if r["classification"] in (FLAG, MISSING_DOC, ORPHAN, UNREADABLE))
    n_auto    = sum(
        1 for r in results
        if r["classification"] not in (CLEAN, EXEMPT)
        and (r.get("auto_resolution") or (r.get("triage") or {}).get("auto_resolvable"))
    )
    n_manual  = n_flag - n_auto
    avg_conf  = sum(r.get("confidence", 0) for r in results) / max(total_run, 1)

    auto_clear_rate = (n_clean + n_exempt) / max(total_run, 1)
    auto_res_rate   = n_auto / max(n_flag, 1)
    manual_rate     = n_manual / max(total_run, 1)
    auto_res_of_all = n_auto / max(total_run, 1)

    # ── Baseline assumptions ──────────────────────────────────────────────────
    MONTHLY_VOL      = 20_000
    ANALYST_COUNT    = 200
    ANALYST_RATE     = 85       # USD loaded cost / hr
    MANUAL_MINS      = 45       # avg mins per invoice, manual end-to-end
    WORKING_HRS_MO   = 160      # hrs per analyst per month

    MIN_SPOT_CHECK   = 1.5      # auto-cleared: QC glance only
    MIN_CONFIRM      = 8.0      # agent-suggested: analyst confirms
    MIN_FULL_REVIEW  = 28.0     # no suggestion: analyst investigates

    # ── Manual baseline ───────────────────────────────────────────────────────
    manual_hrs  = MONTHLY_VOL * MANUAL_MINS / 60
    manual_cost = manual_hrs * ANALYST_RATE
    manual_ftes = manual_hrs / WORKING_HRS_MO

    # ── Agent-assisted projection ─────────────────────────────────────────────
    agent_hrs = MONTHLY_VOL * (
        auto_clear_rate * MIN_SPOT_CHECK +
        auto_res_of_all * MIN_CONFIRM +
        manual_rate     * MIN_FULL_REVIEW
    ) / 60
    agent_cost = agent_hrs * ANALYST_RATE
    agent_ftes = agent_hrs / WORKING_HRS_MO

    hrs_saved   = manual_hrs  - agent_hrs
    cost_saved  = manual_cost - agent_cost
    ftes_freed  = manual_ftes - agent_ftes
    speed_mult  = manual_hrs  / max(agent_hrs, 1)

    # ── Section 1: headline KPIs ──────────────────────────────────────────────
    st.subheader("Projected Monthly Impact — 20,000 invoices · 200 analysts")
    k1, k2, k3, k4 = st.columns(4)
    k1.metric("Analyst Hours Saved",  f"{hrs_saved:,.0f} hrs",
              delta=f"−{hrs_saved / manual_hrs * 100:.0f}% vs manual")
    k2.metric("Cost Savings",         f"${cost_saved:,.0f} / mo",
              delta=f"−{cost_saved / manual_cost * 100:.0f}% vs manual")
    k3.metric("FTEs Redeployable",    f"{ftes_freed:.0f}",
              delta=f"of {manual_ftes:.0f} billing FTEs")
    k4.metric("Throughput Multiplier", f"{speed_mult:.1f}×",
              delta="faster than current process")

    st.divider()

    # ── Section 2: this run ───────────────────────────────────────────────────
    st.subheader(f"This Pipeline Run — {total_run} transactions")
    r1, r2, r3, r4 = st.columns(4)
    r1.metric("Auto-cleared",            f"{n_clean + n_exempt} / {total_run}",
              delta=f"{auto_clear_rate * 100:.0f}%  — zero analyst time")
    r2.metric("Auto-resolved exceptions", f"{n_auto} / {n_flag}",
              delta=f"{auto_res_rate * 100:.0f}% of flagged items")
    r3.metric("Needs manual review",      f"{n_manual} / {total_run}",
              delta=f"{manual_rate * 100:.0f}% of transactions")
    r4.metric("Avg agent confidence",     f"{avg_conf * 100:.0f}%")

    st.divider()

    # ── Section 3: charts ─────────────────────────────────────────────────────
    col_left, col_right = st.columns(2)

    with col_left:
        st.subheader("Monthly Analyst Hours")
        st.caption("Manual process vs agent-assisted — breakdown by work type")
        hrs_df = pd.DataFrame(
            {
                "Auto-cleared (spot check)": [
                    MONTHLY_VOL * auto_clear_rate * MANUAL_MINS / 60,
                    MONTHLY_VOL * auto_clear_rate * MIN_SPOT_CHECK / 60,
                ],
                "Agent-suggested (confirm)": [
                    MONTHLY_VOL * auto_res_of_all * MANUAL_MINS / 60,
                    MONTHLY_VOL * auto_res_of_all * MIN_CONFIRM / 60,
                ],
                "Full manual review": [
                    MONTHLY_VOL * manual_rate * MANUAL_MINS / 60,
                    MONTHLY_VOL * manual_rate * MIN_FULL_REVIEW / 60,
                ],
            },
            index=["Manual Process", "Agent-Assisted"],
        )
        st.bar_chart(hrs_df, use_container_width=True)
        st.caption(
            f"Manual total: **{manual_hrs:,.0f} hrs/mo** &nbsp;→&nbsp; "
            f"Agent total: **{agent_hrs:,.0f} hrs/mo**"
        )

    with col_right:
        st.subheader("Invoice Disposition")
        st.caption(f"From this run — extrapolated to {MONTHLY_VOL:,} monthly invoices")
        disp_df = pd.DataFrame(
            {"Invoices": [
                round(MONTHLY_VOL * n_clean   / max(total_run, 1)),
                round(MONTHLY_VOL * n_exempt  / max(total_run, 1)),
                round(MONTHLY_VOL * n_auto    / max(total_run, 1)),
                round(MONTHLY_VOL * n_manual  / max(total_run, 1)),
            ]},
            index=["✅ CLEAN", "⚡ EXEMPT", "🤖 Auto-resolved", "👤 Manual review"],
        )
        st.bar_chart(disp_df, use_container_width=True)
        st.caption(
            f"Manual review queue shrinks from **{MONTHLY_VOL:,}** to "
            f"**{round(MONTHLY_VOL * manual_rate):,}** invoices/mo"
        )

    st.divider()

    # ── Section 4: assumptions ────────────────────────────────────────────────
    with st.expander("📋 Assumptions used in projection"):
        st.markdown(f"""
| Parameter | Value |
|---|---|
| Monthly invoice volume | {MONTHLY_VOL:,} |
| Current billing analysts | {ANALYST_COUNT} |
| Manual review time per invoice | {MANUAL_MINS} min |
| Analyst loaded cost | ${ANALYST_RATE}/hr |
| Working hours per analyst per month | {WORKING_HRS_MO} hrs |
| Agent — auto-cleared (spot check) | {MIN_SPOT_CHECK} min |
| Agent — exception confirmation | {MIN_CONFIRM} min |
| Agent — full manual investigation | {MIN_FULL_REVIEW} min |
        """)
        st.caption(
            "Auto-clear rate, auto-resolution rate, and manual rate are derived from this pipeline run "
            f"({total_run} transactions) and applied to the full monthly volume. "
            "Actual savings will vary based on contract complexity and exception volume."
        )


# ── Main ──────────────────────────────────────────────────────────────────────

def main():
    render_header()
    st.divider()

    tabs = st.tabs(["⚙ Setup Ruleset", "📊 Dashboard", "🚨 Exception Queue", "📋 All Transactions", "📈 KPI & Savings", "📜 Audit Trail"])

    with tabs[0]:
        tab_setup_ruleset()

    with tabs[1]:
        tab_dashboard()

    with tabs[2]:
        tab_exceptions()

    with tabs[3]:
        tab_all_transactions()

    with tabs[4]:
        tab_kpi()

    with tabs[5]:
        tab_audit()


if __name__ == "__main__":
    main()
