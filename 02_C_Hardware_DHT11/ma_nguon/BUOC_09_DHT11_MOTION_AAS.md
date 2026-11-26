# Bước 9 — Kiểm thử motor hoạt động với firmware DHT11

Giữ firmware MotorC_DHT11Telemetry đã nạp và đã đọc DHT thành công ở bước 8. Không nạp lại firmware. Thay đổi bước này chỉ ở Python: dht_basyx_check.py có thêm --motion.

## Chạy thật

ESP32 cắm USB, DHT11 giữ GPIO7/3V3/GND. Cấp nguồn motor theo cách đấu đã thử thành công, đặt motor ổn định và để trục quay tự do. Đóng Serial Monitor và Python khác giữ COM3; MQTT và BaSyx vẫn chạy.

```powershell
& 'C:\Users\Admin\IIoT_AAS_Demo\.venv312\Scripts\python.exe' -B 'C:\Users\Admin\Documents\ChatGPT\IoT\AAS_Demo_Workspace\motor_c\dht_basyx_check.py' --port COM3 --motion
```

Lệnh trên thực sự điều khiển motor qua USB: thuận (lệnh 512 bước) → dừng sau khoảng 1,25 giây → chờ 3 giây sau xác nhận dừng → nghịch 512 bước → tự hoàn tất. Thu 40 bản tin (~20 giây), sau đó chờ stale của hai submodel và tự kết thúc. Ctrl+C dừng phép thử và cố gửi s nếu motor có thể còn chạy, không bảo đảm lệnh dừng đến thiết bị nếu USB mất kết nối.

Không có --motion, chương trình vẫn chỉ đọc như bước 8. Không dùng hardware_basyx_check.py --motion với firmware DHT kết hợp vì validator mặc định của chương trình đó là schema motor cũ.

## Điều kiện DHT MOTION AAS PASS

- Các điều kiện DHT AAS PASS vẫn giữ nguyên: transport khớp, không lỗi cảm biến/HTTP, đọc lại ít nhất ba attempt cảm biến hợp lệ khác nhau, motor có ít nhất ba trạng thái fresh, cả hai submodel đọc lại stale cuối.
- Motor có telemetry và AAS đủ bốn pha theo thứ tự: thuận → dừng giữa chừng → nghịch → hoàn tất; bộ đếm bước đúng ý nghĩa phát lệnh.
- Có ít nhất một attempt DHT hợp lệ mới, được đọc lại ở AAS với Freshness=fresh, có read uptime sau mẫu motor hoàn tất và cùng BridgeRunId. Chỉ gửi lại số đo DHT trước chuyển động không đủ để đạt.

Firmware chỉ đọc DHT khi motor nghỉ; trong lúc phát bước, telemetry vẫn mang số đo DHT gần nhất kèm attempt/thời điểm cũ. Không mô tả là đo DHT liên tục trong từng bước quay. Kiểm thử này xác nhận cùng firmware có thể thực hiện kịch bản motor và tiếp tục đọc cảm biến sau chuyển động.

Lệnh quay vẫn đi qua USB Serial, MQTT truyền dữ liệu giám sát. Không có encoder, nên log không xác nhận góc/tốc độ/chuyển động vật lý. Người dùng cần quan sát và báo riêng motor có quay hai chiều, dừng giữa chừng đúng hay không.

## Bằng chứng

Trong evidence/dht-basyx-...: commands.jsonl, verification.json (thêm motion và combined_motion), motor/ và environment/ với log ghi/đọc AAS, cùng transport_evidence trỏ tới dữ liệu Serial/MQTT. Giá trị cờ physical_motion_verified vẫn false trong báo cáo tự động; quan sát người dùng là bằng chứng riêng.

Code đã qua kiểm thử offline; chưa chạy kịch bản kết hợp trên phần cứng trong phiên trợ lý. Các kết quả motor không DHT và DHT khi idle ở lượt trước không thay thế kết quả mới này.
