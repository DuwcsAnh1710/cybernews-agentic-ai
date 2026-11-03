"""Script to generate, validate, and persist the 2026-11-03 edition."""

import json
from pathlib import Path
from cyberpulse.config import load_settings
from cyberpulse.models import (
    Caption,
    CitedText,
    Digest,
    DigestSection,
    EditorialPackage,
    PodcastSegment,
    PodcastScript,
    ResearchResult,
    VideoScene,
    VideoScript,
)
from cyberpulse.render_preview import render_markdown
from cyberpulse.validator import validate_package
from cyberpulse.storage import connect, init_db, save_editorial_package, save_validation_report

def main():
    settings = load_settings(Path("config/settings.toml"))
    research_path = Path("output/research/run-20261103T045516Z-19a3e8e9.json")
    research = ResearchResult.model_validate_json(research_path.read_text(encoding="utf-8"))
    run_id = research.run_id

    # Cited article IDs
    a_ivanti = "ea9cb89b4ab15bf2"
    a_binance = "f43e91605878bd5e"
    a_botnet = "5eac2a1ee9b14040"
    a_langflow = "0bed561f10e179cb"
    a_cursor = "5621acfb0e5a1d8b"

    sections = [
        DigestSection(
            heading="CISA yêu cầu khẩn cấp vá hai lỗ hổng đang bị khai thác trên Ivanti Connect Secure",
            body=CitedText(
                text="Cơ quan An ninh mạng và Cơ sở hạ tầng Mỹ (CISA) đã bổ sung hai lỗ hổng mới của Ivanti Connect Secure gồm CVE-2026-111357 và CVE-2026-118527 vào danh mục KEV (Known Exploited Vulnerabilities). Lỗ hổng CVE-2026-111357 do thiếu xác thực cho chức năng quan trọng cho phép kẻ tấn công cục bộ leo thang đặc quyền, kết hợp với CVE-2026-118527 cho phép tải tệp từ xa. Ba lỗ hổng này bị kẻ tấn công kết hợp thành chuỗi khai thác để xâm nhập và chiếm quyền kiểm soát hệ thống.",
                cited_article_ids=[a_ivanti],
            ),
        ),
        DigestSection(
            heading="FBI xác nhận tin tặc Triều Tiên đánh cắp hơn 6 tỷ USD tiền mã hóa từ sàn Binance",
            body=CitedText(
                text="Cục Điều tra Liên bang Mỹ (FBI) chính thức xác nhận nhóm tin tặc Triều Tiên chịu trách nhiệm cho vụ đánh cắp hơn 6 tỷ USD tài sản ảo từ sàn giao dịch tiền mã hóa Binance. Vụ việc diễn ra vào khoảng ngày 29 và FBI đã phát đi cảnh báo an ninh vào ngày 31. Trong khi người dùng lo lắng, các nhóm lừa đảo cũng đang lợi dụng tình hình để thực hiện các chiến dịch giả mạo nhằm chiếm đoạt thêm tài sản.",
                cited_article_ids=[a_binance],
            ),
        ),
        DigestSection(
            heading="Liên minh công nghệ triệt phá botnet kiểm soát 1,3 triệu thiết bị Android TV Box",
            body=CitedText(
                text="Google thông báo đã phối hợp cùng các đối tác trong ngành công nghệ để triệt phá mạng botnet quy mô lớn bao gồm hơn 1,3 triệu thiết bị Android TV set-top box. Các thiết bị này bị cài sẵn phần mềm độc hại nhằm bí mật bán băng thông kết nối internet của người dùng và thực hiện gian lận quảng cáo. Giới chuyên gia nhận định chiến dịch này cắt đứt khả năng truy cập của botnet tới các hệ thống bị xâm nhập khác.",
                cited_article_ids=[a_botnet],
            ),
        ),
        DigestSection(
            heading="Hai lỗ hổng nghiêm trọng trên Langflow cho phép thực thi mã từ xa không cần xác thực",
            body=CitedText(
                text="IBM cảnh báo hai lỗ hổng nghiêm trọng trong công cụ mã nguồn mở Langflow cho phép kẻ tấn công từ xa không cần tài khoản vượt qua cơ chế xác thực hoặc thực thi mã tùy ý trên máy chủ. Langflow là công cụ phổ biến dùng để xây dựng các đại lý AI và quy trình làm việc tự động. Mọi phiên bản từ 1.0.0 đến 1.12.8 đều bị ảnh hưởng và bản vá duy nhất được phát hành là phiên bản 1.13.0.",
                cited_article_ids=[a_langflow],
            ),
        ),
        DigestSection(
            heading="Phát hiện tiện ích mở rộng Claude Code độc hại trong cuộc tấn công chuỗi cung ứng Cursor",
            body=CitedText(
                text="Các nhà nghiên cứu bảo mật vừa phát hiện một tiện ích mở rộng độc hại mang tên Claude Code được phân phối trong chiến dịch tấn công chuỗi cung ứng nhắm vào người dùng trình biên tập mã nguồn Cursor. Tiện ích này thực hiện hành vi trinh sát hệ thống máy chủ của nạn nhân và cố gắng thiết lập đường hầm SSH ngược (reverse SSH tunnel) để tạo cửa sau cho kẻ tấn công xâm nhập từ bên ngoài.",
                cited_article_ids=[a_cursor],
            ),
        ),
    ]

    digest = Digest(
        title="Điểm tin an ninh mạng: CISA cảnh báo Ivanti, FBI công bố vụ hack Binance hơn 6 tỷ USD và triệt phá botnet 1,3 triệu TV Box",
        intro="Bản tin an ninh mạng hôm nay tổng hợp các diễn biến quan trọng: CISA ban hành chỉ thị yêu cầu các cơ quan liên bang vá khẩn cấp hai lỗ hổng đang bị khai thác trên Ivanti Connect Secure; FBI xác nhận tin tặc Triều Tiên đánh cắp hơn 6 tỷ USD tiền mã hóa từ sàn Binance; liên minh công nghệ do Google dẫn đầu triệt phá mạng botnet 1,3 triệu Android TV Box; phát hiện lỗ hổng nghiêm trọng trên Langflow và tiện ích độc hại nhắm vào Cursor.",
        sections=sections,
    )

    all_cites = [a_ivanti, a_binance, a_botnet, a_langflow, a_cursor]

    podcast_segments = [
        PodcastSegment(
            speaker="Minh",
            line=CitedText(
                text="Chào mừng quý vị thính giả quay trở lại với bản tin âm thanh chuyên sâu. Tôi là Minh, người đồng hành cùng quý vị điểm qua những diễn biến an ninh mạng và an toàn thông tin quan trọng nhất trong ngày hôm nay.",
                cited_article_ids=all_cites,
            ),
        ),
        PodcastSegment(
            speaker="Lan",
            line=CitedText(
                text="Xin chào anh Minh và kính chào quý vị thính giả. Mở đầu bản tin là thông tin chỉ thị khẩn cấp từ cơ quan an ninh mạng CISA, yêu cầu các cơ quan liên bang nhanh chóng khắc phục hai lỗ hổng bảo mật nghiêm trọng gồm CVE-2026-111357 và CVE-2026-118527 trên hệ thống Ivanti Connect Secure, vốn đã bị đưa vào danh mục các lỗ hổng đang bị khai thác trong thực tế.",
                cited_article_ids=[a_ivanti],
            ),
        ),
        PodcastSegment(
            speaker="Minh",
            line=CitedText(
                text="Đúng vậy thưa chị Lan, kẻ tấn công kết hợp chuỗi ba điểm yếu để chiếm đoạt đặc quyền cao nhất trên thiết bị mà không cần vượt qua khâu xác thực người dùng. Tiếp theo là thông tin chấn động về tội phạm mạng quốc tế: FBI chính thức xác nhận các nhóm tin tặc đến từ Triều Tiên đứng đằng sau phi vụ đánh cắp hơn 6 tỷ USD tài sản ảo từ sàn tiền mã hóa Binance, xảy ra vào ngày 29 và cảnh báo an ninh đã được phát đi vào ngày 31.",
                cited_article_ids=[a_binance],
            ),
        ),
        PodcastSegment(
            speaker="Lan",
            line=CitedText(
                text="Đây thực sự là một con số kỷ lục đáng báo động. Trong bối cảnh đó, người dùng cũng cần hết sức tỉnh táo trước các chiến dịch lừa đảo mạo danh nhằm ăn theo sự cố này. Tuy nhiên, tin vui về phòng chống tội phạm công nghệ là Google và các liên minh công nghệ quốc tế vừa lập chiến công lớn khi triệt phá thành công mạng botnet quy mô kiểm soát hơn 1,3 triệu thiết bị Android TV Box, vốn bị kẻ xấu cài phần mềm độc hại ngầm để chiếm dụng băng thông kết nối internet.",
                cited_article_ids=[a_botnet],
            ),
        ),
        PodcastSegment(
            speaker="Minh",
            line=CitedText(
                text="Chiến dịch truy quét này đã giải tỏa mối đe dọa to lớn cho hạ tầng mạng. Đối với cộng đồng các nhà phát triển và nghiên cứu trí tuệ nhân tạo, IBM vừa phát đi cảnh báo đỏ về hai lỗ hổng an ninh nguy hiểm trên nền tảng Langflow, công cụ mã nguồn mở phổ biến dùng để phát triển các đại lý AI. Lỗ hổng ảnh hưởng trực tiếp đến toàn bộ các phiên bản từ 1.0.0 đến 1.12.8, và các chuyên gia khuyến cáo quản trị viên phải nâng cấp ngay lập tức lên phiên bản 1.13.0 để ngăn ngừa nguy cơ bị chiếm quyền thực thi mã tùy ý từ xa.",
                cited_article_ids=[a_langflow],
            ),
        ),
        PodcastSegment(
            speaker="Lan",
            line=CitedText(
                text="Một cảnh báo chuỗi cung ứng phần mềm cũng vừa được các chuyên gia bảo mật đưa ra, liên quan đến một tiện ích mở rộng độc hại đội lốt Claude Code trên môi trường phát triển Cursor. Tiện ích này lén lút do thám thông tin máy chủ và thiết lập các kết nối đường hầm SSH ngược ra ngoài Internet. Các lập trình viên cần hết sức thận trọng và chỉ cài đặt các gói mở rộng từ các nguồn được xác minh.",
                cited_article_ids=[a_cursor],
            ),
        ),
        PodcastSegment(
            speaker="Minh",
            line=CitedText(
                text="Những diễn biến vừa rồi là lời nhắc nhở sắc bén về tầm quan trọng của việc cập nhật bản vá và kiểm soát an ninh chuỗi cung ứng. Cảm ơn quý vị đã chú ý theo dõi chương trình hôm nay. Đừng quên truy cập trang tin để đọc đầy đủ các phân tích chi tiết. Xin kính chào và hẹn gặp lại trong bản tin tiếp theo.",
                cited_article_ids=all_cites,
            ),
        ),
    ]

    podcast = PodcastScript(
        title="Bản Tin An Ninh Mạng Mới Nhất: Ivanti, Hack Binance và Triệt Phá Botnet TV Box",
        segments=podcast_segments,
    )

    video_scenes = [
        VideoScene(
            scene_no=1,
            on_screen_text="Bản tin An ninh Mạng Mới Nhất",
            narration=CitedText(
                text="Điểm qua những tiêu điểm an ninh mạng nóng nhất hôm nay.",
                cited_article_ids=all_cites,
            ),
            duration_sec=5.0,
        ),
        VideoScene(
            scene_no=2,
            on_screen_text="CISA: Vá khẩn cấp Ivanti Connect Secure",
            narration=CitedText(
                text="CISA cảnh báo hai lỗ hổng CVE-2026-111357 và CVE-2026-118527 trên Ivanti Connect Secure đang bị khai thác.",
                cited_article_ids=[a_ivanti],
            ),
            duration_sec=15.0,
        ),
        VideoScene(
            scene_no=3,
            on_screen_text="FBI: Triều Tiên đánh cắp hơn $6B tại Binance",
            narration=CitedText(
                text="FBI xác nhận tin tặc Triều Tiên đứng sau vụ đánh cắp hơn 6 tỷ USD tiền số từ sàn Binance.",
                cited_article_ids=[a_binance],
            ),
            duration_sec=15.0,
        ),
        VideoScene(
            scene_no=4,
            on_screen_text="Triệt phá Botnet 1.3 triệu Android TV Box",
            narration=CitedText(
                text="Google và các đối tác triệt hạ botnet 1,3 triệu thiết bị Android TV Box bị cài mã độc ngầm.",
                cited_article_ids=[a_botnet],
            ),
            duration_sec=15.0,
        ),
        VideoScene(
            scene_no=5,
            on_screen_text="Lỗ hổng Langflow & Mã độc chuỗi cung ứng Cursor",
            narration=CitedText(
                text="Cảnh báo lỗ hổng thực thi mã trên Langflow và tiện ích độc hại nhắm vào người dùng Cursor.",
                cited_article_ids=[a_langflow, a_cursor],
            ),
            duration_sec=15.0,
        ),
        VideoScene(
            scene_no=6,
            on_screen_text="Cập nhật chi tiết tại trang tin trực tuyến",
            narration=CitedText(
                text="Truy cập trang tin trực tuyến để xem toàn văn phân tích kỹ thuật và khuyến nghị bảo mật.",
                cited_article_ids=all_cites,
            ),
            duration_sec=5.0,
        ),
    ]

    video = VideoScript(
        title="Điểm Tin An Ninh Mạng Nổi Bật Mới Nhất",
        scenes=video_scenes,
    )

    captions = [
        Caption(
            platform="facebook",
            text="Điểm tin an ninh mạng nổi bật hôm nay:\n- CISA cảnh báo 2 lỗ hổng Ivanti Connect Secure đang bị khai thác thực tế [A1].\n- FBI xác nhận tin tặc Triều Tiên đánh cắp hơn 6 tỷ USD từ sàn Binance [A2].\n- Google triệt phá botnet 1,3 triệu thiết bị Android TV Box bị cài mã độc [A3].\n- IBM cảnh báo lỗ hổng thực thi mã từ xa trên nền tảng AI Langflow [A4].\n- Phát hiện extension Claude Code độc hại trong chuỗi cung ứng Cursor [A5].",
            hashtags=["#AnNinhMang", "#CyberSecurity", "#Ivanti", "#Binance", "#Botnet"],
            cited_article_ids=all_cites,
        ),
        Caption(
            platform="youtube",
            text="Bản tin an ninh mạng hôm nay dành cho chuyên gia công nghệ và quản trị hệ thống:\n1. CISA chỉ đạo vá khẩn cấp hai lỗ hổng Ivanti Connect Secure (CVE-2026-111357, CVE-2026-118527) [A1].\n2. FBI xác nhận nhóm tin tặc Triều Tiên đánh cắp hơn 6 tỷ USD từ sàn tiền ảo Binance [A2].\n3. Google triệt phá thành công mạng botnet kiểm soát 1,3 triệu Android TV Box [A3].\n4. IBM cảnh báo lỗ hổng nghiêm trọng trên Langflow từ bản 1.0.0 đến 1.12.8 [A4].\n5. Cảnh báo tiện ích độc hại nhắm vào trình soạn thảo Cursor [A5].",
            hashtags=["#AnNinhMang", "#BaoMat", "#Ivanti", "#Binance"],
            cited_article_ids=all_cites,
        ),
        Caption(
            platform="tiktok",
            text="Tin an ninh mạng hot hôm nay: CISA cảnh báo Ivanti [A1], FBI xác nhận vụ hack hơn 6 tỷ USD Binance [A2], triệt phá botnet 1,3 triệu TV Box [A3], lỗ hổng AI Langflow [A4] và mã độc Cursor [A5].",
            hashtags=["#AnNinhMang", "#TinCongNghe", "#Ivanti", "#Binance", "#AI"],
            cited_article_ids=all_cites,
        ),
    ]

    pkg = EditorialPackage(
        run_id=run_id,
        language="vi",
        digest=digest,
        podcast=podcast,
        video=video,
        captions=captions,
    )

    print("Validating editorial package...")
    report = validate_package(research, pkg, settings, llm=None)
    print(f"Validation passed: {report.passed}")
    for e in report.errors:
        print(f"ERROR: {e.path} -> {e.message} (evidence: {e.evidence})")
    for w in report.warnings:
        print(f"WARNING: {w.path} -> {w.message}")

    if not report.passed and report.errors:
        print("Validation failed with critical errors! Aborting.")
        return 1

    # Save to output/editorial/
    editorial_dir = Path("output/editorial")
    editorial_dir.mkdir(parents=True, exist_ok=True)
    json_path = editorial_dir / f"{run_id}.json"
    md_path = editorial_dir / f"{run_id}.md"

    json_path.write_text(pkg.model_dump_json(indent=2), encoding="utf-8")
    md_path.write_text(render_markdown(pkg, research), encoding="utf-8")
    print(f"Saved editorial files: {json_path} and {md_path}")

    # Save to output/validation/
    val_dir = Path("output/validation")
    val_dir.mkdir(parents=True, exist_ok=True)
    val_path = val_dir / f"{run_id}.json"
    val_path.write_text(report.model_dump_json(indent=2), encoding="utf-8")
    print(f"Saved validation report: {val_path}")

    # Save to SQLite database
    from cyberpulse.storage import create_run, save_research_result, update_run_status
    from cyberpulse.models import RunStatus

    db_path = settings.paths.data_dir / "cyberpulse.db"
    conn = connect(db_path)
    init_db(conn)
    try:
        create_run(conn, run_id)
    except sqlite3.IntegrityError:
        pass
    save_research_result(conn, research)
    save_editorial_package(conn, pkg)
    save_validation_report(conn, run_id, report)
    update_run_status(conn, run_id, RunStatus.awaiting_review)
    conn.close()
    print("Persisted to SQLite database successfully!")
    return 0

if __name__ == "__main__":
    main()
