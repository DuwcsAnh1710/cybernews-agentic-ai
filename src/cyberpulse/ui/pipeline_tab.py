"""Pipeline Runner tab (Tab 5) for Streamlit Workbench UI.

Interactive interface to execute CLI steps, monitor realtime logs,
and manage pipeline executions with atomic locking.
"""

from __future__ import annotations

from datetime import datetime, timezone
import os
from pathlib import Path
from typing import Any

import streamlit as st

from cyberpulse.ui.runner import (
    JobRecord,
    JobStatus,
    PipelineBusyError,
    cancel_job,
    get_job,
    get_lock_path,
    list_jobs,
    read_lock_info,
    read_log_tail,
    start_job,
)


def _list_research_files(output_dir: Path) -> list[Path]:
    """List valid research JSON files, excluding .original.json."""
    r_dir = output_dir / "research"
    if not r_dir.exists() or not r_dir.is_dir():
        return []
    files = [f for f in r_dir.glob("*.json") if not f.name.endswith(".original.json")]
    files.sort(key=lambda f: f.stat().st_mtime, reverse=True)
    return files


def _list_editorial_files(output_dir: Path) -> list[Path]:
    """List valid editorial JSON files, excluding .original.json."""
    e_dir = output_dir / "editorial"
    if not e_dir.exists() or not e_dir.is_dir():
        return []
    files = [f for f in e_dir.glob("*.json") if not f.name.endswith(".original.json")]
    files.sort(key=lambda f: f.stat().st_mtime, reverse=True)
    return files


@st.fragment(run_every=2)
def _render_live_running_fragment(output_dir: Path, job_id: str, log_path: str) -> None:
    """Auto-refreshing fragment during active job execution."""
    job = get_job(output_dir, job_id)
    tail = read_log_tail(log_path, max_lines=250)

    col_h1, col_h2 = st.columns([4, 1])
    with col_h1:
        st.markdown(f"**Đang thực thi Job:** `{job_id}`")
    with col_h2:
        if st.button("Hủy tiến trình", key=f"btn_cancel_{job_id}", type="secondary"):
            ok, msg = cancel_job(job_id)
            if ok:
                st.warning(msg)
            else:
                st.error(msg)

    st.code(tail if tail else "(Đang khởi động tiến trình...)", language="text")

    # If job has transitioned out of RUNNING, trigger one rerun to switch out of auto-refresh
    if job and job.status != JobStatus.RUNNING:
        if st.session_state.get("pipeline_last_rerun_job") != job_id:
            st.session_state["pipeline_last_rerun_job"] = job_id
            st.session_state["pipeline_active_job_id"] = None
            st.rerun()


def _render_finished_job_details(job: JobRecord) -> None:
    """Render details for a finished job."""
    status_badges = {
        JobStatus.SUCCEEDED: ("Thành công", "success", "✅"),
        JobStatus.FINISHED_WITH_FINDINGS: ("Hoàn thành có lưu ý", "warning", "⚠️"),
        JobStatus.FAILED: ("Thất bại", "danger", "❌"),
        JobStatus.CANCELLED: ("Đã hủy", "secondary", "⏹️"),
        JobStatus.TIMEOUT: ("Hết thời gian", "danger", "⏱️"),
    }
    label, color, icon = status_badges.get(
        job.status, (job.status.value, "secondary", "ℹ️")
    )

    st.markdown(
        f"### {icon} Kết quả: :{color}[{label}] — Bước `{job.step}`"
    )

    col1, col2, col3 = st.columns(3)
    with col1:
        st.write(f"**Job ID:** `{job.job_id}`")
        st.write(f"**Mã thoát:** `{job.exit_code}`")
    with col2:
        st.write(f"**Bắt đầu:** {job.started_at}")
        st.write(f"**Kết thúc:** {job.finished_at or '—'}")
    with col3:
        st.write(f"**Ý nghĩa:** {job.exit_label_vi or '—'}")

    if job.new_files:
        st.markdown("**Tệp mới / chỉnh sửa:**")
        for f in job.new_files:
            st.markdown(f"- `{f}`")

    with st.expander("Nhật ký thực thi (Log)", expanded=True):
        tail = read_log_tail(job.log_path, max_lines=300)
        st.code(tail if tail else "(Không có dữ liệu nhật ký)", language="text")


def render_pipeline_tab(output_dir: Path, settings_path: Path) -> None:
    """Render the Streamlit Pipeline Runner Tab 5."""
    st.subheader("Bộ điều khiển quy trình (Pipeline Runner)")
    st.caption("Khởi chạy từng bước độc lập, theo dõi nhật ký thời gian thực và kiểm soát tài nguyên.")

    # 1. Check lock status
    lock_info = read_lock_info(output_dir)
    is_locked = lock_info is not None

    if is_locked and lock_info:
        owner_pid = lock_info.get("child_pid") or lock_info.get("owner_pid")
        step_name = lock_info.get("step", "unknown")
        started_at = lock_info.get("started_at", "")
        st.warning(
            f"🟡 **Đang chạy:** Bước `{step_name}` (PID: `{owner_pid}`, Job: `{lock_info.get('job_id')}`) | Bắt đầu: {started_at}"
        )
        # Update active job id in session state
        st.session_state["pipeline_active_job_id"] = lock_info.get("job_id")
    else:
        st.success("🟢 **Trạng thái:** Rảnh (Sẵn sàng khởi chạy)")

    st.divider()

    # 2. Step cards (Fetch, Collect, Research, Editorial, Validate)
    st.markdown("#### Khởi chạy từng bước (Pipeline Steps)")

    col_fetch, col_collect = st.columns(2)
    with col_fetch:
        st.markdown("##### 1. Fetch")
        st.caption("Thu thập tin thô từ tất cả RSS sources đã bật.")
        if st.button("Chạy Fetch", disabled=is_locked, use_container_width=True):
            try:
                rec = start_job(
                    step="fetch",
                    output_dir=output_dir,
                    settings_path=settings_path,
                )
                st.session_state["pipeline_active_job_id"] = rec.job_id
                st.session_state["pipeline_last_rerun_job"] = None
                st.rerun()
            except Exception as err:
                st.error(f"Lỗi: {err}")

    with col_collect:
        st.markdown("##### 2. Collect")
        st.caption("Thu thập, chuẩn hóa, lọc trùng và lưu vào cơ sở dữ liệu.")
        if st.button("Chạy Collect", disabled=is_locked, use_container_width=True):
            try:
                rec = start_job(
                    step="collect",
                    output_dir=output_dir,
                    settings_path=settings_path,
                )
                st.session_state["pipeline_active_job_id"] = rec.job_id
                st.session_state["pipeline_last_rerun_job"] = None
                st.rerun()
            except Exception as err:
                st.error(f"Lỗi: {err}")

    st.markdown("---")
    col_res, col_edit = st.columns(2)
    with col_res:
        st.markdown("##### 3. Research")
        st.caption("Agent 1 xếp hạng và chọn lọc các bài viết đáng giá.")
        st.info("⚠️ Bước này sẽ gọi Gemini API (tiêu tốn quota).")
        res_confirm = st.checkbox(
            "Tôi đồng ý gọi Gemini API (tốn quota)",
            key="chk_res_confirm",
        )
        if st.button("Chạy Research", disabled=is_locked or not res_confirm, use_container_width=True):
            try:
                rec = start_job(
                    step="research",
                    output_dir=output_dir,
                    settings_path=settings_path,
                )
                st.session_state["pipeline_active_job_id"] = rec.job_id
                st.session_state["pipeline_last_rerun_job"] = None
                st.rerun()
            except Exception as err:
                st.error(f"Lỗi: {err}")

    with col_edit:
        st.markdown("##### 4. Editorial")
        st.caption("Agent 2 sản xuất bản tin tổng hợp, podcast và video.")
        st.info("⚠️ Bước này sẽ gọi Gemini API (tiêu tốn quota).")

        research_files = _list_research_files(output_dir)
        if research_files:
            r_options = [f.name for f in research_files]
            sel_r_name = st.selectbox("Chọn kết quả Research:", options=r_options, key="sb_edit_res")
            selected_r_path = next(f for f in research_files if f.name == sel_r_name)
        else:
            st.warning("Chưa có kết quả research nào trong output/research/")
            selected_r_path = None

        edit_confirm = st.checkbox(
            "Tôi đồng ý gọi Gemini API (tốn quota)",
            key="chk_edit_confirm",
        )
        edit_disabled = is_locked or not edit_confirm or selected_r_path is None
        if st.button("Chạy Editorial", disabled=edit_disabled, use_container_width=True):
            try:
                rec = start_job(
                    step="editorial",
                    output_dir=output_dir,
                    settings_path=settings_path,
                    params={"research": selected_r_path},
                )
                st.session_state["pipeline_active_job_id"] = rec.job_id
                st.session_state["pipeline_last_rerun_job"] = None
                st.rerun()
            except Exception as err:
                st.error(f"Lỗi: {err}")

    st.markdown("---")
    st.markdown("##### 5. Validate")
    st.caption("Kiểm tra tính hợp lệ của gói tin biên tập qua 3 lớp đối soát.")

    research_files_val = _list_research_files(output_dir)
    editorial_files_val = _list_editorial_files(output_dir)

    val_col1, val_col2 = st.columns(2)
    selected_val_r = None
    selected_val_e = None

    # Try to find newest matching run_id pair
    default_r_idx = 0
    default_e_idx = 0
    r_run_ids = {f.stem: idx for idx, f in enumerate(research_files_val)}
    for e_idx, e_file in enumerate(editorial_files_val):
        if e_file.stem in r_run_ids:
            default_e_idx = e_idx
            default_r_idx = r_run_ids[e_file.stem]
            break

    with val_col1:
        if research_files_val:
            selected_val_r_name = st.selectbox(
                "Chọn tệp Research đối soát:",
                options=[f.name for f in research_files_val],
                index=default_r_idx,
                key="sb_val_res",
            )
            selected_val_r = next(f for f in research_files_val if f.name == selected_val_r_name)
        else:
            st.warning("Thiếu tệp Research.")

    with val_col2:
        if editorial_files_val:
            selected_val_e_name = st.selectbox(
                "Chọn tệp Editorial đối soát:",
                options=[f.name for f in editorial_files_val],
                index=default_e_idx,
                key="sb_val_edit",
            )
            selected_val_e = next(f for f in editorial_files_val if f.name == selected_val_e_name)
        else:
            st.warning("Thiếu tệp Editorial.")

    val_llm_review = st.checkbox("Bật LLM claim review (tốn quota)", value=False, key="chk_val_llm")
    val_quota_confirmed = True
    if val_llm_review:
        val_quota_confirmed = st.checkbox(
            "Tôi đồng ý gọi Gemini API cho LLM claim review (tốn quota)",
            key="chk_val_quota_confirm",
        )

    val_disabled = (
        is_locked
        or selected_val_r is None
        or selected_val_e is None
        or (val_llm_review and not val_quota_confirmed)
    )

    if st.button("Chạy Validate", disabled=val_disabled, use_container_width=True):
        try:
            rec = start_job(
                step="validate",
                output_dir=output_dir,
                settings_path=settings_path,
                params={
                    "research": selected_val_r,
                    "editorial": selected_val_e,
                    "llm_review": val_llm_review,
                },
            )
            st.session_state["pipeline_active_job_id"] = rec.job_id
            st.session_state["pipeline_last_rerun_job"] = None
            st.rerun()
        except Exception as err:
            st.error(f"Lỗi: {err}")

    st.divider()

    # 3. Live Log Panel
    active_job_id = st.session_state.get("pipeline_active_job_id")
    if active_job_id:
        active_job = get_job(output_dir, active_job_id)
        if active_job and active_job.status == JobStatus.RUNNING:
            st.markdown("#### Nhật ký trực tiếp (Live Log)")
            _render_live_running_fragment(output_dir, active_job_id, active_job.log_path)
        elif active_job:
            _render_finished_job_details(active_job)
    elif is_locked and lock_info and lock_info.get("job_id"):
        # Another job is running from a different thread/session
        jid = lock_info["job_id"]
        job = get_job(output_dir, jid)
        log_p = job.log_path if job else str(output_dir / "pipeline" / "jobs" / f"{jid}.log")
        st.markdown("#### Nhật ký trực tiếp (Live Log)")
        _render_live_running_fragment(output_dir, jid, log_p)

    st.divider()

    # 4. Job History Table
    st.markdown("#### Lịch sử thực thi (20 lần chạy gần nhất)")
    history_jobs = list_jobs(output_dir, limit=20)
    if not history_jobs:
        st.info("Chưa có lượt chạy nào được ghi nhận.")
    else:
        history_data = [
            {
                "Job ID": j.job_id,
                "Bước": j.step,
                "Trạng thái": j.status.value,
                "Mã thoát": j.exit_code if j.exit_code is not None else "—",
                "Ý nghĩa": j.exit_label_vi or "—",
                "Thời gian bắt đầu": j.started_at,
                "Tệp sinh ra": ", ".join(j.new_files) if j.new_files else "—",
            }
            for j in history_jobs
        ]
        st.dataframe(history_data, use_container_width=True)

