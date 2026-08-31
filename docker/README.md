# Radiology Atlas — Ubuntu Docker, ARM64 và AMD64

Nếu cài từ GitHub, bắt đầu bằng [README của repository](../README.md): upload nguồn, `git clone`, cấu hình data ngoài Git và `git pull --ff-only` khi cập nhật.

## Kiến trúc

```text
Người dùng → https://TEN-DDNS → Caddy (80/443, TLS tự động)
                               ↓ mạng Docker riêng
                         Gunicorn + Flask :8080
                          ├─ viewer local hiện tại (một nguồn mã)
                          ├─ /admin: user / mật khẩu / phân quyền module
                          ├─ /state: SQLite tài khoản, session, audit
                          └─ /data: all_modules gắn CHỈ ĐỌC
```

Website hiện có Home công khai, popup Login và thư viện Anatomy theo quyền; viewer nằm ở `/viewer`.
Xem `WEBSITE.md` cho luồng Home → Anatomy → mở module. Website dùng chung container và domain với viewer.
Tên miền thường hoặc DDNS đều dùng được qua biến `DDNS_HOST` hiện tại; không cần đổi tên biến.
Docker chạy toàn bộ viewer: catalogue, series/slice, labels/leader highlight bật-tắt, Detail,
Scroll/Pan/Zoom, MPR, Anatomical parts, filter.layer/Overlays, ảnh module, preload và ngôn ngữ.
Gateway import trực tiếp `offline_anatomy_viewer/server.py`; không viết lại ánh xạ giải phẫu.
Các file capture và dữ liệu giữ nguyên. Bản local vẫn mở bằng launcher cũ, chỉ bind loopback;
đăng nhập/phân quyền áp dụng cho bản server, không biến launcher local thành server public.

## 1. Chuẩn bị Ubuntu

- Ubuntu 64-bit **amd64 (x86-64)** hoặc **arm64 (aarch64)**; x86 32-bit không phải nền tảng đích.
- Docker Engine, Compose plugin và Buildx theo [hướng dẫn Ubuntu chính thức](https://docs.docker.com/engine/install/ubuntu/).
- Nên có ít nhất 2 GB RAM và đủ đĩa cho data + image Docker + một image rollback + build cache.
- Đồng hồ server đồng bộ; DDNS A/AAAA trỏ đúng IP public. Router chuyển tiếp TCP 80/443 tới server
  (UDP 443 tùy chọn cho HTTP/3). Không public 8080 hoặc 8765.
- DDNS updater cấu hình ở router hoặc dịch vụ DDNS hiện có. Caddy tự cấp/gia hạn HTTPS khi DNS
  và các cổng xác thực hoạt động; xem [Automatic HTTPS](https://caddyserver.com/docs/automatic-https).
- Nếu đường truyền nằm sau CGNAT và không có đường inbound IPv6 phù hợp, DDNS đơn thuần chưa tạo
  kết nối từ ngoài vào. Cần IP public từ nhà mạng hoặc VPN/tunnel riêng trước bước truy cập từ xa.

Đặt mã nguồn vào `/opt/radiology-atlas`, giữ data ở nơi đang dùng, ví dụ:

```text
/opt/radiology-atlas/
  offline_anatomy_viewer/
  anatomy_identity.py
  overlay_capture.py
  overlay_capture.js
  overlay_runtime.js
  docker/
/srv/radiology/imaios_data/all_modules/
  module_catalogue.json
  modules/
    BRAIN/mri-brain/...
    HEAD_AND_NECK/...
    THORAX/...
```

Data không nằm trong Docker image. Container UID/GID `10001:10001` cần quyền đọc file và đi qua
các thư mục data; cấp quyền đọc bằng group/ACL nếu cần, không cần quyền ghi. Giữ đường dẫn hiện tại
bằng cách đổi `DATA_ROOT`, không đổi tên hay sắp xếp lại cây data.

## 2. Cấu hình và khởi chạy

```bash
cd /opt/radiology-atlas/docker
cp .env.example .env
chmod 600 .env
nano .env
# Sửa DDNS_HOST và DATA_ROOT; không dùng tên miền ví dụ.
bash deploy.sh
docker compose run --rm viewer python docker/manage.py create-root --username root
```

CLI hỏi mật khẩu hai lần, tối thiểu 12 ký tự. Không có mật khẩu mặc định, không đặt mật khẩu
trong `.env` hoặc command line. Mở `https://TEN-DDNS`, đăng nhập; vào `/admin` hoặc
**Tài khoản → Quản trị**. Tài khoản/quyền nằm trong volume `radiology-atlas_atlas-state`.
Container đầu tiên tự tạo schema nhưng không tạo tài khoản; tài khoản mẫu chỉ tồn tại trong test.

Nếu source ở đường dẫn khác `/opt/radiology-atlas`, sửa đường dẫn trong service cleanup trước khi cài.
Chạy các lệnh Docker bằng cùng tài khoản quản lý Docker để dùng cùng Buildx builder.
Giữ project name `radiology-atlas` của Compose để dùng đúng volume đã tạo.

## 3. Quản lý tài khoản và phân quyền

- **Root:** toàn bộ module, quản lý user, phân quyền vùng/module, reset mật khẩu, khóa/xóa, thu hồi session và dọn cache.
- **Admin:** xem mọi vùng/module hiện tại và tương lai; không quản lý user hoặc cache.
- **Standard:** chỉ những vùng được Root tick, cộng module cấp riêng nếu có. Module mới trong vùng được cấp tự kế thừa quyền.
- Xem [ACCOUNTS.md](ACCOUNTS.md) để tạo Root đầu tiên, nâng cấp user cũ và phân quyền.
- Mặc định user mới có danh sách rỗng, chưa được xem data; không tự cấp quyền toàn bộ.
- API module/slice/search/structure/translations và ảnh/overlay/filter icon đều kiểm tra quyền.
  Đổi khoảng trắng thành `_` trong URL không thay đổi quyền. Đường dẫn source/raw JSON bị chặn.
- Mật khẩu Argon2id; cookie Secure/HttpOnly/SameSite=Lax; CSRF cho POST; giới hạn thử đăng nhập.
- Phiên mặc định hết hạn sau 30 phút không hoạt động hoặc 8 giờ kể từ đăng nhập; tối đa 5 phiên/user.
  Tải ảnh/preload cũng là hoạt động. Thay quyền/khóa/reset mật khẩu thu hồi phiên ngay từ request kế tiếp.
- Mỗi người dùng tự đổi mật khẩu ở `/account`. Root cuối cùng đang hoạt động được bảo vệ
  khỏi xóa, khóa hoặc hạ quyền. Tối đa 500 tài khoản (phù hợp nhóm nhỏ).
- Quyền xem cho phép trình duyệt nhận ảnh và thông tin giải phẫu; đây không phải DRM hay chế độ
  chống lưu ảnh. Thu hồi quyền không thu hồi các bản người dùng đã tải trước đó.

Console server khi cần khôi phục quyền truy cập:

```bash
docker compose run --rm viewer python docker/manage.py list
docker compose run --rm viewer python docker/manage.py reset-password --username root
# Nếu cần Root dự phòng:
docker compose run --rm viewer python docker/manage.py create-root --username backuproot
```

## 4. Cache, log và dọn định kỳ

| Thành phần | Mặc định | Dọn / giới hạn |
|---|---:|---|
| Ảnh/JSON copy trên đĩa server | 0 | Đọc từ data, không ghi cache xuống data |
| Metadata RAM server | 512 MiB | 3 LRU, tối đa 8 mục mỗi loại; TTL 1800 giây; dọn mỗi 60 giây |
| Blob + data URL trong RAM mỗi tab | 512 MiB | Preload toàn bộ series đang mở; LRU byte-budget; TTL 1800 giây; dọn mỗi 60 giây |
| Ảnh đã giải mã mỗi tab | 32 ảnh | LRU riêng; RAM phụ thuộc kích thước ảnh, ngoài ngân sách ảnh nén |
| Slice JSON và trạng thái preload | Series hiện tại | Giới hạn số mục theo độ dài series, xóa khi đổi series/module |
| Cache HTTP dữ liệu server | Không lưu lâu | `private, no-store`; preload dùng RAM tab thay cho cache đĩa |
| `/tmp` viewer | 64 MiB tmpfs | Tự hết khi container tái tạo |
| Log Docker | 3 × 10 MB / dịch vụ | Docker `local` tự xoay vòng |
| Audit tài khoản | 30 ngày, tối đa 10.000 sự kiện | Dọn mỗi 60 giây; không ghi mật khẩu |
| SQLite chính | Tối đa 64 MiB | Session/rate hết hạn tự xóa, WAL checkpoint, incremental vacuum |
| Build cache chuyên dụng | Mục tiêu 2 GB, giữ 256 MB | BuildKit GC + script cleanup hàng ngày |

Các biến cache/preload trong `.env` có thể đổi rồi
`docker compose up -d --force-recreate viewer`. TTL RAM là thời gian không dùng mục cache.
Tab bị trình duyệt đưa nền có thể bị trì hoãn timer; cache vẫn bị giới hạn byte khi thêm ảnh.
Series lớn hơn ngân sách vẫn được preload theo lượt nhưng LRU sẽ bỏ ảnh cũ; muốn giữ hết ảnh nén
cùng lúc cần tăng `BROWSER_CACHE_MIB` theo RAM máy người dùng. Tổng RAM tab lớn hơn riêng số này
vì còn ảnh giải mã, JSON, DOM, preload đang tải và bộ nhớ trình duyệt. Container mặc định giới hạn
3 GiB qua `VIEWER_MEMORY_LIMIT`. Đây là profile VPS 2 core / 12 GB RAM; ngân sách không cấp phát trước.
Xem [PERFORMANCE.md](PERFORMANCE.md) để biết cấu hình 20/11 slice, 2 preload worker,
4 request ảnh, giới hạn máy ít RAM và cách cập nhật `.env` cũ.

BuildKit GC là cơ chế thu gom sau build, không phải quota cứng trong lúc build. Xem
[BuildKit GC settings](https://docs.docker.com/build/buildkit/toml-configuration/).
Các deploy/update script dùng builder riêng `radiology-atlas-bounded`; không dùng builder chung.
`cleanup.sh` chỉ dọn cache của builder này và image **không còn tag** có nhãn Radiology Atlas.
Giữ image hiện tại và tag rollback; không xóa volume, data hay cache của dự án khác.

Cài tác vụ daily trên Ubuntu (đổi ExecStart nếu cài ở đường dẫn khác):

```bash
sudo cp radiology-atlas-cleanup.service radiology-atlas-cleanup.timer /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now radiology-atlas-cleanup.timer
systemctl list-timers radiology-atlas-cleanup.timer
# Dọn thủ công:
bash cleanup.sh
```

Timer chạy root; nếu build bằng tài khoản Docker không phải root, thêm `User=TEN_USER` vào
service để cleanup nhìn thấy đúng builder. Log xoay theo [Docker local logging](https://docs.docker.com/engine/logging/drivers/local/).
Backup do quản trị viên tạo không tự xóa để tránh mất bản phục hồi; tự đặt chính sách lưu backup
phù hợp ở nơi lưu ngoài server. Data mới do capture bổ sung vẫn tăng theo dữ liệu thật, không phải cache.

## 5. Update local và Docker dùng cùng nguồn

Chỉ sửa viewer ở `offline_anatomy_viewer/`. Không có bản copy `app.js` riêng trong `docker/`.
Dockerfile COPY viewer từ thư mục này ở mỗi lần build. Cập nhật bản local xong, chạy:

```bash
# Tại máy local, dùng Python hiện có (không cần cài Docker để đóng gói):
python docker/release.py
# Gửi zip mới trong releases/ sang server, giải nén vào /opt/radiology-atlas.
# Zip không có data, .env, tài khoản, session hoặc mật khẩu.

cd /opt/radiology-atlas/docker
bash backup-state.sh /srv/atlas-backups
# Sau khi giải nén nguồn mới, kiểm tra lại .env và thay BUILD_REVISION nếu muốn.
bash update.sh
docker compose ps
```

`update.sh` giữ image đang chạy ở tag `radiology-atlas-viewer:rollback`, build nguồn mới, thay viewer
và chờ healthy. Khi khởi động bản mới thất bại, script yêu cầu Compose chạy lại tag cũ; xem `compose ps`
để xác nhận health sau phục hồi. Volume tài khoản và data không bị thay thế. Nếu chỉ thay data,
không cần build: mở catalogue hoặc Refresh trong viewer; dữ liệu đang capture có ảnh + labels mới hiện.
Nếu thay Caddyfile/Compose, áp dụng thêm `docker compose up -d` (và restart Caddy khi đổi Caddyfile).

Rollback server thủ công:

```bash
VIEWER_IMAGE=radiology-atlas-viewer:rollback docker compose up -d --no-deps --force-recreate viewer
docker compose ps
```

Tag rollback giữ một bản gần nhất. Không chạy một lần update khác trước khi quyết định giữ bản
hiện tại, vì tag này sẽ được cập nhật. Với thay đổi schema tài khoản trong tương lai, giữ cả source/image
cũ và backup `/state` tương ứng; không phục hồi schema cũ lên database đã migrate nếu chưa kiểm thử.
Không dùng `docker compose down -v`: cờ `-v` xóa volume tài khoản/chứng chỉ.

## 6. Image đa kiến trúc

Build trực tiếp trên Ubuntu arm64 sẽ tạo arm64; trên Ubuntu amd64 tạo amd64.
Một image tag chứa cả hai kiến trúc dùng registry và Buildx:

```bash
docker login YOUR_REGISTRY
IMAGE=YOUR_REGISTRY/OWNER/radiology-atlas:VERSION bash build-multiarch.sh
```

Builder phải có node cho cả hai kiến trúc hoặc emulation đã cấu hình, theo
[Docker multi-platform builds](https://docs.docker.com/build/building/multi-platform/).
Để chạy image đã push, đặt `VIEWER_IMAGE` trong `.env`, rồi `docker compose pull viewer`
và `docker compose up -d --no-build`. Không chạy deploy/update build-local nếu mục tiêu chỉ là pull image.

## 7. Kiểm tra vận hành

```bash
cd /opt/radiology-atlas/docker
bash check.sh
docker compose logs --tail=100 viewer caddy
docker compose exec viewer python docker/manage.py list
```

Smoke test build native, xác nhận health, chạy self-test đọc module→slice→PNG bằng cùng mã viewer.
Nó để service chạy và không xóa volume. Sau đó thử qua HTTPS DDNS: đăng nhập, tạo viewer chỉ có
MRI brain, kiểm tra module ngoài quyền bị chặn, bật/tắt highlight, Detail/MPR/Overlays, cuộn series.

Bộ test gateway độc lập (state test **mới/rỗng**, không dùng state thật):

```bash
python -m pip install -r docker/requirements.txt
python docker/tests/test_portal.py --data-root /PATH/all_modules --state-dir /PATH/EMPTY_TEST_STATE
node docker/tests/test_resource_cache.cjs .
```

Đã kiểm thử gateway/shared viewer bằng Python và trình duyệt trên máy phát triển.
Máy phát triển hiện chưa có Docker Engine; build container thật, smoke ARM64/AMD64 và cấp HTTPS
trên DDNS thực tế là bước xác nhận ở Ubuntu bằng các lệnh trên, không được coi là đã chạy ở bản phát hành này.
