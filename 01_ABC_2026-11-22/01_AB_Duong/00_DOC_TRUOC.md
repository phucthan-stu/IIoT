# Gói bàn giao kiểm thử tích hợp cho Dương

Mở Bao_cao_kiem_thu_tich_hop.pdf hoặc .docx để đọc báo cáo chốt. Bản .md cùng nội dung dùng cho Obsidian và tham chiếu ảnh trong 03_Bang_chung. Đọc 01_Huong_dan_chay_lai.md trước khi tái hiện; 02_Danh_muc_bang_chung.md chỉ nơi tra cứu từng kết luận.

Nguồn là hai simulator; BaSyx thật; UI chỉ xác minh mẫu cuối bằng ảnh. Chưa có phần cứng đo thật, UI cập nhật liên tục được nghiệm thu, bảo mật đầu cuối hoặc benchmark dài hạn. Khi mất nguồn, giá trị cuối được giữ lại nhưng chưa có cờ stale.

Thời gian phục hồi báo cáo theo monotonic: A 3.625 s, B 3.344 s. Bảng UTC được tách riêng, giải thích làm tròn, không sửa log gốc. Đây không phải benchmark reconnect thuần túy.

04_Ma_nguon_Duong chứa mã chức năng gốc của Dương. Bộ bọc kiểm thử và bằng chứng là phần bổ sung trong đợt tích hợp. Không đưa bí mật vào gói; dữ liệu vận hành cũ chỉ được đọc để so sánh trước/sau. Không chạy thử lại khi đóng gói.
