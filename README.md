# Radiology Atlas

Home → Anatomy → Viewer, Root/Admin/Standard. Ubuntu **ARM64 và AMD64**.
Repo: https://github.com/bscongluanbui/radiology-atlas

## Cài trên VPS hiện tại

Hướng dẫn từng bước: **[docker/INSTALL.md](docker/INSTALL.md)**.

- Source đã clone tại `/home/ubuntu/radiology-atlas/`.
- Domain `bcanatomy.site`; dùng lại Cloudflare Tunnel `arm`, container `thirsty_agnesi`.
- Image: `ghcr.io/bscongluanbui/radiology-atlas:latest` (đặt package Public một lần sau lần publish đầu).
- Production Compose chỉ pull image; VPS không cần build hay Buildx.
- Data ngoài image, giữ nguyên cây `all_modules`; volume tài khoản giữ nguyên khi tạo lại container.

## Cập nhật phần mềm sau này

Máy phát triển: kiểm thử nguồn, commit/push vào `main`. GitHub Actions tự kiểm thử,
build và chạy thử **cả linux/amd64 và linux/arm64**, rồi publish `latest` + `sha-<commit>`.
Đợi workflow **Publish viewer image** thành công, sau đó trên VPS:

```bash
cd /home/ubuntu/radiology-atlas/docker
docker compose pull
docker compose up -d --force-recreate --pull never --wait --wait-timeout 180
```

Hai lệnh cập nhật image và container, không thay data hoặc volume tài khoản.
`--pull never` ở lệnh thứ hai dùng đúng image vừa pull; không tải lần nữa.
Nếu pull báo lỗi, dừng trước bước tạo lại. Có thể nối hai lệnh bằng `&&`.
Viewer có thể gián đoạn ngắn khi container được thay.

`docker compose pull` **không lấy file Compose/.env/scripts từ Git**. Chỉ khi bản phát hành
thay cấu hình hạ tầng, chạy thêm `git pull --ff-only` và bổ sung biến `.env` theo release note.
`.env` được Git bỏ qua nên các giá trị cũ không tự đổi. Mỗi thay đổi viewer dùng chung nguồn
`offline_anatomy_viewer/` với bản local, không duy trì bản viewer thứ hai trong Docker.

Tùy chọn cập nhật có tự phục hồi image trước nếu healthcheck thất bại:
`bash update.sh`. Sao lưu tài khoản trước bản thay schema: `bash backup-state.sh`.

## Phần mềm / dữ liệu

Repo và image gồm viewer, website, API, quản lý tài khoản và thumbnails module.
Không đóng gói `.env`, token tunnel, database user, data anatomy, cache hoặc bản backup.
CI dùng bộ dữ liệu mô phỏng riêng, không lấy bộ capture lâm sàng.
Ánh xạ cấu trúc giải phẫu và chức năng viewer được giữ nguyên khi thay cơ chế triển khai.

## Phát triển local

```bash
python -m pip install -r docker/requirements.txt PyYAML==6.0.3
python -m unittest discover -s docker/tests -p test_distribution.py -v
node docker/tests/test_unified_preload.cjs
# Build local chỉ khi cần kiểm thử Docker trên máy phát triển:
docker compose -f docker/compose.yaml -f docker/compose.build.yaml --env-file docker/.env up -d --build viewer
```

Viewer desktop vẫn dùng launcher cũ trong `offline_anatomy_viewer/`.
Đóng gói source sạch (có cả workflow): `python docker/release.py`.

Tài liệu: [Docker](docker/README.md) · [Tài khoản](docker/ACCOUNTS.md) ·
[Cache/preload](docker/PERFORMANCE.md) · [Website](docker/WEBSITE.md) ·
[Thành phần bên thứ ba](THIRD_PARTY_NOTICES.md).
