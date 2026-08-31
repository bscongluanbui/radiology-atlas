# Radiology Atlas

Website Home → Anatomy → Viewer, quản trị Root/Admin/Standard và Docker cho Ubuntu **amd64 / arm64**.
Một bộ nguồn dùng chung cho viewer local và container; các công cụ giải phẫu bên trong giữ nguyên.

**Repo này chỉ chứa phần mềm. Data giải phẫu sẽ được đưa lên server riêng sau.**

## Có gì trong repo?

```text
README.md
.gitignore / .gitattributes
THIRD_PARTY_NOTICES.md
docker/                         # Website, tài khoản, Docker, cài đặt/update/cache
offline_anatomy_viewer/          # Viewer hiện tại, thumbnails và chỗ trống bản dịch
anatomy_identity.py              # Thư viện định danh giải phẫu
overlay_capture.py              # Dependency kiểm tra overlay mà viewer đang import
overlay_capture.js
overlay_runtime.js
```

Bốn file Python/JS ở gốc là dependency của viewer, không phải bộ công cụ collect đầy đủ.
Không có `imaios_data`, ảnh slice, raw captures, database user, `.env`, mật khẩu, cache, log,
browser profile hay các thư mục delivery/test cũ. 84 thumbnail giao diện vẫn được giữ để hiển thị module.
Thư mục bản dịch trong viewer giữ cấu trúc hiện tại để bổ sung tiếng Việt sau.

## 1. Đưa nguồn lên GitHub

Tạo một repository **rỗng** trên GitHub, chưa thêm README, license hoặc gitignore vì bộ này đã có README/gitignore.
Thay URL ví dụ bên dưới bằng URL repo thật. Trong PowerShell trên máy hiện tại:

```powershell
cd E:\coding\radiology\web\radiology-atlas-github
git init -b main
git add .
git status --short
git commit -m "Initial Radiology Atlas website and viewer"
git remote add origin https://github.com/YOUR_USER/radiology-atlas.git
git push -u origin main
```

Đăng nhập GitHub bằng cơ chế xác thực Git của bạn khi được yêu cầu. Không đặt token/mật khẩu trong URL.
Nếu dùng giao diện upload, đưa **nội dung bên trong thư mục này** vào gốc repo, không thêm một lớp thư mục ngoài.
Không upload toàn bộ workspace cũ `E:\coding\radiology\web`.

## 2. Clone về Ubuntu

Cài Git và Docker Engine + Compose + Buildx theo [Docker Ubuntu](https://docs.docker.com/engine/install/ubuntu/).
Sau đó, bằng tài khoản quản lý Docker:

```bash
sudo install -d -m 0755 -o "$USER" -g "$(id -gn)" /opt/radiology-atlas
git clone https://github.com/YOUR_USER/radiology-atlas.git /opt/radiology-atlas
cd /opt/radiology-atlas/docker
cp -n .env.example .env
chmod 600 .env
nano .env
```

Điền tên miền đã trỏ VPS và đường dẫn data dự kiến:

```dotenv
DDNS_HOST=atlas.example.com
DATA_ROOT=/srv/radiology/imaios_data/all_modules
```

`DDNS_HOST` nhận tên miền thường, không bắt buộc dùng DDNS; bỏ `https://` và dấu `/`.
`.env` bị Git bỏ qua, nên `git pull` không thay cấu hình riêng của server.

## 3. Data sẽ bổ sung sau

Bây giờ có thể upload repo, clone và chuẩn bị `.env`. **Chỉ chạy deploy khi data đã có**, vì gateway
đang kiểm tra catalogue/data lúc khởi động. Không tạo catalogue giả hoặc xáo trộn data để qua bước này.

Khi đã chọn cách upload data, giữ nguyên cây thư mục ở ngoài repo:

```text
/srv/radiology/imaios_data/all_modules/
  module_catalogue.json
  modules/
    BRAIN/mri-brain/...
    HEAD_AND_NECK/...
    THORAX/...
```

Nếu đặt ở vị trí khác, chỉ sửa `DATA_ROOT`. Container UID/GID `10001:10001` cần quyền đọc/traverse.
Docker bind-mount data **read-only**, không đưa data vào image. Không commit data vào Git.

## 4. Cài đặt lần đầu

Sau khi data và `.env` đã sẵn sàng:

```bash
cd /opt/radiology-atlas/docker
bash deploy.sh
docker compose run --rm viewer python docker/manage.py create-root --username root
```

Nhập mật khẩu hai lần, ít nhất 12 ký tự. Mở tên miền → Login → `/admin`.
Root quản lý tài khoản/quyền; Admin xem tất cả; Standard xem vùng/module được Root cấp.
Không có tài khoản hoặc mật khẩu sản xuất mặc định.

## 5. Cập nhật sau này bằng Git

Trên máy phát triển, sửa nguồn **trong repo này**, kiểm thử, `git add`, `git commit`, `git push`.
Dockerfile dùng chính các file viewer đó, không có một bản viewer riêng phải sửa lần thứ hai.

Trên VPS đã cài:

```bash
cd /opt/radiology-atlas
git status --short
bash docker/backup-state.sh
git pull --ff-only
bash docker/update.sh
```

`git pull --ff-only` dừng khi lịch sử phân nhánh, thay vì tự tạo merge. Nếu có sửa code trực tiếp trên VPS,
xử lý các thay đổi đó trước khi cập nhật. Dừng quy trình nếu bất kỳ lệnh nào báo lỗi.
`update.sh` build lại image, thay container viewer, giữ data/volume tài khoản và image rollback.
Chỉ `git pull` mà chưa chạy `update.sh` thì container cũ vẫn chạy code cũ.
`.env` và data không bị thay bởi thao tác cập nhật nguồn. Không dùng `docker compose down -v` khi update.

Nếu bản cập nhật đổi cấu hình Caddy/Compose, làm thêm bước tương ứng trong [Docker README](docker/README.md).
Nếu chỉ thêm data, không cần rebuild image; mở lại catalogue/Refresh trong viewer.

## Viewer local

Viewer local vẫn chạy từ cùng nguồn:

```powershell
python offline_anatomy_viewer/server.py --data-root "E:/DUONG_DAN_DATA/all_modules"
```

Launcher `.bat` có sẵn dùng vị trí mặc định `imaios_data/all_modules` bên cạnh thư mục viewer;
vị trí này cũng được `.gitignore` loại khỏi Git. Website có đăng nhập chạy qua Docker gateway như trên.

## Tài liệu / kiểm thử

- [Tài khoản Root/Admin/Standard](docker/ACCOUNTS.md)
- [Docker: cài đặt, HTTPS, cache, backup, rollback](docker/README.md)
- [Home / Anatomy / Viewer](docker/WEBSITE.md)
- [Tốc độ tải ảnh, cache/preload và số đo đối chiếu](docker/PERFORMANCE.md)
- [Chi tiết viewer local](offline_anatomy_viewer/README.md)
- [Nguồn assets](THIRD_PARTY_NOTICES.md)
- Tests ở `docker/tests/`; tests HTTP cần data thật và một thư mục state kiểm thử mới, không dùng state sản xuất.

Nguồn hướng dẫn Git: [GitHub — thêm mã nguồn có sẵn](https://docs.github.com/en/migrations/importing-source-code/using-the-command-line-to-import-source-code/adding-locally-hosted-code-to-github),
[git pull](https://git-scm.com/docs/git-pull), [gitattributes](https://git-scm.com/docs/gitattributes).
