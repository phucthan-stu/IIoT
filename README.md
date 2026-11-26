# IIoT — AAS Motor Demo

Mã nguồn bàn giao để trao đổi với anh Dương: A/B mô phỏng, C mô phỏng MQTT và C phần cứng ESP32-C3 + ULN2003 + 28BYJ-48 + DHT11.

## Bắt đầu ở đâu?

| Phần | Thư mục / hướng dẫn |
|---|---|
| A/B (mã nguồn Dương, giữ ghi chú nguồn gốc) | [Hướng dẫn A/B](01_ABC_2026-11-22/01_AB_Duong/01_Huong_dan_chay_lai.md) |
| C mô phỏng | [Hướng dẫn C](01_ABC_2026-11-22/03_C_Ma_nguon/HUONG_DAN_CHAY_C.md) |
| C phần cứng và DHT11 | [Mã nguồn](02_C_Hardware_DHT11/ma_nguon/) |
| Firmware DHT11 kết hợp motor | [Arduino sketch](02_C_Hardware_DHT11/ma_nguon/firmware/MotorC_DHT11Telemetry/MotorC_DHT11Telemetry.ino) |

Snapshot lấy từ gói bàn giao ngày 25/11/2026. Giữ cấu trúc thư mục và mã nguồn để đối chiếu; các tài liệu gốc có thể nhắc đến log/ảnh trong gói ZIP bàn giao. Repo này tập trung vào mã nguồn, không chứa toàn bộ log, ảnh và báo cáo của gói ZIP.

## Phạm vi và giới hạn

- A/B: simulator Modbus TCP / OPC UA → adapter → BaSyx AAS.
- C phần cứng: ESP32 → USB Serial → Python → MQTT → adapter → BaSyx.
- Lệnh điều khiển motor hiện đi qua USB Serial; không tuyên bố điều khiển qua MQTT.
- Bộ đếm bước phản ánh lệnh đã phát; không có encoder để xác nhận góc/tốc độ cơ khí.
- DHT11 đọc khi motor nghỉ; khi motor chạy, telemetry có thể mang số đo gần nhất cùng thông tin thời điểm.
- Fresh/stale diễn tả độ mới dữ liệu, không chứng minh thiết bị đang quay.
- Cấu hình local chưa có xác thực/TLS end-to-end.
- Dashboard/web chưa có trong snapshot này.

## Chạy trên máy khác

Cần Python và dependencies trong requirements.txt của từng phần, Arduino IDE/thư viện DHT phù hợp để nạp firmware, MQTT broker và BaSyx đang hoạt động. Repo chưa cung cấp bộ dựng hạ tầng tự động.

Các tài liệu gốc có đường dẫn máy tác giả, COM3, port loopback và kiểm tra broker riêng. Phải chỉnh theo máy nhận; không chạy nguyên các đường dẫn tuyệt đối trong ví dụ. C mô phỏng dùng project.example.json, sao chép thành project.json rồi điền đường dẫn thật theo hướng dẫn.

Ví dụ chạy phần C phần cứng, từ thư mục gốc repo, sau khi chuẩn bị Python/dependencies và hạ tầng:

```powershell
cd .\02_C_Hardware_DHT11\ma_nguon
python -m pip install -r requirements.txt
python -B dht_basyx_check.py --port COM3
```

Đọc [bước 8](02_C_Hardware_DHT11/ma_nguon/BUOC_08_DHT11_AAS.md) và [bước 9](02_C_Hardware_DHT11/ma_nguon/BUOC_09_DHT11_MOTION_AAS.md) trước khi dùng thêm --motion vì tùy chọn đó phát lệnh chuyển động thật.

## Kiểm tra bản đưa lên GitHub

Ngày 26/11/2026: chạy 19 unittest offline của phần C phần cứng bằng Python 3.12 trong môi trường đã có dependencies, tất cả đạt. Đây là kiểm thử logic offline; không chạy lại phần cứng, MQTT/BaSyx hay biên dịch Arduino trong lần đưa lên GitHub.

```powershell
python -B -m unittest discover -s .\02_C_Hardware_DHT11\ma_nguon -p "test_*.py"
```

Không đưa mật khẩu, token, môi trường Python hoặc cấu hình cá nhân vào repo. Giữ NGUON_GOC.md ở các thư mục A/B và C để đối chiếu nguồn.
