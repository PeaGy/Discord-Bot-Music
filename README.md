# Tracen Jukebox

Bot Discord đa chức năng viết bằng Python, tập trung vào phát nhạc trong voice channel và được mở rộng thêm trợ lý AI, tìm kiếm web, bộ nhớ hội thoại và hệ thống tìm ảnh anime.

> Dự án được phát triển từ mã nguồn liên quan đến Ryuz-V/Eva Music Bot và đã được chỉnh sửa đáng kể cho nhu cầu sử dụng riêng.

## Tính năng chính

### Âm nhạc

- Phát nhạc từ từ khóa, YouTube, SoundCloud và liên kết Spotify track.
- Tìm kiếm YouTube với menu chọn 5 kết quả.
- Hàng đợi, quay lại bài trước, tạm dừng, tiếp tục, bỏ bài và dừng phát.
- Mỗi Discord server có phiên phát riêng: queue, lịch sử, loop, autoplay, 24/7, timer và Music Panel không ảnh hưởng lẫn nhau.
- Music Panel dùng Discord Components V2 với ảnh bìa, thanh tiến trình, hai hàng điều khiển, loop track/queue, autoplay và tải MP3 riêng tư.
- Thư viện nhạc SQLite theo người dùng/server: yêu thích, playlist cá nhân và lịch sử nghe gần đây.
- Nút **Yêu thích** ngay trên Music Panel; playlist và favorites vẫn còn sau khi bot khởi động lại.
- Autoplay lấy bài liên quan từ YouTube Mix hoặc kết quả tìm kiếm dự phòng.
- Radio internet từ Radio Browser với giao diện phân trang.
- Lấy lời bài hát từ LRCLIB.
- Chế độ 24/7 và tự rời voice channel sau 3 phút không hoạt động.
- Tự tải trước bài kế tiếp để giảm thời gian chờ.
- Tự bỏ qua bài có stream lỗi để tiếp tục hàng đợi; khóa phát riêng từng server tránh hai yêu cầu chạy đè nhau.
- Cache nhạc ngắn trên ổ đĩa và chuẩn hóa âm lượng hai lượt về khoảng `-16 LUFS`.
- Bài dài hơn 10 phút và radio được stream trực tiếp thay vì lưu cache.

### Trợ lý AI Peto

- Trò chuyện trong server bằng cách mention/reply bot, hoặc dùng `/private` để mở DM và nhắn tự nhiên không cần mention.
- Sử dụng Grok qua xAI Responses API; model mặc định là `grok-4.5` và có thể đổi bằng `XAI_MODEL`.
- Hỗ trợ đăng nhập SuperGrok bằng OAuth PKCE, tự refresh token và dùng `XAI_API_KEY` làm phương án dự phòng.
- Có thể đọc tối đa 4 ảnh đính kèm trong tin nhắn Discord; hỗ trợ JPEG, PNG, WebP và GIF.
- Có thể tìm thông tin mới trên web thông qua Tavily.
- Có thể gọi công cụ để phát nhạc, bỏ qua bài hoặc tìm fanart SFW từ Danbooru ngay trong hội thoại.
- Lưu lịch sử theo người dùng và kênh bằng SQLite; trí nhớ dài hạn được tách riêng giữa DM và từng server.
- Giữ tối đa 15 tin nhắn gần nhất làm ngữ cảnh và cập nhật tóm tắt riêng cho đúng phạm vi sau mỗi 20 lượt tương tác.
- Dữ liệu hội thoại vẫn còn sau khi bot khởi động lại.
- `/andanh` bật chế độ **Ẩn danh** theo DM hoặc server: chỉ giữ ngữ cảnh tạm trong RAM, không đọc/ghi trí nhớ SQLite.
- Có lệnh để người dùng, admin server hoặc chủ bot xóa dữ liệu ở phạm vi phù hợp.
- Tự nhận diện bài tập/toán học để mở **Study Mode** với các nút Gợi ý, Giải chi tiết, Kiểm tra đáp án và Xuất PNG.
- Study Mode đọc lại ảnh đề khi bấm nút, chỉ người gửi đề được thao tác và tự khóa sau 15 phút.

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

# Mức chi tiết console log: DEBUG | INFO | WARNING | ERROR
# LOG_LEVEL=INFO

# File SQLite lưu favorites, playlists và lịch sử nghe
# MUSIC_LIBRARY_DB=music_library.db

# Font tùy chỉnh cho ảnh lời giải Study Mode (không bắt buộc)
# STUDY_FONT_PATH=C:\Windows\Fonts\arial.ttf
```

`XAI_MODEL`, `XAI_TOKEN_PATH`, `XAI_BASE_URL`, `XAI_IMAGE_DETAIL`, `XAI_MAX_IMAGES`, `LOG_LEVEL`, `MUSIC_LIBRARY_DB` và `STUDY_FONT_PATH` là tùy chọn. Nếu không khai báo, mã nguồn sử dụng các giá trị mặc định nội bộ.

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
| `/favorite` | — | Thêm hoặc xóa bài đang phát khỏi danh sách yêu thích cá nhân. |
| `/favorites` | — | Xem tối đa 20 bài yêu thích gần nhất của bạn trong server. |
| `/recent` | — | Xem 15 bài được phát gần nhất trong server. |
| `/playlist create` | `name` | Tạo playlist cá nhân. |
| `/playlist list` | — | Liệt kê playlist và số bài của bạn. |
| `/playlist add` | `name` | Thêm bài đang phát vào playlist. |
| `/playlist show` | `name` | Xem các bài trong playlist. |
| `/playlist play` | `name` | Thêm toàn bộ playlist vào hàng đợi hiện tại. |
| `/playlist delete` | `name` | Xóa playlist và các mục đã lưu trong đó. |
| `/pause` | — | Tạm dừng bài đang phát. |
| `/resume` | — | Tiếp tục bài đang tạm dừng. |
| `/next` | — | Bỏ qua bài hiện tại. |
| `/previous` | — | Quay lại bài trước trong lịch sử phát. |
| `/stop` | — | Xóa hàng đợi, dừng nhạc và rời voice channel. |
| `/connect` | — | Kết nối bot vào voice channel của người dùng. |
| `/leave` | — | Ngắt kết nối bot khỏi voice channel. |
| `/autoplay` | — | Bật hoặc tắt tự động phát bài liên quan. |
| `/loop` | — | Chuyển lần lượt giữa Loop Off, Loop Track và Loop Queue. |
| `/247` | — | Bật hoặc tắt chế độ ở lại voice channel. |
| `/lyric` | — | Lấy lời của bài đang phát từ LRCLIB. |
| `/radio` | — | Mở danh sách radio internet và chọn đài để phát. |
| `/latency` | — | Hiển thị độ trễ giữa bot và Discord. |
| `/help` | — | Mở bảng trợ giúp tương tác. |

Nút **Loop** trên Music Panel và lệnh `/loop` cùng sử dụng một trạng thái, theo chu kỳ `Off → Track → Queue → Off`.

Music Panel được dựng hoàn toàn bằng Discord Components V2 (`LayoutView`, `Container`, `Section`, `TextDisplay` và `ActionRow`) thay cho embed truyền thống. Bot tái sử dụng cùng một tin nhắn cho các trạng thái đang tải, đang phát, lỗi và hết hàng đợi. Thanh tiến trình được tính lại khi panel có tương tác cập nhật như Pause/Resume, Loop hoặc Autoplay; bot không chạy timer chỉnh sửa tin nhắn liên tục.

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
| `/private` | Người dùng hiện tại | Mở cuộc trò chuyện DM riêng với Peto. |
| `/andanh` | DM hoặc server hiện tại | Bật/tắt Ẩn danh; ngữ cảnh chỉ giữ tạm trong RAM và không lưu SQLite. |
| `/resetmemory` | Người dùng hiện tại | Xóa lịch sử và bản tóm tắt của chính người gọi trong DM và mọi server. |
| `/resetmemoryall` | Admin server | Xóa lịch sử và tóm tắt chỉ trong server hiện tại. |
| `/resetmemoryglobal` | Chủ bot | Xóa toàn bộ lịch sử, bộ đếm và tóm tắt của mọi người dùng. |

Ẩn danh chỉ ngăn bot lưu nội dung vào database cục bộ; yêu cầu vẫn phải được gửi đến Grok/xAI để tạo câu trả lời. Khi tắt Ẩn danh hoặc bot khởi động lại, ngữ cảnh tạm sẽ bị xóa. `/resetmemory` vẫn là lệnh duy nhất để người dùng xóa toàn bộ trí nhớ đã lưu của chính họ; dự án không tạo thêm lệnh `/forgetme` trùng chức năng.

### Study Mode

Mention hoặc reply Peto kèm đề bài, ví dụ `@Peto giải bài này`, có thể đính kèm ảnh. Khi nhận diện đây là bài tập, Peto gắn bảng nút dưới câu trả lời:

- **Gợi ý**: đưa hướng đi tăng dần nhưng không tiết lộ đáp án cuối.
- **Giải chi tiết**: giải lại từng bước và tự kiểm tra kết quả.
- **Kiểm tra đáp án**: mở hộp nhập bài làm, chỉ ra bước sai đầu tiên và cách sửa.
- **Xuất PNG**: render lời giải gần nhất thành ảnh nền tối để lưu hoặc chia sẻ.

Chỉ người gửi đề sử dụng được bảng nút. Phiên tồn tại trong RAM 15 phút và sẽ hết hiệu lực nếu bot restart. Người dùng vẫn có thể reply Peto để hỏi tiếp về một bước trong lời giải như hội thoại bình thường.

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
│   ├── state.py            # Trạng thái phát nhạc độc lập theo từng server
│   └── spotify.py          # Lấy metadata Spotify track
├── cache_manager.py        # Cache, loudness normalization và preload
├── logging_setup.py        # Cấu hình console log thống nhất
├── music_library.py        # SQLite favorites, playlists và lịch sử nghe
├── study_mode.py           # Nút học tập và xuất lời giải PNG
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
├── music_library.db        # Thư viện nhạc cá nhân được tạo khi chạy
└── bot_memory.db           # Cơ sở dữ liệu AI được tạo khi chạy
```

Các file `scratch_*.py` và `test_*.py` là script kiểm tra thủ công cho Discord API, yt-dlp, YouTube Mix và radio; chúng không được nạp khi chạy `bot.py`.

## Dữ liệu và giới hạn hiện tại

- Hàng đợi, lịch sử phát, loop, autoplay và 24/7 được tách riêng theo từng Discord server nhưng vẫn chỉ giữ trong RAM, nên sẽ mất khi bot khởi động lại.
- Favorites, playlist và 100 lượt nghe gần nhất mỗi server được lưu trong `music_library.db`, nên không mất khi restart.
- Mỗi người có tối đa 100 favorites, 25 playlist/server và 100 bài/playlist.
- Phiên nút Study Mode chỉ giữ trong RAM 15 phút; lời giải chat tuân theo phạm vi bộ nhớ hoặc chế độ Ẩn danh hiện tại.
- Spotify hiện chỉ hỗ trợ link track; bot lấy metadata Spotify rồi tìm nguồn phát tương ứng trên YouTube.
- `audio_cache/` chưa có cơ chế giới hạn dung lượng hoặc tự dọn file cũ.
- Lịch sử AI thường được lưu trong `bot_memory.db` và tồn tại qua restart; nội dung Ẩn danh không được ghi vào file này.
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

### Discord reconnect hoặc không cập nhật được status

Khi WebSocket Discord đang đóng rồi khôi phục, bot có thể ghi một dòng `WARNING` rằng đã tạm bỏ qua cập nhật trạng thái. Đây là lỗi kết nối tạm thời và không làm dừng nhạc; dòng `discord.gateway ... RESUMED` sau đó nghĩa là phiên đã phục hồi thành công. Cảnh báo status giống nhau được giới hạn tối đa một lần mỗi phút để tránh spam console.

Console dùng `INFO` theo mặc định. Chỉ đặt `LOG_LEVEL=DEBUG` trong `.env` khi cần chẩn đoán chi tiết; có thể dùng `WARNING` nếu muốn ít log hơn.

## Bảo mật

- Không đưa Discord token, API key, cookie hoặc file database lên Git.
- Không commit `.xai_tokens.json`, `xai_tokens.json` hoặc nội dung `~/.grok/auth.json`; các file token project đã được thêm vào `.gitignore`.
- Nếu một token từng bị chia sẻ công khai, hãy reset/rotate token ngay tại dịch vụ tương ứng.
- Chỉ cấp cho bot những quyền Discord thật sự cần thiết.
- Sao lưu `bot_memory.db` và `music_library.db` nếu cần giữ trí nhớ AI cùng thư viện nhạc trước khi di chuyển hoặc cài lại bot.

## Ghi nhận

- Ryuz-V
- Eva Music Bot
- Lara Bot
- PeaGy
