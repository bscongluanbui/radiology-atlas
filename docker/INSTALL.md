# Cài đặt bằng image public + Cloudflare Tunnel

Áp dụng cho repo `bscongluanbui/radiology-atlas`, VPS Ubuntu ARM64/AMD64,
source tại `/home/ubuntu/radiology-atlas/`, domain `bcanatomy.site`.
Docker Engine và plugin Compose V2 phải hoạt động (`docker compose version`).
Cài/upgrade theo [Docker Ubuntu](https://docs.docker.com/engine/install/ubuntu/).
VPS chỉ pull/run; Buildx/QEMU chỉ chạy trên GitHub Actions.

## 1. Publish lần đầu và đặt Public (một lần)

1. Push bản nguồn mới vào nhánh `main`.
2. Mở [Actions](https://github.com/bscongluanbui/radiology-atlas/actions), đợi
   **Publish viewer image** xanh ở cả job `test` và `image`.
3. Mở [package radiology-atlas](https://github.com/users/bscongluanbui/packages/container/package/radiology-atlas).
   **Package settings → Change visibility → Public**. Nếu đã Public thì giữ nguyên.
   Public của repository và Public của container package là hai cấu hình riêng.
4. Kiểm tra không cần tài khoản registry:

```bash
python3 /home/ubuntu/radiology-atlas/docker/verify-public-image.py
```

Kết quả phải có `PUBLIC_IMAGE=PASS` và cả `linux/amd64,linux/arm64`.
Image public không chứa data anatomy hoặc database tài khoản. VPS không cần `docker login`.
Workflow dùng `GITHUB_TOKEN` có `packages:write`, không cần thêm PAT vào repo.
[GitHub: visibility](https://docs.github.com/en/packages/learn-github-packages/configuring-a-packages-access-control-and-visibility),
[GHCR](https://docs.github.com/en/packages/working-with-a-github-packages-registry/working-with-the-container-registry).

## 2. Lấy cấu hình mới trên VPS

Repo đã clone thì không clone lại:

```bash
cd /home/ubuntu/radiology-atlas
git pull --ff-only
cd docker
test -f .env || cp .env.example .env
chmod 600 .env
nano .env
```

Sửa các dòng sau (đặc biệt thay `VIEWER_IMAGE=...:local` của cấu hình cũ):

```dotenv
DDNS_HOST=bcanatomy.site
DATA_ROOT=/srv/radiology/imaios_data/all_modules
VIEWER_IMAGE=ghcr.io/bscongluanbui/radiology-atlas:latest
TUNNEL_CONTAINER=thirsty_agnesi
```

`DATA_ROOT` ở đây là **ví dụ**: thay bằng đường dẫn data thật trên VPS.
Không thêm `https://` hay `/` vào `DDNS_HOST`. Không thêm token tunnel vào `.env`.
Giữ các dòng cache/preload của `.env.example`, phù hợp VPS 2 core/12 GB, ít user:
metadata 512 MiB, mỗi tab browser tối đa 512 MiB, TTL 1800 giây, decoded 32 ảnh,
decode trước/sau 20/11, decode/preload concurrency 2/2, image concurrency 4,
container memory limit 3 GiB. Đây là ngân sách tối đa, không phải RAM luôn được cấp hết.

## 3. Data phải có trước khi khởi chạy viewer

```text
DATA_ROOT/
  module_catalogue.json
  modules/
    BRAIN/mri-brain/...
    HEAD_AND_NECK/...
    THORAX/...
```

Giữ nguyên data, không cần đủ mọi module nhưng phải có ít nhất một module có cặp
ảnh và labels. Đường dẫn phải là thư mục `all_modules`, không phải thư mục cha.
Nếu chưa upload data, dừng ở bước chuẩn bị; viewer kiểm tra dữ liệu lúc startup.
Container UID/GID `10001:10001` cần đọc file và đi qua thư mục cha; dùng group hoặc ACL
cấp quyền đọc nếu cần. `preflight.py` chạy trong container sẽ báo chính xác lỗi đọc mount.
Không dùng `chmod 777`; mount `/data` luôn read-only.

## 4. Pull và khởi chạy website, dùng lại connector

```bash
cd /home/ubuntu/radiology-atlas/docker
bash deploy.sh
```

Script kiểm tra cấu hình, pull viewer, kiểm tra data/state, đợi container healthy rồi gắn
connector `thirsty_agnesi` vào `radiology-atlas_backend`. Connector vẫn giữ mạng `bridge`
để ra Internet. Chạy script nhiều lần không gắn trùng mạng. Không tạo tunnel mới và không
đụng tới wg-easy, Portainer hay các container khác. Viewer không publish cổng ra VPS.
Caddy nằm trong profile `direct`, không chạy ở cấu hình mặc định Tunnel.

Nếu container website đã healthy nhưng bước gắn connector lỗi, sửa `TUNNEL_CONTAINER` rồi:

```bash
bash attach-tunnel.sh
```

Tạo Root đầu tiên và nhập mật khẩu do bạn tự chọn hai lần:

```bash
docker compose exec viewer python docker/manage.py create-root --username root
```

Tài khoản lưu trong volume `radiology-atlas_atlas-state`, không có mật khẩu mặc định.
Root quản lý user; Admin xem tất cả; Standard chỉ vùng/module Root cấp.

## 5. Cấu hình route trên Cloudflare

Trong tunnel **arm**, mở Published application routes/Public hostnames của `bcanatomy.site`, sửa:

| Trường | Giá trị |
|---|---|
| Hostname | `bcanatomy.site` (không subdomain, không path) |
| Service type | `HTTP` |
| URL | `viewer:8080` |
| HTTP Host Header (nếu đã có override) | `bcanatomy.site` |

Không dùng `localhost:8080` vì cloudflared đang ở container bridge riêng.
DNS đã trỏ tunnel thì giữ nguyên; không thêm bản ghi A cạnh cùng hostname.
Trong cấu hình SSL/TLS của domain, bật **Always Use HTTPS**.
Không tạo Cache Everything cho `/api`, `/data`, `/viewer`, `/admin`; giữ `private, no-store`.
Cloudflare Tunnel chuyển request đến origin; quyền anatomy vẫn do đăng nhập của ứng dụng quyết định.
[Cloudflare: setup](https://developers.cloudflare.com/tunnel/setup/),
[Always Use HTTPS](https://developers.cloudflare.com/ssl/edge-certificates/additional-options/always-use-https/),
[Docker network connect](https://docs.docker.com/reference/cli/docker/network/connect/).

Mở https://bcanatomy.site → Login. Bảng Root: https://bcanatomy.site/admin.
Nếu tạo lại container cloudflared bằng Portainer sau này, chạy lại `bash attach-tunnel.sh`.
Chỉ tạo lại container viewer thì không cần gắn lại connector.

## 6. Kiểm tra sau cài đặt

```bash
cd /home/ubuntu/radiology-atlas/docker
docker compose ps
bash check.sh
curl -I https://bcanatomy.site/
```

`check.sh` kiểm tra trong container, không build hoặc restart. Kỳ vọng healthcheck healthy,
`PREFLIGHT=PASS`, `CONTAINER_SMOKE=PASS`, trang Home HTTP 200 qua HTTPS.
Kiểm tra thêm login Root, tạo Standard cho một vùng rồi mở vùng được cấp / vùng chưa cấp.

| Triệu chứng | Kiểm tra |
|---|---|
| GHCR denied/not found | Workflow `image` đã xanh? Package đã Public? `.env` còn image local? |
| module_catalogue.json missing / permission denied | Upload data, sửa DATA_ROOT, quyền đọc UID 10001 |
| Cloudflare 1033 | Tunnel connector đang chạy và Connected trong Cloudflare |
| 502 | Viewer healthy, connector cùng network, route HTTP `viewer:8080` |
| 400 / Host rejected | DDNS_HOST và Host Header cùng `bcanatomy.site` |
| Đăng nhập không giữ phiên | Mở bằng HTTPS, bật Always Use HTTPS, không cache response đăng nhập |
| Standard thấy ít module | Root cấp vùng/module trong `/admin`; đây là phân quyền |

Logs website: `docker compose logs --tail=100 viewer`.
Không gửi token tunnel hoặc nội dung đầy đủ `docker inspect`/`.env` để chẩn đoán.

## 7. Update chỉ pull và tạo lại

Sau mỗi lần sửa phần mềm, push `main` và đợi **Publish viewer image** thành công.
Trên VPS:

```bash
cd /home/ubuntu/radiology-atlas/docker
docker compose pull && docker compose up -d --force-recreate --pull never --wait --wait-timeout 180
```

Đây là hai thao tác user yêu cầu; data, quyền user và mật khẩu còn nguyên.
`pull` tải image chứ không cập nhật file Git; chỉ cần `git pull --ff-only` thêm khi Compose/scripts
hoặc biến `.env` có thay đổi hạ tầng. Không dùng `down -v` vì sẽ xóa volume tài khoản.
[Docker Compose up](https://docs.docker.com/reference/cli/docker/compose/up/).

### Rollback và backup

Thay hai lệnh trên bằng `bash update.sh` nếu muốn script giữ image của container đang chạy và
tự khôi phục khi healthcheck bản mới thất bại. Lỗi pull không dừng container đang chạy.
Trước update thay schema, chạy `bash backup-state.sh` để có bản sao database riêng.
Rollback image không tự đảo ngược migration database; giữ cả image và backup tương ứng.

Để quay lại revision đã biết, sửa `VIEWER_IMAGE` trong `.env` thành
`ghcr.io/bscongluanbui/radiology-atlas:sha-COMMIT_CU`, rồi pull/up như trên.
Xem SHA/digest ở summary của workflow. Khi muốn theo latest lại, đổi về `:latest`.

## 8. Dọn cache định kỳ

```bash
cd /home/ubuntu/radiology-atlas/docker
sudo cp radiology-atlas-cleanup.service radiology-atlas-cleanup.timer /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now radiology-atlas-cleanup.timer
systemctl list-timers radiology-atlas-cleanup.timer
```

Service đã dùng `/home/ubuntu/radiology-atlas/docker/cleanup.sh`.
Chỉ dọn build cache riêng và image Atlas không còn tag; không xóa data, volume tài khoản,
image đang chạy, image rollback có tag hoặc tài nguyên app khác. Không dùng `docker system prune --volumes`.
Metadata cache là RAM có giới hạn và TTL; browser cache là RAM theo tab; logs Docker xoay vòng.
Chi tiết: [PERFORMANCE.md](PERFORMANCE.md).
