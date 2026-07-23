# Tracen Jukebox

Bot Discord đa chức năng viết bằng Python, tập trung vào phát nhạc trong voice channel và được mở rộng thêm trợ lý AI, tìm kiếm web, bộ nhớ hội thoại và hệ thống tìm ảnh anime.

> Dự án được phát triển từ mã nguồn liên quan đến Ryuz-V/Eva Music Bot và đã được chỉnh sửa đáng kể cho nhu cầu sử dụng riêng.

## Tính năng chính

### Âm nhạc

- Phát nhạc từ từ khóa, YouTube, SoundCloud và liên kết Spotify track.
- Tìm kiếm YouTube với menu chọn 5 kết quả.
- Hàng đợi, quay lại bài trước, tạm dừng, tiếp tục, bỏ bài và dừng phát.
- Music Panel có nút điều khiển, thanh tiến trình, loop track/queue và autoplay.
- Autoplay lấy bài liên quan từ YouTube Mix hoặc kết quả tìm kiếm dự phòng.
- Radio internet từ Radio Browser với giao diện phân trang.
- Lấy lời bài hát từ LRCLIB.
- Chế độ 24/7 và tự rời voice channel sau 3 phút không hoạt động.
- Tự tải trước bài kế tiếp để giảm thời gian chờ.
- Cache nhạc ngắn trên ổ đĩa và chuẩn hóa âm lượng hai lượt về khoảng `-16 LUFS`.
- Bài dài hơn 10 phút và radio được stream trực tiếp thay vì lưu cache.

### Trợ lý AI Peto

- Trò chuyện bằng cách mention bot hoặc reply tin nhắn của bot, không cần slash command.
- Sử dụng Google Gemini thông qua SDK `google-genai`.
- Có thể đổi model bằng biến `GEMINI_MODEL`; cấu hình mẫu hiện dùng `gemini-3.6-flash`.
- Cho phép chọn ngưỡng safety filter bằng `GEMINI_SAFETY_THRESHOLD`.
- Có thể tìm thông tin mới trên web thông qua Tavily.
- Có thể gọi công cụ để phát nhạc hoặc bỏ qua bài ngay trong hội thoại.
- Lưu lịch sử theo người dùng và kênh bằng SQLite.
- Giữ tối đa 15 tin nhắn gần nhất làm ngữ cảnh và cập nhật tóm tắt trí nhớ dài hạn sau mỗi 20 lượt tương tác.
- Dữ liệu hội thoại vẫn còn sau khi bot khởi động lại.
- Có lệnh để người dùng, admin server hoặc chủ bot xóa dữ liệu ở phạm vi phù hợp.

### Danbooru

- Tìm ảnh anime an toàn, ecchi hoặc explicit theo tag.
- Bắt buộc dùng kênh NSFW đối với nội dung ecchi/explicit.
- Tìm wallpaper ngang/dọc có độ phân giải cao.
- Xem chi tiết một Danbooru post theo ID.
- Nút “Ảnh khác” giúp tìm lại mà không cần nhập lại lệnh.

## Yêu cầu

- Python 3.10 trở lên.
- FFmpeg có `libopus` và có thể gọi bằng lệnh `ffmpeg`.
- Một Discord Bot Token.
- Google Gemini API key.
- Tavily API key.
- Kết nối internet tới Discord, YouTube/Spotify/SoundCloud, LRCLIB, Radio Browser, Danbooru, Gemini và Tavily.

Bot dùng trực tiếp `discord.py` voice client, `yt-dlp` và FFmpeg; dự án hiện tại **không dùng Lavalink/Wavelink**.

## Cài đặt

### 1. Tải mã nguồn và tạo môi trường ảo

```powershell
git clone <repository-url>
cd Discord-Bot-Music-main
py -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
pip install -r requirements.txt
```

`requirements.txt` đã bao gồm SDK Gemini, Tavily, SQLite async, discord.py voice và các thư viện phát nhạc cần thiết.

Trên Linux/macOS, dùng `python3` và kích hoạt môi trường bằng:

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
pip install -r requirements.txt
```

### 2. Cài FFmpeg

Windows:

```powershell
winget install Gyan.FFmpeg
```

Ubuntu/Debian:

```bash
sudo apt update
sudo apt install ffmpeg
```

macOS:

```bash
brew install ffmpeg
```

Kiểm tra sau khi cài:

```bash
ffmpeg -version
```

### 3. Tạo `.env`

Sao chép file cấu hình mẫu:

```powershell
Copy-Item .env.example .env
```

Trên Linux/macOS:

```bash
cp .env.example .env
```

Sau đó điền các giá trị thật:

```dotenv
DISCORD_TOKEN=discord_bot_token_cua_ban
GEMINI_API_KEY=google_ai_studio_api_key_cua_ban
TAVILY_API_KEY=tavily_api_key_cua_ban

GEMINI_MODEL=gemini-3.6-flash
GEMINI_SAFETY_THRESHOLD=BLOCK_ONLY_HIGH
```

`GEMINI_API_KEY` cũng có thể được cung cấp qua biến `GOOGLE_API_KEY`. `GEMINI_MODEL` và `GEMINI_SAFETY_THRESHOLD` là tùy chọn; nếu không khai báo, mã nguồn dùng giá trị mặc định.

Các giá trị hợp lệ cho `GEMINI_SAFETY_THRESHOLD`:

- `BLOCK_ONLY_HIGH`
- `BLOCK_MEDIUM_AND_ABOVE`
- `BLOCK_LOW_AND_ABOVE`
- `BLOCK_NONE`
- `OFF`

Module AI được tự động nạp khi bot khởi động. Thiếu `DISCORD_TOKEN`, Gemini API key hoặc `TAVILY_API_KEY` sẽ làm quá trình khởi động thất bại.

### 4. Cấu hình Discord Developer Portal

Trong phần cấu hình Bot:

1. Bật **Message Content Intent** để Peto có thể đọc mention và reply.
2. Mời bot với hai OAuth2 scope: `bot` và `applications.commands`.
3. Cấp tối thiểu các quyền:
   - View Channels
   - Send Messages
   - Embed Links
   - Read Message History
   - Connect
   - Speak
   - Use Application Commands



### 5. Cookie YouTube

Cấu hình phát nhạc hiện tại đọc file `cookies.txt` tại thư mục gốc. Khi YouTube yêu cầu đăng nhập hoặc xác minh, hãy xuất cookie theo định dạng Netscape và lưu vào file này.


## Chạy bot

Chạy trực tiếp:

```bash
python bot.py
```

Trên Windows có thể dùng:

```powershell
.\run.bat
```

`run.bat` sẽ tự khởi động lại bot sau 5 giây nếu tiến trình dừng hoặc gặp lỗi.

## Danh sách lệnh

### Nhạc và voice

| Lệnh | Tham số | Chức năng |
| --- | --- | --- |
| `/play` | `query` | Phát hoặc thêm nhạc từ từ khóa/URL YouTube, SoundCloud hay Spotify track. |
| `/search` | `query` | Tìm 5 kết quả YouTube và chọn bài bằng menu. |
| `/queue` | — | Xem bài đang phát và hàng đợi, 10 bài mỗi trang. |
| `/pause` | — | Tạm dừng bài đang phát. |
| `/resume` | — | Tiếp tục bài đang tạm dừng. |
| `/next` | — | Bỏ qua bài hiện tại. |
| `/previous` | — | Quay lại bài trước trong lịch sử phát. |
| `/stop` | — | Xóa hàng đợi, dừng nhạc và rời voice channel. |
| `/connect` | — | Kết nối bot vào voice channel của người dùng. |
| `/leave` | — | Ngắt kết nối bot khỏi voice channel. |
| `/autoplay` | — | Bật hoặc tắt tự động phát bài liên quan. |
| `/loop` | — | Chuyển trạng thái lặp theo cơ chế slash command hiện có. |
| `/247` | — | Bật hoặc tắt chế độ ở lại voice channel. |
| `/lyric` | — | Lấy lời của bài đang phát từ LRCLIB. |
| `/radio` | — | Mở danh sách radio internet và chọn đài để phát. |
| `/latency` | — | Hiển thị độ trễ giữa bot và Discord. |
| `/help` | — | Mở bảng trợ giúp tương tác. |

> Để chọn đầy đủ `Loop Off`, `Loop Track` hoặc `Loop Queue`, nên dùng nút **Loop** trên Music Panel. Slash command `/loop` hiện vẫn dùng cơ chế cũ.

### Ảnh Danbooru

| Lệnh | Tham số | Chức năng |
| --- | --- | --- |
| `/art` | `tags` tùy chọn | Tìm ảnh anime. |
| `/artecchi` | `tags` tùy chọn | Tìm ảnh sensitive/questionable. |
| `/artnsfw` | `tags` tùy chọn | Tìm ảnh NSFW. |
| `/wallpaper` | `huong`, `tags` tùy chọn | Tìm wallpaper SFW ngang hoặc dọc. |
| `/artinfo` | `id` | Xem nguồn, artist, score và tags của một post. |

Các lệnh ecchi và explicit chỉ hoạt động trong Discord channel đã bật chế độ Age-Restricted/NSFW.

Tag Danbooru thường dùng dấu gạch dưới, ví dụ:
```text
agnes_tachyon_(umamusume)
1girl solo smile
```

### Bộ nhớ AI

| Lệnh | Phạm vi | Chức năng |
| --- | --- | --- |
| `/resetmemory` | Người dùng hiện tại | Xóa lịch sử và bản tóm tắt của chính người gọi trên mọi server. |
| `/resetmemoryall` | Admin server | Xóa lịch sử trong các kênh thuộc server hiện tại; không xóa tóm tắt dài hạn toàn cục. |
| `/resetmemoryglobal` | Chủ bot | Xóa toàn bộ lịch sử, bộ đếm và tóm tắt của mọi người dùng. |

## Cách hoạt động của hệ thống phát nhạc

```text
Từ khóa hoặc URL
        │
        ▼
  yt-dlp lấy metadata/stream
        │
        ├── Radio hoặc bài > 10 phút ──► stream trực tiếp qua FFmpeg
        │
        └── Bài ≤ 10 phút
                │
                ▼
          tải vào audio_cache/
                │
                ▼
       đo và chuẩn hóa âm lượng 2 lượt
                │
                ▼
         mã hóa Opus 160 kbps và phát
```

Nếu đã có bài hợp lệ trong `audio_cache/`, bot phát lại file cục bộ. Trong lúc bài hiện tại đang chạy, bài kế tiếp có thể được tải trước ở background.

## Cấu trúc dự án

```text
.
├── bot.py                  # Điểm khởi động, nạp extension và đồng bộ lệnh
├── commands/               # Các slash command
├── features/
│   └── ai_chat.py          # Gemini, Tavily, safety và tool calling
├── music/
│   ├── player.py           # Hàng đợi, autoplay, phát nhạc và radio
│   ├── controls.py         # Music Panel và các nút tương tác
│   └── spotify.py          # Lấy metadata Spotify track
├── cache_manager.py        # Cache, loudness normalization và preload
├── danbooru_client.py      # Giao tiếp Danbooru API
├── user_memory.py          # Bộ nhớ SQLite của AI
├── requirements.txt
├── run.bat
├── config.py               # File local đọc Discord token từ môi trường
├── .env.example            # Mẫu cấu hình có thể commit
├── .env                    # Token và API keys, không commit
├── cookies.txt             # Cookie yt-dlp, không commit
├── audio_cache/            # Cache âm thanh được tạo khi chạy
└── bot_memory.db           # Cơ sở dữ liệu AI được tạo khi chạy
```

Các file `scratch_*.py` và `test_*.py` là script kiểm tra thủ công cho Discord API, yt-dlp, YouTube Mix và radio; chúng không được nạp khi chạy `bot.py`.

## Dữ liệu và giới hạn hiện tại

- Hàng đợi, lịch sử phát, autoplay và 24/7 được giữ trong RAM nên sẽ mất khi bot khởi động lại.
- `queue` và `history` hiện là biến toàn cục, chưa tách riêng theo từng Discord server. Nếu chạy bot ở nhiều server cùng lúc, các phiên phát có thể ảnh hưởng lẫn nhau.
- Spotify hiện chỉ hỗ trợ link track; bot lấy metadata Spotify rồi tìm nguồn phát tương ứng trên YouTube.
- `audio_cache/` chưa có cơ chế giới hạn dung lượng hoặc tự dọn file cũ.
- Lịch sử AI được lưu trong `bot_memory.db` và tồn tại qua các lần restart.
- Các API bên ngoài có thể giới hạn tần suất, đổi định dạng hoặc tạm thời không phản hồi.

## Xử lý lỗi thường gặp

### `ffmpeg` không được nhận diện

Mở terminal mới sau khi cài FFmpeg và kiểm tra lại bằng `ffmpeg -version`.

### Bot lỗi ngay khi nạp extension AI

Kiểm tra `.env` có đủ `GEMINI_API_KEY` (hoặc `GOOGLE_API_KEY`) và `TAVILY_API_KEY`, đồng thời đã cài `google-genai`, `tavily-python`, `python-dotenv` và `aiosqlite`.

Nếu Gemini trả lỗi `401` hoặc `403`, hãy kiểm tra API key và quyền truy cập model trong `GEMINI_MODEL`. Lỗi `429` thường có nghĩa tài khoản đã hết quota hoặc đang bị giới hạn tốc độ.

### `GEMINI_SAFETY_THRESHOLD` không hợp lệ

Chọn một trong các giá trị được liệt kê trong phần cấu hình `.env`. Bot chủ động dừng khởi động nếu nhận giá trị khác để tránh dùng nhầm cấu hình safety.

### Không phát được YouTube

Cập nhật yt-dlp và làm mới `cookies.txt`:

```bash
pip install --upgrade yt-dlp
```

### Không thấy slash command

Đảm bảo bot được mời với scope `applications.commands`, có quyền dùng application commands và đã khởi động thành công đến bước đồng bộ lệnh.

## Bảo mật

- Không đưa Discord token, API key, cookie hoặc file database lên Git.
- Nếu một token từng bị chia sẻ công khai, hãy reset/rotate token ngay tại dịch vụ tương ứng.
- Chỉ cấp cho bot những quyền Discord thật sự cần thiết.
- Sao lưu `bot_memory.db` nếu cần giữ trí nhớ AI trước khi di chuyển hoặc cài lại bot.

## Ghi nhận

- Ryuz-V
- Eva Music Bot
- Lara Bot
- PeaGy