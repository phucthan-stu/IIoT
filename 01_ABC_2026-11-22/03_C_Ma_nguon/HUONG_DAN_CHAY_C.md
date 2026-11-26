# Hướng dẫn Motor C — đọc trước khi chạy

Tài liệu hướng dẫn cho lần chạy tương lai. Khi đóng gói không chạy demo, không gọi Docker, không thay đổi nguồn. Dùng Windows và Python 3.12 (đã thử 3.12.10). Gói có mã cần thiết cho nhánh C, không có môi trường BaSyx/broker cài sẵn. Các đường dẫn tài liệu là tương đối; đường dẫn ổ đĩa trong lệnh dưới đây chỉ là ví dụ người nhận phải chỉnh.

## 1. Chuẩn bị

Giải nén toàn bộ ZIP. Từ thư mục 03_C_Ma_nguon, sao chép project.example.json thành project.json rồi chỉnh python tới python.exe của venv máy nhận, borrow tới Lib/site-packages của chính venv đó, source tới ../01_AB_Duong/04_Ma_nguon_Duong (ghi đường dẫn tuyệt đối của máy nhận trong JSON).

Ba khóa mẫu đủ cho đường gọi Motor C qua run_c.py và basyx_c.py. Không dùng demo.py để chạy task A/B hoặc start BaSyx với cấu hình mẫu này: các chức năng đó cần thêm cấu hình máy/hạ tầng; đọc hướng dẫn A/B riêng.

Nếu cần tạo venv mới, thực hiện riêng bước chuẩn bị rồi cài requirements.txt bằng Python của venv đó. Ví dụ PowerShell, tại 03_C_Ma_nguon:

```powershell
$Python = 'C:\DUONG\IIoT\.venv312\Scripts\python.exe'
& $Python -m pip install -r .\requirements.txt
```

Lệnh cài đặt chỉ là hướng dẫn, chưa thực hiện trong lần đóng gói. Không sao chép venv máy cũ. Không chạy các bản script trong thư mục bằng chứng để khởi động demo.

## 2. Kiểm tra hạ tầng thật trước khi chạy

```powershell
docker context show
docker version
docker ps -a
Invoke-RestMethod 'http://127.0.0.1:8082/shells'
Invoke-RestMethod 'http://127.0.0.1:8082/submodels'
docker inspect motorc_mqtt-broker-1
```

BaSyx API cần hoạt động ở 127.0.0.1:8082, Viewer ở http://127.0.0.1:3000/aasviewer. Trên máy hiện tại giữ nguyên container/image/volume đã có, chỉ start đúng container sau kiểm tra khi cần. Không pull, recreate hay đổi context để làm theo tài liệu này.

Launcher run_c.py kiểm cứng broker tên motorc_mqtt-broker-1, label iiot.workspace=motor-c-local, image ID sha256:75d6662dee52c947dc9dd9c29f1d8d7ffef78ddaf38001caa09755681e8bbc98, port duy nhất 127.0.0.1:1883 và không có container đang chạy dùng chung volume. Nếu broker đúng cấu hình nhưng đang dừng, launcher start chính container đó và kiểm MQTT. Trên máy khác, broker chưa có hoặc khác các điều kiện này sẽ bị từ chối. Cần thống nhất hạ tầng tương thích riêng, không bỏ qua kiểm tra hoặc coi gói này tự dựng được môi trường mới.

Broker local hiện cho phép anonymous; chưa có xác thực/TLS end-to-end, không mở cho ESP32/Wi-Fi. Topic cố định trong telemetry.py: university/aas-demo/motor-c-stepper/v1/state. API loopback nằm trong basyx_c.py. Cấu hình mẫu không làm thay đổi những hằng này.

## 3. Chạy C

Mỗi terminal đặt $Python đúng venv và đứng tại 03_C_Ma_nguon. Chỉ chạy một bản C, không đồng thời chạy bản C trong workspace cũ: khóa tiến trình được quản lý theo thư mục workspace, không khóa toàn máy.

1. Khi các tiến trình C đã dừng, kiểm/tạo mô hình (tương ứng task 13):
```powershell
& $Python -B .\motor_c\basyx_c.py
```
Chờ C model ready. Chương trình kiểm cả hai tài nguyên trước khi tạo tài nguyên thiếu; metadata khác sẽ báo lỗi. Không xóa mô hình để xử lý lỗi.

2. Terminal thứ nhất chạy adapter (tương ứng task 14):
```powershell
& $Python -B .\motor_c\run_c.py adapter
```
Chờ READY adapter C. Không chạy receiver-only cùng adapter.

3. Terminal thứ hai chạy simulator (tương ứng task 11):
```powershell
& $Python -B .\motor_c\run_c.py simulator
```
Quan sát accepted và basyx_verified. Viewer chọn MotorCStepperAAS → StepControlTelemetry, kiểm đúng Submodel urn:university:aas-demo:submodel:motor-c-stepper:telemetry. AAS là urn:university:aas-demo:shell:motor-c-stepper. Không dùng MotorC cũ urn:iiot-demo:...

4. Ctrl+C chỉ terminal simulator. Giữ adapter; sau khoảng 5 giây không có mẫu hợp lệ mới watchdog chuyển stale. Đợi ghi/đọc lại thành công và làm mới Viewer. Chạy lại lệnh simulator để có SessionId mới, Sequence bắt đầu từ 1 và fresh. Ảnh chụp có thể bắt được sequence lớn hơn 1.

5. Kết thúc: dừng simulator, đợi freshness_written=stale nếu muốn lưu kết thúc stale; sau đó ở terminal khác:
```powershell
& $Python -B .\motor_c\run_c.py stop
```
Tương ứng task 12, dừng riêng tiến trình C do launcher tại thư mục này quản lý; broker/BaSyx giữ nguyên. Không dùng lệnh dừng mọi python.exe. Gói này không kèm VS Code tasks; các số task chỉ để đối chiếu workspace cũ.

## 4. Dữ liệu, log và giới hạn

Chạy mới tự tạo motor_c/state và motor_c/evidence; không ghi vào 02_C_Bao_cao_va_bang_chung. Gói không mang checkpoint cũ: lần chạy đầu chưa có mẫu sẽ waiting/HasSample=false, không nhất thiết có lần ghi stale khởi đầu giống log lịch sử. Mỗi lần thử có số mẫu/session khác, không kỳ vọng lặp đúng 96/97.

SessionId định danh phiên nguồn; Sequence là thứ tự mẫu theo phiên. LastReceivedAt là lúc nhận mẫu mới được chấp nhận. Mẫu trùng/cũ/retained bị từ chối và GET không gia hạn. Freshness theo thời gian không nhận mẫu hợp lệ, không theo việc tốc độ có đổi hay không. Ngưỡng stale 5 giây được kiểm bằng monotonic trong freshness.py. Timestamp không tự tạo lịch sử.

SetpointSpeedRPM là tốc độ đặt; CommandedSteps là số bước lệnh mô phỏng. Chưa chứng minh chuyển động cơ khí. Worker HTTP lưu trạng thái mới nhất: số mẫu nhận không bằng số lần ghi, và một lần đối soát thường có PATCH + GET. Khi HTTP chậm hoặc adapter dừng, cờ đã lưu trên BaSyx có thể cũ; cần xét tuổi LastReceivedAt. Không có watchdog chạy độc lập trong Viewer.

Bản đồ code: telemetry.py make_sample/run_simulator tạo nguồn, Receiver.on_message/watch nhận MQTT và theo dõi timeout; freshness.py FreshnessTracker.accept/tick lọc và đổi chất lượng; adapter_c.py record_events/write_latest ghi log và cập nhật; basyx_c.py models/bootstrap/values_for/verify_values mô hình và đối soát; run_c.py quản lý tiến trình, demo.py cung cấp khóa và Windows Job; mqtt_local_check.py cung cấp Probe.

Chưa triển khai firmware/phần cứng, bảo mật end-to-end, dashboard, điều khiển từ xa, lịch sử mọi mẫu trên BaSyx hoặc phối hợp liên động thiết bị. Mã hiện tại đã kiểm tra cú pháp tĩnh khi đóng gói; chưa chạy nghiệm thu trên máy Dương.
