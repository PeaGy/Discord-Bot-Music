# Manual diagnostics

Các script trong thư mục này chỉ dùng để kiểm tra thủ công và có thể gọi mạng.
Chúng không được import khi bot chạy và không phải bộ test tự động.

## Kiểm tra YouTube qua IPv4 trực tiếp

Từ thư mục project, sau khi kích hoạt môi trường Python của bot:

```bash
python scripts/manual/check_youtube_ipv4.py
```

Script tự đọc `.env` của project, giữ cấu hình Node/BgUtils và client của Peto,
nhưng ép kết nối IPv4 trực tiếp (`proxy=""`, bỏ cả proxy môi trường) và không
gửi cookie tài khoản. Thử tối đa hai lượt lấy thông tin bài AIZO đang dùng để
chẩn đoán; không tải audio, không đăng nhập Discord và không sửa `.env`.

`LAY DUOC THONG TIN AUDIO` nghĩa là tìm được định dạng âm thanh, chưa chứng minh
CDN audio hoặc phát Discord hoạt động. `THU IPv4 THAT BAI` kèm lỗi là kết quả
cần gửi để kiểm tra tiếp.

## Kiểm tra đo và chuẩn hóa âm lượng bằng FFmpeg thật

```bash
python scripts/manual/check_loudness_normalization.py
```

Script tạo âm thanh giả 8 giây trong thư mục tạm, chạy đúng bộ đọc kết quả đo
và bộ mã hóa Opus của Peto rồi đo lại file kết quả. Không gọi mạng, không đọc
`.env`, không đăng nhập Discord, không dùng hay xóa cache nhạc sẵn có. File thử
tự được dọn sau khi kết thúc; kết quả này kiểm tra kỹ thuật, không phải phép đo
tốc độ proxy hay thời gian xử lý một bài nhạc thật trên VPS.

## Đo hiệu năng từng bước của pipeline âm thanh

```bash
python scripts/manual/benchmark_audio_pipeline.py
```

Chạy lúc bot ít người dùng: đây là phép thử có dùng CPU, có thể mất vài phút.
Không cần restart Peto. Script chỉ dùng Python chuẩn cùng `ffmpeg` và `ffprobe`
trong PATH; không nhập module bot/cache, không đọc `.env`, không gọi YouTube/mạng,
không thay cấu hình hoặc cài phiên bản FFmpeg khác.

- Tạo một mẫu **âm thanh giả stereo 48 kHz, 120 giây**, nhiều tần số và biên độ
  biến thiên, mã hóa thành Opus một lần. Tất cả lượt đo dùng lại cùng file này.
- Làm nóng một lượt không tính vào kết quả, sau đó lặp 3 lần và luân phiên thứ tự
  `default`, `filter1`, `filter2`. Hai cấu hình thử chỉ đặt `-filter_threads 1/2`;
  `default` giữ số luồng tự động của FFmpeg, không có nghĩa là một luồng.
- Dùng chung bộ tạo lệnh với Peto trong `music/loudness.py`, giữ mục tiêu
  `I=-16:TP=-1.5:LRA=11`, Opus 160 kbps, frame 20 ms, FEC và packet-loss 10.
- Báo riêng giải mã, giải mã + mã hóa **không loudnorm** (đối chứng), đo âm lượng,
  và chuẩn hóa + mã hóa. Các bước đo/mã hóa đã bao gồm giải mã, không cộng tất cả
  dòng hoặc coi phép trừ trung vị là thời gian CPU chính xác của riêng bộ lọc.
- `Wall` là thời gian chờ thực tế gồm khởi động tiến trình. `CPU user`, `CPU sys`,
  `FF real` là số FFmpeg báo qua `-benchmark`, `N/A` nếu không có; không phải mức
  tải CPU toàn VPS. Chỉ tổng hai bước đo + chuẩn hóa/mã hóa được dùng so sánh,
  tính theo từng cặp cùng lượt rồi lấy trung vị, kèm min/max.
- Mỗi đầu ra chuẩn hóa được giải mã/đo lại và kiểm tra Opus/stereo/48 kHz,
  thời lượng (sai lệch tối đa 0,15 giây), âm lượng (±0,5 LUFS), true peak
  (cho phép lệch tối đa +0,3 dB sau mã hóa mất dữ liệu). Việc kiểm tra này không
  nằm trong thời gian đo. Chế độ linear/dynamic và số đo giữa các cấu hình cũng
  phải nhất quán; lỗi trả exit khác 0, không báo PASS hoặc âm thầm single-pass.

Gửi phần từ `ffmpeg:` đến bảng kết quả, hoặc dòng `LOI` nếu thử thất bại.
File thử chỉ nằm trong thư mục tạm riêng và tự được dọn khi hoàn tất/lỗi/Ctrl+C
(tắt máy hoặc cưỡng bức kill tiến trình có thể để lại file tạm). Không xóa cache
nhạc để làm phép thử. Không tự động chọn cấu hình thắng hoặc áp dụng vào bot.

Tùy chọn `--seconds 10 --repeats 1` dùng kiểm tra nhanh, **không đủ để kết luận
hiệu năng**. Giới hạn là 10–300 giây âm thanh và 1–5 lượt. Có thể chỉ định
`--ffmpeg`/`--ffprobe` bằng đường dẫn nếu cần so sánh các bản đã cài riêng; không
cần thay bản hệ thống. Mỗi tiến trình có timeout tối đa 180 giây.

Đây là mẫu giả, không chứng minh chất lượng cảm nhận hay tốc độ trên mọi bài
nhạc. Không suy ra thời gian tải mạng từ kết quả này. Nếu min/max chồng lấp hoặc
chênh lệch nhỏ, cần đo lại lúc VPS rảnh trước khi quyết định thay đổi.
