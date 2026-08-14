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
- Quản lý queue đầy đủ: ưu tiên bài kế tiếp, xóa, di chuyển, xáo trộn và dọn hàng đợi; bot cảnh báo trước khi thêm bài trùng.
- Playlist nâng cao: lưu cả queue, chia sẻ/sao chép bằng mã, nhập playlist YouTube và xáo thông minh tránh các bài vừa nghe.
- Thống kê nghe nhạc `/stats`, tổng kết `/wrapped`; chỉ tính bài đã nghe ít nhất 30 giây.
- Lyrics gọn với nút xem đầy đủ và dịch Việt/Anh riêng tư, tránh làm đầy kênh chat.
- Autoplay lấy bài liên quan từ YouTube Mix hoặc kết quả tìm kiếm dự phòng.
- Radio internet từ Radio Browser với giao diện phân trang.
- Lấy lời bài hát từ LRCLIB.
- Chế độ 24/7 và tự rời voice channel sau 3 phút không hoạt động.
- Tự tải trước bài kế tiếp để giảm thời gian chờ.
- Tự bỏ qua bài có stream lỗi để tiếp tục hàng đợi; khóa phát riêng từng server tránh hai yêu cầu chạy đè nhau.
- Cache nhạc ngắn trên ổ đĩa và chuẩn hóa âm lượng hai lượt về khoảng `-16 LUFS`.
- Bài dài hơn 10 phút và radio được stream trực tiếp thay vì lưu cache.

### Universal Media Downloader

- Lệnh `/download link:<URL> [format]` tạo custom embed **Only Visible to you** có metadata, thumbnail và nút tải cho YouTube, TikTok hoặc X/Twitter; bot không tự quét link trong chat.
- YouTube hiện một hàng lựa chọn gồm **MP3 chất lượng cao** (mục tiêu 320 kbps) và tối đa ba nút MP4 gần các mốc **360p, 720p, 1080p**. Bot chỉ hiện độ phân giải thật sự có trên video và còn nằm trong giới hạn Download Gateway. TikTok/X giữ MP4; TikTok video không watermark và TikTok photo tải toàn bộ ảnh gốc.
- Với video YouTube có nhiều track lồng tiếng, MP3 và MP4 luôn ưu tiên track được YouTube đánh dấu **original/default** hoặc trùng ngôn ngữ gốc; bitrate của bản dub không được phép ghi đè lựa chọn này.
- TikTok ưu tiên `yt-dlp`; khi extractor gặp challenge/rehydration hoặc IP bị chặn, bot tự thử TikWM. Link TikTok chỉ được gửi tới dịch vụ bên thứ ba này khi nguồn chính thất bại.
- File chỉ bắt đầu được tạo khi có người bấm nút và được gửi riêng tư cho người đó qua Discord.
- Sau khi bấm nút, phản hồi riêng tư hiển thị **Download Tracker** theo từng giai đoạn: chờ lượt, tải hình, tải audio gốc, ghép bằng FFmpeg, hoàn thiện/kiểm tra MP4, gửi Discord hay tạo link Cloudflare. Thay đổi giai đoạn được hiện gần như ngay; phần trăm, số MiB, tốc độ và ETA trong cùng một giai đoạn được giới hạn khoảng bốn giây một lần để tránh Discord rate-limit.
- Panel và nút tải có hiệu lực trong 10 phút. Với TikTok photo, bot nhận tối đa 35 ảnh, giới hạn tổng dữ liệu tạm 80 MiB và tự chia kết quả thành các lượt tối đa 10 file theo giới hạn upload hiện tại của Discord.
- `/download` xử lý một video công khai có thời lượng **dưới 60 phút**; không nhận playlist, livestream, Facebook hoặc Instagram. Giới hạn 10 phút của nút tải trên Music Panel là luồng riêng và không thay đổi.
- Khi Download Gateway hoạt động, YouTube MP3 được xuất ở 320 kbps. Nếu gateway bị tắt, bot tự chọn từ 320 xuống tối thiểu 96 kbps theo giới hạn upload Discord.
- MP4 giữ nguyên luồng hình và ghép luồng tiếng phù hợp từ YouTube thay vì encode lại; file vượt giới hạn gateway sẽ bị từ chối.
- Tối đa hai lượt tải chạy đồng thời trên toàn bot và mỗi người chỉ có một lượt; file tạm được xóa ngay sau khi gửi.
- File vừa giới hạn Discord vẫn được gửi trực tiếp. Khi Download Gateway được bật, file lớn hơn được chuyển sang `https://download.pearto.shop` bằng token ngẫu nhiên, không lộ đường dẫn thật và hết hạn sau 2 giờ.

### Trợ lý AI Peto

- Trò chuyện trong server bằng cách mention/reply bot, hoặc dùng `/private` để mở DM và nhắn tự nhiên không cần mention.
- Sử dụng Grok qua xAI Responses API; model mặc định là `grok-4.5` và có thể đổi bằng `XAI_MODEL`.
- Hỗ trợ đăng nhập SuperGrok bằng OAuth PKCE, tự refresh token và dùng `XAI_API_KEY` làm phương án dự phòng.
- Có thể đọc tối đa 6 ảnh từ tin hiện tại hoặc chuỗi reply; hỗ trợ JPEG, PNG, WebP và GIF.
- Hiểu tối đa 8 tin nhắn trong chuỗi reply và có thể tóm tắt 40 tin gần nhất trong đúng kênh khi được hỏi rõ.
- Câu trả lời dài được chia theo đoạn thành tối đa ba tin Discord và giữ code block. Nội dung dài hơn được gửi trọn vẹn bằng file `.txt` UTF-8 dễ mở trên điện thoại; các nút tương tác vẫn nằm dưới tin cuối.
- Có thể tìm thông tin mới trên web thông qua Tavily.
- Câu hỏi Limbus có tính thời sự như ngày phát hành, event, update, notice,
  Reflectrial hoặc banner sắp tới sẽ tự tra thêm X Search của xAI trong tài khoản
  chính thức `@LimbusCompany_B`, đọc cả ảnh/video và kiểm tra link Steam chính
  thức. Nếu X Search không khả dụng với tài khoản/model hiện tại, bot tự quay về
  Tavily; dữ kiện chung chung trong notice không được dùng để khẳng định một nội
  dung cụ thể chưa được nguồn nhắc tên.
- Với Steam News có notice dạng ảnh, bot đọc `announcement_body` để lấy toàn bộ
  ảnh nội dung theo đúng thứ tự thay vì chỉ xem thumbnail OpenGraph. Mỗi trang
  ảnh dài được vision đọc riêng rồi mới tổng hợp, tránh bỏ sót chữ hoặc bị xAI
  thu nhỏ quá mức khi gửi nhiều infographic trong cùng một request.
- Luồng tin Limbus còn đối chiếu Steam News API của app `1973530`; ngày đọc được
  từ bài X/thumbnail được dùng để nối đúng notice dù tiêu đề Steam chỉ ghi chung
  chung. Nhờ đó bot không chọn nhầm một Reflectrial cũ chỉ vì tiêu đề cũ khớp
  từ khóa hơn.
- Kết quả Vision của từng ảnh notice được lưu lâu dài trong hai bảng
  `official_news_image_cache` và `official_news_answer_cache` thuộc
  `limbus_knowledge.db`; cache không chứa ảnh gốc, chỉ chứa hash, URL và phần chữ
  đã đọc. Ảnh chỉ được đọc lại khi hash thay đổi. Câu trả lời cùng chủ đề được
  dùng lại trong 60 phút mặc định (đổi bằng `LIMBUS_NEWS_ANSWER_CACHE_MINUTES`),
  nên người hỏi sau không phải chờ lại toàn bộ lượt X Search + Vision.
  nhiều từ khóa hơn.
- Có kho kiến thức **Limbus Company Wiki** riêng theo mô hình RAG: tự đồng bộ khoảng 2.200 bài viết chính từ MediaWiki API vào SQLite FTS5, chỉ cập nhật trang có revision mới và vẫn dùng bản gần nhất khi wiki tạm lỗi.
- Câu hỏi về Identity, E.G.O., skill/passive, status, enemy, lore, Mirror Dungeon hoặc team building được ưu tiên tra `limbuscompany.wiki.gg` trước Tavily. Bot dẫn link nguồn, phân biệt dữ kiện wiki với suy luận chiến thuật và không tự bịa khi nguồn chưa đủ.
- Một số tên tắt cộng đồng được ánh xạ sang tên wiki chính thức trước khi tìm kiếm, chẳng hạn `NClair` và `RienSang`/`Rien Sang`; vì vậy alias ghép không bị hiểu nhầm thành trang NPC/enemy có tên gần giống.
- Bộ nhận diện alias còn hiểu các tên ghép như `Lord Honglu`/`Hongyuan Honglu`, `Captain Ish`, `Spicebush`, `K Honglu`, `T Don`, `BL Meursault`, `Ring Sang`, `W Heath` và cách viết liền tên Sinner (`Honglu`, `Yisang`, `Donquixote`...). Alias Identity chỉ được ưu tiên trong truy vấn kit/skill và khi khớp đủ cụm để hạn chế chọn nhầm trang lore, NPC hoặc enemy.
- Khi hỏi `full skill` hoặc `full kit` của một Identity, Peto đọc trực tiếp template wiki và trình bày từng skill/passive bằng embed riêng. Card tổng quan có HP và Defense Level theo level hiện hành, Speed ở Uptie cao nhất, cùng kháng Slash/Pierce/Blunt kèm rating và hệ số. Các card còn lại có viền màu theo Sin Affinity; badge damage/defense; emoji Sin, status, HP, Offense, Coin và Unbreakable Coin; đầy đủ Base/Coin Power cùng hiệu ứng từng Coin. Passive dài được tách card để không bị giới hạn field của Discord cắt mất.
- Có thể hỏi riêng `Skill 1/2/3`, `S1/S2/S3` hoặc `Defense` của một Identity để nhận đúng một embed tương ứng, không kèm card tổng quan, skill khác hay passive. Yêu cầu `Skill 3` chỉ lấy Skill 3 gốc; biến thể `Skill 3-2/3-3` chỉ hiện khi gọi rõ biến thể đó.
- Emoji Coin thường chỉ xuất hiện ở dòng chỉ số và tiêu đề từng Coin, không chen vào mọi cụm `Coin Power`, `[Coin Start]` hay câu mô tả damage. `Unbreakable Coin` vẫn giữ badge riêng để phân biệt cơ chế đặc biệt.
- Loại Coin được đọc riêng cho từng Coin từ template wiki: skill toàn Unbreakable Coin dùng badge đỏ, còn skill trộn Coin thường/Unbreakable hiển thị đúng số lượng và đúng badge ở từng field.
- Parser hỗ trợ cả cú pháp `complexcoin` của wiki (ví dụ `unbreakable,5`) và dùng số field `ceN` làm fallback, nên các skill đặc biệt không còn bị hiểu thành `0 Coin` hoặc chỉ hiện Coin 1.
- Nếu template chứa đồng thời passive hiện tại và bản Uptie cũ (`2passiveN`), bot gộp theo tên/Sin/requirement và giữ effect đầy đủ nhất; vì vậy full kit không tạo hai card trùng tên.
- Emoji status dùng phép khớp theo tên đầy đủ cho `Offense/Defense Level Up/Down`, tránh chèn badge chung vào câu thường như `Defense Skills`. Bộ emoji còn nhận Bind, Haste, Ammo, Shin, Tigermark/Savage Tigermark Round, The Living/The Departed, Magic Bullet, Bullet of Solitude và Butterfly.
- Các status damage/power cũng được phân biệt theo cụm đầy đủ: Slash/Pierce/Blunt Resist Down, Protection, Power Up/Down, Fragility và DMG Up/Down; Attack/Defense/Clash/Base Power; Plus/Minus Coin Boost/Drop; Charge Barrier, Paralyze, Aggro, Damage Up/Down, Power Up/Down và Fragile. Defense skill dạng Counter dùng badge Counter riêng.
- Câu hỏi về Identity/E.G.O **mới nhất** hoặc banner hiện tại được đối chiếu trực tiếp với thời gian bắt đầu–kết thúc trong `Extraction/Banner History`, thay vì xếp hạng theo từ khóa thông thường.
- Các câu hỏi đếm/liệt kê roster như Nursefathers ưu tiên dữ liệu cấu trúc từ trang tổ chức; bot nêu rõ phạm vi đếm và không kết thúc bằng lời hứa “đang tra thêm”.
- Link trích dẫn trong câu trả lời AI vẫn bấm được nhưng Discord không tự bung preview website lớn, giúp cuộc hội thoại gọn hơn.
- Lần đồng bộ đầu chạy nền nên không chặn bot khởi động. Nếu một trang chưa được lập chỉ mục, câu hỏi đầu tiên có thể tra trực tiếp MediaWiki API rồi cache lại; những lần sau dùng database cục bộ nhanh hơn. Không có slash command đồng bộ thủ công.
- Menu chuột phải **Hỏi Peto** cho phép giải thích, dịch, tóm tắt hoặc soạn phản hồi từ một tin nhắn mà không cần copy nội dung.
- Có thể đọc trực tiếp tối đa hai link công khai trong yêu cầu, với kiểm tra URL/redirect để chặn địa chỉ mạng nội bộ.
- Câu hỏi thực tế có nút **Kiểm tra nguồn** để Tavily tìm nguồn độc lập rồi Grok đối chiếu câu trả lời.
- Menu **Tạo sticker & emoji** crop ảnh, thử xóa nền nối với mép và xuất PNG 320×320 cùng 128×128.
- `/sticker` và `/emoji` nhận ảnh đính kèm; reply ảnh rồi nói `@Peto tạo sticker/emoji` cũng dùng cùng bộ xử lý cục bộ và không gọi Grok Imagine.
- Có thể gọi công cụ để phát nhạc, bỏ qua bài hoặc tìm fanart SFW từ Danbooru ngay trong hội thoại.
- Lưu lịch sử gần theo người dùng và kênh bằng SQLite; trí nhớ dài hạn cá nhân được đồng bộ theo Discord user ID giữa DM và mọi server.
- Quan hệ với Peto tiến triển riêng theo từng người. Bot có thể giữ cách xưng hô và sở thích khi chuyển server, nhưng không tiết lộ trí nhớ đó cho người khác hoặc trích nguyên văn chat riêng ở nơi công cộng.
- Giữ tối đa 15 tin nhắn gần nhất làm ngữ cảnh trong từng kênh và cập nhật bản tóm tắt cá nhân dùng chung sau mỗi 20 lượt tương tác.
- Dữ liệu hội thoại vẫn còn sau khi bot khởi động lại.
- `/andanh` bật chế độ **Ẩn danh** theo DM hoặc server: chỉ giữ ngữ cảnh tạm trong RAM, không đọc/ghi trí nhớ SQLite.
- Có lệnh để người dùng, admin server hoặc chủ bot xóa dữ liệu ở phạm vi phù hợp.
- Tự nhận diện bài tập/toán học để mở **Study Mode** với các nút Gợi ý, Giải chi tiết, Kiểm tra đáp án, Chép đề và Xuất PNG.
- Study Mode đọc lại ảnh đề khi bấm nút, chỉ người gửi đề được thao tác và tự khóa sau 15 phút.
- Các nút Study Mode chỉ xuất hiện khi tin nhắn hiện tại có ý định học tập rõ ràng như giải, tính, kiểm tra, gợi ý cách làm hoặc chép đề; từ khóa chủ đề đứng riêng không tự kích hoạt.
- Study Mode tự giải từ đúng dữ kiện người dùng cung cấp và không dùng Tavily để tìm một bài tương tự. Nếu đề nhắc đến hình/đồ thị nhưng chưa có ảnh trong tin hoặc chuỗi reply, bot yêu cầu gửi ảnh thay vì đoán đáp án.

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
- Kết nối internet tới Discord, YouTube/Spotify/SoundCloud, LRCLIB, Radio Browser, Danbooru, xAI, Tavily và `limbuscompany.wiki.gg`.

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
# LIMBUS_OFFICIAL_X_HANDLES=LimbusCompany_B

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
   - Attach Files
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
| `/playnext` | `query` | Thêm bài vào đầu hàng đợi. |
| `/remove`, `/move` | vị trí | Xóa hoặc di chuyển bài trong queue. |
| `/shuffle`, `/clear` | — | Xáo trộn hoặc dọn queue. |
| `/stats`, `/wrapped` | tùy chọn | Thống kê và tổng kết nghe nhạc. |
| `/favorite` | — | Thêm hoặc xóa bài đang phát khỏi danh sách yêu thích cá nhân. |
| `/favorites` | — | Xem tối đa 20 bài yêu thích gần nhất của bạn trong server. |
| `/recent` | — | Xem 15 bài được phát gần nhất trong server. |
| `/playlist create` | `name` | Tạo playlist cá nhân. |
| `/playlist list` | — | Liệt kê playlist và số bài của bạn. |
| `/playlist add` | `name`, `query` tùy chọn | Thêm bài đang phát hoặc một link/tên bài vào playlist. |
| `/playlist show` | `name` | Xem các bài trong playlist. |
| `/playlist play` | `name`, `shuffle` | Thêm playlist vào queue, tùy chọn xáo thông minh. |
| `/playlist savequeue` | `name` | Lưu toàn bộ queue vào playlist. |
| `/playlist share`, `/playlist clone` | tên/mã | Chia sẻ và sao chép playlist. |
| `/playlist import` | `name`, `url` | Nhập playlist YouTube (tối đa 100 bài). |
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
| `/download` | `link`, `format` | YouTube: chọn MP3 chất lượng cao hoặc MP4 theo chất lượng có sẵn; TikTok video MP4 không watermark, TikTok photo hoặc X/Twitter MP4. |
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
| `/resetmemoryall` | Admin server | Xóa lịch sử chat trong server hiện tại; không xóa trí nhớ cá nhân dùng chung. |
| `/resetmemoryglobal` | Chủ bot | Xóa toàn bộ lịch sử, bộ đếm và tóm tắt của mọi người dùng. |

Ẩn danh chỉ ngăn bot lưu nội dung vào database cục bộ; yêu cầu vẫn phải được gửi đến Grok/xAI để tạo câu trả lời. Khi tắt Ẩn danh hoặc bot khởi động lại, ngữ cảnh tạm sẽ bị xóa. `/resetmemory` vẫn là lệnh duy nhất để người dùng xóa toàn bộ trí nhớ đã lưu của chính họ; dự án không tạo thêm lệnh `/forgetme` trùng chức năng.

Trí nhớ dài hạn gắn với Discord `user_id`, không gắn với server: Peto có thể dùng cùng bản ghi nhớ cá nhân khi chính người đó chuyển giữa DM và các server. Những câu chủ động như **“hãy nhớ…”**, **“ghi nhớ…”** hoặc **“chốt từ giờ…”** được cập nhật ngay; hội thoại thông thường được gom định kỳ từ mọi nơi người đó đã nói chuyện. Khi nâng cấp, bot còn nhập lại các câu chốt còn tồn tại trong lịch sử cũ.

Lịch sử gốc trong SQLite không còn tự xóa sau một số lượt. Chat thường vẫn chỉ gửi một cửa sổ gần cho Grok để giữ tốc độ; khi người dùng hỏi **“Peto còn nhớ…?”**, **“lần trước chúng ta đã chốt gì?”** hoặc câu tương tự, bot mới tìm cục bộ những đoạn liên quan trong toàn bộ kho của đúng `user_id` rồi bổ sung vào request hiện tại. Việc tìm sâu không gọi thêm dịch vụ AI/embedding. Lịch sử hội thoại gần vẫn tách theo kênh để tránh mang nguyên cuộc trò chuyện riêng sang nơi công cộng, và nội dung trong chế độ Ẩn danh không tham gia đồng bộ.

### Study Mode

Mention hoặc reply Peto kèm đề bài, ví dụ `@Peto giải bài này`, có thể đính kèm ảnh. Khi nhận diện đây là bài tập, Peto gắn bảng nút dưới câu trả lời:

- **Gợi ý**: đưa hướng đi tăng dần nhưng không tiết lộ đáp án cuối.
- **Giải chi tiết**: giải lại từng bước và tự kiểm tra kết quả.
- **Kiểm tra đáp án**: mở hộp nhập bài làm, chỉ ra bước sai đầu tiên và cách sửa.
- **Chép đề**: OCR toàn bộ ảnh theo thứ tự reply, tách dữ kiện/yêu cầu và đánh dấu phần không đọc rõ mà không tự bịa.
- **Xuất PNG**: render lời giải gần nhất thành ảnh nền tối để lưu hoặc chia sẻ.

Chỉ người gửi đề sử dụng được bảng nút. Có thể gửi đề nhiều trang bằng chuỗi reply, tối đa 6 ảnh. Phiên tồn tại trong RAM 15 phút và sẽ hết hiệu lực nếu bot restart. Người dùng vẫn có thể reply Peto để hỏi tiếp về một bước trong lời giải như hội thoại bình thường.

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

Universal Media Downloader hoạt động độc lập với Music Panel. Người dùng gọi `/download link:<URL>` để bot tạo custom embed và button **Only Visible to you**; việc tải và chuyển đổi chỉ chạy sau khi bấm nút, file kết quả cũng được gửi riêng tư. Các link được gửi như tin nhắn bình thường không kích hoạt bot.

`features/download_gateway.py` mở một file gateway chỉ tại `127.0.0.1:8765`; Cloudflare Tunnel ánh xạ `download.pearto.shop` vào cổng này. Gateway dùng bearer token ngẫu nhiên, `Cache-Control: no-store`, giới hạn lượt/IP, file tối đa 512 MiB, kho tạm mặc định 2 GiB và tự hết hạn sau 2 giờ. Không mở port router và không đổi host thành `0.0.0.0` khi dùng Tunnel.

## Cấu trúc dự án

```text
.
├── bot.py                  # Điểm khởi động, nạp extension và đồng bộ lệnh
├── commands/               # Các slash command
├── features/
│   ├── ai_chat.py          # Grok, vision, Tavily và tool calling
│   ├── limbus_kit_view.py  # Embed skill/passive theo Sin Affinity
│   ├── limbus_wiki.py      # RAG Limbus Wiki tự đồng bộ vào SQLite FTS5
│   ├── media_downloader.py # Lệnh /download video/ảnh cho YouTube/TikTok/X
│   └── download_gateway.py # Link tải file lớn qua Cloudflare Tunnel
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
- Phiên nút Study Mode chỉ giữ trong RAM 15 phút; chế độ Ẩn danh không đọc hoặc cập nhật trí nhớ cá nhân dùng chung.
- Spotify hiện chỉ hỗ trợ link track; bot lấy metadata Spotify rồi tìm nguồn phát tương ứng trên YouTube.
- `audio_cache/` chưa có cơ chế giới hạn dung lượng hoặc tự dọn file cũ.
- Lịch sử AI thường được lưu trong `bot_memory.db` và tồn tại qua restart; nội dung Ẩn danh không được ghi vào file này.
- Các API bên ngoài có thể giới hạn tần suất, đổi định dạng hoặc tạm thời không phản hồi.
- Media Downloader phụ thuộc vào extractor của `yt-dlp`; nền tảng có thể thay đổi cách phân phối media khiến một link tạm thời không tải được.

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

Cài lại dependency để nhận đúng bản `yt-dlp` đã khóa và làm mới `cookies.txt`:

```bash
pip install -r requirements.txt
```

### TikTok báo `Unable to extract universal data for rehydration`

Đây là lỗi tương thích/challenge khi TikTok thay đổi dữ liệu trang, không đồng nghĩa video riêng tư. Bot sẽ tự thử TikWM sau khi `yt-dlp` thất bại. Nếu cả hai nguồn đều lỗi với nhiều link công khai, chạy `pip install --pre --upgrade yt-dlp`, cập nhật phiên bản được khóa trong `requirements.txt` theo bản vừa cài, rồi khởi động lại bot. Nếu chỉ một link lỗi, nội dung đó có thể đã bị gỡ, giới hạn khu vực hoặc yêu cầu đăng nhập; hãy thử mở link trong cửa sổ ẩn danh trước.

Nếu TikWM riêng lẻ ngừng hoạt động, kiểm tra `https://www.tikwm.com/` trên cùng mạng. Khi website cũng lỗi thì chờ dịch vụ phục hồi; khi website hoạt động nhưng bot lỗi, xem log `features.media_downloader` để phân biệt lỗi HTTP, dữ liệu API không hợp lệ và CDN hết hạn. TikWM là fallback bên thứ ba nên không nên đưa cookie hoặc thông tin đăng nhập TikTok vào request của nó.

URL ảnh TikTok do CDN cấp có thể hết hạn. Nếu panel cũ không tải được ảnh, hãy chạy lại `/download` để bot lấy URL mới rồi bấm **Tải ảnh** trong vòng 10 phút.

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
