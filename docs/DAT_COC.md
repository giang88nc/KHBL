# Trang ĐẶT-CỌC — 11/09/2026

Đường dẫn: `/banle/dat-coc/`. Django + HTMX, icon SVG riêng trên topbar;
thêm/xem/sửa/xóa đều qua popup, thông báo toast, giữ bộ lọc khi làm mới.

- Bốn tab: DANH SÁCH (toàn bộ), Hàng sẵn sàng (R), Đã giao (C), Chưa giao (W/R/P).
- Nội dung chính là bảng phiếu gọn, XEM/SỬA cuối dòng; tóm tắt khách, món, nguồn hàng, lịch hẹn, cọc, nhân viên và ghi chú.
- Phân trang 30 phiếu; DANH SÁCH mặc định mới nhất, Chưa giao/Hàng sẵn sàng ưu tiên công việc; Đã giao dùng ngày giao được xác nhận (thiếu ngày để trống, thứ tự phụ theo ngày lập).
- Bộ lọc Của tôi theo liên kết nhân viên PMV, nguồn hàng, việc tới hạn, phiếu hủy và hàng cần kiểm tra. Bộ đếm nhanh theo phạm vi tìm/ngày/nguồn/người, không cộng chồng Chưa giao và Hàng sẵn sàng.
- Phiếu mới bắt buộc ít nhất một dòng hàng và nhập tiền cọc (cho phép ghi rõ 0).
- Nguồn hàng theo từng dòng: Có sẵn trong kho / Đặt mới. Tra cứu `T_PRODUCT.Status=I`, tự điền mã, tên, vàng, trọng lượng và ni; kiểm tra lại hàng tồn khi thêm mã vào phiếu.
- Mã có sẵn lưu tương thích appMobile trong `Notes`: `SP:mã | yêu cầu`; hiển thị tách riêng mã và yêu cầu, không đổi schema KK.
- Quyền `DAT_COC` trong Hệ thống → Người dùng: Xem, Tạo/sửa, Hủy/xóa.
  Superuser có quyền sẵn; tài khoản khác cần được quản trị viên phân quyền.

## Nghiệp vụ

Theo appMobile đã khảo sát ở lượt bổ sung: W = Đang chờ, R = Hàng sẵn sàng,
C = Đã giao khách, D = Đã hủy. Phiếu cọc vendor có P = đã thanh toán chưa sử
dụng; W = lưu tạm; C = đã sử dụng vào hóa đơn. Trang đặt hàng dùng nhãn giao hàng;
phân biệt nguồn appMobile bằng TrnID TRC và ShopID trống khi xử lý trọng lượng.

CRUD chỉ dành cho phiếu W theo đúng điều kiện của `TRN_DATCOC_Upd/Del`.
Phiếu đã ghi CashPay/CardPay không cho sửa đè tiền cọc hoặc xóa cứng; kiểm cả ở máy chủ.
Lưu mới chưa thực hiện thu tiền / chốt sổ quỹ. Các nghiệp vụ thanh toán,
hoàn cọc, nhận hàng không được giả lập bằng cách cập nhật Status trực tiếp.

Ghi qua `TRN_DATCOC_Ins`, `TRN_DATCOC_Upd`, `TRN_DATCOC_Del` trong gateway.
Theo công tắc đích chung của ứng dụng (KK/sandbox), giữ khóa ghi và chốt KK.
Popup ký đích/mốc dữ liệu; gửi lại sau khi đổi đích hay sửa ngoài PMV bị chặn.
Token một lần lưu dạng SHA-256 trong MySQL, tránh tạo trùng nếu gửi lặp.
Kết quả chưa chắc chắn sẽ giữ token đã gửi và yêu cầu kiểm tra danh sách.

Tiền cọc/tiền công truyền nguyên đơn vị trong bảng (đồng); Decimal, không float.
TL đọc theo phép chia `dbo.fun_GetHS()` của `TRN_DATCOC_Get`; Ins/Upd nhân
ngược với vàng WeightUnit=L. TL vàng dự kiến được giữ riêng (phiếu cũ có thể
chỉ có GoldWeight, không có tổng TL); bỏ trống thì tự tính TotalWeight − DiamondWeight.
Mỗi món bắt buộc mã vàng hợp lệ vì vendor INNER JOIN I_GOLD khi chèn chi tiết.
Phiếu appMobile (TRC, ShopID trống) lưu trực tiếp trọng lượng theo chỉ/gram;
khi sửa qua vendor cần bù hệ số để không đổi trọng lượng đang lưu. Không chỉ
dựa vào prefix vì vendor có thể sinh cùng tiền tố TRC.

## Áp dụng trang mẫu localhost:5000

Đã đọc UI danh sách/chi tiết và source tham chiếu (chỉ đọc):
`D:/PYTHON/BANLE_V5/app/templates/stock/_order.html` và
`D:/PYTHON/BANLE_V5/app/domain/stock.py`.

- Thiết kế mới thay thẻ phiếu và hai tab cũ bằng bốn tab trạng thái và bảng danh sách.
- Dòng phiếu có khách/SĐT, nhân viên, ghi chú liên hệ, món, tiền cọc và ngày hẹn.
- Tổng phiếu / tổng cọc / Quá hẹn theo bộ lọc; lọc ngày hẹn
  hôm nay, trong 2 ngày tới, chưa có ngày; phiếu C/D không tính quá hẹn.
- Popup thêm/sửa tách ngày hẹn, ghi chú và tổng tiền tạm tính; đóng gói tương
  thích `HEN:YYYY-MM-DD | ghi chú | tổng tiền`. Đọc được định dạng cũ và ngày
  hẹn trong UserID_Upd; KHBL giữ cột người cập nhật đúng nghĩa khi ghi qua proc.
- Popup xem hiển thị TM/CK-thẻ đã ghi, nhắc khi chưa phân bổ đủ tổng cọc,
  và có nút In phiếu (bố cục A5, chỉ in nội dung phiếu đang xem).

App mẫu ghi trực tiếp bảng, cho tự chọn W/R/C/D và không chạy chuỗi sổ quỹ PMV.
KHBL giữ CRUD qua proc và điều kiện W. Đợt này áp dụng tra cứu/tiến độ, thông tin
lịch hẹn và phiếu in; không chuyển cách ghi bảng trần, QR thu cọc hay thao tác
giao/hủy trạng thái của app mẫu sang KHBL.

## Kiểm chứng

- So source ba proc CRUD: KK và sandbox giống nhau.
- Sandbox: tạo một phiếu/món có Unicode và ký tự XML, kiểm tiền và TL;
  sửa tiền/số lượng, từ chối mốc cũ, xóa phiếu và chi tiết thử.
- `python -X utf8 manage.py test tests.test_deposits tests.test_user_access --settings=config.settings.test_prices`
- UI chạy tại localhost: bốn tab, popup tạo/chi tiết, thêm dòng, tra cứu hàng sẵn, topbar.
- Đối chiếu KK cùng khoảng 13/06–11/09/2026: 58 chưa giao = 25 chờ + 33 sẵn
  sàng, khớp trang mẫu; lọc quá hẹn ra 29 phiếu (10 chờ + 19 sẵn sàng).

Migration PMV 0010 thêm lựa chọn quyền; POS 0015–0016 thêm bảng token chống
gửi trùng. Không thay schema trên KK.

## Nâng cấp theo quyết định ngày 11/09/2026

GĐ xác nhận: không cần chặn app khác; tự đối soát và thực hiện nghiệp vụ tiền;
thêm THÔNG BÁO với danh sách từng ngày, sửa/xóa, mẫu OA chưa duyệt và lịch gửi.

### Vận hành phiếu

- Năm tab: DANH SÁCH / Hàng sẵn sàng / Đã giao / Chưa giao / THÔNG BÁO.
- Popup từ nút XEM: tiến độ từng món (sẵn sàng/đã giao/giao một phần), liên hệ,
  phân công, nhắc lại, ngày hẹn mới, giá tạm tính/chốt giá, giữ/giải phóng hàng và hủy đặt hàng.
- Giữ hàng chỉ trong KHBL; màn bán KHBL kiểm tra trước lưu/chốt. Không khóa PMVGoldRT/appMobile.
  Cần kiểm tra lại kho khi bàn giao, vì ứng dụng khác vẫn có thể bán hàng.
- MySQL lưu tiến độ, vị trí giữ hàng, ngày hẹn ban đầu, ngày giao thực tế và nhật ký chỉ ghi thêm.
  Các khóa đều gồm đích KK/bản thử. Token và phiên bản chống gửi lặp/ghi đè thay đổi của người khác.
- Thay chi tiết món làm mất hiệu lực tiến độ cũ, yêu cầu xác nhận lại. Phiếu nguồn đã giao/hủy
  không được khôi phục nhắc từ trạng thái KHBL cũ. Không giảm số lượng đã giao để thay cho chứng từ trả hàng.
- Danh sách ưu tiên quá hẹn, tới lịch gọi, hẹn hôm nay; lọc nguồn hàng/người phụ trách/ngày lập/ngày hẹn.
- Báo cáo công việc, tuổi phiếu tồn, theo nhân viên, giao đúng hẹn (chỉ các phiếu đủ dữ liệu),
  số dư có chứng từ; CSV theo bộ lọc, chống công thức và giữ số tiền Decimal.
- Snapshot có thời điểm, cache riêng đích 30 giây: bốn truy vấn theo lô cho header, món,
  quỹ và liên kết hóa đơn; không nhân tổng cọc theo số món. Vượt trần dữ liệu thì báo lỗi.

### Thu, hoàn và cấn cọc

- Quyền `DAT_COC.can_approve`, tài khoản đã liên kết PMV/két; vẫn chịu gateway/chốt ghi/khóa hệ thống.
- Thu toàn bộ cọc trên phiếu vendor W chưa có phân bổ/quỹ:
  `TRN_DATCOC_Complete → CARDPAY_Ins (TDC) → T_TILL_TXN_Proc`.
  BẮT BUỘC `p_ProductIDs=None,p_CardAmounts=None`; truyền chuỗi rỗng làm vendor rẽ sai nhánh SRT.
  Kiểm tra chi tiết VND, dấu cộng, số tiền không NULL và đúng tiền mặt trước khi Proc vào két.
- Hoàn toàn bộ: `T_TILL_TXN_Del` loại TDC, `p_Type='0'`; giữ phiếu và lịch sử, dùng đúng két đã thu,
  đúng phân bổ TM/CK. Đọc lại phải W, CashPay/CardPay=0 và không còn giao dịch quỹ.
- CK: chỉ ghép duy nhất mã TrnID/BillCode nguyên vẹn + số tiền + chiều in/out trong 3 ngày kể từ
  ngày lập yêu cầu. Không ghép chỉ vì bằng tiền. Khóa bản ghi ngân hàng, đánh dấu `is_check` và
  khóa chứng từ duy nhất trong cùng giao dịch MySQL trước khi gọi PMV. Đây là ghi nhận chứng từ,
  không phải API chuyển tiền ngân hàng. Người dùng xác nhận phần tiền mặt thực thu/hoàn.
- Lập yêu cầu bền trước khi ghi, giữ khóa khi không rõ kết quả, không tự lặp lại thu/chi.
  Yêu cầu chờ chưa ghi có thể hủy; quá 3 ngày hoặc quyền/két thay đổi chuyển cần kiểm tra.
- Cấn toàn bộ vào hóa đơn cùng khách đang W, TienCoc và PayAmount đã khớp, chưa có liên kết khác:
  `TRN_RT_BUYSELL_DatCoc_Ins`. Không ghi đè danh sách phiếu cọc. Chỉ coi đã sử dụng khi hóa đơn C.
  Màn bán kiểm tra liên kết cọc trước sửa/chốt. Khóa chứng từ theo cả phiếu cọc và hóa đơn.
- Sửa lỗi vendor Ins không nhận TienCoc: với hóa đơn mới có cọc, gọi Upd ngay sau Ins rồi
  xác minh cả TienCoc và PayAmount. Đã chạy qua luồng cấn/chốt/hủy trên sandbox.
- Cọc cũ appMobile thiếu chứng từ quỹ không tự thu lại hay tạo số dư đầu kỳ. Nhãn Chưa đối soát
  giữ nguyên dù CashPay+CardPay bằng tổng cọc. Số dư xác minh cần header/quỹ/chi tiết/liên kết khớp.
- **Hoàn một phần chưa bật:** đã hỏi GĐ cách xử lý vì proc hiện tại chỉ đảo toàn bộ cọc;
  không tự thay bằng hoàn toàn bộ rồi lập phiếu mới khi chưa được duyệt.

### Thông báo OA

- Hai mẫu nháp ban đầu: báo hàng sẵn sàng, nhắc lịch nhận hàng. Có thể thay nội dung và mã mẫu.
- Danh sách theo ngày, trạng thái, tên/SĐT/phiếu; soạn hàng loạt các phiếu đủ điều kiện,
  chống trùng phiếu/ngày/mẫu. Nội dung hiển thị chỉ là bản xem trước theo biến của mẫu.
- Sửa tin qua các ô tên khách, mã phiếu, ngày hẹn, tiền cọc, cửa hàng. Xóa chỉ hủy khỏi lịch,
  vẫn tra được lịch sử. Mẫu chưa duyệt vẫn lưu lịch nhưng ở Chờ duyệt mẫu OA.
- Chỉ kiểm tra duyệt khi OA trả template ENABLE. Sửa mẫu tăng phiên bản và bỏ xác nhận cũ.
  Khi mẫu được duyệt lại, cần mở tin và lên lịch lại; không tự gửi bù các lịch cũ.
- Biến môi trường máy chủ `DATCOC_OA_ACCESS_TOKEN` (mặc định rỗng), mã template và ánh xạ
  tên tham số OA trong popup quản lý mẫu. Không lưu token vào trình duyệt/repository.
- Gửi theo lịch 08:00–trước 21:00, bỏ lịch quá 2 giờ. Trước gửi kiểm tra lại quyền người lập,
  mẫu, trạng thái phiếu, tiến độ, SĐT, ngày hẹn. Chỉ gửi ở đích KK; bản thử không gửi ra ngoài.
- OA trả msg_id = **OA đã nhận tin**, chưa phải xác nhận khách đã đọc/nhận. Mất kết nối sau khi
  bắt đầu gửi hoặc worker gián đoạn chuyển Chưa rõ kết quả; tra tracking_id, không tự phát lại.
- Command `manage.py process_deposit_work`; scheduler gọi mỗi phút. Danh sách thông báo cập nhật
  mỗi 30 giây. Chưa có token/mẫu được duyệt thì không gọi API gửi.
- API chính thức: https://docs.zaloplatforms.com/docs/ZBS/quan-ly-template/template-api/api-lay-thong-tin-chi-tiet-template
  và https://stc-developers.zdn.vn/docs/v2/zbs-template-message/gui-tin-template-qua-sdt/api-gui-tin-qua-sdt/api-gui-tin

### Triển khai và kiểm chứng đợt này

- Migration POS 0017–0019 cho vận hành, tin nhắn, mẫu nháp và khóa thao tác tiền. Không đổi schema PMV.
- Kiểm thử: `manage.py test tests.test_deposits tests.test_deposit_workspace tests.test_deposit_operations tests.test_user_access --settings=config.settings.test_prices`.
- `manage.py smoke_datcoc_money --bank 0`, `--bank 250`, `--bank 1000`: thu 1.000,
  kiểm két chỉ tăng phần TM, cấn vào hóa đơn, chốt → số dư cọc 0, mở lại/xóa hóa đơn,
  hoàn cọc → về đúng số dư ban đầu. Dọn phiếu/hàng thử; nhật ký vendor được giữ.
- Source 5 proc tiền KK và sandbox giống nhau. Backup COPY_ONLY + VERIFY:
  `D:\KHJ_PMV_BACKUP\DATCOC_BEFORE_20260911_100542.bak` trên KK trước thêm allowlist.
- Lần smoke đầu phát hiện lỗi tham số chuỗi rỗng gây NULL trên két bản thử. Đã sửa tham số,
  phục hồi đúng dòng két thử theo số dư chụp trước (259.563.371.000), kiểm công thức
  OpenBal+InFlow−OutFlow=TillBal và dọn phiếu thử còn sót. Khôi phục hạ tầng giới hạn
  đúng sandbox/đúng dòng kiểm thử; không sửa trực tiếp dữ liệu nghiệp vụ KK.
- Không dùng thử bằng phiếu/tiền thật và chưa gửi thông báo tới khách. Giao diện OA kiểm bằng
  fixture riêng; kiểm tra trong phiên thật cần người dùng đăng nhập lại.

### Popup thêm / sửa phiếu — 11/09/2026

- Bốn phần: khách hàng (tìm theo tên/SĐT/CCCD hoặc quét QR), thông tin đặt, danh sách món, tổng tiền. Nút + mở popup khách hàng dùng chung; đóng/lưu khách không mất phiếu đang soạn. Địa chỉ lấy từ hồ sơ khách, nhân viên chỉ chọn một lần.
- Hai nút + Hàng sẵn / + Hàng đặt luôn thêm dòng. Dòng gồm mã, tên, loại vàng, TL vàng/hột, size, SL, tạm tính, ghi chú và tiền công theo món. Mã mẫu hàng đặt lưu trong Notes với tiền tố MA_DAT; không tạo giữ kho hoặc cảnh báo trùng tồn cho mã mẫu.
- Popup hiển thị TL chỉ hoặc gram thống nhất. Phiếu PMV chuyển qua hệ số gốc khi đọc/ghi; phiếu appMobile giữ cách quy đổi riêng. Tổng TL được tính từ TL vàng + TL hột. Mã hàng sẵn được kiểm lại tồn trước lưu.
- Tạm tính được tính lại ở máy chủ bằng Decimal từ giá đã ký, SL và TL vàng; làm tròn qua công thức tiền chung. Trình duyệt dùng BigInt cho phần xem trước. Giá / quy cách làm tròn được giữ trên phiếu, thiếu giá hiển thị rõ, không lấy 0 làm giá thay thế. Chỉnh tạm tính làm giá chốt cũ trở về tham khảo.
- Cọc tiền mặt + cọc chuyển khoản = tổng cọc. Lưu phiếu chỉ lưu dự kiến; Lưu & thu cọc xác nhận tiền mặt, tạo yêu cầu đối soát CK và gọi luồng chứng từ đã kiểm chứng. Khoản đã ghi tiền không được đổi phân bổ qua popup sửa. Ngày đặt lấy ngày tạo PMV; giao hàng đổi qua Tiến độ món.
- Migration 0020 chỉ thêm pricing/payment_plan vào MySQL. Giá ký và token thao tác kiểm đích/phiên bản, không ghi lặp khi kết quả chưa rõ.
- Kiểm thử: 72 ca về đặt cọc, vận hành, giá/tổng tiền và quyền; fixture trình duyệt thật kiểm thêm dòng, tra kho, dropdown, popup khách lồng, tiền, xóa dòng và bố cục điện thoại. `manage.py smoke_datcoc_editor` đã tạo/sửa/xóa phiếu trên sandbox qua proc, khớp TL 8 số lẻ, không sinh quỹ.
