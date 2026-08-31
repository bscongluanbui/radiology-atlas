> Triển khai / update hiện tại: [INSTALL.md](INSTALL.md) (GHCR public + Tunnel, không build trên VPS).

# Cache và preload — profile VPS ARM64/AMD64, 2 core, 12 GB RAM

Cập nhật 31/08/2026. Nguồn local và Docker dùng chung viewer. Home/Back, tương tác,
phân quyền và dữ liệu giải phẫu được giữ lại. Các thay đổi dưới đây đã kiểm thử trên
Windows local; chưa đo FPS trình duyệt hay triển khai container trên VPS ARM.

## Cấu hình mặc định mới

| Biến / thành phần | Trước | Mới | Bộ nhớ / ý nghĩa |
|---|---:|---:|---|
| `METADATA_CACHE_MIB` | 128 | **512 MiB** | RAM server, chia 3 cache khoảng 170,7 MiB/cache, tối đa 8 module/cache |
| `BROWSER_CACHE_MIB` | 256 | **512 MiB/tab** | RAM máy người xem: tổng Blob + chuỗi data URL được giữ trong cache |
| `CACHE_TTL_SECONDS` | 900 | **1800 giây** | 30 phút không sử dụng; dọn mỗi 60 giây |
| `DECODED_IMAGE_CACHE` | 24 | **32 ảnh** | LRU ảnh giải mã riêng, ngoài ngân sách trên |
| `DECODE_FORWARD` / `DECODE_BACKWARD` | 16 / 7 | **20 / 11** | Cửa sổ lân cận theo hướng cuộn; tự giảm để vừa số ảnh giải mã |
| `DECODE_CONCURRENCY` | 2 | **2** | Giải mã nền song song |
| `PRELOAD_CONCURRENCY` | 4 | **2** | Vẫn tải hết series/variant đang mở, giảm cạnh tranh CPU và đường truyền |
| `IMAGE_CONCURRENCY` | Chưa có hàng đợi chung | **4** | Tối đa 3 tải nền, chừa chỗ cho ảnh đang xem |
| Hàng đợi JSON slice | Chưa ưu tiên chung | **2 request** | Tối đa 1 nền; 1 suất ưu tiên slice đang xem |
| `VIEWER_MEMORY_LIMIT` | 768 MiB | **3 GiB** | Giới hạn toàn container, không cấp phát trước |
| Gunicorn | 1 worker / 8 threads | **Giữ nguyên** | Metadata dùng chung RLock; không nhân cache thành nhiều worker |

Preload vẫn thử lại lỗi tối đa 1 lần. Cache JSON / trạng thái preload giữ
`max(128, số slice + 8)` mục mỗi loại; đây là giới hạn số mục, không phải byte.
Trình duyệt báo RAM ≤2 GB: ngân sách ảnh giảm còn tối đa 128 MiB;
≤4 GB: tối đa 256 MiB. Cả hai hạ cache giải mã xuống tối đa 16 ảnh (10 trước / 5 sau
với cấu hình mặc định). Trình duyệt không báo RAM dùng cấu hình được cấp.
DOM, JSON, ảnh RGBA, các request đang chạy và overhead trình duyệt tính riêng.

**Khuyến nghị cho VPS này:** dùng profile trên trước, chưa cần tăng cao hơn.
512 MiB server là ngân sách chỉ mục metadata, không phải cache toàn bộ PNG.
512 MiB/tab tiêu thụ RAM máy của từng người dùng, không phải 12 GB RAM của VPS.
Tăng cache vô hạn hoặc tăng mọi mức song song sẽ không giải quyết nút thắt 2 core.
Giới hạn container 3 GiB để chừa RAM cho hệ điều hành, filesystem cache và dịch vụ khác.

## Những đường tải đã sửa

- Main image, filmstrip, ảnh tham chiếu MPR và overlay SVG dùng chung cache theo URL.
  Repaint không tải lại tài nguyên vẫn còn cache. Filmstrip tắt thì không tải ảnh nền của thanh đó.
- Blob → data URL chỉ chuyển một lần khi mục cache còn tồn tại; chuỗi được tính vào
  ngân sách theo ước lượng UTF-16 thận trọng. CSP hiện tại vẫn giữ nguyên, không dùng blob URL.
- Request foreground vượt lên trước hàng đợi preload; request cùng URL/JSON được gộp.
  Đổi series/module sẽ abort request web đang chạy và bỏ hàng đợi cũ; kết quả cũ không gắn vào thẻ đã tháo.
- Preload toàn bộ ảnh nền PNG và JSON của series/variant đang mở, không tải toàn thư viện.
  Overlay chỉ tải khi cần hiển thị, vẫn kiểm tra status/filter/transform; không tải trước mọi mask
  ẩn của mọi slice. Icon module giữ cách tải lazy riêng.
- Mỗi API slice lấy snapshot chỉ mục points **một lần**, rồi dùng chung cho các label.
  Vẫn kiểm tra mtime từng request, TA/taxon/point/filter, hình ảnh và overlay; không cache
  JSON hoàn chỉnh để che mất sửa đổi data. Các kiểm tra định danh không bị lược bỏ.
- Thanh timeline hiển thị `Preload x/y`. Đây là tiến độ lượt tải, không cam kết mọi ảnh
  còn trong RAM sau khi LRU/TTL loại bỏ. Lần truy cập sau sẽ tải lại mục đã hết hạn.

Cache server không ghi ảnh/JSON phụ xuống đĩa. Cache web vẫn `private, no-store`,
xác thực và phân quyền trên mỗi request. Series/module mới, refresh dữ liệu và rời trang
xóa cache ảnh tab; khi quay lại bằng browser history, timer và tải slice được khởi động lại.
Tab nền có thể bị trì hoãn timer; giới hạn byte vẫn áp dụng khi thêm ảnh.
Standalone tiếp tục dùng HTTP cache phiên bản hóa, với preload/decode mặc định mới.

## Đo đối chiếu và độ chính xác

Cùng data MRI brain, cùng máy Windows, 8 slice Axial; server tạm, account test riêng.
Số sau không phải benchmark VPS, FPS hay cam kết tốc độ; OS file cache có thể đã ấm.

| Web gateway | Trước | Sau |
|---|---:|---:|
| JSON slice: median khi chỉ mục đã ấm | 66,519 ms | **34,607 ms** |
| 4 request đồng thời: median | 167,385 ms | **106,516 ms** |
| JSON slice đầu tiên/chậm nhất lượt lạnh | 444,243 ms | 533,611 ms |
| PNG qua HTTP: median | 7,018 ms | 8,877 ms |

Warm JSON giảm khoảng 48%; lượt lạnh và tốc độ HTTP PNG thô chưa cải thiện trong mẫu này.
Lợi ích frontend là dùng lại ảnh/preload và ưu tiên frame, không phải sửa PNG hay giảm chất lượng.
132 PNG Axial có 87.109.161 byte ≈83,1 MiB; sau chuyển data URL sẽ chiếm thêm RAM.
Metadata MRI brain đã đo: structures ≈2,1 MiB, points ≈16,0 MiB, cross references ≈1,0 MiB.
Module này vốn vừa cache cũ; tăng cache server chủ yếu giúp giữ thêm các module khác.

- **379/379 slice của cả 4 series MRI brain: JSON trước/sau giống hoàn toàn.**
  Tổng lượt gọi `_points` giảm từ 24.097 xuống 379; file ảnh/capture không thay đổi.
- Test VM 60 slice: đúng 60 request JSON + 60 request PNG cho series, warm repaint
  không thêm request; kiểm tra renderer MPR/overlays, lọc overlay sai/không chọn, giới hạn RAM,
  ưu tiên foreground, abort/TTL/LRU và retry. Đây là kiểm thử hành vi, không phải số đo FPS.
- Test snapshot riêng kiểm tra cập nhật mtime, TA/filter/point sai và label thay đổi vẫn được nhận biết.

## Áp dụng cho VPS đã cài

Git update không ghi đè `docker/.env` hiện có. Sau khi lưu/commit/push nguồn mới lên GitHub,
ở thư mục clone của VPS, chạy `git pull --ff-only`, rồi sửa các giá trị này trong `docker/.env`
(giữ nguyên domain, data path, thông tin khác):

```dotenv
METADATA_CACHE_MIB=512
BROWSER_CACHE_MIB=512
CACHE_TTL_SECONDS=1800
DECODED_IMAGE_CACHE=32
DECODE_FORWARD=20
DECODE_BACKWARD=11
DECODE_CONCURRENCY=2
PRELOAD_CONCURRENCY=2
IMAGE_CONCURRENCY=4
VIEWER_MEMORY_LIMIT=3g
```

Tại thư mục gốc repository:

```bash
cd docker
docker compose pull && docker compose up -d --force-recreate --pull never --wait --wait-timeout 180
docker compose ps
docker stats --no-stream
```

Tùy chọn: `bash update.sh` giữ image rollback và tự phục hồi nếu healthcheck thất bại.
Chạy `bash backup-state.sh` riêng trước bản update đổi schema. Sau đó reload tab viewer.
Preview Windows dùng default của Python, không tự đọc `docker/.env`.
Trên Root `/admin`, xem ngân sách/usage metadata. Trong console tab viewer:

```javascript
window.viewerSliceCacheDiagnostics()
```

Theo dõi `encoded.bytes/maxBytes`, `encoded.evictions`, `encoded.requests`, `metadataRequests`,
`readyImages`, `seriesPreloadCompleted/Total/Failed`. Nếu thật sự đụng trần nhiều, đo RAM
máy người xem và băng thông trước khi tăng; giới hạn cấu hình browser là 1024 MiB, server là 512 MiB.
Không tăng decoded lên hàng trăm ảnh vì RGBA lớn hơn PNG nhiều.

Cơ chế tham khảo: [MDN Cache-Control](https://developer.mozilla.org/en-US/docs/Web/HTTP/Reference/Headers/Cache-Control),
[MDN FileReader.readAsDataURL](https://developer.mozilla.org/en-US/docs/Web/API/FileReader/readAsDataURL),
[Docker memory constraints](https://docs.docker.com/engine/containers/resource_constraints/).
