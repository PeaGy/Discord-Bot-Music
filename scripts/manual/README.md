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
