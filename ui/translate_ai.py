import sys
import io
import os

path = r'd:\Projects\Automation-Ecosystem\ui\src\pages\SettingsAI.tsx'
with io.open(path, 'r', encoding='utf-8') as f:
    text = f.read()

replacements = [
    ("PageHeader title=\"AI Providers & API Keys\" subtitle=\"Manage encrypted provider keys and model routing.\"", "PageHeader title=\"AI Providers & API Keys\" subtitle=\"Quản lý mã khóa bảo mật và định tuyến mô hình AI.\""),
    ("<div className=\"page-title\">AI Providers & API Keys</div>", "<div className=\"page-title\">Nhà Cung Cấp AI & API Keys</div>"),
    ("<div className=\"page-subtitle\">Encrypted local key store for AI routing, fallback, and model defaults.</div>", "<div className=\"page-subtitle\">Lưu trữ khóa bảo mật cục bộ dùng cho định tuyến AI, fallback và thiết lập mô hình mặc định.</div>"),
    ("PageHeader title=\"AI Providers & API Keys\" />", "PageHeader title=\"Nhà Cung Cấp AI & API Keys\" />"),
    ("Add Provider", "Thêm Nhà Cung Cấp"),
    ("'Provider saved.'", "'Đã lưu nhà cung cấp.'"),
    ("'Provider and display name are required.'", "'Tên nhà cung cấp và tên hiển thị là bắt buộc.'"),
    ("'Provider created.'", "'Đã tạo nhà cung cấp.'"),
    ("'Key label and API key are required.'", "'Nhãn khóa và API key là bắt buộc.'"),
    ("'API key saved. Raw key was encrypted and cleared from the form.'", "'Đã lưu API key. Khóa gốc đã được mã hóa và xóa khỏi biểu mẫu.'"),
    ("'API key updated. Raw replacement key was encrypted and cleared.'", "'Đã cập nhật API key. Khóa mới đã được mã hóa và xóa khỏi biểu mẫu.'"),
    ("'Model name and display name are required.'", "'Tên mô hình và tên hiển thị là bắt buộc.'"),
    ("'Model saved.'", "'Đã lưu mô hình.'"),
    ("'Model updated.'", "'Đã cập nhật mô hình.'"),
    ("'Reply with one short sentence confirming this provider is working.'", "'Trả lời bằng một câu ngắn gọn để xác nhận nhà cung cấp này đang hoạt động.'"),
    ("Test succeeded in", "Kiểm tra thành công trong"),
    ("Test Routing", "Kiểm Tra Định Tuyến"),
    ("Testing' : 'Test'", "Đang kiểm tra' : 'Kiểm tra'"),
    (">Auto routing<", ">Tự động định tuyến<"),
    (">Default enabled model<", ">Mô hình mặc định đã bật<"),
    (">Auto key fallback<", ">Tự động fallback khóa<"),
    ("Provider enabled", "Bật nhà cung cấp"),
    (">Enabled<", ">Đã bật<"),
    (">Disabled<", ">Đã tắt<"),
    (" keys</span>", " khóa</span>"),
    (" models</span>", " mô hình</span>"),
    ("priority ", "ưu tiên "),
    ("Delete provider ${provider.display_name}? This also deletes its keys and models.", "Xóa nhà cung cấp ${provider.display_name}? Thao tác này cũng xóa các khóa và mô hình của nó."),
    ("FieldLabel>Provider</FieldLabel", "FieldLabel>Nhà cung cấp</FieldLabel"),
    ("FieldLabel>Display name</FieldLabel", "FieldLabel>Tên hiển thị</FieldLabel"),
    ("FieldLabel>Base URL</FieldLabel", "FieldLabel>URL Gốc</FieldLabel"),
    ("FieldLabel>Priority</FieldLabel", "FieldLabel>Độ ưu tiên</FieldLabel"),
    ("FieldLabel>Model</FieldLabel", "FieldLabel>Mô hình</FieldLabel"),
    ("FieldLabel>Key</FieldLabel", "FieldLabel>Khóa</FieldLabel"),
    ("placeholder=\"Optional\"", "placeholder=\"Tùy chọn\""),
    ("No enabled key configured", "Chưa cấu hình khóa nào được bật"),
    ("No default model", "Chưa có mô hình mặc định"),
    (">Keys<", ">Khóa (Keys)<"),
    (">Models<", ">Mô hình (Models)<"),
    ("<th>Label</th>", "<th>Nhãn</th>"),
    ("<th>Preview</th>", "<th>Xem trước</th>"),
    ("<th>Enabled</th>", "<th>Bật</th>"),
    ("<th>Priority</th>", "<th>Độ ưu tiên</th>"),
    ("<th>Last used</th>", "<th>Lần dùng cuối</th>"),
    ("<th>Last error</th>", "<th>Lỗi cuối</th>"),
    ("<th>Failures</th>", "<th>Lỗi</th>"),
    ("<th>Replace key</th>", "<th>Thay khóa</th>"),
    ("<th>Model name</th>", "<th>Tên mô hình</th>"),
    ("<th>Display name</th>", "<th>Tên hiển thị</th>"),
    ("<th>Default</th>", "<th>Mặc định</th>"),
    ("<th>Max tokens</th>", "<th>Token tối đa</th>"),
    ("<th>Temperature</th>", "<th>Temperature</th>"),
    ("Key enabled", "Bật khóa"),
    ("placeholder=\"New key\"", "placeholder=\"Khóa mới\""),
    ("Delete key ${key.label}?", "Xóa khóa ${key.label}?"),
    ("Encrypted after save", "Sẽ mã hóa sau khi lưu"),
    ("New key enabled", "Bật khóa mới"),
    ("Raw key is never returned after save", "Khóa gốc không bao giờ được trả về sau khi lưu"),
    ("placeholder=\"API key\"", "placeholder=\"API key\""),
    ("Model enabled", "Bật mô hình"),
    ("Default model", "Mô hình mặc định"),
    ("placeholder=\"Any\"", "placeholder=\"Bất kỳ\""),
    ("placeholder=\"Call\"", "placeholder=\"Gọi API\""),
    ("Delete model ${model.model_name}?", "Xóa mô hình ${model.model_name}?"),
    ("New model enabled", "Bật mô hình mới"),
    ("New model default", "Mô hình mới mặc định"),
    (">Save<", ">Lưu<"),
    (">Delete<", ">Xóa<")
]

for old_s, new_s in replacements:
    text = text.replace(old_s, new_s)

with io.open(path, 'w', encoding='utf-8') as f:
    f.write(text)
print('Done translating SettingsAI.tsx')
