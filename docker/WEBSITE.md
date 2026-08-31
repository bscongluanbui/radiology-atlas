# Website Home → Anatomy → Viewer

Website được phục vụ trong cùng Docker gateway, cùng domain, session và database tài khoản.
Không thêm frontend server riêng, không đổi cấu trúc data và không thay đổi các công cụ viewer.

## Đường dẫn

- `/`: Home công khai; bố cục, gradient và nhịp trang lấy cảm hứng từ Stellar (HTML5 UP).
- `/anatomy`: danh mục module theo quyền, nhóm vùng và loại hình ảnh học, tìm kiếm, bộ lọc nhiều lựa chọn.
- `/viewer?key=BRAIN%2Fmri-brain`: mở chính xác module được chọn; kiểm tra quyền trước khi phục vụ viewer và runtime.
- `/login`: trang đăng nhập dự phòng nếu JavaScript tắt; trên Home, click Anatomy/Login mở popup đăng nhập.
- `/admin`: chỉ Root quản trị user/quyền; `/account`: thông tin vai trò và tự đổi mật khẩu. Xem [ACCOUNTS.md](ACCOUNTS.md).
- Root và Admin xem toàn bộ Anatomy; Standard chỉ vùng/module được Root cấp. Tài khoản quản trị cũ chuyển thành Root.

Người chưa đăng nhập chỉ xem Home. Đường dẫn trực tiếp tới Anatomy/viewer vẫn yêu cầu session.
Sau login, user chỉ thấy các module được cấp quyền. User chưa có quyền module thấy thông báo riêng,
không lặp popup login. Module có trong catalogue nhưng chưa có ảnh + labels được hiển thị mờ với
“Đang cập nhật”, không tạo link vào viewer. Login không hỗ trợ tự đăng ký công khai.

Các nhóm modality lấy từ giá trị `modality` trong catalogue, không suy đoán theo tên module.
Chọn nhiều mục trong cùng nhóm dùng OR; Regions × Modalities × từ khóa × tình trạng dùng AND.
Bộ lọc lưu trong query URL và khôi phục khi tải lại trang; không lưu thông tin đăng nhập trong URL.
Module icons dùng nguyên 84 thumbnail có sẵn với ánh xạ module đã kiểm tra ở bản viewer trước.
Sơ đồ silhouette là điều hướng vùng, không bổ sung nhãn hay cấu trúc vào ảnh giải phẫu.

## Viewer giữ nguyên

Thay đổi duy nhất trong app.js là ưu tiên `viewerRuntime.moduleKey` do gateway đã kiểm tra quyền
khi mở lần đầu. Nếu module đã chọn biến mất khỏi catalogue, viewer báo lỗi thay vì tự mở một module khác.
Các code render labels/leader/overlay, anatomy identity, Detail, MPR, Scroll/Pan/Zoom, language và preload
giữ nguyên. Local launcher vẫn đọc module trước đó từ preferences như cũ.

## Cập nhật trên VPS hiện có

Đóng gói nguồn mới bằng `python docker/release.py`, backup state trước, đưa toàn bộ nguồn mới lên VPS
(không ghi đè `.env`, không thay data), rồi chạy `bash /opt/radiology-atlas/docker/update.sh`.
Dockerfile dùng chung nguồn local nên website và viewer được cập nhật cùng một image.
Tên miền thường vẫn dùng biến `DDNS_HOST`; không cần dịch vụ cập nhật DDNS nếu domain đã trỏ VPS.

Truy cập domain sau update sẽ vào Home thay vì viewer trực tiếp. Các API/data giữ nguyên đường dẫn.
Links viewer cũ ở `/` sẽ tới Home; dùng `/viewer` hoặc mở module từ Anatomy để xem ảnh.

## Kiểm thử

- `python docker/tests/test_portal.py --data-root /PATH/all_modules --state-dir /PATH/NEW_EMPTY_TEST_STATE`
- `python docker/tests/test_website.py --data-root /PATH/all_modules --state-dir /PATH/ANOTHER_EMPTY_TEST_STATE`
- `node docker/tests/test_site_filters.cjs .`

Đã kiểm tra source và giao diện local; chưa có hostname/VPS credentials để triển khai domain thực tế.
Native Docker ARM64/AMD64 vẫn cần smoke test trên server như `docker/README.md`.

## Nguồn giao diện và hình ảnh

- Phong cách tham khảo: [Stellar — HTML5 UP](https://html5up.net/stellar).
  Trang website dùng HTML/CSS/JavaScript riêng, không mang nội dung Lorem Ipsum hay scripts của bản demo.
- Thumbnail module: tài sản IMAIOS đã có trong `offline_anatomy_viewer/assets/module-icons/manifest.json`.
- Body navigation SVG: [Human silhouette gender neutral front](https://commons.wikimedia.org/wiki/File:Human_silhouette_gender_neutral_front.svg), Sebastian Wallroth, CC0 1.0; file nguồn giữ nguyên.

## Điều hướng trong viewer

Viewer web có **Home** và **Back** ngay cạnh Menu/Detail. Home mở trang chủ `/`.
Back dùng lịch sử trình duyệt khi đến từ một trang cùng website; mở `/anatomy` khi
vào bằng liên kết trực tiếp, tab mới, trang đăng nhập hoặc trang bên ngoài.
Ctrl/Cmd-click vẫn mở liên kết bình thường. Viewer standalone và các công cụ giải phẫu giữ nguyên.
