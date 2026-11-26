# Bước 8 — DHT11 + motor → Serial → MQTT → AAS

## Chuẩn bị và chạy

Đấu DHT11 đã chốt: DATA → GPIO7, VCC → 3V3, GND → GND. Motor giữ GPIO3–6. Dùng thư viện DHT sensor library của Adafruit và Adafruit Unified Sensor đã cài cho phép thử DHT riêng.

1. Đóng Python đang giữ COM3. Mở `firmware/MotorC_DHT11Telemetry/MotorC_DHT11Telemetry.ino` trong Arduino IDE. Chọn board/USB như lượt thành công trước, COM3, Upload. Đây là firmware mới thay DHT11_Test đang nạp; giữ file firmware cũ để có thể nạp lại.
2. Sau Upload thành công, đóng Serial Monitor/Plotter. Broker và BaSyx phải đang chạy. Chưa cần cấp nguồn motor; phép thử này chỉ đọc dữ liệu, không gửi lệnh f/r/s.
3. Chạy trong PowerShell:

```powershell
& 'C:\Users\Admin\IIoT_AAS_Demo\.venv312\Scripts\python.exe' -B 'C:\Users\Admin\Documents\ChatGPT\IoT\AAS_Demo_Workspace\motor_c\dht_basyx_check.py' --port COM3
```

Thu 40 mẫu (~20 giây), sau đó ngừng gửi và xác nhận stale của cả hai submodel. Chương trình tự kết thúc. Chỉ chốt đạt nếu có `DHT AAS PASS`; không chỉ nhìn `SERIAL MQTT PASS`.

4. Mở http://localhost:3000/aasviewer → refresh → MotorCHardwareAAS → EnvironmentTelemetry. Sau khi chương trình kết thúc, Freshness=stale là đúng. HardwareTelemetry vẫn hiển thị dữ liệu motor.

Không chạy script kiểm thử motor cũ trực tiếp với firmware kết hợp: validator mặc định vẫn dành cho 8 trường motor; firmware mới thêm environment. Lệnh dht_basyx_check.py chọn đúng validator kết hợp. Nếu cần quay lại phép thử --motion cũ, nạp lại MotorC_SerialTelemetry trước; chưa nghiệm thu đồng thời motor chuyển động và đọc DHT trong phiên này.

## Firmware

Motor telemetry vẫn gửi khoảng 500 ms. DHT đọc mỗi 2500 ms khi motor nghỉ. Lệnh f/r/s của firmware được giữ, nhưng dht_basyx_check không phát các lệnh này. Hàm đọc DHT của thư viện có chờ và xử lý timing; firmware hoãn đọc khi `running=true` để tránh chặn vòng phát bước. Do đó chu kỳ cảm biến có thể dài hơn khi motor hoạt động, phải xét tuổi số đo.

Mỗi bản tin có object environment gồm attempt, valid, read_uptime_ms, temperature_c, humidity_pct. Attempt tăng mỗi lần thử đọc, kể cả lần lỗi. Những bản tin motor gửi giữa hai lần đọc giữ nguyên object environment. Chưa đọc: attempt=0, valid=false, giá trị null. Đọc lỗi/ngoài phạm vi kiểm tra phần mềm: valid=false và cả hai giá trị null; không phát NaN hay số 0 giả làm phép đo. Kiểm tra phần mềm dùng 0..50 °C và 0..100 %RH, không có nghĩa cảm biến đã được hiệu chuẩn hay bảo đảm độ chính xác toàn miền.

Thư viện chính thức dùng MIN_INTERVAL=2000 ms; lựa chọn 2500 ms có khoảng dư: https://github.com/adafruit/DHT-sensor-library/blob/master/DHT.cpp

## AAS và tính hợp lệ

Thêm submodel `urn:university:aas-demo:submodel:motor-c-stepper:environment-telemetry`, idShort EnvironmentTelemetry vào MotorCHardwareAAS bằng POST submodel và POST submodel-refs. Không tạo shell khác, không xóa/ghi đè submodel motor, A/B hoặc hồ sơ mô phỏng. Kiểm tra metadata có sẵn trước khi thêm. Nếu chạy lại, không thêm trùng reference.

| Thuộc tính | Ý nghĩa |
|---|---|
| AmbientTemperatureC | Nhiệt độ không khí tại cảm biến, °C; KHÔNG phải nhiệt độ motor |
| RelativeHumidityPct | Độ ẩm tương đối, %RH |
| HasSample | Đã từng nhận số đo hợp lệ trong lượt này; false thì số khởi tạo không phải phép đo |
| LatestAttemptValid | Lần thử đọc cảm biến mới nhất có hợp lệ không |
| Sequence / LastValidAttempt | Lần thử mới nhất / lần thử của số đo tốt đang giữ |
| ReadUptimeMs / LastValidReadUptimeMs | Uptime ESP32 sau lần đọc mới nhất / lần đọc tốt đang giữ; không phải UTC |
| LastReceivedAt | UTC khi MQTT lần đầu chấp nhận attempt hợp lệ mới; gửi lại cùng attempt không cập nhật |
| Freshness | waiting, fresh, error hoặc stale |
| StaleAfterSeconds | 6 giây đối với tuổi số đo cảm biến |
| BridgeRunId | Mã lượt Python; không phải boot ID ESP32 |

Ngừng nhận MQTT 2 giây cũng buộc sensor stale. Khi bản tin motor vẫn đến nhưng DHT không có attempt mới, số đo cảm biến stale sau 6 giây; không được làm mới bởi việc đọc GET hay gửi lại số cũ. Tuổi số đo gồm chênh uptime trong payload, tuổi từ lúc PC nhận đến MQTT và thời gian monotonic sau tiếp nhận. Chưa có xác thực nguồn hoặc UTC đo tại ESP32.

Khi DHT lỗi, giữ số đo tốt cuối cùng cùng thời điểm cũ, LatestAttemptValid=false, Freshness=error. Nếu chưa từng có số đo tốt, HasSample=false. Mất telemetry chuyển stale. Vì cờ trạng thái lưu trên server không tự đổi khi toàn bộ adapter tắt, người xem còn cần xét tuổi LastReceivedAt.

Endpoint thêm reference theo AAS API: https://github.com/admin-shell-io/aas-specs-api/blob/main/Part2-API-Schemas/openapi.yaml

## Kiểm thử và bằng chứng

Đã đạt 16 kiểm thử offline, bao gồm 9 kiểm thử motor cũ và 7 kiểm thử môi trường: schema; gửi lại sensor reading không làm mới; lỗi giữ giá trị tốt; waiting/mất transport; attempt không đổi mà giá trị đổi bị từ chối; tuổi số đo và trễ bridge; thêm submodel/reference không làm mất motor và chạy lại không tạo trùng.

Chưa biên dịch bằng toolchain ESP32 hoặc chạy firmware kết hợp trên phần cứng trong phiên trợ lý. Arduino IDE phải Upload thành công; sau đó chạy phép thử trên máy người dùng. Kết quả DHT11_Test trước đó chỉ chứng minh cảm biến đọc được qua Serial, không thay thế kết quả tích hợp.

PASS thực tế yêu cầu 40 mẫu transport khớp, ít nhất 3 attempt DHT hợp lệ khác nhau đã được đọc lại đúng từ AAS, ít nhất 3 trạng thái motor fresh đã đọc lại, không lỗi cảm biến trong lượt, không lỗi HTTP, xác nhận stale cuối cả hai submodel. Có lỗi đọc DHT thì stream vẫn lưu để chẩn đoán nhưng phép nghiệm thu không đạt.

Log nằm trong `evidence/dht-basyx-...`: verification.json tổng, motor/ và environment/ chứa preflight, HTTP, receiver, writes; đường dẫn transport_evidence trỏ đến raw.bin, serial.jsonl, mqtt.jsonl. Hai writer HTTP độc lập với watchdog; mỗi submodel có thể cập nhật trễ khác nhau, không phải giao dịch nguyên tử giữa motor và môi trường.
