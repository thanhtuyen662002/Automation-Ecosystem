# 📖 Hướng Dẫn Sử Dụng — Automation Ecosystem

> **Dành cho:** CEO / Quản lý không có kỹ thuật  
> **Phiên bản:** 1.0 — Tháng 5 năm 2026

---

## 1. Giới Thiệu

**Automation Ecosystem** là hệ thống tự động hóa marketing nội dung.  
Nó hoạt động như một **đội nhân viên kỹ thuật số** — tự động tìm nội dung, quyết định có nên đăng hay không, và quản lý các tài khoản mạng xã hội của bạn.

**Bạn chỉ cần làm một việc:** xem quyết định của hệ thống và phê duyệt hoặc từ chối.

Hệ thống trả lời 3 câu hỏi mỗi ngày:
- 🟥 **Đang xảy ra gì?** (Cảnh báo, sự cố)
- 🟨 **Cần làm gì?** (Quyết định đang chờ bạn)
- 🟩 **Kết quả là gì?** (Nội dung đã đăng, thu nhập kỳ vọng)

---

## 2. Cài Đặt Lần Đầu

### Bước 1 — Khởi động máy chủ (làm 1 lần)

Nhờ kỹ thuật viên chạy:
```
uvicorn api.main:app --reload
```
Sau đó mở trình duyệt tại `http://localhost:5173`

### Bước 2 — Đăng nhập

1. Nhập **Tên Tài Khoản** (ví dụ: `@your_name`)
2. Nhập **License Key** (ví dụ: `AE-XXXX-YYYY-ZZZZ`)
3. Nhấn **⚡ Đăng Nhập**

> ⚠️ License key được cấp bởi quản trị viên. Nếu quên, liên hệ người cài đặt hệ thống.

### Bước 3 — Kiểm tra cài đặt cơ bản

Vào **Settings → General** và xác nhận:
- **Ngôn ngữ:** Tiếng Việt
- **Execution Engine:** BẬT ✓
- **Tự động duyệt:** TẮT (để bạn kiểm soát thủ công ban đầu)

---

## 3. Các Tính Năng Chính

### 🏠 Command Dashboard (Bảng điều khiển chính)

Đây là màn hình bạn mở **đầu tiên mỗi ngày**.

**3 vùng cần chú ý:**

#### 🔴 PHẦN 1: HỆ THỐNG
Hiện cảnh báo nghiêm trọng nhất. Ví dụ:
- "Máy thực thi đang TẮT"
→ Nhấn **⚡ Bật Máy Ngay** để khôi phục

#### 📄 PHẦN 2: NỘI DUNG — Đang chờ duyệt
Danh sách nội dung hệ thống muốn đăng. Mỗi khối hiển thị:

| Thông tin | Ý nghĩa |
|-----------|---------|
| **Tiêu đề** | Nội dung sẽ được đăng |
| **EV (Expected Value)** | Thu nhập kỳ vọng ($) |
| **Độ Tin Cậy** | Hệ thống tự tin bao nhiêu % |
| **Rủi ro** | Thấp / Trung bình / Cao |
| **Nếu bỏ qua** | Điều gì xảy ra nếu bạn không làm gì |

**Hành động:**
- ✅ **Đăng Ngay** → nội dung được lên lịch
- ❌ **Từ chối** → nội dung bị loại bỏ

#### ⚡ PHẦN 3: ĐỘI — Tài khoản cần chú ý
Tài khoản mạng xã hội đang gặp vấn đề.

**Hành động:**
- 🔒 **Đóng Băng Ngay** → dừng hoàn toàn tài khoản (khi có nguy hiểm)
- 👁 **Chỉ Giám Sát** → theo dõi, chưa làm gì

---

### 📋 Hàng Chờ Nội Dung (Content Queue)

Xem **tất cả nội dung** trong hệ thống:

- **⏳ Chờ Duyệt** — nội dung cần bạn quyết định
- **✓ Đã Đăng** — nội dung đã được xuất bản
- **✗ Đã Từ Chối** — nội dung bị loại bỏ

**Nút "Duyệt X Giá Trị Cao":** Phê duyệt hàng loạt tất cả nội dung có EV cao nhất.  
→ Dùng khi bạn tin tưởng hệ thống và muốn tiết kiệm thời gian.

---

### ⚕️ Sức Khỏe Đội (Fleet Health)

Xem trạng thái **từng tài khoản** mạng xã hội:

| Chỉ số | Ý nghĩa |
|--------|---------|
| **Trust** | Mức độ uy tín với nền tảng (càng cao càng tốt) |
| **Mệt Mỏi** | Tần suất hoạt động (>70% = cần nghỉ) |
| **Bất Thường** | Số lần hành vi lạ bị phát hiện |
| **Pha** | WARM_UP / NORMAL / COOLDOWN / RAMP_UP |

**Màu sắc:**
- 🟢 Xanh = ổn định
- 🟡 Vàng = cần theo dõi
- 🔴 Đỏ = cần hành động ngay

---

### ⚙️ Cài Đặt (Settings → General)

Tất cả nút bật/tắt quan trọng nằm ở đây:

| Cài đặt | Mô tả |
|---------|-------|
| **Execution Engine** | BẬT/TẮT toàn bộ hệ thống |
| **Tự động duyệt** | Hệ thống tự đăng không cần bạn xét duyệt |
| **Ngôn ngữ** | Tiếng Việt / English |
| **Giao diện** | Dark / Light / Neon |

> ⚠️ **KHÔNG bật Tự động duyệt** khi bạn mới bắt đầu dùng hệ thống.

---

## 4. Quy Trình Hàng Ngày (5 phút/ngày)

```
Sáng:
  1. Mở Command Dashboard
  2. Đọc phần "HỆ THỐNG" — có cảnh báo gì không?
  3. Đọc phần "NỘI DUNG" — duyệt những nội dung EV cao, tin cậy cao
  4. Đọc phần "ĐỘI" — có tài khoản nguy hiểm không?

Chiều (tùy chọn):
  5. Mở Fleet Health — kiểm tra sức khỏe đội
  6. Mở Content Queue — xem nội dung đã đăng

Tuần 1 lần:
  7. Xem báo cáo doanh thu trong CEO Brain
```

---

## 5. Lưu Ý An Toàn (QUAN TRỌNG)

> ⚠️ Đây là hệ thống tự động. Sử dụng như trợ lý, không phải autopilot.

### ❌ KHÔNG nên:
- Duyệt tất cả mọi thứ mà không đọc
- Bật Tự động duyệt khi chưa hiểu rõ hệ thống
- Bỏ qua cảnh báo màu đỏ

### ✅ NÊN:
- Đọc **lý do** (reason) trước khi duyệt
- Chú ý **Độ Tin Cậy** — dưới 60% nên xem lại
- Chú ý **Cờ Rủi Ro** — nếu có cờ đỏ, đọc kỹ trước khi duyệt
- Đóng băng tài khoản ngay khi thấy cảnh báo đỏ

---

## 6. Xử Lý Sự Cố

### Không thấy dữ liệu / màn hình trống
→ Máy chủ backend chưa chạy. Nhờ kỹ thuật viên kiểm tra.

### Đăng nhập thất bại
→ Kiểm tra license key (không có dấu cách thừa)
→ Liên hệ quản trị viên nếu key không hoạt động

### Lỗi "Không thể tải dữ liệu"
→ Nhấn nút **Thử lại** hoặc **Làm Mới** trên trang
→ Nếu vẫn lỗi: tắt và khởi động lại máy chủ backend

### Dashboard trống, không có quyết định
→ Hệ thống chạy bình thường — không có gì cần làm!
→ Nội dung queue trống = hệ thống chưa xử lý batch mới

### Tài khoản bị đóng băng nhầm
→ Vào Fleet Health → tìm tài khoản → nhấn "Xóa Cooldown"

---

## 7. Bảng Tóm Tắt Nhanh

| Trang | Dùng để làm gì | Tần suất |
|-------|---------------|----------|
| Command Dashboard | Quyết định hàng ngày | **Mỗi sáng** |
| Content Queue | Duyệt chi tiết nội dung | Khi có alert |
| Fleet Health | Kiểm tra sức khỏe tài khoản | 2 lần/tuần |
| Settings | Cấu hình hệ thống | Hiếm khi |

---

*Phiên bản tài liệu: 1.0 | Cập nhật: 2026-05-11*
