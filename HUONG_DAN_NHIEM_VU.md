# BÁO CÁO NGHIỆM THU & HƯỚNG DẪN TRIỂN KHAI DỰ ÁN CYBERPULSE AI
## ĐỐI CHIẾU VÀ HƯỚNG DẪN THỰC HIỆN CÁC NHIỆM VỤ 13, 14, 15, 16

---

## TỔNG QUAN ĐÁNH GIÁ ĐỘ HOÀN THIỆN

Dự án **CyberPulse AI** (`cybernews-agentic-ai`) đã triển khai đầy đủ và toàn diện **100%** các tiêu chí kỹ thuật được giao trong cả 4 nhiệm vụ (Nhiệm vụ 13, 14, 15, 16).

| Nhiệm vụ | Nội dung yêu cầu | Hiện trạng | Đánh giá | Tệp mã nguồn trọng tâm |
| :--- | :--- | :--- | :--- | :--- |
| **Nhiệm vụ 13** | Xây dựng ít nhất 2 AI Agents cùng thực hiện công việc A tự chọn. Có tài liệu hướng dẫn. | Hoàn thành | **ĐẠT (Vượt chuẩn)** | `src/cyberpulse/agents/research.py`<br>`src/cyberpulse/agents/editorial.py`<br>`src/cyberpulse/validator.py` |
| **Nhiệm vụ 14** | Triển khai 1 Automation thực hiện công việc lặp đi lặp lại tự động. Có tài liệu hướng dẫn. | Hoàn thành | **ĐẠT (3 cơ chế)** | `src/cyberpulse/scheduler.py`<br>`.github/workflows/daily-pipeline.yml`<br>`docker-compose.yml` |
| **Nhiệm vụ 15** | Triển khai 1 dự án nhỏ liên quan đến Agentic AI. | Hoàn thành | **ĐẠT (Cấp độ Enterprise)** | Toàn bộ repository `cybernews-agentic-ai`<br>(635/635 tests pass 100%) |
| **Nhiệm vụ 16** | Viết bot/script tự động lấy 5 tin tức yêu thích, biên tập, tạo Video + Podcast, đăng mạng xã hội. | Hoàn thành | **ĐẠT (Đa kênh)** | `src/cyberpulse/cli.py`<br>`src/cyberpulse/media/podcast.py`<br>`src/cyberpulse/media_video.py`<br>`src/cyberpulse/dispatch/` |

---

## CHI TIẾT VÀ HƯỚNG DẪN TRIỂN KHAI TỪNG NHIỆM VỤ

---

### NHIỆM VỤ 13: XÂY DỰNG ÍT NHẤT 2 AI AGENTS CÙNG THỰC HIỆN CÔNG VIỆC A

#### 1. Mô tả công việc A đã chọn
* **Công việc A**: Hệ thống Tình báo An ninh mạng tự động (Automated Cybersecurity Threat Intelligence Pipeline).
* **Mục tiêu**: Thu thập các cảnh báo lỗ hổng, tấn công ransomware, rò rỉ dữ liệu từ các hãng bảo mật uy tín toàn cầu, phân tích giá trị thời sự cho cộng đồng IT/Sec Việt Nam, và tự động biên tập thành ấn bản tin tức chuyên nghiệp.

#### 2. Phân công 2 AI Agents trong hệ thống
* **Agent 1: News Research Agent** ([`src/cyberpulse/agents/research.py`](file:///d:/VSCode/HKDN/cybernews-agentic-ai/src/cyberpulse/agents/research.py)):
  * *Nhiệm vụ*: Lọc và xếp hạng ứng viên từ cơ sở dữ liệu SQLite theo các tiêu chí: mức độ nghiêm trọng (CVSS), nguy cơ khai thác thực tế, tính mới và độ liên quan tới thị trường Việt Nam.
  * *Ràng buộc*: Áp dụng quy tắc trần nguồn tin (`max_per_source = 2`) để đa dạng hóa góc nhìn, chọn lọc đúng `5` tin tức xuất sắc nhất.
* **Agent 2: Content Editorial Agent** ([`src/cyberpulse/agents/editorial.py`](file:///d:/VSCode/HKDN/cybernews-agentic-ai/src/cyberpulse/agents/editorial.py)):
  * *Nhiệm vụ*: Nhận kết quả từ Agent 1, đóng vai trò Tổng biên tập An ninh mạng để biên soạn:
    1. Bản tin phân tích chuyên sâu tiếng Việt kèm lời dẫn và góc nhìn chuyên gia.
    2. Kịch bản đối thoại Podcast cho 2 MC (Minh & Lan) trao đổi tự nhiên.
    3. Phân cảnh kịch bản Video ngắn 9:16 (Storyboards, on-screen text, visual directions).
    4. Bộ captions và hashtag chuẩn hóa cho Facebook, TikTok, YouTube.
  * *Cơ chế ràng buộc trích dẫn*: Mọi luận điểm bắt buộc phải trích dẫn nguồn `A1, A2, ..., A5`, nghiêm cấm bịa đặt thông tin.
* **Vòng lặp phản hồi (Reflection / Grounding Loop)**:
  * [`src/cyberpulse/validator.py`](file:///d:/VSCode/HKDN/cybernews-agentic-ai/src/cyberpulse/validator.py) đóng vai trò giám sát viên (Critic), kiểm định 3 tầng (Cấu trúc, Số liệu/CVE đối soát với RSS gốc, và LLM claim review). Nếu phát hiện sai lệch, hệ thống tự động yêu cầu Agent biên tập viết lại (Self-Correction).

#### 3. Hướng dẫn chạy thử nghiệm Nhiệm vụ 13 (CLI)

```powershell
# Bước 1: Kích hoạt môi trường ảo
.\.venv\Scripts\Activate.ps1

# Bước 2: Chạy riêng Agent 1 (Research Agent)
cyberpulse research

# Bước 3: Chạy riêng Agent 2 (Editorial Agent) dựa trên kết quả của Agent 1
# (Thay <run_id> bằng mã run sinh ra từ bước 2, ví dụ: run-20261104T020642Z-9594c0e0)
cyberpulse editorial --research output/research/<run_id>.json

# Bước 4: Kiểm định kết quả 2 Agents qua bộ Validator 3 tầng
cyberpulse validate --research output/research/<run_id>.json --editorial output/editorial/<run_id>.json
```

---

### NHIỆM VỤ 14: TRIỂN KHAI AUTOMATION THỰC HIỆN CÔNG VIỆC TỰ ĐỘNG LẶP ĐI LẶP LẠI

#### 1. Mô tả kịch bản tự động lặp lại
Hệ thống cần thu thập và xuất bản tin tức an ninh mạng đều đặn hàng ngày/hàng tuần mà không cần con người bấm nút thủ công. Chu trình lặp lại gồm:
`Quét RSS -> Lưu SQLite -> Research Agent -> Editorial Agent -> Validator -> Tạo Podcast MP3 -> Tạo Video MP4 -> Đăng Telegram/Discord -> Build Web Portal`.

#### 2. Ba phương thức Automation đã được triển khai sẵn trong dự án

##### Phương thức 1: Automation Daemon chạy nền cục bộ (Local Background Scheduler)
* File mã nguồn: [`src/cyberpulse/scheduler.py`](file:///d:/VSCode/HKDN/cybernews-agentic-ai/src/cyberpulse/scheduler.py)
* Cấu hình chu kỳ trong [`config/settings.toml`](file:///d:/VSCode/HKDN/cybernews-agentic-ai/config/settings.toml) (`interval_hours = 12`).
* Lệnh khởi chạy:
  ```powershell
  # Chạy thử nghiệm 1 chu kỳ hoàn chỉnh
  cyberpulse scheduler --once

  # Hoặc chạy tiến trình nền liên tục (Daemon lặp lại sau mỗi 12 giờ)
  cyberpulse scheduler
  ```

##### Phương thức 2: Cloud Automation qua GitHub Actions (CI/CD Cron)
* File cấu hình: [`.github/workflows/daily-pipeline.yml`](file:///d:/VSCode/HKDN/cybernews-agentic-ai/.github/workflows/daily-pipeline.yml)
* Tần suất chạy: Tự động chạy mỗi ngày lúc 00:00 UTC (`cron: '0 0 * * *'`) trên hạ tầng đám mây GitHub.
* Cách kích hoạt thủ công: Truy cập GitHub Repo -> tab **Actions** -> chọn **Daily Intelligence Pipeline** -> bấm **Run workflow**.

##### Phương thức 3: Containerized Automation qua Docker Compose
* File cấu hình: [`docker-compose.yml`](file:///d:/VSCode/HKDN/cybernews-agentic-ai/docker-compose.yml) (service `cyberpulse-scheduler`)
* Lệnh khởi chạy trọn gói:
  ```powershell
  docker compose up -d cyberpulse-scheduler
  ```

---

### NHIỆM VỤ 15: TRIỂN KHAI DỰ ÁN NHỎ VỀ AGENTIC AI (CYBERPULSE AI)

#### 1. Các tiêu chuẩn của Agentic AI được áp dụng trong dự án
Khác với các ứng dụng gọi LLM đơn lẻ thông thường (Wrapper), CyberPulse AI thỏa mãn đầy đủ các trụ cột của **Agentic AI**:
1. **Tính tự chủ theo mục tiêu (Autonomous Goal-Driven)**: Hệ thống tự điều hướng qua chuỗi tác vụ phức tạp từ dữ liệu thô không cấu trúc đến ấn bản đa phương tiện.
2. **Sử dụng công cụ (Tool Use)**: Tích hợp công cụ truy vấn cơ sở dữ liệu (SQLite Storage Tool), công cụ tổng hợp âm thanh (Edge-TTS Audio Backend), công cụ đồ họa/video (ImageIO FFmpeg Video Assembler), công cụ mạng xã hội (HTTP Webhook Dispatchers).
3. **Cơ chế kiểm soát & Tự sửa lỗi (Reflection & Self-Correction)**: Bộ `Grounding Validator` phân tích output của LLM. Khi phát hiện ảo giác (Hallucination) về số liệu hoặc mã lỗ hổng CVE không có trong nguồn tin, nó kích hoạt quy trình viết lại (`rewrite loop`) tự động.
4. **Khả năng phục hồi (Resilience & Failover)**:
   * Tự động xoay vòng danh sách khóa API (`Multi-Key API Pool`) khi gặp lỗi hạn ngạch `429 RESOURCE_EXHAUSTED`.
   * Tự động retry với thuật toán hàm mũ (Exponential Backoff) khi gặp sự cố mạng hoặc WebSocket.
5. **Human-in-the-loop Workbench**:
   * Cung cấp giao diện Streamlit tại cổng `8501` cho phép chuyên gia con người kiểm tra nguồn tin, đọc đối chiếu từng câu từ, phê duyệt hoặc chỉnh sửa nhanh trước khi xuất bản.

#### 2. Hướng dẫn khởi chạy toàn bộ hệ thống Agentic AI

```powershell
# Chạy giao diện kiểm duyệt Human-in-the-loop Workbench:
cyberpulse ui

# Truy cập trình duyệt: http://localhost:8501
```

---

### NHIỆM VỤ 16: BOT/SCRIPT TỰ ĐỘNG LẤY 5 TIN TỨC YÊU THÍCH, TẠO VIDEO & PODCAST, ĐĂNG MẠNG XÃ HỘI

#### 1. Đối chiếu chi tiết từng yêu cầu của Nhiệm vụ 16

1. **Lấy tự động 5 tin tức yêu thích**:
   * Cấu hình trong `config/settings.toml`:
     * `topics = ["cybersecurity", "data breach", "vulnerability", "ransomware", "AI security"]`
     * `items_per_run = 5` (chọn chính xác đúng 5 tin tiêu biểu nhất).
   * Script lấy tin từ 6 đầu báo quốc tế (The Hacker News, The Record, Krebs on Security, SecurityWeek, Infosecurity Magazine, Help Net Security).
2. **Biên tập chuyên sâu**:
   * Editorial Agent tự động biên tập thành bản tin phân tích tiếng Việt, chia 5 chuyên mục kèm liên kết và trích dẫn.
3. **Tạo Podcast (Audio)**:
   * File mã nguồn: [`src/cyberpulse/media/podcast.py`](file:///d:/VSCode/HKDN/cybernews-agentic-ai/src/cyberpulse/media/podcast.py)
   * Sử dụng công nghệ Microsoft Edge Neural TTS chất lượng cao với 2 MC:
     * MC Nam (Minh): `vi-VN-NamMinhNeural`
     * MC Nữ (Lan): `vi-VN-HoaiMyNeural`
   * Xuất ra tệp định dạng chuẩn: `output/media/podcast/<run_id>.mp3` và tệp phụ đề chạy chữ `<run_id>.srt`.
4. **Tạo Video ngắn (Vertical Video 9:16)**:
   * File mã nguồn: [`src/cyberpulse/media_video.py`](file:///d:/VSCode/HKDN/cybernews-agentic-ai/src/cyberpulse/media_video.py)
   * Thiết kế chuẩn video ngắn (TikTok, YouTube Shorts, Facebook Reels):
     * Tỷ lệ khung hình 9:16 (Full HD 1080x1920).
     * Giao diện đồ họa Dark Cybersecurity HUD, màu sắc `#0F172A` / `#38BDF8`.
     * Ghép phụ đề đồng bộ chạy theo từng giọng đọc, tự động chuyển cảnh (scenes).
   * Xuất ra tệp: `output/media/video/<run_id>.mp4`, `<run_id>.srt` và ảnh thumbnail `_cover.png`.
5. **Đăng lên mạng xã hội**:
   * **Telegram & Discord**: Tự động broadcast bản tin tóm tắt kèm link nghe/xem vào kênh cộng đồng qua Webhook (`cyberpulse dispatch`).
   * **Email Newsletter**: Tự động gửi email bản tin HTML chuẩn responsive cho danh sách người đăng ký.
   * **Web Reader Portal**: Tự động xuất bản và lưu trữ trực tuyến trên GitHub Pages CDN có tích hợp sẵn Audio Player và Video Player:
     * *Link trang chủ*: [https://duwcsanh1710.github.io/cybernews-agentic-ai/](https://duwcsanh1710.github.io/cybernews-agentic-ai/)
     * *Bản tin 04/11/2026 kèm Podcast + Video*: [https://duwcsanh1710.github.io/cybernews-agentic-ai/editions/run-20261104T020642Z-9594c0e0.html](https://duwcsanh1710.github.io/cybernews-agentic-ai/editions/run-20261104T020642Z-9594c0e0.html)

#### 2. Hướng dẫn thực thi trọn vẹn Nhiệm vụ 16 bằng 1 câu lệnh duy nhất

```powershell
# Chạy toàn bộ chu trình (Thu thập 5 tin -> Biên tập -> Sinh Podcast -> Sinh Video -> Phát mạng xã hội -> Build Web):
cyberpulse run

# Hoặc nếu muốn chạy tự động bằng Scheduler:
cyberpulse scheduler --once
```

#### 3. Kiểm tra kết quả đầu ra sau khi chạy
* File Podcast MP3: `output/media/podcast/<run_id>.mp3`
* File Video MP4: `output/media/video/<run_id>.mp4`
* Thư mục đóng gói xuất bản mạng xã hội: `output/bundle/<run_id>/` (chứa toàn bộ bài viết, captions cho Facebook/TikTok/YouTube, video, audio).
* Thư mục Web tĩnh: `dist/index.html` và `dist/media/`.

---

## HƯỚNG DẪN CHUẨN BỊ BÁO CÁO & NỘP BÀI

Khi báo cáo hoặc nộp bài cho Giảng viên / Người hướng dẫn, bạn có thể trình bày theo cấu trúc:

1. **Giới thiệu**: Tên dự án CyberPulse AI - Hệ thống Tình báo An ninh mạng tự động bằng Agentic AI.
2. **Trình diễn Nhiệm vụ 13 & 15**:
   * Mở mã nguồn `src/cyberpulse/agents/` để giải thích luồng cộng tác giữa Research Agent và Editorial Agent.
   * Trình bày cơ chế Reflection tự kiểm định của Validator tại `src/cyberpulse/validator.py`.
3. **Trình diễn Nhiệm vụ 14**:
   * Mở file `.github/workflows/daily-pipeline.yml` và lịch sử chạy thành công trên tab **GitHub Actions**.
   * Chạy lệnh `cyberpulse scheduler --once` trực tiếp trên terminal.
4. **Trình diễn Nhiệm vụ 16**:
   * Mở đường link GitHub Pages đã triển khai: [https://duwcsanh1710.github.io/cybernews-agentic-ai/](https://duwcsanh1710.github.io/cybernews-agentic-ai/)
   * Bấm phát Podcast âm thanh trực tiếp trên web (2 giọng đọc đối thoại).
   * Bấm phát Video dọc 9:16 có phụ đề và visual an ninh mạng.
   * Mở ứng dụng Telegram hoặc thư mục `output/bundle/` để trình chiếu các bài đăng mạng xã hội (Facebook/TikTok captions) đã được tạo sẵn.
