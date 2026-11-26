# Nguồn gốc và phạm vi

Các file Python trong thư mục này được sao chép nguyên byte từ workspace ngày 22/11/2026, không sửa mã để đóng gói. Xem SOURCE_SHA256.json. Đây là mã hiện có tại thời điểm bàn giao; không tuyên bố hash này đã được ghi tại thời điểm lượt thử trước.

Motor C là phần mở rộng riêng, không gán tác giả phần này cho nguồn A/B Dương cung cấp. demo.py là bộ điều phối workspace dùng chung; run_c.py dùng Lock, Job, docker, wait và cấu hình của nó.

requirements.txt, project.example.json và HUONG_DAN_CHAY_C.md được soạn bổ sung cho bàn giao. Chỉ paho-mqtt là dependency bên ngoài của đường chạy C; mã dùng Windows msvcrt và Windows Job. Python đã dùng: 3.12.10, paho-mqtt 2.1.0. Không có lockfile đầy đủ cho mọi chức năng A/B của demo.py.

Không kèm checkpoint/state/lock đang dùng, venv, backup broker hoặc bí mật. Không kèm provision_broker_local.py để tránh hiểu nhầm là cần dựng lại broker. Máy nhận cần chuẩn bị hạ tầng tương thích riêng; gói này chưa được nghiệm thu như môi trường cài mới tự động.
