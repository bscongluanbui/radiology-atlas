# Docker: Radiology Atlas

**Bắt đầu tại [INSTALL.md](INSTALL.md)** — hướng dẫn đúng VPS `/home/ubuntu/radiology-atlas`,
Cloudflare Tunnel hiện có và cập nhật bằng `docker compose pull`.

```text
https://bcanatomy.site → Cloudflare Tunnel (cloudflared)
                              ↓ radiology-atlas_backend
                        viewer:8080 (Flask/Gunicorn)
                          ├─ Home / Anatomy / Viewer
                          ├─ /admin: Root quản lý user/quyền
                          ├─ /data: data anatomy read-only
                          └─ /state: volume SQLite persistent
```

## Image và cập nhật

Production `compose.yaml` dùng GHCR, không có `build:`.
Workflow `.github/workflows/publish-viewer.yml` kiểm thử và publish cả ARM64 + AMD64;
`latest` dành cho update, `sha-<commit>` dành cho quay lại phiên bản xác định.
Public package cho phép VPS pull không cần đăng nhập registry.

```bash
cd /home/ubuntu/radiology-atlas/docker
docker compose pull && docker compose up -d --force-recreate --pull never --wait --wait-timeout 180
```

`bash update.sh` là tùy chọn thêm tự rollback image khi unhealthy. `backup-state.sh`
chụp volume tài khoản trước thay schema. Cấu hình Git ngoài image chỉ cập nhật khi chạy `git pull`.

## Giữ nguyên viewer / local

Container import cùng `offline_anatomy_viewer/server.py`: không viết lại ánh xạ giải phẫu.
Labels/leader highlight, Detail, Scroll/Pan/Zoom, MPR, Anatomical parts, filter.layer,
Overlays, icons, ngôn ngữ, cache và preload giữ nguyên.

Build thử trên máy phát triển:

```bash
docker compose -f compose.yaml -f compose.build.yaml up -d --build viewer
```

Hoặc `bash build-local.sh` chỉ build image local; không dùng cho production update.
`build-multiarch.sh` là công cụ publish thủ công tùy chọn, không cần chạy trên VPS.

## Tùy chọn trỏ trực tiếp VPS

Mặc định không bật Caddy. Chỉ với DNS trỏ IP VPS, cổng 80/443 đã mở và không dùng Tunnel:
`bash deploy.sh direct`. Caddy ở profile `direct` cấp TLS và reverse proxy đến viewer.
Đây là nhánh triển khai khác; VPS hiện tại dùng nhánh Tunnel trong INSTALL.md.

## Tài khoản và cache

- Root quản lý user/quyền; Admin xem mọi module; Standard chỉ vùng/module được cấp.
- Data là bind mount read-only; tài khoản lưu ở `radiology-atlas_atlas-state`.
- Cookie Secure/HttpOnly, CSRF và kiểm tra quyền áp dụng cho cả API lẫn ảnh.
- Cache bounded/TTL trong RAM; Docker log tối đa 3 file × 10 MB mỗi service.
- Systemd cleanup có phạm vi Atlas, không xóa volumes/data hay image app khác.

Chi tiết: [ACCOUNTS.md](ACCOUNTS.md), [PERFORMANCE.md](PERFORMANCE.md), [WEBSITE.md](WEBSITE.md).
