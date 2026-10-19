# 🎶Peto Bot

Bot Discord đa chức năng viết bằng Python, kết hợp nghe nhạc, tải media, trợ lý AI, kiến thức Limbus Company và tìm ảnh Danbooru.

Bạn không thể tự host? mời bot về server của bạn 👉 [Add Pearto to your server](https://discord.com/oauth2/authorize?scope=bot&client_id=1516709319108726944&permissions=92224)

[![License](https://img.shields.io/badge/License-MIT-green.svg?style=for-the-badge)](LICENSE)

## ⚡Tính năng

### 🎶Nhạc

- Phát nhạc bằng tên bài hoặc link YouTube, SoundCloud, Spotify track.
- Điều khiển bằng nút bấm; hỗ trợ hàng chờ, lặp bài, tự phát bài liên quan, radio, lời bài hát và chế độ 24/7.
- Lưu bài yêu thích, playlist, lịch sử nghe và xem thống kê cá nhân/server.

### ⬇️Tải media

- `/download` tải MP3/MP4 từ YouTube, video không watermark hoặc ảnh TikTok, và video/ảnh/GIF từ X/Twitter.
- Chọn định dạng, chất lượng và nhận file qua panel riêng tư. File lớn có thể dùng liên kết tải tạm thời nếu đã cấu hình Download Gateway.
- Hỗ trợ video công khai dưới 60 phút; không hỗ trợ playlist, livestream, Facebook hoặc tải file Instagram.

### 🔗Social Embed

- Hiển thị bài Pixiv, X/Twitter và Instagram thành bản xem trước dễ đọc ngay trong Discord; hỗ trợ cả Pixiv Ugoira.
- Cần quyền **Manage Messages** để ẩn bản xem trước mặc định. Thêm `fxignore` hoặc `peto-noembed` vào tin nhắn để bỏ qua.

### 🤖Trợ lý AI

- Nhắc `@Peto`, reply tin của bot hoặc dùng `/private` để trò chuyện, hỏi đáp và nhờ hướng dẫn cách dùng bot.
- Hỗ trợ tìm thông tin trên web, đọc link, ảnh và video ngắn tối đa 2 phút; tạo/sửa ảnh khi được yêu cầu.
- Study Mode hỗ trợ giải bài tập với các nút **Gợi ý**, **Chép đề**, **Xuất PNG**. Quản trị viên chọn kênh, role và khả năng AI bằng `/settings ai`.

### 🎮Gacha Game

- Tra cứu Limbus Company: Identity, E.G.O, kỹ năng, trạng thái, cốt truyện và gợi ý đội hình; có card minh họa bộ kỹ năng.
- `/gacha` hỗ trợ **Limbus Company**, **Blue Archive**, **Fate/Grand Order** và **Brown Dust 2**. Mặc định là mô phỏng miễn phí khi server chưa bật Economy.
- Theo dõi tin Limbus và thông báo video mới từ kênh YouTube chính thức của Project Moon.

![Giao diện gacha](./assets/gacha.png)

### 💰Peto Economy

- Kiếm Peto Points qua chat/voice, dùng điểm quay gacha và lưu bộ sưu tập riêng cho từng game trong server.
- Xem số dư, xếp hạng bộ sưu tập và tổng kết điểm tuần; đổi Extraction Points lấy Identity 3★ hoặc E.G.O Limbus đủ điều kiện.
- Mặc định **tắt**; quản trị viên bật bằng `/economy enable`. Có giới hạn điểm ngày và sự kiện tăng điểm cuối tuần; tắt Economy vẫn giữ số dư và bộ sưu tập.

### ⏰Daily Reset

- Nhắc reset và gửi checklist cho **NIKKE**, **Blue Archive**, **Trickcal: Chibi Go**, **Chaos Zero Nightmare**, **Limbus Company** và **Brown Dust 2**. Riêng Limbus nhắc Mirror Dungeon hằng tuần.
- Thành viên đăng ký nhận DM; quản trị viên chọn game, kênh và role thông báo riêng cho server bằng `/settings notifications`.

### 🎟️Gift Code

| Game | Tự nhập code | Nhập thủ công |
| --- | --- | --- |
| Brown Dust 2 | ✅ | ✅ |
| NIKKE | ❌ | ✅ |
| Blue Archive | ❌ | ✅ |

- Xem code còn hiệu lực, đánh dấu code đã dùng và đăng ký DM khi có code mới, sắp hết hạn hoặc bản tổng hợp tuần.
- Brown Dust 2 hỗ trợ tự nhập code qua BD2 Pulse bằng nickname; không cần cung cấp mật khẩu game.

### 🖼️Danbooru

- Tìm ảnh anime, hình nền và thông tin tác giả/nguồn ảnh. `/artecchi` và `/artnsfw` chỉ dùng trong kênh Age-Restricted/NSFW.

### 🔎Tìm nguồn ảnh

- Dùng `/saucy` hoặc **Apps → Tìm nguồn ảnh** để mở các nút Google Lens, SauceNAO, IQDB, TinEye, Yandex và Bing. Bạn xem kết quả trực tiếp trên dịch vụ đã chọn.

## 🔧Yêu cầu

- Python 3.10 trở lên.
- FFmpeg có `libopus`, `libmp3lame` và gọi được bằng lệnh `ffmpeg`.
- Discord Bot Token và Tavily API key.
- Tài khoản SuperGrok đã đăng nhập hoặc xAI API key.
- Kết nối internet tới Discord và các dịch vụ media/API liên quan.

## 🚀Cài đặt nhanh

### 1. Tạo môi trường Python

Windows PowerShell:

```powershell
git clone <repository-url>
cd Discord-Bot-Music-main
py -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
pip install -r requirements.txt
```

Linux/macOS:

```bash
git clone <repository-url>
cd Discord-Bot-Music-main
python3 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
pip install -r requirements.txt
```

### 2. Cài FFmpeg

```powershell
# Windows
winget install Gyan.FFmpeg
```

```bash
# Ubuntu/Debian
sudo apt update && sudo apt install ffmpeg

# macOS
brew install ffmpeg
```

Kiểm tra bằng `ffmpeg -version`.

### 3. Cấu hình `.env`

```powershell
Copy-Item .env.example .env
```

Linux/macOS dùng `cp .env.example .env`. Điền tối thiểu:

```dotenv
DISCORD_TOKEN=discord_bot_token
TAVILY_API_KEY=tavily_api_key
XAI_MODEL=grok-4.6
```

Các tùy chọn quan trọng đã được chú thích trong `.env.example`.

Không commit `.env`, cookie, OAuth token hoặc database.

### 4. Đăng nhập SuperGrok

```bash
python -m xai_oauth login
python -m xai_oauth status
```

Token được lưu cục bộ và tự refresh. Nếu không dùng OAuth, đặt `XAI_API_KEY` trong `.env`.

### 5. Cấu hình Discord

Trong Discord Developer Portal:

- Nếu bật `WELCOME_ENABLED=true`, bật **Server Members Intent** tại trang **Bot**.

1. Mời bot với scope `bot` và `applications.commands`.
2. Cứ cấp quyền mạnh nhất cho bot.

Custom embed Pixiv còn cần `PIXIV_PHPSESSID` trong `.env`. Chỉ sao chép **giá trị** cookie `PHPSESSID` từ phiên Pixiv của chính bạn; cookie này có quyền truy cập tài khoản nên tuyệt đối không gửi vào Discord hoặc commit lên Git.

### 6. Cookie YouTube (khi cần)

Khi YouTube yêu cầu đăng nhập/xác minh, xuất cookie định dạng Netscape vào `cookies.txt` tại thư mục gốc. File này không được commit.

## 🛠️Chạy bot

```bash
python bot.py
```

Trên Windows có thể chạy `run.bat`; script sẽ tự khởi động lại bot sau khi tiến trình dừng.

## 🎯Lệnh và chức năng

Gõ `/help` để xem hướng dẫn ngay trong Discord. Trong bảng dưới, `<...>` là thông tin bắt buộc, `[...]` là tùy chọn; nhập lệnh rồi chọn các ô Discord gợi ý.

### Phát nhạc và hàng chờ

Vào kênh voice trước khi phát nhạc; các thao tác quản lý hàng chờ yêu cầu bạn ở cùng kênh với bot.

| Lệnh | Chức năng |
| --- | --- |
| `/play <query>` | Phát nhạc theo tên/link hoặc thêm vào cuối hàng chờ. |
| `/search <query>` | Tìm bài trên YouTube và chọn từ 5 kết quả. |
| `/playnext <query>` | Đưa bài vào đầu hàng chờ để phát kế tiếp. |
| `/queue` | Xem bài đang phát và danh sách bài chờ. |
| `/pause` | Tạm dừng bài đang phát. |
| `/resume` | Tiếp tục bài đã tạm dừng. |
| `/next` | Bỏ qua bài hiện tại. |
| `/previous` | Phát lại bài trước. |
| `/remove <position>` | Xóa một bài theo số thứ tự trong hàng chờ. |
| `/move <from_position> <to_position>` | Chuyển bài sang vị trí khác trong hàng chờ. |
| `/shuffle` | Xáo trộn thứ tự các bài chờ. |
| `/clear` | Xóa hàng chờ, vẫn giữ bài đang phát. |
| `/stop` | Dừng nhạc, xóa hàng chờ và rời voice. |
| `/loop` | Chuyển giữa tắt lặp, lặp bài và lặp hàng chờ. |
| `/autoplay` | Bật/tắt tự phát bài liên quan khi hết hàng chờ. |
| `/247` | Bật/tắt chế độ ở lại voice khi không phát nhạc. |
| `/radio` | Chọn và nghe đài radio internet. |
| `/lyric` | Xem lời bài hát đang phát, mở bản đầy đủ hoặc dịch lời. |
| `/connect` | Gọi bot vào kênh voice của bạn. |
| `/leave` | Cho bot rời voice và xóa hàng chờ. |

### Thư viện, playlist và thống kê

| Lệnh | Chức năng |
| --- | --- |
| `/favorite` | Thêm hoặc bỏ bài đang phát khỏi danh sách yêu thích. |
| `/favorites` | Xem các bài bạn đã yêu thích. |
| `/recent` | Xem lịch sử nghe gần đây của server. |
| `/playlist create <name>` | Tạo playlist cá nhân. |
| `/playlist list` | Xem các playlist của bạn. |
| `/playlist add <name> [query]` | Thêm bài theo tên/link; bỏ trống để lưu bài đang phát. |
| `/playlist show <name>` | Xem danh sách bài trong playlist. |
| `/playlist play <name> [shuffle]` | Thêm playlist vào hàng chờ, tùy chọn xáo trộn. |
| `/playlist savequeue <name>` | Lưu các bài đang chờ vào playlist. |
| `/playlist share <name>` | Tạo mã để chia sẻ playlist. |
| `/playlist clone <code> <new_name>` | Sao chép playlist từ mã chia sẻ. |
| `/playlist import <name> <url>` | Nhập playlist từ YouTube. |
| `/playlist delete <name>` | Xóa playlist của bạn. |
| `/stats [days] [personal]` | Xem thống kê nghe nhạc của server hoặc riêng bạn. |
| `/wrapped` | Xem tổng kết nghe nhạc 30 ngày của server. |

### Tải media và công cụ ảnh

| Lệnh | Chức năng |
| --- | --- |
| `/download <link> [format]` | Mở panel tải media YouTube, TikTok hoặc X/Twitter; chọn tự động, MP3 hoặc MP4. |
| `/art [tags]` | Tìm ảnh anime SFW trên Danbooru. |
| `/wallpaper [huong] [tags]` | Tìm hình nền ngang hoặc dọc. |
| `/artinfo <id>` | Xem tác giả, nguồn và tag của bài Danbooru; nội dung NSFW cần kênh phù hợp. |
| `/artecchi [tags]` | Tìm ảnh ecchi, chỉ trong kênh NSFW. |
| `/artnsfw [tags]` | Tìm ảnh 18+, chỉ trong kênh NSFW. |
| `/sticker <image>` | Tạo file PNG 320×320 để dùng làm sticker. |
| `/emoji <image>` | Tạo file PNG 128×128 để dùng làm emoji. |
| `/saucy <image>` | Mở các liên kết tìm nguồn từ ảnh đính kèm. |

Bạn cũng có thể nhấp phải tin có ảnh → **Apps → Tạo sticker & emoji** hoặc **Tìm nguồn ảnh**.

### AI và quyền riêng tư

Nhắc `@Peto` hoặc reply bot để trò chuyện; nhấp phải tin nhắn → **Apps → Hỏi Peto** để nhờ giải thích, dịch hoặc tóm tắt. Ví dụ: `@Peto giải bài này`, `@Peto video này nói gì?`, `@Peto full kit của ...`.

| Lệnh | Chức năng |
| --- | --- |
| `/private` | Mở cuộc trò chuyện riêng với Peto qua DM. |
| `/andanh` | Bật/tắt ẩn danh tại DM hoặc server hiện tại; không đọc/ghi trí nhớ dài hạn trong phạm vi đó. |
| `/resetmemory` | Xóa trí nhớ AI của chính bạn trên mọi server và DM. |

### Gacha, bộ sưu tập và Limbus

| Lệnh | Chức năng |
| --- | --- |
| `/gacha [game] [pulls] [server] [target]` | Chọn game và quay ×1/×10; FGO dùng ×1/×11. `server` chọn khu vực BA/FGO, `target` chọn nhân vật pickup BA. |
| `/points [member]` | Xem số dư Peto Points, Extraction Points và điểm tuần của bạn hoặc thành viên khác. |
| `/collection [member] [game] [rarity] [page]` | Xem bộ sưu tập theo game, lọc độ hiếm và chọn trang. |
| `/rank [game]` | Xem top 5 thành viên theo số nhân vật/vật phẩm khác nhau đã sưu tập. |
| `/exchange identity <identity>` | Dùng 200 Extraction Points đổi Identity Limbus 3★ chưa sở hữu, trừ Walpurgis. |
| `/exchange ego <ego>` | Dùng 200 Extraction Points đổi E.G.O chưa sở hữu đủ điều kiện, trừ Walpurgis. |
| `/limbusasset status` | Xem trạng thái kho artwork Limbus. |
| `/limbusasset preview <name>` | Xem artwork của Identity hoặc E.G.O theo tên. |

Economy chưa bật thì gacha là mô phỏng miễn phí. Khi bật, gacha dùng Peto Points và lưu bộ sưu tập; chỉ gacha Limbus sinh Extraction Points dùng ở quầy đổi.

### Daily Reset

| Lệnh | Chức năng |
| --- | --- |
| `/dailyreset next <game>` | Xem thời gian reset tiếp theo của game. |
| `/dailyreset subscribe <game>` | Nhận cảnh báo và thông báo reset qua DM. |
| `/dailyreset unsubscribe <game>` | Tắt nhắc reset qua DM của game đó. |
| `/dailyreset subscriptions` | Xem các game bạn đã đăng ký nhắc reset. |

### Gift Code

| Lệnh | Chức năng |
| --- | --- |
| `/coupon codes <game>` | Xem code còn hiệu lực và chưa được bạn đánh dấu đã dùng. |
| `/coupon subscribe <game> [account_id] [auto_redeem]` | Đăng ký DM; có thể lưu UID/nickname và bật tự nhập code cho Brown Dust 2. |
| `/coupon unsubscribe <game>` | Hủy nhận DM và xóa hồ sơ đăng ký của game đó. |
| `/coupon subscriptions` | Xem các game và hồ sơ đã đăng ký. |
| `/coupon history <game>` | Xem lịch sử nhập code của bạn. |
| `/coupon preferences <game> [new_codes] [expiring] [digest]` | Chọn nhận code mới, cảnh báo hết hạn và bản tổng hợp tuần. |
| `/coupon used <game> <code>` | Đánh dấu code đã dùng để ẩn khỏi danh sách của riêng bạn. |

### Cấu hình và quản trị server

Các lệnh cấu hình cần quyền **Manage Server** hoặc chủ bot. Riêng `/resetmemoryall` yêu cầu **Administrator** của server.

| Lệnh | Chức năng |
| --- | --- |
| `/settings notifications <feature> [target]` | Chọn kênh, role và bật/tắt thông báo reset, gift code hoặc Project Moon. |
| `/settings ai [capability]` | Chọn kênh/role dùng AI, chế độ trò chuyện, các khả năng và giới hạn chống spam. |
| `/economy status` | Xem trạng thái và cấu hình Economy của server. |
| `/economy enable` | Bật kiếm Peto Points và gacha có lưu bộ sưu tập. |
| `/economy disable` | Tắt Economy; số dư và bộ sưu tập đã có vẫn được giữ. |
| `/economy channel [channel]` | Chọn kênh đăng bảng điểm tuần/sự kiện; bỏ trống để tắt kênh nhận. |
| `/economy earning <source> <enabled>` | Bật/tắt nguồn kiếm điểm từ chat hoặc voice. |
| `/economy grant <member> <amount>` | Cộng hoặc trừ Peto Points thủ công cho thành viên. |
| `/economy preview` | Xem trước bảng xếp hạng điểm tuần. |
| `/resetmemoryall` | Xóa lịch sử AI trong server hiện tại; giữ trí nhớ cá nhân dùng chung và dữ liệu ở nơi khác. |

### Công cụ dành cho chủ bot

| Lệnh | Chức năng |
| --- | --- |
| `/blacklist <user>` | Chặn một người sử dụng chat AI với Peto. |
| `/unblacklist <user>` | Bỏ chặn quyền chat AI. |
| `/resetmemoryglobal` | Xóa toàn bộ trí nhớ AI của mọi người trên mọi server và DM. |
| `/limbusasset sync <name>` | Làm mới artwork của một Identity/E.G.O. |
| `/projectmoon status` | Xem cấu hình và trạng thái thông báo YouTube Project Moon. |
| `/projectmoon preview` | Xem trước card video mới nhất. |
| `/projectmoon test` | Gửi thử card vào kênh đã cấu hình, không ping role. |
| `/projectmoon check` | Kiểm tra và thông báo video mới ngay. |
| `/dailyreset status` | Xem cấu hình hệ thống Daily Reset. |
| `/dailyreset preview <game> <event>` | Xem trước card cảnh báo hoặc reset của game. |
| `/dailyreset test <game> <event>` | Gửi thử card cảnh báo/reset vào kênh game. |
| `/dailyreset check` | Kiểm tra và xử lý lịch reset đến hạn ngay. |
| `/coupon add <game> <code> <rewards> [expiration] [source]` | Thêm gift code, phần thưởng, hạn dùng và nguồn; gửi thông báo code mới. |
| `/coupon remove <game> <code>` | Ngừng hiển thị một gift code. |
| `/coupon status` | Xem trạng thái hệ thống gift code. |
| `/welcome preview` | Xem trước lời chào thành viên mới. |

### Trợ giúp và kết nối

| Lệnh | Chức năng |
| --- | --- |
| `/help` | Mở bảng trợ giúp có danh mục và nút chuyển trang. |
| `/latency` | Kiểm tra độ trễ kết nối tới Discord. |

## 🧠Trí nhớ và quyền riêng tư

- Trí nhớ cá nhân gắn với Discord `user_id` và theo người dùng giữa DM/các server.
- Câu “hãy nhớ…”, “ghi nhớ…” hoặc “chốt từ giờ…” ghim cả ngữ cảnh liên quan; các bản tóm tắt cũ cũng được giữ để tránh mất chi tiết khi đổi model.
- Lịch sử gốc trong `bot_memory.db` không tự bị cắt; chat thường chỉ gửi một cửa sổ gần cho Grok để giữ tốc độ.
- Khi hỏi “Bot còn nhớ…?”, bot mới tìm sâu trong kho của đúng người đó bằng SQLite cục bộ.
- Prompt giữ nguyên tính cách/nhịp trò chuyện; hướng dẫn toán, ảnh và Limbus chỉ được ghép khi đúng ngữ cảnh. Console ghi token và thời gian của từng lượt xAI để theo dõi chi phí thực tế.
- Grok 4.6 dùng reasoning thích ứng: `low` cho chat/roleplay/trí nhớ/nhạc/ảnh, `medium` cho Limbus/web/kỹ thuật và `high` cho Study Mode hoặc suy luận nhiều bước.
- `/andanh` chỉ giữ ngữ cảnh tạm trong RAM và không đọc/ghi trí nhớ dài hạn.
- `/resetmemory` xóa dữ liệu của người gọi; lệnh admin/chủ bot có phạm vi rộng hơn như tên lệnh mô tả.

## Download Gateway (tùy chọn)

Gateway cho file vượt giới hạn Discord chạy tại `127.0.0.1:8765`; Cloudflare Tunnel ánh xạ domain tải về cổng này.

## Dữ liệu được tạo khi chạy

| Đường dẫn | Nội dung |
| --- | --- |
| `bot_memory.db` | Lịch sử và trí nhớ AI theo người dùng |
| `music_library.db` | Favorites, playlist và lịch sử nghe |
| `limbus_knowledge.db` | Wiki RAG và cache notice chính thức |
| `youtube_notifications.db` | Video Project Moon đã thấy và trạng thái chống thông báo trùng |
| `daily_reset_notifications.db` | Lịch đã gửi và đăng ký Daily Reset DM của thành viên |
| `coupon_codes.db` | Gift code, hồ sơ nickname/UID, lịch sử redeem, đăng ký DM và trạng thái chống gửi trùng |
| `guild_settings.db` | Notification và chính sách AI riêng theo `guild_id` |
| `audio_cache/` | Cache âm thanh đã xử lý |
| `temp_downloads/` | File tải tạm thời |

Queue, loop, autoplay, 24/7 và phiên nút Study Mode nằm trong RAM nên mất khi restart. Database, playlist, favorites và trí nhớ AI vẫn còn.

Các kiểm tra mạng thủ công nằm trong `scripts/manual/`; chúng không chạy cùng bot.

## Xử lý lỗi nhanh

- **Không nhận `ffmpeg`:** mở terminal mới và chạy `ffmpeg -version`.
- **AI chưa đăng nhập:** chạy `python -m xai_oauth login` rồi `python -m xai_oauth status`.
- **OAuth 403/429:** kiểm tra gói/quota SuperGrok hoặc dùng `XAI_API_KEY` dự phòng.
- **YouTube lỗi:** bot tự thử client kế tiếp trong `YTDLP_YOUTUBE_CLIENT`. Khi VPS đã dùng WARP qua `YTDLP_PROXY`, đặt `YTDLP_YOUTUBE_DIRECT_FALLBACK=true` để thử lại qua IP trực tiếp của VPS sau bot-check/403/429; tuyến trực tiếp không dùng cookie. Sau đó có thể bật `YTDLP_SOUNDCLOUD_FALLBACK=true` và `YTDLP_AUDIUS_FALLBACK=true`; hai nguồn ngoài YouTube chỉ stream trực tiếp, dùng chung `YTDLP_AUDIO_FALLBACK_TIMEOUT_SECONDS` và không tạo audio cache. Nếu mọi tuyến đều lỗi, kiểm tra WARP/BgUtils rồi cập nhật các gói trong `requirements.txt`.
- **TikTok extractor lỗi:** cập nhật `yt-dlp`; bot tự thử TikWM khi nguồn chính thất bại.
- **Không thấy slash command:** kiểm tra scope `applications.commands`, quyền bot và log đồng bộ lệnh.
- **Discord báo reconnect rồi `RESUMED`:** thường là lỗi mạng tạm thời, không phải bot crash.

## Bảo mật và sao lưu

- Không chia sẻ Discord token, API key, cookie, `.xai_tokens.json` hoặc database.
- Nếu bí mật từng xuất hiện công khai, hãy rotate/reset ngay tại dịch vụ tương ứng.
- Sao lưu `bot_memory.db`, `music_library.db`, `limbus_knowledge.db` và `coupon_codes.db` trước khi chuyển máy.

## 🤝Ghi nhận

- Ryuz-V
- rapi-bot - mascarell

## 📄 License

Dự án này được phân phối dưới giấy phép MIT.

---

**Made by [PeaGy](https://github.com/PeaGy)** ❤️
