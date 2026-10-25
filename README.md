# PDF Signature

Trình xem PDF đơn giản, cho phép chèn chữ ký (ảnh PNG) vào bất kỳ vị trí nào trên trang
và **ký số bằng chứng chỉ số** dạng file `.p12` (chuẩn PAdES, kiểm tra được trong Adobe Reader).
Giao diện theo phong cách Windows 11, tự đổi sáng/tối theo cài đặt của Windows.

Chương trình **không dùng USB token**: khoá bí mật nằm trong file chứng chỉ `.p12` / `.pfx`,
tự tạo được trong mục **Cài đặt** hoặc nhập từ file đã có của nhà cung cấp chữ ký số.

## Cài đặt thư viện

```bash
pip install -r requirements.txt
```

## Chạy

```bash
python pdf_sign.py
```

## Giao diện

- **Thanh trên**: 5 bước làm việc, đánh số theo thứ tự. Nút của bước nên làm tiếp theo được tô màu nổi bật.
  Góc bên phải là nút **Cài đặt**.
- **Thanh dưới**: thông báo trạng thái, chuyển trang (‹ số trang ›) và zoom (− thanh trượt +, *Vừa khung*).

## Các bước

1. **Mở file** (Ctrl+O): chọn file PDF. Khi chưa mở file, có thể kéo thả file PDF từ Explorer
   vào vùng giữa cửa sổ, hoặc bấm vào vùng đó để mở.
2. **Nhập chữ ký PNG**: chọn ảnh chữ ký (nên dùng PNG nền trong suốt). Ảnh chỉ hiện ở dạng
   thu nhỏ trên thanh công cụ, chưa đặt vào trang.
3. **Đặt chữ ký**: bấm nút rồi bấm vào vị trí trên trang để đặt chữ ký lên PDF. Có thể đặt nhiều lần.
   - Kéo chữ ký để di chuyển.
   - Kéo ô vuông xanh ở góc dưới-phải để phóng to hoặc thu nhỏ (giữ nguyên tỉ lệ).
   - Bấm **Delete** hoặc chuột phải → *Xoá chữ ký* để xoá.
   - Khi tài liệu có từ 2 ảnh chữ ký trở lên (tính trên toàn bộ các trang), chương trình cảnh báo
     để kiểm tra lại và xoá bớt.
4. **Ký số**: ký số bằng chứng chỉ trong file `.p12` (xem bên dưới).
5. **Lưu file đã ký** (Ctrl+S): chỉ chèn **hình ảnh** chữ ký vào trang, không phải chữ ký số.
   File lưu ra file mới (mặc định `<tên>_signed.pdf`), file gốc giữ nguyên.

## Mục Cài đặt (nút *Cài đặt* hoặc Ctrl+,)

Nơi quản lý **chứng chỉ ký số**:

- **Tạo chứng chỉ mới**: tự sinh khoá RSA và chứng chỉ tự ký, nhập họ và tên (bắt buộc),
  tổ chức, phòng ban, quốc gia, email, thời hạn (1–10 năm) và mật khẩu bảo vệ file `.p12`.
  Kết quả là 2 file trong `%USERPROFILE%\.pdf_sign\certs\`:
  - `<tên>.p12` — khoá bí mật, dùng để ký (bảo mật như thẻ ngân hàng, **không chia sẻ**);
  - `<tên>.crt` — bản chứng chỉ, gửi cho người kiểm tra/chữ ký số đối tác.
- **Nhập file .p12**: dùng lại chứng chỉ đã có của nhà cung cấp chữ ký số (VNPT-CA, Viettel-CA,
  FPT-CA, BKAV-CA...) nếu họ cấp file `.p12`/`.pfx` thay vì USB token.
- **Xoá**: bỏ chứng chỉ khỏi danh sách (hỏi thêm nếu muốn xoá cả file đã tạo trong chương trình).

Danh sách chứng chỉ và các lựa chọn ký (lý do, nơi ký, dấu thời gian) được lưu ở
`%USERPROFILE%\.pdf_sign.json`. **Mật khẩu không bao giờ được lưu.**

## Phím tắt

| Phím | Chức năng |
|---|---|
| ← / → , PageUp / PageDown | Chuyển trang |
| Ctrl + / Ctrl − / Ctrl + lăn chuột | Phóng to / thu nhỏ |
| Ctrl+0 | Vừa khung |
| Lăn chuột / Shift + lăn chuột | Cuộn dọc / cuộn ngang (khi cả trang đã hiển thị, ví dụ ở chế độ Vừa khung: chuyển trang) |
| Ctrl+, | Mở mục Cài đặt |
| Esc | Huỷ đặt chữ ký / bỏ chọn |

## Ký số (bước 4)

1. Làm bước 1–3 để đặt hình chữ ký lên trang. Chữ ký số sẽ hiển thị đúng tại vị trí đó.
   Tài liệu phải có **đúng 1** hình chữ ký; nếu có từ 2 hình trở lên, chương trình cảnh báo
   để kiểm tra lại và xoá bớt.
2. Bấm **4 · Ký số**. Nếu chưa có chứng chỉ, chương trình mở ngay mục **Cài đặt** để tạo.
3. Chọn chứng chỉ, nhập **mật khẩu** (nếu chứng chỉ có mật khẩu), điền lý do và nơi ký
   (không bắt buộc), bấm **Ký** rồi chọn nơi lưu file.

Ghi chú:
- Hình chữ ký gồm ảnh PNG, kèm tên người ký và thời gian ký (có thể tắt).
- Có thể ký nhiều lần (nhiều người ký): mở file đã ký, đặt chữ ký mới rồi ký tiếp. Chữ ký cũ vẫn
  giữ hiệu lực. Không chèn ảnh thường vào file đã ký số vì sẽ làm chữ ký cũ mất hiệu lực.
- Chứng chỉ **tự tạo** trong Cài đặt là chứng chỉ tự ký: chữ ký hợp lệ về kỹ thuật, nhưng người
  mở file phải tự tin chứng chỉ này (bấm *Chi tiết chữ ký* rồi thêm chứng chỉ vào danh sách tin cậy).
  Muốn người lạ tin ngay, hãy nhập file `.p12` do CA cấp.
- Máy chủ dấu thời gian (TSA) là tuỳ chọn, giúp chứng minh thời điểm ký.
