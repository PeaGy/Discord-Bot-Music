# Tracen Jukebox

Bot Discord đa chức năng viết bằng Python, tập trung vào phát nhạc trong voice channel và được mở rộng thêm trợ lý AI, tìm kiếm web, bộ nhớ hội thoại và hệ thống tìm ảnh anime.

> Dự án được phát triển từ mã nguồn liên quan đến Ryuz-V/Eva Music Bot và đã được chỉnh sửa đáng kể cho nhu cầu sử dụng riêng.

## Tính năng chính

### Âm nhạc

- Phát nhạc từ từ khóa, YouTube, SoundCloud và liên kết Spotify track.
- Tìm kiếm YouTube với menu chọn 5 kết quả.
- Hàng đợi, quay lại bài trước, tạm dừng, tiếp tục, bỏ bài và dừng phát.
- Music Panel có nút điều khiển, thanh tiến trình, loop track/queue, autoplay và tải MP3 riêng tư.
- Autoplay lấy bài liên quan từ YouTube Mix hoặc kết quả tìm kiếm dự phòng.
- Radio internet từ Radio Browser với giao diện phân trang.
- Lấy lời bài hát từ LRCLIB.
- Chế độ 24/7 và tự rời voice channel sau 3 phút không hoạt động.
- Tự tải trước bài kế tiếp để giảm thời gian chờ.
- Cache nhạc ngắn trên ổ đĩa và chuẩn hóa âm lượng hai lượt về khoảng `-16 LUFS`.
- Bài dài hơn 10 phút và radio được stream trực tiếp thay vì lưu cache.

### Trợ lý AI Peto

- Trò chuyện bằng cách mention bot hoặc reply tin nhắn của bot, không cần slash command.
- Sử dụng Grok qua xAI Responses API; model mặc định là `grok-4.5` và có thể đổi bằng `XAI_MODEL`.
- Hỗ trợ đăng nhập SuperGrok bằng OAuth PKCE, tự refresh token và dùng `XAI_API_KEY` làm phương án dự phòng.
- Có thể đọc tối đa 4 ảnh đính kèm trong tin nhắn Discord; hỗ trợ JPEG, PNG, WebP và GIF.
- Có thể tìm thông tin mới trên web thông qua Tavily.
- Có thể gọi công cụ để phát nhạc, bỏ qua bài hoặc tìm fanart SFW từ Danbooru ngay trong hội thoại.
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
- FFmpeg có `libopus`, `libmp3lame` và có thể gọi bằng lệnh `ffmpeg`.
- Một Discord Bot Token.
- Tài khoản SuperGrok đã đăng nhập hoặc xAI API key.
- Tavily API key.
- Kết nối internet tới Discord, YouTube/Spotify/SoundCloud, LRCLIB, Radio Browser, Danbooru, xAI và Tavily.

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

`requirements.txt` đã bao gồm OpenAI-compatible SDK dùng để gọi xAI, Pillow cho vision, Tavily, SQLite async, discord.py voice và các thư viện phát nhạc cần thiết.

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
TAVILY_API_KEY=tavily_api_key_cua_ban

XAI_MODEL=grok-4.5

# Tùy chọn: chỉ cần khi không dùng SuperGrok OAuth
# XAI_API_KEY=xai_api_key_cua_ban

# Tùy chọn
# XAI_TOKEN_PATH=.xai_tokens.json
# XAI_BASE_URL=https://api.x.ai/v1
# XAI_IMAGE_DETAIL=auto
# XAI_MAX_IMAGES=4
```

`XAI_MODEL`, `XAI_TOKEN_PATH`, `XAI_BASE_URL`, `XAI_IMAGE_DETAIL` và `XAI_MAX_IMAGES` là tùy chọn. Nếu không khai báo, mã nguồn sử dụng các giá trị mặc định nội bộ.

`XAI_IMAGE_DETAIL` nhận một trong ba giá trị: `auto`, `low` hoặc `high`. `XAI_MAX_IMAGES` mặc định là `4`; mỗi ảnh được giới hạn tối đa 20 MiB.

Thiếu `DISCORD_TOKEN` hoặc `TAVILY_API_KEY` sẽ làm quá trình nạp bot/extension thất bại. Nếu chưa có OAuth token và cũng không đặt `XAI_API_KEY`, bot âm nhạc vẫn khởi động nhưng Peto sẽ báo chưa đăng nhập khi được mention.

### 4. Đăng nhập SuperGrok

Chạy một lần trong môi trường Python của dự án:

```bash
python -m xai_oauth login
```

Trình duyệt sẽ mở trang đăng nhập xAI. Sau khi hoàn tất, token được lưu cục bộ tại `.xai_tokens.json` và tự refresh khi cần. Nếu máy đã đăng nhập Grok CLI, bot cũng có thể đọc session tại `~/.grok/auth.json`.

Kiểm tra trạng thái hoặc đăng xuất:

```bash
python -m xai_oauth status
python -m xai_oauth logout
```

Không muốn dùng OAuth thì đặt `XAI_API_KEY` trong `.env`; đây là phương án API pay-as-you-go dự phòng.

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



### 6. Cookie YouTube

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

Nút **Tải xuống** trên Music Panel hỗ trợ bài có thời lượng tối đa 10 phút. Bot chuyển bản cache Opus sang MP3 128 kbps, gửi riêng cho người bấm và xóa MP3 tạm sau khi gửi. Radio, bài không xác định thời lượng và file vượt giới hạn upload hiện tại của Discord sẽ không được gửi.

## Cấu trúc dự án

```text
.
├── bot.py                  # Điểm khởi động, nạp extension và đồng bộ lệnh
├── commands/               # Các slash command
├── features/
│   └── ai_chat.py          # Grok, vision, Tavily và tool calling
├── music/
│   ├── player.py           # Hàng đợi, autoplay, phát nhạc và radio
│   ├── controls.py         # Music Panel và các nút tương tác
│   └── spotify.py          # Lấy metadata Spotify track
├── cache_manager.py        # Cache, loudness normalization và preload
├── danbooru_client.py      # Giao tiếp Danbooru API
├── user_memory.py          # Bộ nhớ SQLite của AI
├── xai_oauth.py            # Đăng nhập SuperGrok OAuth và refresh token
├── requirements.txt
├── run.bat
├── config.py               # Đọc Discord token và cấu hình chung
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

Kiểm tra `.env` có `TAVILY_API_KEY`, đồng thời đã cài `openai`, `Pillow`, `tavily-python`, `python-dotenv` và `aiosqlite`.

### Peto báo chưa đăng nhập SuperGrok

Chạy:

```bash
python -m xai_oauth login
python -m xai_oauth status
```

Nếu OAuth bị từ chối với mã `403`, hãy kiểm tra quyền/gói SuperGrok hoặc đặt `XAI_API_KEY` làm phương án dự phòng. Lỗi `429` thường có nghĩa tài khoản đang bị giới hạn tốc độ hoặc hết quota.

### Không phát được YouTube

Cập nhật yt-dlp và làm mới `cookies.txt`:

```bash
pip install --upgrade yt-dlp
```

### Không thấy slash command

Đảm bảo bot được mời với scope `applications.commands`, có quyền dùng application commands và đã khởi động thành công đến bước đồng bộ lệnh.

## Bảo mật

- Không đưa Discord token, API key, cookie hoặc file database lên Git.
- Không commit `.xai_tokens.json`, `xai_tokens.json` hoặc nội dung `~/.grok/auth.json`; các file token project đã được thêm vào `.gitignore`.
- Nếu một token từng bị chia sẻ công khai, hãy reset/rotate token ngay tại dịch vụ tương ứng.
- Chỉ cấp cho bot những quyền Discord thật sự cần thiết.
- Sao lưu `bot_memory.db` nếu cần giữ trí nhớ AI trước khi di chuyển hoặc cài lại bot.

## Ghi nhận

- Ryuz-V
- Eva Music Bot
- Lara Bot
- PeaGy
