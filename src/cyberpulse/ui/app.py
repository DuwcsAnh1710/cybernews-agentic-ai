"""Streamlit Workbench UI for CyberPulse AI (Task T4.3).

Client inspection & Human-in-the-Loop review layer.
Binds to 127.0.0.1, opens SQLite read-only, and performs NO pipeline execution calls.
"""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import html
import json
from pathlib import Path
import sys

import streamlit as st

from cyberpulse.config import load_app_config
from cyberpulse.ui.data import (
    ArtifactError,
    blocks_by_article,
    caption_stats,
    highlight,
    issues_by_path,
    list_runs,
    load_editorial,
    load_research,
    load_validation,
    parse_research_notes,
    podcast_stats,
    query_articles,
    source_cap_view,
    uncited_blocks,
    unselected_candidates,
    validation_freshness,
    video_storyboard,
)
from cyberpulse.ui.pipeline_tab import render_pipeline_tab
from cyberpulse.ui.review import (
    EDITABLE_PATH_PATTERNS,
    ReviewRuleError,
    apply_text_edit,
    current_status,
    is_editable_path,
    load_review,
    record_decision,
)


def parse_app_args() -> Path:
    """Parse --settings argument from CLI if passed."""
    parser = argparse.ArgumentParser(add_help=False)
    parser.add_argument("--settings", type=Path, default=Path("config/settings.toml"))

    # When run via streamlit run ..., custom args appear after '--'
    script_args = sys.argv[1:]
    if "--" in script_args:
        idx = script_args.index("--")
        script_args = script_args[idx + 1 :]

    args, _ = parser.parse_known_args(script_args)
    return args.settings


def main() -> None:
    """Main Streamlit application entry point."""
    st.set_page_config(
        page_title="CyberPulse AI — Workbench",
        page_icon="🛡️",
        layout="wide",
    )

    settings_path = parse_app_args()
    sources_path = Path("config/sources.toml")

    try:
        settings, sources = load_app_config(settings_path=settings_path, sources_path=sources_path)
    except Exception as err:
        st.error(f"Lỗi tải cấu hình từ '{settings_path}': {err}")
        return

    output_dir = settings.paths.output_dir
    db_path = settings.paths.data_dir / "cyberpulse.db"

    # ==========================================================================
    # Sidebar
    # ==========================================================================
    st.sidebar.title("CyberPulse AI")
    st.sidebar.caption("Pipeline Inspection & Review Workbench")
    st.sidebar.write(f"**Cấu hình:** `{settings_path}`")

    runs = list_runs(output_dir)

    if not runs:
        st.sidebar.warning("Chưa có phiên chạy (run) nào trong thư mục output.")
        selected_run = None
    else:
        def _format_run(r) -> str:
            flags = (
                f"{'✅' if r.has_research else '—'}R "
                f"{'✅' if r.has_editorial else '—'}E "
                f"{'✅' if r.has_validation else '—'}V "
                f"{'✅' if r.has_review else '—'}D"
            )
            return f"{r.run_id}  [{flags}]"

        run_labels = {r.run_id: _format_run(r) for r in runs}
        selected_run_id = st.sidebar.selectbox(
            "Chọn phiên chạy (Run ID):",
            options=[r.run_id for r in runs],
            format_func=lambda rid: run_labels[rid],
        )
        selected_run = next((r for r in runs if r.run_id == selected_run_id), None)

    # Review status badge in sidebar
    if selected_run and selected_run.has_editorial:
        review_data = load_review(selected_run.review_path)
        status = current_status(review_data, selected_run.editorial_path)
        status_map = {
            "unreviewed": ("Chưa duyệt", "secondary"),
            "approved": ("Đã duyệt", "success"),
            "rejected": ("Đã từ chối", "danger"),
            "changes_requested": ("Yêu cầu sửa đổi", "warning"),
        }
        status_text, status_color = status_map.get(status, ("Chưa duyệt", "secondary"))
        st.sidebar.markdown(f"**Trạng thái duyệt:** :{status_color}[{status_text}]")
    else:
        st.sidebar.markdown("**Trạng thái duyệt:** :secondary[Chưa có dữ liệu biên tập]")

    with st.sidebar.expander("Cách dùng", expanded=False):
        st.markdown(
            "1. **Nguồn & Dữ liệu**: Tra cứu bài viết và số liệu từ RSS.\n"
            "2. **Research Agent**: Xem bài được chọn, lý do và trần nguồn.\n"
            "3. **Editorial Studio**: Xem bản tin, mạng xã hội, podcast, video.\n"
            "4. **Đối soát & Duyệt**: So sánh nguồn, chỉnh sửa nhanh và ghi nhận quyết định duyệt.\n"
            "5. **Pipeline Runner**: Khởi chạy từng bước pipeline độc lập và theo dõi nhật ký trực tiếp."
        )

    # ==========================================================================
    # Main Tabs
    # ==========================================================================
    tab1, tab2, tab3, tab4, tab5 = st.tabs([
        "Nguồn & Dữ liệu",
        "Research Agent",
        "Editorial Studio",
        "Đối soát & Duyệt",
        "Pipeline Runner",
    ])

    # --------------------------------------------------------------------------
    # Tab 1: Nguồn & Dữ liệu
    # --------------------------------------------------------------------------
    with tab1:
        st.subheader("Nguồn tin tức (Sources)")
        sources_data = [
            {"Nguồn": s.name, "URL": s.url, "Kích hoạt": "Bật" if s.enabled else "Tắt"}
            for s in sources
        ]
        st.dataframe(sources_data, use_container_width=True)

        st.divider()
        st.subheader("Cơ sở dữ liệu tin tức")

        if not db_path.exists():
            st.info("Chưa có cơ sở dữ liệu — hãy chạy: `cyberpulse collect`")
        else:
            all_articles = query_articles(db_path, limit=2000)
            col1, col2, col3 = st.columns(3)
            col1.metric("Tổng số bài viết", len(all_articles))

            source_counts = {}
            for a in all_articles:
                source_counts[a.source_name] = source_counts.get(a.source_name, 0) + 1
            col2.metric("Số nguồn đã lưu", len(source_counts))

            latest_pub = max((a.published_at for a in all_articles), default=None)
            col3.metric(
                "Bài mới nhất",
                latest_pub.strftime("%Y-%m-%d %H:%M UTC") if latest_pub else "N/A",
            )

            with st.expander("Bộ lọc tra cứu bài viết", expanded=True):
                f_col1, f_col2, f_col3 = st.columns([2, 1, 2])
                with f_col1:
                    filter_sources = st.multiselect(
                        "Lọc theo nguồn:",
                        options=sorted(list(source_counts.keys())),
                        default=[],
                    )
                with f_col2:
                    date_field = st.radio(
                        "Trường ngày:",
                        options=["published_at", "fetched_at"],
                        horizontal=True,
                    )
                with f_col3:
                    keyword = st.text_input("Từ khóa tìm kiếm (tiêu đề/tóm tắt):", value="")

            filtered_articles = query_articles(
                db_path,
                sources=filter_sources if filter_sources else None,
                date_field=date_field,
                keyword=keyword if keyword else None,
                limit=500,
            )

            st.write(f"Hiển thị **{len(filtered_articles)}** bài viết:")
            display_rows = [
                {
                    "Thời gian": (
                        a.published_at.strftime("%Y-%m-%d %H:%M")
                        if date_field == "published_at"
                        else a.fetched_at.strftime("%Y-%m-%d %H:%M")
                    ),
                    "Nguồn": a.source_name,
                    "Tiêu đề": a.title,
                    "Chủ đề": ", ".join(a.topics_matched),
                    "Link": a.url,
                }
                for a in filtered_articles
            ]
            st.dataframe(
                display_rows,
                column_config={"Link": st.column_config.LinkColumn("Link gốc")},
                use_container_width=True,
            )

    # --------------------------------------------------------------------------
    # Tab 2: Research Agent
    # --------------------------------------------------------------------------
    with tab2:
        if not selected_run or not selected_run.has_research:
            st.info("Chưa có kết quả nghiên cứu — hãy chạy: `cyberpulse research`")
        else:
            try:
                research = load_research(selected_run.research_path)
                st.subheader(f"Kết quả Research Agent — Run: `{research.run_id}`")

                # Metrics from notes
                notes_dict = parse_research_notes(research.notes)
                if notes_dict:
                    m_col1, m_col2, m_col3, m_col4, m_col5 = st.columns(5)
                    m_col1.metric("Số bài chọn", len(research.items))
                    m_col2.metric("Loại do trần nguồn", notes_dict.get("source_cap_dropped", "0"))
                    m_col3.metric("Bịa ID (Hallucinated)", notes_dict.get("hallucinated", "0"))
                    m_col4.metric("Trùng lặp (Duplicates)", notes_dict.get("duplicates", "0"))
                    m_col5.metric("Mô hình LLM", notes_dict.get("model", "N/A"))

                # Selected items table
                st.markdown("### Danh sách bài viết được chọn")
                sel_rows = [
                    {
                        "Hạng": item.rank,
                        "Nguồn": item.article.source_name,
                        "Tiêu đề": item.article.title,
                        "Ngày xuất bản": item.article.published_at.strftime("%Y-%m-%d %H:%M UTC"),
                        "Lý do chọn (Agent 1)": item.relevance_reason,
                        "URL": item.article.url,
                    }
                    for item in research.items
                ]
                st.dataframe(
                    sel_rows,
                    column_config={"URL": st.column_config.LinkColumn("Bài viết gốc")},
                    use_container_width=True,
                )

                # Source cap view
                st.markdown("### Phân bổ theo nguồn & Trần đa dạng")
                caps = source_cap_view(research, settings.research.max_per_source)
                cap_cols = st.columns(len(caps) if caps else 1)
                for i, c in enumerate(caps):
                    with cap_cols[i % len(cap_cols)]:
                        at_cap_str = "⚠️ Đạt trần" if c["at_cap"] else "Trong trần"
                        st.metric(
                            c["source_name"],
                            f"{c['selected_count']} / {c['cap']} bài",
                            delta=at_cap_str,
                            delta_color="inverse" if c["at_cap"] else "normal",
                        )

                # Unselected candidates expander
                with st.expander("Bài không được chọn (Lookback Window)", expanded=False):
                    st.caption("ℹ️ Không lưu lý do chi tiết cho từng bài bị loại (chỉ lưu số lượng trong notes).")
                    if db_path.exists():
                        now_utc = datetime.now(timezone.utc)
                        unselected = unselected_candidates(
                            db_path, research, settings.lookback_hours, now=now_utc
                        )
                        unsel_rows = [
                            {
                                "Nguồn": a.source_name,
                                "Tiêu đề": a.title,
                                "Ngày xuất bản": a.published_at.strftime("%Y-%m-%d %H:%M UTC"),
                                "URL": a.url,
                            }
                            for a in unselected
                        ]
                        st.dataframe(
                            unsel_rows,
                            column_config={"URL": st.column_config.LinkColumn("Link gốc")},
                            use_container_width=True,
                        )
                    else:
                        st.info("Chưa có cơ sở dữ liệu để đối chiếu bài không được chọn.")

            except ArtifactError as err:
                st.error(f"Lỗi khi đọc file research: {err}")

    # --------------------------------------------------------------------------
    # Tab 3: Editorial Studio
    # --------------------------------------------------------------------------
    with tab3:
        if not selected_run or not selected_run.has_editorial:
            st.info("Chưa có gói biên tập — hãy chạy: `cyberpulse editorial --research ...`")
        else:
            try:
                pkg = load_editorial(selected_run.editorial_path)
                research_obj = load_research(selected_run.research_path) if selected_run.has_research else None
                article_lookup = (
                    {it.article.id: it.article for it in research_obj.items} if research_obj else {}
                )

                ed_tab1, ed_tab2, ed_tab3, ed_tab4, ed_tab5 = st.tabs([
                    "Bản tin (Digest)",
                    "Mạng xã hội (Captions)",
                    "Podcast (Script)",
                    "Video (Storyboard)",
                    "Markdown Preview",
                ])

                # Sub-tab: Bản tin
                with ed_tab1:
                    st.title(pkg.digest.title)
                    st.write(pkg.digest.intro)
                    st.divider()
                    for idx, sec in enumerate(pkg.digest.sections):
                        st.subheader(sec.heading)
                        st.write(sec.body.text)
                        # Citations
                        cites_links = [
                            f"[{article_lookup[aid].source_name}]({article_lookup[aid].url})"
                            for aid in sec.body.cited_article_ids
                            if aid in article_lookup
                        ]
                        if cites_links:
                            st.caption(f"Nguồn: {', '.join(cites_links)}")
                        st.divider()

                # Sub-tab: Mạng xã hội
                with ed_tab2:
                    st.subheader("Nội dung đăng mạng xã hội")
                    stats = caption_stats(pkg)
                    cap_cols = st.columns(len(pkg.captions) if pkg.captions else 1)
                    for idx, (cap, stat) in enumerate(zip(pkg.captions, stats)):
                        with cap_cols[idx]:
                            st.markdown(f"#### {cap.platform.upper()}")
                            limit_badge = f":red[{stat['chars']}/{stat['limit']}]" if stat["over_limit"] else f"{stat['chars']}/{stat['limit']}"
                            st.caption(f"Độ dài: {limit_badge} ký tự")
                            st.text_area(
                                f"Nội dung ({cap.platform})",
                                value=cap.text,
                                height=150,
                                disabled=True,
                                key=f"cap_text_{idx}",
                            )
                            st.write("Hashtags: " + " ".join(f"`{tag}`" for tag in cap.hashtags))

                # Sub-tab: Podcast
                with ed_tab3:
                    st.subheader(pkg.podcast.title)
                    p_stat = podcast_stats(
                        pkg,
                        target_words=settings.editorial.podcast_target_words,
                        tolerance=settings.validator.podcast_word_tolerance,
                    )
                    c1, c2, c3 = st.columns(3)
                    range_status = "Đạt mục tiêu" if p_stat["in_range"] else "Ngoài khoảng"
                    c1.metric(
                        "Tổng từ đối thoại",
                        f"{p_stat['total_words']} từ",
                        delta=range_status,
                        delta_color="normal" if p_stat["in_range"] else "inverse",
                    )
                    c2.metric("Mục tiêu cho phép", f"{p_stat['min_allowed']} – {p_stat['max_allowed']} từ")
                    speaker_words = ", ".join(f"{s}: {w}" for s, w in p_stat["words_by_speaker"].items())
                    c3.metric("Số từ theo MC", speaker_words)

                    st.markdown("---")
                    speaker_avatars = {"Minh": "🎙️", "Lan": "🎧"}
                    for seg in pkg.podcast.segments:
                        avatar = speaker_avatars.get(seg.speaker, "👤")
                        with st.chat_message(seg.speaker, avatar=avatar):
                            st.markdown(f"**{seg.speaker}**: {seg.line.text}")

                # Sub-tab: Video
                with ed_tab4:
                    st.subheader(pkg.video.title)
                    sb_rows, total_sec, in_budget = video_storyboard(
                        pkg, max_total_sec=settings.editorial.video_max_total_sec
                    )
                    b_color = "normal" if in_budget else "inverse"
                    b_status = f"{'Trong' if in_budget else 'Vượt'} ngân sách (Tối đa {settings.editorial.video_max_total_sec}s)"
                    st.metric("Tổng thời lượng video", f"{total_sec:.1f} giây", delta=b_status, delta_color=b_color)

                    st.dataframe(
                        [
                            {
                                "Cảnh": r["scene_no"],
                                "Thời lượng": f"{r['duration_sec']}s",
                                "Chữ trên màn hình": r["on_screen_text"],
                                "Ký tự": r["char_count"],
                                "Lời dẫn (Voiceover)": r["narration"],
                            }
                            for r in sb_rows
                        ],
                        use_container_width=True,
                    )

                # Sub-tab: Markdown Preview
                with ed_tab5:
                    if selected_run.preview_path and selected_run.preview_path.exists():
                        md_text = selected_run.preview_path.read_text(encoding="utf-8")
                        st.markdown(md_text)
                    else:
                        st.info("Chưa có file markdown preview.")

            except ArtifactError as err:
                st.error(f"Lỗi khi đọc file editorial: {err}")

    # --------------------------------------------------------------------------
    # Tab 4: Đối soát & Duyệt
    # --------------------------------------------------------------------------
    with tab4:
        if not selected_run:
            st.info("Vui lòng chọn một phiên chạy từ thanh bên (sidebar).")
        else:
            validation_data = load_validation(selected_run.validation_path)
            e_path = selected_run.editorial_path
            v_path = selected_run.validation_path
            r_path = selected_run.review_path

            freshness = validation_freshness(e_path, v_path)

            # Validation summary bar
            st.subheader("Đối soát chất lượng & Kiểm tra nguồn")

            if freshness == "missing":
                st.warning(
                    f"⚠️ Chưa có kết quả kiểm tra cho phiên chạy này.\n\n"
                    f"Hãy chạy lệnh: `cyberpulse validate --research output/research/{selected_run.run_id}.json --editorial output/editorial/{selected_run.run_id}.json`"
                )
            elif freshness == "stale":
                st.warning(
                    f"⚠️ Kết quả kiểm tra đã cũ (stale) do gói biên tập đã bị chỉnh sửa sau lần kiểm tra cuối.\n\n"
                    f"Hãy chạy lại: `cyberpulse validate --research output/research/{selected_run.run_id}.json --editorial output/editorial/{selected_run.run_id}.json`"
                )

            if validation_data:
                passed = validation_data.get("passed", False)
                errors = validation_data.get("errors", [])
                warnings = validation_data.get("warnings", [])
                llm_rev = validation_data.get("llm_review", "skipped")
                scope = validation_data.get("scope_note", "")

                v_col1, v_col2, v_col3, v_col4, v_col5 = st.columns(5)
                v_col1.metric("Độ tươi validation", freshness.upper())
                v_col2.metric("Kết quả", "ĐẠT (Passed)" if passed else "LỖI (Failed)")
                v_col3.metric("Số Lỗi (Errors)", len(errors))
                v_col4.metric("Cảnh báo (Warnings)", len(warnings))
                v_col5.metric("LLM Claim Review", llm_rev.upper())

                if scope:
                    st.info(f"📋 **Phạm vi kiểm tra:** {scope}")

                # Issues Table & Legend
                all_issues = errors + warnings
                if all_issues:
                    st.markdown("### Bảng danh sách vấn đề phát hiện")
                    issue_codes = sorted(list({iss.get("code", "") for iss in all_issues}))
                    filter_code = st.selectbox(
                        "Lọc theo mã kiểm tra (Check Code):",
                        options=["Tất cả"] + issue_codes,
                    )
                    filtered_issues = (
                        all_issues if filter_code == "Tất cả" else [iss for iss in all_issues if iss.get("code") == filter_code]
                    )
                    st.dataframe(
                        [
                            {
                                "Mức độ": iss.get("severity", "").upper(),
                                "Mã": iss.get("code", ""),
                                "Vị trí (Path)": iss.get("path", ""),
                                "Thông điệp": iss.get("message", ""),
                                "Bằng chứng (Evidence)": iss.get("evidence", ""),
                            }
                            for iss in filtered_issues
                        ],
                        use_container_width=True,
                    )

                with st.expander("Ý nghĩa các mã kiểm tra (Check Code Legend)", expanded=False):
                    st.markdown(
                        "- `E_*`: **Lỗi nghiêm trọng (Error)** — Bắt buộc chặn duyệt, phải sửa trước khi xuất bản.\n"
                        "- `W_TERM_UNSUPPORTED`: **Cảnh báo thuật ngữ** — Thuật ngữ tiếng Anh hoặc viết tắt không có trong bài gốc.\n"
                        "- `W_CLAIM_*`: **Cảnh báo nội dung** — Có thể bịa chi tiết, gán nhầm nguồn hoặc phóng đại so với nguồn RSS.\n"
                        "- `W_PODCAST_LENGTH` / `W_SECTION_LENGTH`: **Cảnh báo độ dài** — Số từ lệch khỏi khoảng mục tiêu đề ra."
                    )

            st.divider()

            # Side-by-side comparison per article
            if selected_run.has_research and selected_run.has_editorial:
                research_obj = load_research(selected_run.research_path)
                pkg = load_editorial(selected_run.editorial_path)
                b_map = blocks_by_article(research_obj, pkg)
                issues_map = issues_by_path(validation_data)

                st.subheader("Đối chiếu Song Song: Nguồn (English) vs Nội dung Tiếng Việt")

                rank_options = [item.rank for item in research_obj.items]
                selected_rank = st.selectbox(
                    "Chọn bài viết để đối chiếu theo thứ tự xếp hạng (Rank):",
                    options=rank_options,
                    format_func=lambda r: f"Hạng {r}: {research_obj.items[r-1].article.title}",
                )

                chosen_item = research_obj.items[selected_rank - 1]
                chosen_article = chosen_item.article

                c_left, c_right = st.columns(2)

                with c_left:
                    st.markdown("#### 📰 Nguồn tin (English RSS)")
                    st.write(f"**Nguồn:** {chosen_article.source_name}")
                    st.write(f"**Tiêu đề:** [{chosen_article.title}]({chosen_article.url})")
                    st.write(f"**Tóm tắt RSS:**\n\n{chosen_article.summary}")
                    st.caption(f"Lý do chọn (Research Agent): {chosen_item.relevance_reason}")

                with c_right:
                    st.markdown("#### 🇻🇳 Các đoạn nội dung tiếng Việt trích dẫn bài này")
                    citing_blocks = b_map.get(chosen_article.id, [])
                    if not citing_blocks:
                        st.info("Không có đoạn nội dung nào trích dẫn bài này.")
                    for b in citing_blocks:
                        b_issues = issues_map.get(b.path, [])
                        evidences = [iss.get("evidence", "") for iss in b_issues if iss.get("evidence")]
                        highlighted_html = highlight(b.text, evidences)

                        st.markdown(f"**Vị trí:** `{b.path}`  *(Loại: {b.kind})*")
                        st.markdown(
                            f"<div style='background-color: #2b303c; padding: 10px; border-radius: 5px; margin-bottom: 5px;'>{highlighted_html}</div>",
                            unsafe_allow_html=True,
                        )

                        if b_issues:
                            chips = " ".join(
                                f":{'red' if iss.get('severity') == 'error' else 'orange'}[[{iss.get('code')}]: {iss.get('message')}]"
                                for iss in b_issues
                            )
                            st.markdown(chips)
                        st.write("")

                # Uncited blocks
                with st.expander("Các khối văn bản chung (Tiêu đề, Giới thiệu) — Đối chiếu toàn nguồn"):
                    uncited = uncited_blocks(pkg)
                    for ub in uncited:
                        u_issues = issues_map.get(ub.path, [])
                        u_evidences = [iss.get("evidence", "") for iss in u_issues if iss.get("evidence")]
                        u_html = highlight(ub.text, u_evidences)
                        st.markdown(f"**Vị trí:** `{ub.path}`")
                        st.markdown(
                            f"<div style='background-color: #2b303c; padding: 10px; border-radius: 5px; margin-bottom: 5px;'>{u_html}</div>",
                            unsafe_allow_html=True,
                        )
                        if u_issues:
                            chips = " ".join(
                                f":{'red' if iss.get('severity') == 'error' else 'orange'}[[{iss.get('code')}]: {iss.get('message')}]"
                                for iss in u_issues
                            )
                            st.markdown(chips)

            st.divider()

            # Quick Edit Section
            st.subheader("Chỉnh sửa nhanh (Quick Edit)")
            st.caption(
                "Chỉ cho phép sửa nội dung văn bản. Không được sửa ID trích dẫn, thời lượng hoặc nền tảng. "
                "Sau khi sửa, kết quả validation sẽ chuyển sang trạng thái cũ (stale) và cần chạy lại validation."
            )

            if selected_run.has_editorial:
                pkg_for_edit = load_editorial(selected_run.editorial_path)
                # Collect editable paths
                editable_paths = [
                    "digest.title",
                    "digest.intro",
                ]
                for i in range(len(pkg_for_edit.digest.sections)):
                    editable_paths.append(f"digest.sections[{i}].heading")
                    editable_paths.append(f"digest.sections[{i}].body.text")
                for i in range(len(pkg_for_edit.podcast.segments)):
                    editable_paths.append(f"podcast.segments[{i}].line.text")
                for i in range(len(pkg_for_edit.video.scenes)):
                    editable_paths.append(f"video.scenes[{i}].on_screen_text")
                    editable_paths.append(f"video.scenes[{i}].narration.text")
                for i in range(len(pkg_for_edit.captions)):
                    editable_paths.append(f"captions[{i}].text")

                edit_col1, edit_col2 = st.columns([1, 2])
                with edit_col1:
                    chosen_edit_path = st.selectbox("Chọn trường cần sửa:", options=editable_paths)
                    reviewer_edit_name = st.text_input("Tên người sửa:", value="Editor")
                    reviewer_edit_note = st.text_input("Ghi chú lý do sửa:", value="Sửa lỗi chính tả / thuật ngữ")

                # Get current value for that path
                raw_ed = json.loads(selected_run.editorial_path.read_text(encoding="utf-8"))
                from cyberpulse.ui.review import _get_nested_field

                current_val = _get_nested_field(raw_ed, chosen_edit_path)

                with edit_col2:
                    new_text_val = st.text_area(
                        "Nội dung văn bản mới:",
                        value=current_val,
                        height=150,
                    )
                    if st.button("Lưu chỉnh sửa (Save Edit)", type="primary"):
                        try:
                            rev_file = output_dir / "review" / f"{selected_run.run_id}.json"
                            apply_text_edit(
                                editorial_path=selected_run.editorial_path,
                                review_path=rev_file,
                                path=chosen_edit_path,
                                new_text=new_text_val,
                                reviewer=reviewer_edit_name,
                                note=reviewer_edit_note,
                            )
                            st.success("✅ Đã lưu chỉnh sửa thành công! Validation hiện đã chuyển sang trạng thái cũ (stale).")
                            st.rerun()
                        except Exception as err:
                            st.error(f"Lỗi khi lưu chỉnh sửa: {err}")

            st.divider()

            # Human Review Decision Form
            st.subheader("Ghi nhận quyết định duyệt (Human-in-the-Loop Decision)")

            if selected_run.has_editorial:
                rev_file = output_dir / "review" / f"{selected_run.run_id}.json"
                review_data = load_review(rev_file)

                has_warnings = False
                if validation_data and validation_data.get("warnings"):
                    has_warnings = True

                d_col1, d_col2 = st.columns([1, 1])
                with d_col1:
                    reviewer_name = st.text_input("Tên người duyệt:", value="Reviewer")
                    decision_note = st.text_area("Ghi chú quyết định (bắt buộc khi Từ chối hoặc Yêu cầu sửa):")
                    ack_warnings = False
                    if has_warnings:
                        ack_warnings = st.checkbox(
                            "Tôi đã đọc và chấp nhận các cảnh báo (Acknowledge warnings)"
                        )

                with d_col2:
                    st.write("**Thao tác quyết định:**")
                    b_col1, b_col2, b_col3 = st.columns(3)

                    with b_col1:
                        if st.button("✅ Duyệt (Approve)", type="primary"):
                            try:
                                record_decision(
                                    review_path=rev_file,
                                    editorial_path=selected_run.editorial_path,
                                    validation_path=selected_run.validation_path,
                                    decision="approve",
                                    reviewer=reviewer_name,
                                    note=decision_note,
                                    warnings_acknowledged=ack_warnings,
                                )
                                st.success("✅ Đã ghi nhận quyết định: DUYỆT (Approve)!")
                                st.rerun()
                            except ReviewRuleError as err:
                                st.error(str(err))

                    with b_col2:
                        if st.button("⚠️ Yêu cầu sửa", type="secondary"):
                            try:
                                record_decision(
                                    review_path=rev_file,
                                    editorial_path=selected_run.editorial_path,
                                    validation_path=selected_run.validation_path,
                                    decision="request_changes",
                                    reviewer=reviewer_name,
                                    note=decision_note,
                                    warnings_acknowledged=ack_warnings,
                                )
                                st.warning("⚠️ Đã ghi nhận: YÊU CẦU SỬA ĐỔI!")
                                st.rerun()
                            except ReviewRuleError as err:
                                st.error(str(err))

                    with b_col3:
                        if st.button("❌ Từ chối (Reject)", type="secondary"):
                            try:
                                record_decision(
                                    review_path=rev_file,
                                    editorial_path=selected_run.editorial_path,
                                    validation_path=selected_run.validation_path,
                                    decision="reject",
                                    reviewer=reviewer_name,
                                    note=decision_note,
                                    warnings_acknowledged=ack_warnings,
                                )
                                st.error("❌ Đã ghi nhận: TỪ CHỐI!")
                                st.rerun()
                            except ReviewRuleError as err:
                                st.error(str(err))

                # Decision History
                if review_data.get("decisions"):
                    st.markdown("#### Lịch sử duyệt (Decision History)")
                    dec_rows = [
                        {
                            "Thời gian": d.get("timestamp", ""),
                            "Quyết định": d.get("decision", "").upper(),
                            "Người duyệt": d.get("reviewer", ""),
                            "Ghi chú": d.get("note", ""),
                            "Validation": f"Passed={d.get('validation_passed')} ({d.get('validation_freshness')})",
                        }
                        for d in reversed(review_data["decisions"])
                    ]
                    st.dataframe(dec_rows, use_container_width=True)

                # Edit History
                if review_data.get("edits"):
                    st.markdown("#### Lịch sử chỉnh sửa (Edit History)")
                    edit_rows = [
                        {
                            "Thời gian": e.get("timestamp", ""),
                            "Người sửa": e.get("reviewer", ""),
                            "Vị trí": e.get("path", ""),
                            "Trước": e.get("before", ""),
                            "Sau": e.get("after", ""),
                            "Ghi chú": e.get("note", ""),
                        }
                        for e in reversed(review_data["edits"])
                    ]
                    st.dataframe(edit_rows, use_container_width=True)

    # --------------------------------------------------------------------------
    # Tab 5: Pipeline Runner
    # --------------------------------------------------------------------------
    with tab5:
        render_pipeline_tab(output_dir=output_dir, settings_path=settings_path)


if __name__ == "__main__":
    main()
