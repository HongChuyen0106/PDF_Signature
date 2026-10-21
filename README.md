# UD Trucks PDF Signature

Trình xem PDF đơn giản, cho phép chèn chữ ký (ảnh PNG) vào bất kỳ vị trí nào trên trang
và **ký số bằng USB token** (chuẩn PAdES, kiểm tra được trong Adobe Reader).
Giao diện theo phong cách Windows 11, tự đổi sáng/tối theo cài đặt của Windows.

## Cài đặt

```bash
pip install -r requirements.txt
```

## Chạy

```bash
python pdf_sign.py
```

## Giao diện

- **Thanh trên**: 5 bước làm việc, đánh số theo thứ tự. Nút của bước nên làm tiếp theo được tô màu nổi bật.
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
4. **Ký số USB Token**: ký số có giá trị pháp lý (xem bên dưới).
5. **Lưu file đã ký** (Ctrl+S): chỉ chèn **hình ảnh** chữ ký vào trang, không phải chữ ký số.
   File lưu ra file mới (mặc định `<tên>_signed.pdf`), file gốc giữ nguyên.

## Phím tắt

| Phím | Chức năng |
|---|---|
| ← / → , PageUp / PageDown | Chuyển trang |
| Ctrl + / Ctrl − / Ctrl + lăn chuột | Phóng to / thu nhỏ |
| Ctrl+0 | Vừa khung |
| Lăn chuột / Shift + lăn chuột | Cuộn dọc / cuộn ngang (khi cả trang đã hiển thị, ví dụ ở chế độ Vừa khung: chuyển trang) |
| Esc | Huỷ đặt chữ ký / bỏ chọn |

## Ký số bằng USB token (bước 4)

Yêu cầu: cắm USB token và cài phần mềm/driver của nhà cung cấp chữ ký số (VNPT-CA, Viettel-CA,
FPT-CA, BKAV-CA, CA2, SafeNet, ePass...). Chương trình dùng thư viện PKCS#11 (file `.dll`)
mà driver cài vào `C:\Windows\System32`.

1. Làm bước 1–3 để đặt hình chữ ký lên trang. Chữ ký số sẽ hiển thị đúng tại vị trí đó.
2. Bấm **4 · Ký số USB Token**. Tài liệu phải có **đúng 1** hình chữ ký; nếu có từ 2 hình trở lên,
   chương trình cảnh báo để kiểm tra lại và xoá bớt.
3. Chọn driver token (chương trình tự dò; nếu không thấy, bấm *Chọn file...* để chọn file `.dll`),
   nhập **mã PIN**, bấm **Đọc chứng thư** và chọn chứng thư.
4. Điền lý do, nơi ký (không bắt buộc), bấm **Ký** và chọn nơi lưu file.

Ghi chú:
- Hình chữ ký gồm ảnh PNG, kèm tên người ký và thời gian ký (có thể tắt).
- Có thể ký nhiều lần (nhiều người ký): mở file đã ký, đặt chữ ký mới rồi ký tiếp. Chữ ký cũ vẫn
  giữ hiệu lực. Không chèn ảnh thường vào file đã ký số vì sẽ làm chữ ký cũ mất hiệu lực.
- Máy chủ dấu thời gian (TSA) là tuỳ chọn, giúp chứng minh thời điểm ký.
- Mã PIN không bao giờ được lưu. Các lựa chọn khác được nhớ trong `%USERPROFILE%\.pdf_sign.json`.
