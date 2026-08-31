> Triển khai / update hiện tại: [INSTALL.md](INSTALL.md) (GHCR public + Tunnel, không build trên VPS).

# Root / Admin / Standard

## Vào trang quản trị

- Bản xem thử trên máy: `http://127.0.0.1:8961/admin`.
- Server: thêm `/admin` sau tên miền của bạn.
- Đăng nhập bằng tài khoản **Root**, rồi chọn **Quản trị** trên header hoặc **Tài khoản → Mở bảng quản trị Root**.
- Root là vai trò của website, không phải tài khoản root của Ubuntu. Tên đăng nhập có thể là `root`, `owner` hoặc tên bạn chọn.

| Vai trò | Xem giải phẫu | Quản lý tài khoản / quyền / cache |
|---|---|---|
| Root | Toàn bộ vùng và module, kể cả dữ liệu bổ sung sau | Có |
| Admin | Toàn bộ vùng và module, kể cả dữ liệu bổ sung sau | Không |
| Standard | Những vùng được Root tick; cộng các module được cấp riêng nếu có | Không |

Standard mới mặc định chưa có quyền xem. Cấp vùng BRAIN sẽ cho xem mọi module hiện có và bổ sung sau
trong vùng BRAIN, không cấp các vùng khác. Quyền được kiểm tra ở server trên catalogue, viewer,
API slice/structure/search/translations, ảnh, overlays và cả thumbnail; sửa URL không mở rộng quyền.

## Tạo Root đầu tiên trên VPS Docker

Sau khi cập nhật nguồn, build/chạy container:

```bash
cd /home/ubuntu/radiology-atlas/docker
docker compose run --rm viewer python docker/manage.py create-root --username root
```

Lệnh hỏi mật khẩu hai lần, tối thiểu 12 ký tự; không đưa mật khẩu vào command line hoặc `.env`.
Sau đó đăng nhập website bằng tên `root` và mật khẩu bạn vừa nhập.
Hệ thống không có mật khẩu Root mặc định, không có đăng ký công khai.

Xem tài khoản và vai trò hiện tại:

```bash
docker compose run --rm viewer python docker/manage.py list
```

Nếu tài khoản `root` đã có và cần đặt lại mật khẩu:

```bash
docker compose run --rm viewer python docker/manage.py reset-password --username root
```

Lệnh reset thu hồi mọi phiên đăng nhập nhưng giữ nguyên vai trò, vùng và module đã cấp.
`create-admin` vẫn tồn tại, nhưng ở bản này tạo **Admin chỉ xem toàn bộ**, không phải Root.

## Tạo và phân quyền trên giao diện

1. Root mở `/admin` → **Tạo tài khoản**.
2. Điền tên đăng nhập và mật khẩu ban đầu.
3. Chọn **Admin** để xem tất cả, hoặc **Standard** để giới hạn vùng.
4. Với Standard, tick các vùng trong **Vùng giải phẫu được phép xem**, ví dụ Não bộ và Lồng ngực.
5. Bấm **Tạo tài khoản**. Khi sửa tài khoản, bấm **Lưu**.

Phần **Quyền module riêng / tương thích tài khoản cũ** giữ các lựa chọn module cũ và hỗ trợ cấp hẹp
hơn một vùng. Muốn giới hạn hoàn toàn theo vùng, bỏ các module riêng không cần dùng.
Bỏ tick một vùng nhưng còn module riêng trong vùng đó thì module riêng vẫn được phép xem.
Lưu quyền/khóa/đổi mật khẩu thu hồi phiên ngay; người dùng phải đăng nhập lại.
Hệ thống giữ ít nhất một Root đang hoạt động; Admin và Standard nhận HTTP 403 ở `/admin` và mọi API quản trị.

## Tài khoản cũ và nâng cấp

- Quản trị viên `admin` của v49 → **Root**, giữ nguyên username và mật khẩu.
- Người xem có `all_modules` ở v49 → **Admin**, vẫn chỉ xem, không được quyền quản trị.
- Người xem giới hạn module → **Standard**, giữ đúng module đã cấp, chưa tự cấp cả vùng.
- Phiên cũ được thu hồi một lần khi nâng cấp; đăng nhập lại bằng mật khẩu cũ.
- Nâng cấp v2 thêm cột `access_role` và `regions` trong một transaction. Password hash, ID và module grants giữ nguyên.
- Database v1 có user được tự backup một lần vào `/state/accounts.before-rbac-v2.sqlite3`; file này chứa dữ liệu tài khoản,
  nằm trong volume riêng, không phục vụ qua HTTP và không nằm trong gói source. Chỉ một backup này được giữ, tránh tăng vô hạn.

Trước update production, vẫn nên chạy `bash backup-state.sh` để có cả database và session key.
Chỉ một phiên bản server dùng volume tại một thời điểm; không chạy đồng thời gateway cũ và mới.

## Rollback

Giữ backup state trước update và image trước đó theo `README.md`. Script rollback source chỉ đổi mã nguồn,
không sửa/xóa tài khoản. Các trường v1 được giữ để phiên bản cũ không biến Admin mới thành quản trị viên:

- Root được lưu tương thích dưới dạng legacy `role=admin`.
- Admin mới được lưu legacy `role=viewer, all_modules=1`.
- Standard được lưu legacy `role=viewer, all_modules=0`; vùng cấp ở v2 không được v49 hiểu,
  nên khi quay lại v49 chỉ các module riêng còn hiệu lực (quyền bị thu hẹp, không mở rộng).
- Nếu chạy v49 rồi nâng cấp lại v2, các vùng v2 cũ được xóa để tránh dùng quyền đã lỗi thời; Root tick lại vùng.

Muốn phục hồi chính xác cả tài khoản trước update, dừng viewer và phục hồi backup state cùng image cũ,
không chỉ đổi image. Backup chứa dữ liệu ở thời điểm sao lưu, không gồm thay đổi tài khoản về sau.

## Kiểm thử và nguồn thiết kế

```bash
python docker/tests/test_roles.py --data-root /PATH/all_modules --state-dir /PATH/NEW_EMPTY_TEST_STATE
```

Thiết kế mặc định không cấp quyền và kiểm tra mỗi request theo
[OWASP Authorization Cheat Sheet](https://cheatsheetseries.owasp.org/cheatsheets/Authorization_Cheat_Sheet.html).
Schema dùng thao tác thêm cột trong transaction, không ghi trực tiếp schema hay dựng lại bảng;
xem [SQLite ALTER TABLE](https://www.sqlite.org/lang_altertable.html).
