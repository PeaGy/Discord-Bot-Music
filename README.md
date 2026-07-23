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
- Sử dụng Groq với model `openai/gpt-oss-120b`.
- Có thể tìm thông tin mới trên web thông qua Tavily.
- Có thể gọi công cụ để phát nhạc hoặc bỏ qua bài ngay trong hội thoại.
- Lưu lịch sử theo người dùng và kênh bằng SQLite.
- Tạo bản tóm tắt trí nhớ dài hạn định kỳ; dữ liệu vẫn còn sau khi bot khởi động lại.
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
- Groq API key.
- Tavily API key.
- Kết nối internet tới Discord, YouTube/Spotify/SoundCloud, LRCLIB, Radio Browser, Danbooru, Groq và Tavily.

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
pip install aiosqlite
```

`user_memory.py` đang sử dụng `aiosqlite` nhưng package này chưa có trong `requirements.txt`, vì vậy cần cài thêm bằng lệnh ở trên.

Trên Linux/macOS, dùng `python3` và kích hoạt môi trường bằng:

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
pip install -r requirements.txt
pip install aiosqlite
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

### 3. Tạo `config.py`

Tạo file `config.py` tại thư mục gốc:

```python
TOKEN = "DISCORD_BOT_TOKEN_CUA_BAN"
```

### 4. Tạo `.env`

Tạo file `.env` tại thư mục gốc:

```dotenv
GROQ_API_KEY=groq_api_key_cua_ban
TAVILY_API_KEY=tavily_api_key_cua_ban
```

Module AI được tự động nạp khi bot khởi động. Với mã nguồn hiện tại, thiếu một trong hai API key trên sẽ làm quá trình khởi động thất bại.

### 5. Cấu hình Discord Developer Portal

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

Các lệnh ecchi và explicit chỉ hoạt động trong Discord channel đã bật chế độ Age-Restricted/NSFW.

### 6. Cookie YouTube

Cấu hình phát nhạc hiện tại đọc file `cookies.txt` tại thư mục gốc. Khi YouTube yêu cầu đăng nhập hoặc xác minh, hãy xuất cookie theo định dạng Netscape và lưu vào file này.

Không chia sẻ hoặc commit `config.py`, `.env` và `cookies.txt`. Các file này đã được đưa vào `.gitignore`.

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

Khi khởi động, bot tự động:

1. Nạp toàn bộ module `.py` trong `commands/` và `features/`.
2. Khởi tạo cơ sở dữ liệu SQLite cho AI.
3. Đồng bộ global slash commands với Discord.
4. Luân phiên trạng thái hiển thị mỗi 30 giây.

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
| `/art` | `tags` tùy chọn | Tìm ảnh anime SFW. |
| `/artecchi` | `tags` tùy chọn | Tìm ảnh sensitive/questionable; chỉ dùng trong kênh NSFW. |
| `/artnsfw` | `tags` tùy chọn | Tìm ảnh explicit; chỉ dùng trong kênh NSFW. |
| `/wallpaper` | `huong`, `tags` tùy chọn | Tìm wallpaper SFW ngang hoặc dọc. |
| `/artinfo` | `id` | Xem nguồn, artist, score và tags của một post. |

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
│   └── groq_chat.py        # AI chat, Tavily và tool calling
├── music/
│   ├── player.py           # Hàng đợi, autoplay, phát nhạc và radio
│   ├── controls.py         # Music Panel và các nút tương tác
│   └── spotify.py          # Lấy metadata Spotify track
├── cache_manager.py        # Cache, loudness normalization và preload
├── danbooru_client.py      # Giao tiếp Danbooru API
├── user_memory.py          # Bộ nhớ SQLite của AI
├── requirements.txt
├── run.bat
├── config.py               # Token Discord, không commit
├── .env                    # API keys, không commit
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

### `python` không được nhận diện

Cài Python và bật tùy chọn **Add Python to PATH**. Nếu máy chỉ có lệnh `py`, có thể chạy:

```powershell
py bot.py
```

Đồng thời sửa `python bot.py` thành `py bot.py` trong `run.bat` nếu muốn dùng script tự khởi động lại.

### `ffmpeg` không được nhận diện

Mở terminal mới sau khi cài FFmpeg và kiểm tra lại bằng `ffmpeg -version`.

### Bot lỗi ngay khi nạp extension AI

Kiểm tra `.env` có đủ `GROQ_API_KEY` và `TAVILY_API_KEY`, đồng thời đã cài `groq`, `tavily-python`, `python-dotenv` và `aiosqlite`.

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
