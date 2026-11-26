# Hướng dẫn chạy lại và đọc bằng chứng

Tài liệu này là hướng dẫn cho lần chạy trong tương lai. Khi tạo gói bàn giao, không chạy lại thử nghiệm. Các lệnh có đường dẫn máy cũ trong log là lịch sử, không tự động chạy trên máy khác.

## 1 Chuẩn bị và thay đường dẫn

Giải nén toàn bộ gói; giữ thư mục ảnh đi cùng Markdown để Obsidian hiển thị được hình. Mã chức năng nguyên bản ở `04_Ma_nguon_Duong`. Không chạy trực tiếp các file bằng chứng: chúng có đường dẫn cố định, điều kiện kiểm tra và có thể ghi nối vào log cũ.

Ví dụ các biến PowerShell cần điều chỉnh:

```powershell
$Package = 'D:\IIoT\Ban_giao_Duong_Kiem_thu_tich_hop_2026-11-17'
$Demo = Join-Path $Package '04_Ma_nguon_Duong'
$RunRoot = 'D:\IIoT\Ket_qua_moi'
$Python = 'D:\IIoT\.venv\Scripts\python.exe'
$BasyxDir = 'D:\IIoT\BaSyx'
```

Ưu tiên môi trường Python đã có đủ dependency tương thích. Phiên bản đã thử: Python 3.12.10, pymodbus 3.11.0, asyncua 1.1.8, requests 2.32.3. Nếu dựng Python mới, chỉ thực hiện cài đặt như bước riêng đã thống nhất:

```powershell
py -3.12 -m venv 'D:\IIoT\.venv'
& $Python -m pip install -r (Join-Path $Demo 'requirements.txt')
& $Python -B -c "import sys,requests,pymodbus,asyncua; print(sys.version); print(requests.__version__,pymodbus.__version__,asyncua.__version__)"
```

Ba dependency trực tiếp được ghim; toàn bộ dependency gián tiếp chưa có lockfile hoàn chỉnh. Đường sys.path mượn requests ở máy kiểm thử là giải pháp tận dụng môi trường cũ, không phải môi trường portable. Không sao chép venv từ Python 3.13 sang 3.12.

## 2 BaSyx và image

Trên máy đã có container, chỉ kiểm tra và start container đúng dự án; không dùng `compose up` tùy tiện vì Compose cũ dùng latest/pull_policy=always. Lượt thật dùng:

```powershell
docker version
docker ps -a
docker image ls --no-trunc --digests
docker start postgres_db
# Chờ PostgreSQL healthy rồi mới thực hiện dòng tiếp theo.
docker start aas-environment aas-web-ui
```

Container basyx_configuration cũ đã Exited(0) nên không chạy lại. BaSyx phải đọc được DB cũ; UI và API bind loopback. Trên máy khác, tên container có thể khác: kiểm tra nhãn Compose working_dir, image, mount trước khi start.

Gói này không chứa image hoặc dữ liệu volume. Máy chưa có container cần một bước dựng BaSyx riêng và tạo bí mật riêng, ngoài phạm vi lệnh khởi động trên. Dùng RepoDigests đầy đủ trong `03_Bang_chung/runtime/basyx-runtime-2026-11-17.json` để xác định image, không chỉ dùng tag latest. Trường ComposeImageLabel là nhãn lịch sử, có thể khác ImageId/RepoDigests thực tế; không coi chúng là cùng loại hash. Không mang khóa/mật khẩu từ máy cũ sang.

```powershell
Invoke-RestMethod 'http://127.0.0.1:8082/shells'
Invoke-RestMethod 'http://127.0.0.1:8082/submodels'
```

## 3 Bootstrap và phạm vi ghi

Trong mỗi cửa sổ chạy mã, đặt:

```powershell
$env:AAS_SECURITY_MODE = 'local'
$env:AAS_BASE_URL = 'http://127.0.0.1:8082'
$env:POLL_PERIOD_S = '1.0'
Remove-Item Env:AAS_TOKEN_FILE,Env:AAS_CA_BUNDLE -ErrorAction SilentlyContinue
```

GET từng ID trước ghi. Mã hóa ID bằng UTF-8 base64url không padding:

```powershell
$Id = 'urn:university:aas-demo:submodel:motor-a:operational'
$Encoded = [Convert]::ToBase64String([Text.Encoding]::UTF8.GetBytes($Id)).TrimEnd('=').Replace('+','-').Replace('/','_')
Invoke-WebRequest -Uri ($env:AAS_BASE_URL + '/submodels/' + $Encoded) -Method Get -UseBasicParsing
```

Lặp cho motor-b và hai shell `urn:university:aas-demo:shell:motor-a`, `urn:university:aas-demo:shell:motor-b` (dùng `/shells/`). Chỉ tạo nếu thiếu; tài nguyên đã tồn tại phải đối chiếu metadata và reference. Nếu có khác biệt, dừng và phân tích, không xóa/ghi đè. Bootstrap nguyên bản gửi POST rồi xử lý 409 bằng GET; trong lượt đã nghiệm thu cả bốn đều 404 trước chạy và POST 201.

```powershell
& $Python -B (Join-Path $Demo 'aas_bootstrap.py')
```

Lệnh trực tiếp trên là cách chạy chức năng. Lượt kiểm thử thực tế gọi `bootstrap.main()` qua audit_bootstrap.py để ghi request/response. Nó chỉ cho apply khi cả bốn ID đều vắng mặt; nếu ID đã có, không chạy apply.

## 4 Cách tái hiện có ghi bằng chứng

Tạo thư mục kết quả MỚI cho từng lần, không sửa hoặc xóa log lịch sử. Chỉ sao chép các script kiểm thử sang thư mục mới, không sao chép log, JSON kết quả hay stop-sync.flag.

- Bootstrap: sao chép audit_bootstrap.py; sửa hằng SOURCE trên bản sao trỏ tới $Demo; dùng Python đã có requests. Chạy `preflight`, đọc kết quả rồi mới `apply` khi đủ điều kiện.
- Sync: sao chép run_trial.py và analyze_evidence.py; sửa SOURCE; BORROW trỏ tới thư mục Lib/site-packages của venv mới (hoặc bỏ thao tác sys.path.append trên bản sao nếu mọi dependency đã cài). ROOT tự lấy thư mục script. Chạy run_trial.py để thử 60 giây; rồi analyze_evidence.py để kiểm tra offline. Không sửa mã trong 04_Ma_nguon_Duong.
- Recovery: sao chép run_recovery.py, controller.py và analyze_evidence.py. Sửa SOURCE/BORROW trong bộ bọc và SOURCE trong trình phân tích. Trong controller.py, sửa đường đọc `previous` tới source-hashes.json của lượt sync mới đã đạt. Không mang stop-sync.flag cũ sang; file đó làm sync thoát ngay. Chạy run_recovery.py, sau đó analyze_evidence.py. Mỗi giai đoạn 60 giây, readiness 30 giây, trần tổng sync 420 giây.

Đây là các điều chỉnh đường dẫn cho máy khác, chưa được chạy nghiệm thu như một gói portable. Bộ bọc nguyên bản dùng ở máy kiểm thử được giữ nguyên trong bằng chứng để tra cứu; không sửa chúng tại chỗ.

Các lệnh thực tế đã dùng ở máy kiểm thử:

```powershell
# Bootstrap: chỉ ở tiến trình lệnh này mượn thư viện từ gói cũ.
$env:PYTHONPATH='C:\Users\Admin\Documents\IoT\cuoi_ky_1\cuoi_ky_1\.review_build\testenv\Lib\site-packages'
py -3.12 -B 'C:\Users\Admin\Documents\ChatGPT\IoT\evidence\bootstrap-duong-2026-11-17\audit_bootstrap.py' preflight
py -3.12 -B 'C:\Users\Admin\Documents\ChatGPT\IoT\evidence\bootstrap-duong-2026-11-17\audit_bootstrap.py' apply
# Sync/recovery: bộ bọc xóa PYTHONPATH và ưu tiên native dependencies trong venv312.
& 'C:\Users\Admin\IIoT_AAS_Demo\.venv312\Scripts\python.exe' -B 'C:\Users\Admin\Documents\ChatGPT\IoT\evidence\sync-duong-2026-11-17\run_trial.py'
& 'C:\Users\Admin\IIoT_AAS_Demo\.venv312\Scripts\python.exe' -B 'C:\Users\Admin\Documents\ChatGPT\IoT\evidence\recovery-duong-2026-11-17\run_recovery.py'
```

## 5 Nếu chạy thủ công để học

Sau khi kiểm tra cổng và mô hình, mở ba cửa sổ; mỗi cửa sổ đặt Python, Demo và môi trường local như trên, rồi chạy đúng một lệnh tương ứng:

```powershell
& $Python -B (Join-Path $Demo 'motor_a_modbus.py')
& $Python -B (Join-Path $Demo 'motor_b_opcua.py')
& $Python -B (Join-Path $Demo 'aas_sync.py')
```

Các dòng trên dành cho ba cửa sổ khác nhau, không dán tuần tự vào cùng một cửa sổ. Dừng bằng Ctrl+C ở sync trước, rồi ở hai simulator. Với bộ bọc, để nó kết thúc theo hạn và thực hiện finally; recovery có thể yêu cầu dừng nhẹ bằng tạo stop-sync.flag trong đúng thư mục lượt mới. Nếu bộ điều phối bị gián đoạn bất thường, đối chiếu PID với command line và thời điểm tạo trong log trước khi dừng; không giết mọi python.exe và không tái dùng PID lịch sử.

BaSyx được giữ chạy sau nghiệm thu. Khi người phụ trách muốn kết thúc toàn bộ phiên, dừng đúng container đã xác định; không dùng down -v hoặc xóa volume. Giữ dữ liệu urn:iiot-demo: và các ID ngoài hai Submodel được phép.

## 6 Tiêu chí đọc kết quả

Kiểm từng PATCH/GET, kiểu xs:double/xs:boolean, giá trị và raw→normalized. `verified` không chứng minh dữ liệu phần cứng thật. Khi mất nguồn, nguồn còn lại cần ≥3 chu kỳ thành công; nguồn khởi động lại cần ≥3 chu kỳ và sync PID không đổi. Phân loại lỗi trong khoảng dừng chủ động là lỗi nguồn dự kiến; lỗi HTTP/metadata/dữ liệu là lỗi khác, phải điều tra.

Mở http://localhost:3000/aasviewer → MotorAAAS/MotorBAAS → OperationalData. Kiểm ID urn:university:aas-demo:. Giá trị khác bộ mẫu cuối trong báo cáo sau một lượt mới là bình thường; phải so với bằng chứng của đúng lượt mới. Gói hiện tại chỉ có ảnh mẫu cuối của lượt sync, không có ảnh chứng minh cập nhật liên tục hoặc phục hồi trực quan.
