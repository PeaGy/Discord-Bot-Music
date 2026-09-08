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
