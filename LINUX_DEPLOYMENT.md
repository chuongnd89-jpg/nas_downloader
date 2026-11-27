# NAS Downloader — Linux Deployment Guide (Ubuntu 14.04 & Ubuntu 18.04)

Tài liệu này hướng dẫn chi tiết cách cài đặt, kiểm tra kết nối (`test_connection.py`), dò thư mục (`explore_nas.py`) và chạy **NAS Downloader** trên **Ubuntu 14.04 LTS** và **Ubuntu 18.04 LTS**.

---

## 1. So sánh nhanh Ubuntu 14.04 và Ubuntu 18.04

| Tiêu chí | Ubuntu 14.04 LTS (Trusty Tahr) | Ubuntu 18.04 LTS (Bionic Beaver) |
| :--- | :--- | :--- |
| **Python mặc định** | Python 2.7 & Python 3.4 | Python 3.6.9 |
| **OpenSSL hệ thống** | OpenSSL 1.0.1f (quá cũ cho `cryptography`/`paramiko` mới) | OpenSSL 1.1.1 (tương thích tốt) |
| **Cách cài Python khuyên dùng** | **Miniconda (Python 3.9)** để đóng gói sẵn OpenSSL mới | **Python 3.8** qua PPA `deadsnakes` (hoặc Python 3.6 + `dataclasses`) |
| **Công cụ giải nén CLI** | `p7zip-full`, `unrar` | `p7zip-full`, `unrar` |
| **Chạy dịch vụ nền** | Upstart (`/etc/init/*.conf`) | Systemd (`/etc/systemd/system/*.service`) |

---

## 2. Hướng dẫn Setup cho UBUNTU 18.04 LTS

### Cách A: Dùng Python 3.8 (Khuyên dùng, tương thích 100%)
```bash
# 1. Cài đặt Python 3.8 và công cụ giải nén CLI (7z, rar)
sudo apt-get update
sudo apt-get install -y software-properties-common p7zip-full unrar
sudo add-apt-repository -y ppa:deadsnakes/ppa
sudo apt-get update
sudo apt-get install -y python3.8 python3.8-venv python3.8-dev

# 2. Tạo môi trường ảo và cài dependencies
cd "/path/to/Nas_download spec"
python3.8 -m venv venv
source venv/bin/activate
pip install --upgrade pip
pip install -r requirements.txt
```

### Cách B: Dùng Python 3.6 mặc định của Ubuntu 18.04
```bash
sudo apt-get update
sudo apt-get install -y python3-pip python3-venv p7zip-full unrar

cd "/path/to/Nas_download spec"
python3 -m venv venv
source venv/bin/activate
pip install dataclasses
pip install -r requirements.txt
```

---

## 3. Hướng dẫn Setup cho UBUNTU 14.04 LTS

> **Lưu ý cho Ubuntu 14.04**: Ubuntu 14.04 đã End-of-Life. Kho `apt` mặc định có thể báo lỗi 404 nếu chưa trỏ sang `old-releases.ubuntu.com`, và OpenSSL 1.0.1f quá cũ để build `paramiko`/`cryptography`. **Hãy dùng Miniconda** để cô lập hoàn toàn môi trường Python 3.9 + OpenSSL mới.

```bash
# 1. (Nếu apt-get update bị lỗi 404) Chuyển repo sang old-releases
sudo sed -i -re 's/([a-z]{2}\.)?archive.ubuntu.com|security.ubuntu.com/old-releases.ubuntu.com/g' /etc/apt/sources.list
sudo apt-get update

# 2. Cài công cụ giải nén hệ thống & wget
sudo apt-get install -y p7zip-full unrar wget

# 3. Cài Miniconda (tự đóng gói Python 3.9 và OpenSSL mới)
wget https://repo.anaconda.com/miniconda/Miniconda3-latest-Linux-x86_64.sh
bash Miniconda3-latest-Linux-x86_64.sh -b -p $HOME/miniconda3
source $HOME/miniconda3/bin/activate

# 4. Tạo môi trường Python 3.9 và cài dependencies
conda create -n nas_downloader python=3.9 -y
conda activate nas_downloader

cd "/path/to/Nas_download spec"
pip install -r requirements.txt
```

---

## 4. Cấu hình & Cách Test trên Linux (Áp dụng cho cả Ubuntu 14 & 18)

### Bước 1: Tạo file `config.json`
```bash
cp config.example.json config.json
nano config.json
```
Điền `host`, `port`, `username`, `password`, `remote_dir`, `folder_patterns`, `file_patterns`.

### Bước 2: Cách Test Kết Nối (`test_connection.py`)
Kiểm tra đăng nhập SFTP/FTPS tới NAS và liệt kê nội dung trong `remote_dir`:
```bash
python test_connection.py -c config.json
```
- **Thành công**: In ra `>>> SUCCESS: Connected and authenticated successfully` kèm danh sách file/folder trong `remote_dir`.
- **Thất bại**:
  - Sai user/mật khẩu -> báo `Authentication failed`.
  - Sai đường dẫn `remote_dir` -> báo `[Errno 2] No such file` -> chuyển sang Bước 3 để dò đường dẫn đúng.

### Bước 3: Cách Test Dò Thư Mục NAS (`explore_nas.py`)
Khi chưa rõ NAS chia sẻ đường dẫn gốc thế nào qua SFTP/FTPS (ví dụ `/data` hay `/volume1/data`):
```bash
# Dò tự động các đường dẫn phổ biến và thư mục mặc định
python explore_nas.py -c config.json

# Kiểm tra chi tiết bên trong 1 đường dẫn cụ thể
python explore_nas.py -c config.json -p /data
```
Sau khi thấy đường dẫn có dấu `[OK] EXISTS: '...'`, copy đường dẫn đó vào trường `remote_dir` trong `config.json` rồi chạy lại `test_connection.py`.

### Bước 4: Chạy thử chương trình chính
```bash
# Chạy 1 lượt kiểm tra tải & giải nén
python cli.py --config config.json --once

# Hoặc bật log debug chi tiết
python cli.py --config config.json --once --verbose
```
Kiểm tra log chi tiết tại `./logs/nas_sync.log` và kết quả tại `./downloads/` hoặc `./latest_sync_manifest.json`.

---

## 5. Cấu hình chạy nền tự động (Service)

### Trên Ubuntu 18.04 (Systemd)
Tạo file `/etc/systemd/system/nas-downloader.service`:
```ini
[Unit]
Description=NAS Downloader Service
After=network.target

[Service]
Type=simple
User=ubuntu
WorkingDirectory=/path/to/Nas_download spec
ExecStart=/path/to/Nas_download spec/venv/bin/python cli.py --config config.json
Restart=always
RestartSec=10

[Install]
WantedBy=multi-user.target
```
Kích hoạt:
```bash
sudo systemctl daemon-reload
sudo systemctl enable --now nas-downloader.service
sudo systemctl status nas-downloader.service
```

### Trên Ubuntu 14.04 (Upstart)
Tạo file `/etc/init/nas-downloader.conf`:
```ini
description "NAS Downloader Service"
start on runlevel [2345]
stop on runlevel [016]
respawn

setuid ubuntu
chdir /path/to/Nas_download spec
exec /home/ubuntu/miniconda3/envs/nas_downloader/bin/python cli.py --config config.json
```
Kích hoạt:
```bash
sudo start nas-downloader
sudo status nas-downloader
```
