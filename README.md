# NAS Downloader

Ứng dụng Python CLI kết nối NAS qua **SFTP** hoặc **FTPS**, tự động theo dõi thư mục chỉ định (ví dụ `/data`), lọc các thư mục con và file theo pattern đơn giản (Wildcard/Glob hoặc chứa cụm từ), tải về, tự động giải nén các file nén (`.zip`, `.7z`, `.rar`, `.tar`...) hoặc giữ nguyên các file tài liệu (`.xlsx`, `.pdf`, `.csv`...), xóa file nén sau khi giải nén và tránh tải trùng lặp.

## Tài liệu kỹ thuật chi tiết

- [INTEGRATION_GUIDE.md](INTEGRATION_GUIDE.md): Tài liệu API tích hợp cho module khác (Song ngữ Anh / Việt, Data Contract JSON, 3 Pattern tích hợp).
- [ARCHITECTURE.md](ARCHITECTURE.md): Tài liệu kiến trúc hệ thống, sơ đồ luồng dữ liệu (Mermaid), cơ chế an toàn, hệ thống ghi log chi tiết và hướng dẫn xử lý lỗi (dành cho developer và AI).
- [LINUX_DEPLOYMENT.md](LINUX_DEPLOYMENT.md): Hướng dẫn triển khai trên Linux (Đầy đủ setup cho **Ubuntu 14.04** & **Ubuntu 18.04**, cách test kết nối, cách dò đường dẫn NAS và chạy service nền).

## Tính năng chính

- **Hỗ trợ cả 2 giao thức**: `sftp` (SSH, mặc định port 22) và `ftps` (FTP over TLS explicit port 21 hoặc implicit port 990).
- **Pattern đơn giản**:
  - Không có `*`/`?` (ví dụ `"update"`): khớp mọi tên có chứa cụm từ đó.
  - Có `*` hoặc `?` (ví dụ `"[*]20*_update"`, `"*.zip"`): khớp wildcard toàn tên (dấu `[` và `]` được xử lý an toàn).
- **Hỗ trợ đa định dạng**:
  - **File nén** (`.zip`, `.7z`, `.rar`, `.tar`, `.tgz`): Giải nén an toàn vào thư mục đích và xóa file nén gốc ở local (`keep_archive_after_extract: false`).
  - **File tài liệu** (`.xlsx`, `.xls`, `.pdf`, `.csv`, `.docx`, `.txt`...): Giữ nguyên vẹn, không giải nén.
- **Xử lý ghi đè & chống Concurrency (file 500–600MB đang upload lên NAS)**:
  - Theo dõi theo `(remote_path, size, mtime)`. Nếu file trên NAS bị ghi đè bản mới, tự động tải lại và cập nhật bản mới.
  - **Stability Check**: Chờ `stability_wait_seconds` (mặc định 5s) rồi kiểm tra lại `size` và `mtime`. Nếu file vẫn đang thay đổi kích thước hoặc có đuôi tạm (`.part`, `.tmp`, `.filepart`), bỏ qua ở lượt này để tránh tải file dở dang.
- **API tích hợp cho module khác**:
  - Python Callback API (`on_file_ready`, `on_batch_complete`) và `run_once()` trả về `SyncResult`.
  - Tùy chọn ghi `manifest_file` JSON và gửi `webhook_url` HTTP POST sau mỗi lượt có dữ liệu mới.

## Cài đặt

```bash
pip install -r requirements.txt
```

## Cấu hình

Sao chép [config.example.json](config.example.json) thành `config.json` và điền thông tin NAS, pattern, thư mục local.

## Kiểm tra kết nối & Dò đường dẫn NAS

Trước khi chạy đồng bộ chính thức, hãy dùng 2 công cụ có sẵn để kiểm tra:

1. **Test kết nối & xác thực (`test_connection.py`)**:
   ```bash
   python test_connection.py -c config.json
   ```
   Kiểm tra đăng nhập SFTP/FTPS và liệt kê nội dung bên trong `remote_dir`.

2. **Dò đường dẫn thư mục trên NAS (`explore_nas.py`)**:
   ```bash
   # Quét tự động thư mục gốc và các đường dẫn phổ biến
   python explore_nas.py -c config.json

   # Hoặc xem chi tiết 1 đường dẫn cụ thể
   python explore_nas.py -c config.json -p /data
   ```

## Chạy ứng dụng chính (CLI)

```bash
# Chạy kiểm tra 1 lần
python cli.py --config config.json --once

# Chạy định kỳ theo interval_seconds trong config
python cli.py --config config.json

# Chạy kèm log debug chi tiết
python cli.py --config config.json --once --verbose
```

## Tích hợp với module Python khác

Xem hướng dẫn tích hợp chi tiết tại [INTEGRATION_GUIDE.md](INTEGRATION_GUIDE.md) và ví dụ trong [example_integration.py](example_integration.py):

```python
from nas_spec_sync import NasSpecSync

syncer = NasSpecSync.from_config_file("config.json")
syncer.on_file_ready(lambda item: print("Ready:", item.extracted_dir))
result = syncer.run_once()
```
