# Cập nhật bản dịch gia tăng

Công cụ chạy với viewer local và có sẵn trong Docker image khi image được build
từ nguồn này. Công cụ chỉ tạo bản cập nhật, không sửa capture/data, tự dịch,
tự duyệt thuật ngữ hoặc tự thay bản dịch đang chạy.

## Quy tắc

| Thay đổi nguồn | Xử lý bản dịch |
| --- | --- |
| Cùng khóa, nguồn và binding không đổi | Giữ nội dung, ghi chú và trạng thái duyệt từng trường |
| Khóa mới | Tạo ô trống `draft`; không sao chép từ khóa khác dù tên giống nhau |
| Một trường nguồn đổi | Giữ bản dịch cũ để tham khảo; đặt trường đó thành `needs_review` |
| Binding/ID chuẩn hoặc vị trí occurrence đổi | Yêu cầu duyệt lại các trường của mục đó |
| Mục biến mất | Chuyển vào `archived`, không xóa nội dung dịch |
| Mục xuất hiện lại | Lấy lịch sử cùng khóa; nguồn/binding thay đổi vẫn cần duyệt lại |
| Chạy lại với nguồn không đổi | Không thêm lịch sử lặp; nội dung pack giữ nguyên |

Ví dụ: mô tả thay đổi nhưng tên giữ nguyên → tên tiếng Việt đã duyệt vẫn hiển
thị; mô tả dùng tiếng Anh cho đến khi duyệt lại. Cả chế độ Tiếng Việt và Song ngữ
dùng chung quy tắc này. Latin, ID, tọa độ và liên kết giải phẫu không được dịch.

## Chạy trên Windows

Từ thư mục `radiology-atlas-github`, mỗi lần dùng một thư mục kết quả mới:

```powershell
python .\offline_anatomy_viewer\sync_language_pack.py `
  --data-root E:\coding\radiology\web\imaios_data\all_modules `
  --key BRAIN/mri-brain --locale vi `
  --pack .\offline_anatomy_viewer\translations\vi\BRAIN\mri-brain.json `
  --output-dir .\translation-work\mri-brain-update-001
```

Nếu chưa có pack cho module, bỏ `--pack` để tạo bản đầu tiên. Nếu đã chỉ định
`--pack` mà file bị thiếu hoặc lỗi, lệnh sẽ báo lỗi thay vì bỏ qua bản dịch cũ.
Lần tiếp theo truyền pack đã chỉnh sửa/duyệt gần nhất vào `--pack`, không dùng lại
pack cũ trước khi dịch. Dùng `--definitions-only` để chỉ cập nhật structures và
filters; labels/texts hiện có vẫn được giữ nguyên.

## Kết quả

- `pack.json`: bản dịch cập nhật, chưa tự cài vào viewer.
- `report.json`: số mục mới/thay đổi/lưu trữ và danh sách `pending_fields` cần làm.
- `previous.json`: bản sao nguyên byte của pack đầu vào để phục hồi.
- `READY.json`: được ghi cuối cùng; chứa SHA-256 của ba file trên.

Chỉ dùng bundle khi lệnh trả `SYNC=PASS`, exit 0 và có `READY.json`. Thư mục kết
quả đã tồn tại sẽ bị từ chối; chọn tên mới. Bundle thiếu READY là lần chạy chưa
hoàn tất. File đầu vào luôn giữ nguyên. Đừng đặt thư mục làm việc/backup trong
`translations/` để tránh đóng gói nhầm lịch sử vào Docker image.

## Dịch và duyệt

1. Đọc `report.json`, xử lý từng trường trong `pending_fields`.
2. Chỉnh **chỉ** `translation.<field>`, giữ nguyên `source`, khóa và `binding`.
3. Theo yêu cầu người dùng, `translate_with_openai.py` tự đặt trường đã dịch
   thành `field_status.<field>: reviewed` để hiển thị ngay, không chờ duyệt thủ công.
   Đây là cờ kích hoạt hiển thị, không phải chứng nhận đã kiểm định y khoa.
   Ô chưa dịch là `draft`; dữ liệu nguồn thay đổi là `needs_review` cho tới khi dịch lại.
   Tùy chọn `--require-review` bật lại bước duyệt thủ công khi cần.
   `status` của dòng là tổng quan, không ghi đè các cờ riêng từng trường.
4. Kiểm tra English / Tiếng Việt / Song ngữ, tên dài, các nhãn xuống dòng và mobile.
5. Sao lưu pack đang dùng, rồi chép bản `pack.json` đã duyệt vào
   `offline_anatomy_viewer/translations/vi/BRAIN/mri-brain.json` trong repository.
6. Commit và build/publish image như cập nhật viewer thông thường. Server pull
   image mới; không cần thay đổi cấu trúc data. Refresh/chọn lại ngôn ngữ ở viewer.

Khi đã sửa pack.json thủ công, hash trong READY phản ánh **bản lúc sync**, không
phải bản vừa duyệt. Giữ bundle gốc làm mốc đối chiếu; bản đã duyệt được quản lý
bằng Git. Cơ chế này không chứng nhận độ chính xác của bản dịch y khoa.

## Chạy bằng Docker trên Ubuntu

Sau khi image chứa công cụ mới đã được build/publish, từ thư mục `docker`:

```bash
mkdir -p ../translation-work
# Cho UID 10001 của container ghi đúng thư mục làm việc này.
sudo chown 10001:10001 ../translation-work
docker compose run --rm --no-deps \
  -v "$(realpath ../translation-work):/translation-work" \
  viewer python /app/offline_anatomy_viewer/sync_language_pack.py \
  --data-root /data --key BRAIN/mri-brain --locale vi \
  --pack /app/offline_anatomy_viewer/translations/vi/BRAIN/mri-brain.json \
  --output-dir /translation-work/mri-brain-update-001
```

Để đồng bộ từ bản dịch đang làm dở, đặt nó trong `translation-work` rồi truyền
đường dẫn `/translation-work/...json` vào `--pack`. Lệnh không thay đổi image
hoặc pack trong container đang phục vụ người dùng.

## Phục hồi

Sync không ghi đè đầu vào nên chỉ cần bỏ qua bundle mới để tiếp tục dùng pack cũ.
Nếu đã cài pack mới, khôi phục pack từ Git hoặc `previous.json`, rồi build/publish
lại nếu sử dụng Docker. Kiểm tra module/locale và hash của backup trước khi chép.
Nguồn đã thay đổi vẫn khiến bản dịch cũ không khớp tự chuyển về tiếng Anh.

## Kiểm thử

```bash
python -m unittest discover -s docker/tests -p test_distribution.py -v
node docker/tests/test_anatomy_language.cjs
```

Các test dùng nội dung mẫu, kiểm tra giữ bản dịch, duyệt theo trường, archive/
restore, idempotence, nhầm ID/TA/module, partial export, input lỗi, backup/hash và
ngăn ghi đè. Không dùng hoặc tự tạo bản dịch giải phẫu thật trong test.
