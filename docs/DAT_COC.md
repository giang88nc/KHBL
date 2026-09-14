# Trang ĐẶT-CỌC — 11/09/2026

Đường dẫn: `/banle/dat-coc/`. Django + HTMX, icon SVG riêng trên topbar;
thêm/xem/sửa/xóa đều qua popup, thông báo toast, giữ bộ lọc khi làm mới.

- Bốn tab: DANH SÁCH (toàn bộ), Hàng sẵn sàng (R), Đã giao (C), Chưa giao (W/R/P).
- Nội dung chính là bảng phiếu gọn, XEM/SỬA cuối dòng; tóm tắt khách, món, nguồn hàng, lịch hẹn, cọc, nhân viên và ghi chú.
- Phân trang 30 phiếu; DANH SÁCH mặc định mới nhất, Chưa giao/Hàng sẵn sàng ưu tiên công việc; Đã giao dùng ngày giao được xác nhận (thiếu ngày để trống, thứ tự phụ theo ngày lập).
- Bộ lọc Của tôi theo liên kết nhân viên PMV, nguồn hàng, việc tới hạn, phiếu hủy và hàng cần kiểm tra. Bộ đếm nhanh theo phạm vi tìm/ngày/nguồn/người, không cộng chồng Chưa giao và Hàng sẵn sàng.
- Phiếu mới bắt buộc ít nhất một dòng hàng và tiền cọc lớn hơn 0; lưu là xác nhận đã thu.
- Nguồn hàng theo từng dòng: Có sẵn trong kho / Đặt mới. Tra cứu `T_PRODUCT.Status=I`, tự điền mã, tên, vàng, trọng lượng và ni; kiểm tra lại hàng tồn khi thêm mã vào phiếu.
- Từ 12/09/2026: `TRN_DATCOC_DT.ProductDesc` lưu JSON một cặp `{ "mã SP": "tên SP" }`; hàng đặt dùng khóa `Khách đặt`. `Notes` chỉ lưu ghi chú. Vẫn đọc được dữ liệu cũ `SP:`/`MA_DAT:`; chuyển sang JSON khi ghi lại phiếu qua proc.
- Quyền `DAT_COC` trong Hệ thống → Người dùng: Xem, Tạo/sửa, Hủy/xóa.
  Superuser có quyền sẵn; tài khoản khác cần được quản trị viên phân quyền.

## Nghiệp vụ

Theo appMobile đã khảo sát ở lượt bổ sung: W = Đang chờ, R = Hàng sẵn sàng,
C = Đã giao khách, D = Đã hủy. Phiếu cọc vendor có P = đã thanh toán chưa sử
dụng; W = lưu tạm; C = đã sử dụng vào hóa đơn. Trang đặt hàng dùng nhãn giao hàng;
phân biệt nguồn appMobile bằng TrnID TRC và ShopID trống khi xử lý trọng lượng.

CRUD chỉ dành cho phiếu W theo đúng điều kiện của `TRN_DATCOC_Upd/Del`.
Phiếu đã ghi CashPay/CardPay không cho sửa đè tiền cọc hoặc xóa cứng; kiểm cả ở máy chủ.
Từ 12/09/2026, lưu mới thực hiện xác nhận thu tiền / chốt sổ quỹ. Các nghiệp vụ thanh toán,
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
  `TRN_DATCOC_Complete → gateway ghi trực tiếp tiền trên phiếu → T_TILL_TXN_Proc`.
  Khi có CK, gateway gọi nhánh TDC của `TRN_TILL_TXN_Upd` trong cùng transaction để quỹ bằng tiền mặt; không dùng `CARDPAY_Ins`.
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
- Hai nút + Hàng sẵn / + Hàng đặt luôn thêm dòng. Hàng đặt dùng mã `Khách đặt` trong JSON ProductDesc; không tạo giữ kho hoặc cảnh báo trùng tồn cho mã mẫu. Notes chỉ còn ghi chú nếu có.
- Popup hiển thị TL chỉ hoặc gram thống nhất. Phiếu PMV chuyển qua hệ số gốc khi đọc/ghi; phiếu appMobile giữ cách quy đổi riêng. Tổng TL được tính từ TL vàng + TL hột. Mã hàng sẵn được kiểm lại tồn trước lưu.
- Tạm tính được tính lại ở máy chủ bằng Decimal từ giá đã ký, SL và TL vàng; làm tròn qua công thức tiền chung. Trình duyệt dùng BigInt cho phần xem trước. Giá / quy cách làm tròn được giữ trên phiếu, thiếu giá hiển thị rõ, không lấy 0 làm giá thay thế. Chỉnh tạm tính làm giá chốt cũ trở về tham khảo.
- Cọc tiền mặt + cọc chuyển khoản = tổng cọc. Lưu phiếu mới xác nhận đã nhận tiền và gọi luồng thu; CK được đối soát sau. Khoản đã ghi tiền không được đổi phân bổ qua popup sửa. Ngày đặt lấy ngày tạo PMV; giao hàng đổi qua Tiến độ món.
- Migration 0020 chỉ thêm pricing/payment_plan vào MySQL. Giá ký và token thao tác kiểm đích/phiên bản, không ghi lặp khi kết quả chưa rõ.
- Kiểm thử: 72 ca về đặt cọc, vận hành, giá/tổng tiền và quyền; fixture trình duyệt thật kiểm thêm dòng, tra kho, dropdown, popup khách lồng, tiền, xóa dòng và bố cục điện thoại. `manage.py smoke_datcoc_editor` đã tạo/sửa/xóa phiếu trên sandbox qua proc, khớp TL 8 số lẻ, không sinh quỹ.

### Tinh gọn dòng món, ảnh và giao diện tím — 11/09/2026

- RowSP một dòng: mã, tên, loại, TL vàng, size, công, tạm tính. Hàng đặt dùng mã cố định Khách đặt; mỗi món mới SL=1. SL/TL hột/ghi chú của dữ liệu cũ được giữ để không mất lịch sử; không còn ô sửa ghi chú dòng.
- Hàng sẵn tra đúng mã (hỗ trợ máy quét gõ phím + Enter), tự hiện và khóa tên/loại/TL/size/công. Máy chủ lấy lại thông tin từ kho trước lưu, không nhận nội dung sản phẩm do trình duyệt sửa. Công kho đổi nghìn đồng về đồng.
- Tiền nhập/hiện theo Việt Nam: 1.000.000; hỗ trợ phần lẻ bằng dấu phẩy, máy chủ nhận Decimal chính xác.
- 2 hình mẫu + 2 hình thành phẩm, nút Chụp dùng camera và Chọn từ thiết bị, xem lớn, bỏ/thay ảnh. Ảnh JPEG/PNG/WebP tối đa 10 MB, chuẩn hóa JPEG bỏ metadata, lưu riêng media/datcoc. Đọc ảnh qua quyền DAT_COC và đích KK/bản thử; không public thư mục ảnh.
- Ảnh đã chọn giữ lại khi popup báo lỗi. Nút Cập nhật hình mẫu / thành phẩm trong XEM cho phép bổ sung ảnh cả khi phiếu đã thu cọc, không gọi proc sửa tiền. Migration 0021 thêm photos vào trạng thái MySQL.
- Giao diện trang, popup, nút và tab tông tím; cảnh báo/trạng thái vẫn có màu riêng. RowSP không xuống dòng, màn hẹp cuộn ngang.
- 76 kiểm thử đã qua; trình duyệt kiểm mã đúng/khóa ô, tiền định dạng, dòng thẳng hàng, bốn ảnh, upload giữ khi lỗi, camera giả lập và giao diện điện thoại.

### Ghi chú JSON, hẹn nhanh và tình trạng — 11/09/2026

- Khi tạo/sửa phiếu, `TRN_DATCOC.Description` lưu JSON gọn: `{"notes":[{"date":"2026-09-11","text":"Khách chốt cọc"}],"promise":"2026-09-18","estimate":"12500000"}`. Danh sách cho phép nhiều ghi chú trong cùng ngày; ngày hẹn và tạm tính là khóa tùy chọn.
- Vẫn đọc ghi chú văn bản và định dạng `HEN:` cũ; chuyển sang JSON khi lưu phiếu, không ghi lại hàng loạt dữ liệu KK. Ghi chú cũ chưa xác định ngày dùng `date:null`, hiển thị “Ghi chú cũ”. Object ISO-ngày:text cũng đọc được.
- Ô ghi chú + Thêm (hoặc Enter) thêm dòng theo ngày hiện tại tại cửa hàng. X bỏ từng dòng trong bản đang soạn; chỉ cập nhật cơ sở dữ liệu khi lưu phiếu. Nội dung chưa bấm Thêm được đưa vào danh sách khi lưu, không bị mất hoặc lặp.
- Cột Description là nvarchar(max), nhưng tham số vendor Ins/Upd vẫn là nvarchar(500). Máy chủ kiểm tra tối đa 500 đơn vị UTF-16 cho toàn bộ JSON trước gọi proc, báo lỗi để rút gọn, không cắt mất nội dung. Không thay schema/proc vendor.
- Nút + cạnh ngày hẹn mở chọn 7/10/20/30 ngày tính từ hôm nay; vẫn có thể chọn ngày thủ công. Tình trạng nằm cuối hàng Khách hàng / Thông tin đặt / Tình trạng, hiển thị riêng tiến độ và trạng thái phiếu/tiền cọc; cập nhật giao hàng qua Tiến độ món.
- 79 kiểm thử đã qua. Trình duyệt kiểm thêm/xóa ghi chú cùng ngày, ghi chú đang nhập khi lưu, hẹn +7 ngày, Escape, popup khách lồng và bố cục ba phần. Smoke sandbox tạo/đọc/sửa JSON qua proc, xóa một ghi chú, đổi ngày hẹn và dọn phiếu thử; không phát sinh quỹ.

### Tiến độ trong popup Sửa và Passcode — 11/09/2026

- Tình trạng có select Đơn mới / Đang đặt hàng / Thợ đang làm / Hàng sẵn sàng / Đang giao / Đã hủy (hoàn hoặc không hoàn) / Hoàn thành. Hiển thị tổng số sản phẩm theo SL và tổng cọc; nhãn Đã áp dụng phiếu chỉ hiện sau xác minh liên kết hóa đơn đã chốt, khách và tiền khớp.
- Hoàn thành cập nhật đủ số món đã giao và giải phóng giữ hàng KHBL. Không giảm số đã giao hoặc mở lại đơn đã kết thúc bằng thay tiến độ. Đã hủy dừng nhắc hẹn, giải phóng giữ hàng và hủy yêu cầu thu còn chờ. Các bước này không tự ghi sổ quỹ hay hóa đơn.
- Chọn hủy hoàn tiền: lưu yêu cầu hoàn, hiện chờ hoàn cọc trên danh sách; mở popup hoàn cọc nếu người dùng có quyền duyệt. Chỉ ghi nhận đã hoàn khi chứng từ được đối soát. Không hoàn tiền chỉ ghi chính sách xử lý, không biến khoản cọc thành doanh thu.
- Sửa trực tiếp: tiến độ, ngày hẹn, thêm ghi chú, hình ảnh, nhân viên. Ghi chú đã lưu không được sửa/xóa, kể cả sau Passcode; ghi chú mới đang soạn vẫn bỏ được. Máy chủ kiểm tra toàn bộ tiền tố lịch sử và ấn định ngày ghi chú mới.
- Khách hàng, món, cọc dự kiến mặc định khóa bằng fieldset. Mở sửa dùng Passcode chung (chống dò), cấp token 10 phút ràng buộc người dùng, phiên, đích và phiên bản phiếu. POST không có token hợp lệ không thể đổi trường được bảo vệ. Ghi nhật ký cập nhật, không lưu Passcode.
- Proc TRN_DATCOC_Upd chỉ nhận W: phiếu đã khóa PMV vẫn cho sửa thông tin theo dõi qua trạng thái KHBL; ghi chú mới nằm trong extra_notes và được ghép ở XEM/SỬA/danh sách. Không mở lại chứng từ để sửa metadata. Phiếu W có yêu cầu tiền đang xử lý, liên kết hóa đơn hoặc đã hoàn cọc cũng dùng phần vận hành riêng để không làm lệch dấu đối soát. Khi W đủ điều kiện, ghi chú/hẹn/nhân viên lưu qua proc với chi tiết món nguyên gốc.
- Migration 0022 thêm extra_notes và cancellation_policy tại MySQL, không đổi bảng/proc PMV. Trọng lượng hiển thị ba số lẻ (0,800), giữ giá trị chính xác cho tính tiền và lưu; sửa thông tin theo dõi không làm tròn dữ liệu gốc.
- 88 kiểm thử đã qua; trình duyệt kiểm khóa/mở bằng Passcode, trạng thái, lịch sử ghi chú, số tiền, ba số lẻ nhưng gửi đủ độ chính xác và bố cục điện thoại. Smoke sandbox tạo/sửa qua proc, đổi ngày hẹn với chi tiết nguyên gốc, khớp trọng lượng 8 số lẻ/tiền cọc và dọn phiếu thử; không phát sinh quỹ.

### Chọn phiếu cọc trong màn Bán hàng

- Tooltip từng phiếu: `✓ Mã SP: Tên SP`; hàng sẵn thiếu trong giỏ: `⚠️ Mã SP: chưa có trong danh sách.` Không còn dòng tiêu đề đối chiếu.
- Chỉ cần thiếu một mã hàng sẵn thì khóa cả phiếu. Mã lấy từ JSON ProductDesc (hỗ trợ `SP:` cũ). Hàng đặt khóa `Khách đặt` (hoặc `MA_DAT:` cũ) được dùng; phiếu trộn hai nguồn vẫn phải có đủ mã hàng sẵn trong đơn bán. Phiếu cọc không kèm món giữ cách dùng cũ.
- Kiểm tra mã chính xác, không phân biệt hoa/thường; kết quả đối chiếu tính theo giỏ hiện tại, không lưu chung vào cache khách. Đọc lại chi tiết khi bấm chọn, trước lưu/thanh toán và đối chiếu với chi tiết hóa đơn SQL trước gắn cọc. Phiếu đã chọn rồi bị thiếu hàng không thể thanh toán; người bán thêm lại hàng hoặc gỡ cọc.
- `tests.test_ban_coc_matching`: 8 ca kiểm tra phiếu thiếu một mã, hàng đặt, phiếu trộn, HTML disabled/escape, POST giả và chặn trước ghi hóa đơn/chứng từ. Fixture smoke áp tiền dùng hàng đặt để kiểm luồng miễn đối chiếu mã kho.

### Đối soát trước áp cọc — 11/09/2026

- GĐ chốt dùng chuẩn PMV: phiếu mới lấy `TDC` do `TRN_DATCOC_Ins → SYS_CodeMasters_Gen` cấp; `ShopID` theo tài khoản PMV. Không sinh `TRC` riêng, không đổi khóa phiếu cũ. `TRN_DATCOC_DT.TrnID` liên kết đúng phiếu chính; mỗi món có `TrnDTID` riêng.
- Giao diện dùng `TrnID` làm mã phiếu chính; `BillCode` giữ nguyên là số chứng từ cọc. Số hóa đơn áp dụng lấy từ `TRN_RT_BUYSELL_DatCoc → TRN_RT_BUYSELL.BillCode`, hiển thị riêng ở danh sách, popup và CSV. Có thể tìm theo số hóa đơn liên kết.
- Một hóa đơn dùng nhiều phiếu cọc; tổng cấn là tổng các phiếu được liên kết, không nhân theo số món. Tiền cọc đã thu được giữ nguyên chứng từ/ngày thu, kể cả tháng trước. Hóa đơn chỉ thu phần còn lại; không ghi thu cọc lần nữa. Bài sandbox đọc lại số chứng từ/ngày thu/quỹ cọc trước–sau và xác nhận quỹ hóa đơn đúng phần còn lại.

- `deposit_application.check` dùng chung khi chọn, liên kết và chốt: đúng khách, tiền không âm và khớp chứng từ quỹ đã P, cọc P, chưa hủy/hoàn hoặc dùng cho hóa đơn bán/đổi khác. Cọc W/R cũ phải đối soát nguồn tiền, không tự thu lại hoặc đổi trạng thái để vượt kiểm tra. Hàng đặt chỉ miễn đối chiếu mã sản phẩm.
- Vendor `TRN_RT_BUYSELL_Complete` trên KK/sandbox bắt buộc cọc P, rồi chuyển cọc C. Bấm lại hóa đơn C chỉ xác minh/hoàn tất bước két còn thiếu; không gọi Complete lần nữa. Đọc lại cả mã hàng, tiền và quỹ trước khi đánh dấu đã sử dụng.
- Thao tác `apply` ở MySQL chuyển `running → linked → done`. `linked` nghĩa là liên kết hóa đơn nháp, chưa phải đã sử dụng. Hóa đơn chốt và quỹ P mới giải phóng khóa thao tác và đánh dấu hoàn thành. Danh sách báo cáo cộng toàn bộ phiếu cọc của cùng hóa đơn.
- Lưu mã hóa đơn ngay khi vendor cấp mã, trước bước cập nhật tiền cọc. Yêu cầu tạo hóa đơn có cọc được giữ bền; mất phản hồi Ins thì dừng tạo lại để đối soát.
- Chứng từ cọc có nút gỡ toàn bộ liên kết khỏi hóa đơn W chưa phát sinh quỹ. Popup ký snapshot hóa đơn/liên kết; gỡ qua proc vendor rồi tính lại PayAmount bằng luồng cập nhật hóa đơn. Gián đoạn giữ bằng chứng và chặn chốt, không thu/hoàn tự động. Khi phục hồi xong cần nạp lại hóa đơn từ danh sách.
- Scheduler chỉ đọc PMV khi phục hồi thao tác liên kết đang dở; không phát lại bước tiền chưa rõ kết quả. Nhánh đối soát CK sau thu được phép điều chỉnh phân bổ theo luồng riêng bên dưới. Phiếu cũ thiếu chứng từ phải xác minh riêng, không tự thu lại.
- Kiểm thử: `tests.test_deposit_application` cùng matching/operations/workspace/editor/editing; `smoke_ban_coc` trên sandbox kiểm chưa thu, nhiều cọc, bấm lại, phục hồi bản nháp, chốt lặp và cọc dư, dọn dữ liệu thử. Không đổi schema hoặc stored procedure PMV.

## Lưu chi tiết JSON và trạng thái trong gold_bill — 12/09/2026

- KK giữ `TRN_DATCOC` là phiếu tổng và `TRN_DATCOC_DT` là chi tiết; proc vendor gắn cùng `TrnID`, cấp tiền tố `TDC`. ProductDesc JSON được kiểm giới hạn 500 đơn vị UTF-16 trước khi ghi. Không thêm cột ProductCode vào KK.
- `gold_bill.bill_kind='deposit'`, khóa `(target,trn_id)`, giữ tiến độ từng món/phiếu, ngày hẹn, nhân viên, giá chốt/tạm tính, phân bổ dự kiến, lịch liên hệ và ghi chú bổ sung. `DepositOrderState` là proxy trên bảng này. Manager hóa đơn bán loại cọc để tránh cộng doanh số hoặc đánh dấu xóa nhầm khi đối soát bán.
- Bốn ảnh nằm trong BLOB `dc_photo_sample1/2`, `dc_photo_finished1/2`; endpoint kiểm quyền và trả ảnh riêng tư. Migration chuyển ảnh cũ nếu tệp còn tồn tại, giữ nguyên tệp nguồn. Danh sách không tải BLOB.
- Migration 0024/0025 đã chuyển 3 trạng thái cọc. Bảng `pos_depositorderstate` giữ nguyên làm bản đối chiếu, runtime không ghi tiếp vào đó. 1.927 dòng hóa đơn bán và toàn bộ bảng giá được đối chiếu giữ nguyên lúc chuyển đổi. Bản sao trước chuyển: `logs/deposit-storage-before-20260912-165639.json`.
- Kiểm thử: 112 bài unit/integration, sandbox CRUD ProductDesc JSON + đọc lại Notes/TrnID + xóa sạch phiếu thử. Trang danh sách chạy được sau nâng cấp.
- Quy tắc “Lưu phiếu = đã thu tiền” đã triển khai theo xác nhận nhân viên và đối soát CK sau, xem mục tiếp theo. Không tự thu lại các phiếu cũ chỉ có TienCoc nhưng thiếu chứng từ quỹ.

## Lưu xác nhận thu và đối soát CK sau — 12/09/2026 17:15

- Phiếu mới luôn thực hiện thu cọc khi bấm **Lưu & xác nhận đã thu**. Nhân viên có quyền lập phiếu và liên kết cửa hàng/két PMV; số tiền phải > 0. Mặc định nhập tiền mặt; nếu NV đã xác nhận CK có thể nhập phân bổ ngay, không phải chờ ngân hàng. Sửa thông tin phiếu đã thu không thu lại. Phiếu cũ W chỉ thu khi NV chọn thao tác thu riêng.
- Luồng thu (đính chính theo GĐ): `TRN_DATCOC_Complete → gateway.pmv_deposit_money → T_TILL_TXN_Proc`. Ghi trực tiếp `CashPay`, `CardPay`, `TienCoc = CashPay + CardPay`; Status vẫn do PMV quản lý. Không gọi `CARDPAY_Ins` cho cọc. Token dùng một lần và active_key giữ bền khi kết quả chưa rõ.
- Scheduler 1 phút gọi `deposit_bank.process_bank` cho phiếu có `payment_plan.reconcile_enabled`. Tìm tiền vào chưa dùng, từ ngày tạo phiếu đến hiện tại, `description LIKE %TrnID%`, không phân biệt hoa thường. Chấp nhận mã dính liền nội dung khác. Không lấy số hóa đơn bán hoặc chỉ khớp số tiền.
- Tổng cọc giữ cố định: CK là tổng các giao dịch ngân hàng duy nhất đã khớp, TM = tổng cọc − CK. Nếu đã nhập CK thủ công thì giữ ít nhất phần NV đã xác nhận, báo khớp một phần cho tới khi đủ bằng chứng. CK không phải khoản tự động thu thêm.
- Mỗi ID ngân hàng chỉ dùng một lần, kiểm cả nghiệp vụ bán/thâu và Operation.bank_key. Claim ngân hàng + Operation trong transaction MySQL; lưu fingerprint chứng từ. Ngân hàng UPSERT làm đổi chứng từ đã dùng → báo kiểm tra, không tự đảo quỹ. Không sửa bill_code_raw. Chạy lại không gọi CARDPAY lần nữa nếu phân bổ đã đúng.
- Khi cần chuyển tiền mặt sang CK: chỉ tự sửa phiếu P chưa liên kết, cùng ngày, cấu hình PMV không cấp lại BillCode khi chốt. Qua `T_TILL_TXN_Del Type=0` để đảo phần quỹ cũ, kiểm W/quỹ rỗng, rồi chốt lại đúng phân bổ. Giữ TrnID/BillCode/ngày/tổng/khách/cửa hàng; kiểm số dư két trên sandbox. Các proc là nhiều bước: mất phản hồi giữa chừng → uncertain, giữ khóa/bằng chứng, không phát lại hoặc cho áp phiếu đang dở.
- Phiếu ngày trước hoặc đã gắn hóa đơn, nếu phải đổi số tiền TM/CK thì hiện **CK cần kiểm tra**. Nếu phân bổ đã đúng, chỉ bổ sung bằng chứng CK được phép mà không sửa quỹ. CK vượt tổng, nội dung chứa nhiều mã, tiền bị thay đổi bên PMV cũng báo kiểm tra; không tự tăng tổng hay làm tiền mặt âm.
- Báo trạng thái CK trên danh sách và trong Chứng từ cọc. Bộ lọc Đối soát cọc lấy cả lỗi CK; dữ liệu vẫn ở gold_bill, nhật ký thao tác tiền ở DepositMoneyOperation/DepositEvent.
- Kiểm chứng: 120 tests pass; `smoke_datcoc_bank` trên sandbox thu 1.000.000 TM → CK 300.000 → 600.000 → 1.000.000 → chạy lại, quỹ đúng từng bước và InFlow/OutFlow/TillBal trở về ban đầu khi dọn. Backup COPY_ONLY đã VERIFY: `D:\KHJ_PMV_BACKUP\PMV_BANLE_KH2_before_dc_bank_20260912_171407.bak` trên KK.


## Bỏ CARDPAY_Ins, giữ tổng cọc cố định — 12/09/2026

- GĐ xác nhận lại: tổng cọc 1.000.000, ngân hàng khớp 300.000 → CashPay=700.000, CardPay=300.000, TienCoc=1.000.000. Không cộng CK thành khoản thu thêm. Mặc định lúc lưu CashPay=TienCoc, CardPay=0; giữ hỗ trợ NV nhập phân bổ đã nhận như trước.
- Ngoại lệ ghi trực tiếp được GĐ cho phép, chỉ qua gateway chuyên biệt, không có SQL tùy ý. Khóa và đối chiếu phiếu, không có liên kết hóa đơn, quỹ U bằng tổng cọc, tiền chưa phân bổ. Ghi ba cột tiền và mốc cập nhật; khi có CK, gọi cố định `TRN_TILL_TXN_Upd(TDC,TDC)` trong cùng transaction rồi kiểm lại quỹ bằng tiền mặt trước commit. Lỗi rollback cả phần phiếu/quỹ U; chốt ghi KK, khóa hệ thống, audit vẫn giữ.
- Đối soát sau vẫn theo cơ chế đã duyệt: tập ID ngân hàng duy nhất, TM=tổng−CK; không đổi tổng, không dùng CK hai lần. Muốn sửa phân bổ quỹ đã P thì dùng proc đảo quỹ Type=0 → Complete → gateway phân bổ trực tiếp → chốt két. Gián đoạn giữ khóa không phát lại; phiếu ngày trước/đã gắn HĐ/CK vượt tổng cần kiểm tra. Không tác động CARDPAY của bán/thâu.

- Xác minh bản cập nhật: 129 tests qua; smoke đối soát 1 triệu lần lượt CK 300.000/600.000/1.000.000 và gọi lại giữ nguyên tổng, két đúng rồi dọn trả số dư ban đầu. Smoke áp hai phiếu TM/CK vào một hóa đơn qua; chỉ thu phần còn lại. Source ba proc liên quan KK/sandbox khớp. Backup COPY_ONLY + VERIFY: `D:\KHJ_PMV_BACKUP\PMV_BANLE_KH2_before_dc_direct_20260912_172656.bak`. Web và scheduler KHBL đã nạp bản này.


## Bắt lỗi thêm/sửa phiếu — 12/09/2026

- Ngày hẹn và khách hàng bắt buộc; nhân viên chỉ chọn từ T_EMPLOYEE.Active=1, mã ngoài danh sách bị từ chối khi POST (kể cả sửa thông tin không cần Passcode).
- Hàng sẵn kiểm mã/tình trạng kho và lấy lại thông tin từ kho. Hàng đặt bắt buộc tên, loại vàng và TL vàng lớn hơn 0, không suy TL vàng từ tổng TL để lách trường bỏ trống.
- Tổng tiền mặt + CK phải lớn hơn 100.000₫ (đúng 100.000 bị từ chối). Máy chủ tính lại tổng từ hai phần tiền kể cả POST thiếu editor_version; không nhận tổng giả gửi lên. Giữ kiểm số âm, trống và không phải số.
- Lỗi tại ô/row, ngày hẹn/tên/TL có dấu bắt buộc; kiểm nhanh khách/TL/tiền trên trình duyệt. Dòng đã bỏ không còn chặn required của trình duyệt. Không đổi quỹ hoặc đối soát banking.
- 136 tests qua; kiểm popup thật: 100.000 báo lỗi, 100.001 hết lỗi ngưỡng tiền; đóng popup không lưu phiếu thử. Web/scheduler KHBL đã nạp bản mới, JS có phiên bản cache mới.


## Hoàn thành đặt/áp dụng và popup XEM — 12/09/2026

- Tiến độ `delivered` hiển thị **Hoàn thành - đặt**, `applied` hiển thị **Hoàn thành - áp dụng** trong gold_bill. Cả hai thuộc nhóm Đã giao/đã hoàn thành, không phát sinh nhắc hẹn công việc còn mở.
- `deposit_application.finish` chỉ xác nhận áp dụng sau khi hóa đơn C, quỹ P và cọc/liên kết đọc lại khớp. `deposit_completion.sync` cập nhật trạng thái MySQL; không ghi tiền PMV. Chọn “Hoàn thành - áp dụng” thủ công không vượt kiểm chứng hóa đơn.
- Gỡ liên kết sau phục hồi hóa đơn nháp hoặc xóa HĐ thành công gọi `after_detach`, đọc lại phiếu rồi chuyển hoàn thành - đặt. Scheduler kiểm cả các lượt apply đã done và phiếu hoàn thành để nhận thay đổi từ PMV desktop; lỗi chứng từ giữ trạng thái cần kiểm tra, không tự thu/hoàn. Áp lại cùng HĐ vẫn cập nhật được, không bị event token cũ bỏ qua.
- Phiếu đã áp dụng vẫn XEM/SỬA ngày hẹn, ghi chú mới, ảnh và nhân viên. Khách/món/tiền vẫn được bảo vệ, trạng thái áp dụng không thể đổi tay khi HĐ còn sử dụng cọc. Muốn xóa phiếu phải gỡ áp dụng theo luồng HĐ trước.
- Bỏ dc-document-identity và dc-edit-access khỏi popup; mã phiếu nằm ở tiêu đề, nút mở sửa Passcode gọn vẫn giữ khi được phép. XEM dùng bố cục khách/đặt/tình trạng, bảng món 7 cột, tổng vàng/tiền, bốn ảnh; thao tác phụ trong “Thao tác khác”. Bỏ các diễn giải thừa, giữ nguyên nội dung ghi chú khách. Dấu “ĐÃ ÁP DỤNG” có hiệu ứng đóng dấu, tôn trọng prefers-reduced-motion, kèm số hóa đơn.
- 143 tests qua. Smoke PMV sandbox xác nhận áp nhiều cọc, gỡ, áp lại, xóa hóa đơn đưa phiếu về hoàn thành - đặt mà tiền cọc vẫn nguyên; dọn dữ liệu thử. Kiểm popup thật XEM/SỬA, tiền và trọng lượng khớp, hai lựa chọn hoàn thành đúng. Backup COPY_ONLY + VERIFY: `D:\KHJ_PMV_BACKUP\PMV_BANLE_KH2_before_dc_completion_20260912_183019.bak`. Đã cập nhật web/scheduler KHBL.

## Mẫu in phiếu cọc — 12/09/2026

- Nút In phiếu từ popup XEM mở tab xem trước `view/?print=1`, khổ A5 dọc. Người dùng bấm In phiếu trong bản xem trước mới gọi hộp thoại in; không tự in khi tải trang.
- Mẫu tương tự Mobile: thương hiệu, mã TDC, QR mã phiếu, khách/nhân viên/ngày hẹn, bảng món, TL ba số lẻ, tiền mặt/CK/tổng cọc, ghi chú và ký nhận. Dấu đã áp dụng đi kèm số hóa đơn nếu có.
- Dùng chung dữ liệu/quyền của popup XEM. QR SVG sinh nội bộ bằng segno, không gửi mã phiếu tới dịch vụ ngoài; response private/no-store. CSS riêng ẩn thanh công cụ khi in, lặp đầu bảng và tránh tách dòng sản phẩm.
- Kiểm thử: 12 bài test_deposit_editing đạt, gồm preview, dữ liệu và không tự gọi in. Đã mở bản xem trước TDC260900000006 trên trình duyệt, kiểm tiền/chi tiết/QR; chưa gửi lệnh in giấy.

## Phiếu cọc in lên Giấy đảm bảo — 12/09/2026

- Dùng nền GĐB in sẵn static/img/gdb-a5-blank.jpg (148×210 mm) để xem trước. Khi in bỏ nền hoàn toàn, chừa logo/tiêu đề/lưu ý/chữ ký có sẵn. Kế thừa đúng cấu hình máy in _in từ gdb_layout (khổ, canh, dx/dy, tỷ lệ); không sửa mẫu hóa đơn bán.
- Phần giao khách: PHIẾU ĐẶT CỌC, mã/QR, khách, ngày đặt/hẹn, bảng món, tổng cọc và bằng chữ, nhân viên. Phần tiệm giữ có hai cột dùng chung một partial và cùng dữ liệu; chừa logo dưới bên trái.
- JS căn vùng in trước khi mở nút In. Phiếu nhiều món/ghi chú dài được chia tờ, đánh số trang; hai bản tiệm giữ giống nhau trên từng tờ. Tổng tiền ghi rõ tổng cọc cả phiếu. Chỉ gọi print khi người dùng bấm; không in tự động.
- 12 kiểm thử Django đạt. QA trình duyệt headless: hai món vừa một tờ; 16 món và ghi chú đủ dữ liệu, không tràn vùng; ghi chú dài được chia không mất ký tự; hai cột giống nhau; chế độ in không còn nền. scripts/check_deposit_gdb_print.py + .cjs, ảnh tại logs/deposit-print-qa/. Không in giấy thật để thử.

## Mẫu in theo hai ảnh tham chiếu — 13/09/2026

- Giao khách theo Untitled 2332.png: QR trái, khung đen và chữ trắng PHIẾU ĐẶT HÀNG + mã phiếu; khách/SĐT che chỉ còn 4 số cuối/địa chỉ bên trái, ngày đặt/hẹn/cọc bên phải. Bảng #, [mã] tên, vàng, size, TL 3 lẻ; ký khách/nhân viên và dải đen nhắc giữ phiếu.
- Tiệm giữ theo Untitled 2335.png: QR trái, mã phiếu/khách/SĐT đầy đủ/ngày đặt và hẹn bên phải, từng món gọn tên + GoldCode + TL. Hai cột dùng chung partial, cùng dữ liệu.
- Các trường không có trong ảnh (tạm tính, phân tích TM/CK, ghi chú, bằng chữ) không đưa lên mẫu này. Dữ liệu phiếu gốc giữ nguyên. Tiếp tục căn vùng GĐB in sẵn, cấu hình máy in đang dùng, xem trước và phân trang an toàn; chỉ hiện số trang khi nhiều tờ.
- 12 kiểm thử đạt; QA Chromium: 3 món/1 tờ, 16 món không mất dòng, hai bản tiệm giống nhau, SĐT giao khách đã che và tiệm giữ đủ; nền giấy không in, các dải đen/chữ trắng vẫn hiện khi in. Ảnh QA logs/deposit-print-qa/ink-only.png.

## Một phiếu một trang và cấu hình CỌC riêng — 13/09/2026

- Bỏ nhãn ký Khách hàng/Nhân viên. Tên NV giao khách nằm dưới dải nhắc giữ phiếu. Hai bản tiệm giữ thêm Cọc dưới ngày đặt/hẹn và tên NV cố định cuối mỗi cột.
- Thay phân trang bằng đúng một tờ 148×210 mm. Bảng món tự thu nhỏ trong vùng cấu hình, tối đa còn 60%; nếu vẫn tràn hoặc khối vượt giấy thì khóa In, không bỏ dòng. Kiểm lại trước khi in; nền giấy chỉ dùng xem trước. Khổ thực tế máy in phải phù hợp tờ GĐB (A5, hoặc tờ lớn hơn theo cấu hình).
- Hệ thống → Mẫu in CỌC `/he-thong/mau-in-coc/`: xem mẫu hoặc nhập mã phiếu thật, kéo thả/chỉnh tọa độ %, kích thước/cỡ chữ của sáu khối, chỉnh khổ/căn/lệch/tỷ lệ máy in, Mặc định/In thử/Lưu mẫu. Mặc định chỉ đổi bản xem trước cho đến khi bấm Lưu.
- Lưu tại `PmvState.deposit_print_layout`, độc lập `gdb_layout`; lần đầu kế thừa riêng cấu hình máy in GĐB. Kiểm số hữu hạn/giới hạn và khối nằm trên giấy. Quyền HE_THONG can_view để xem, can_edit để lưu; preview cùng origin được nhúng, vẫn giữ quyền XEM phiếu thật.
- 16 kiểm thử SQLite đạt; Chromium kiểm PDF A5 đúng một trang, hai bản giống nhau, vị trí NV/cọc, khóa phiếu 16/100 dòng mà vẫn giữ đủ dòng, cập nhật preview/lưu/reset. Không gửi lệnh in giấy và không ghi dữ liệu tài chính trong QA.

## Cho tràn bản xem trước, chỉ in trang 1 — 13/09/2026

- Theo yêu cầu mới, bỏ khóa In khi nội dung tràn vùng. Tiếp tục thu gọn bảng; nếu vẫn dư thì chia các dòng sản phẩm sang trang tiếp theo trong bản xem trước, cả giao khách và hai bản tiệm giữ cùng nhóm món.
- CSS in ẩn mọi trang tiếp theo, chỉ gửi trang đầu; phần vượt vùng trang đầu cũng được cắt tại vùng in. Hiện rõ “Chỉ in trang 1; phần từ trang 2 không được in.” Nút In thử ở cấu hình vẫn hoạt động khi tràn. Không ép người dùng chỉnh lại cỡ chữ trước khi in.
- QA Chromium: phiếu 16 món xem trước 2 trang; 100 món xem trước 10 trang, giữ đủ dòng. Cả hai cho bấm In và PDF kết quả chỉ có 1 trang. Mẫu thường vẫn 1 trang, hai bản tiệm giữ giống nhau; kiểm chỉnh cấu hình tạo tràn vẫn bật In thử.

## NV tự cập nhật tiến độ, điều kiện áp dụng cọc — 13/09/2026

- Đổi tiến độ trong SỬA lưu metadata gold_bill, không gọi thu/hoàn hoặc ghi lại chi tiết PMV. Bỏ khóa đảo tiến độ kết thúc/đã giao một phần; có thể mở lại phiếu đã hủy/hoàn thành. Bản Mobile đã đóng nhận quyết định mới của NV, vẫn phát hiện thay đổi nguồn phát sinh sau đó.
- Chuyển sang Hủy hoàn cọc/không hoàn cọc, kể cả đổi giữa hai chính sách, bắt buộc ghi chú mới. Giữ ghi chú cũ; popup Hủy đặt hàng cũng lưu lý do vào lịch sử ghi chú và chính sách riêng. Không tự mở popup hoàn tiền. Nhãn “Đã hủy - hoàn cọc” / “Đã hủy - không hoàn cọc” thể hiện lựa chọn NV; chứng từ tiền được đối soát riêng, không suy ra đã thực chi từ nhãn tiến độ.
- Hàng sẵn sàng = đã chuẩn bị xong. Hoàn thành - đặt = kết thúc theo dõi (quá hạn lâu/không liên hệ/khách bỏ cọc...); chọn trạng thái này không tự tăng số món đã giao. Phiếu đã kết thúc không giữ mã kho để chặn bán tiếp.
- Chỉ `ready` hoặc `delivered` đủ điều kiện tiến độ để áp cọc mới/lên hóa đơn nháp. Kiểm lại ở máy chủ khi chọn, liên kết và chốt, cùng điều kiện nguồn tiền/khách/mã hàng/liên kết vốn có. Đọc lại hóa đơn đã chốt cùng liên kết vẫn idempotent để phục hồi `applied`; không tái áp sang hóa đơn khác.
- Hoàn thành - áp dụng tự đồng bộ từ hóa đơn; không chọn tay để tạo áp dụng giả. Hủy thanh toán, xóa hóa đơn hoặc gỡ cọc thành công cập nhật về Hàng sẵn sàng, xóa mốc hoàn thành và số món đã giao; giữ tổng cọc và chứng từ cọc. Tiến trình nền vẫn phục hồi trường hợp đồng bộ gián đoạn.
- 153 kiểm thử đặt cọc/quỹ/đối soát trong SQLite đạt. Không phát sinh phiếu hoặc giao dịch PMV thật trong quá trình kiểm thử. Bộ test CK sửa thời điểm mẫu để không nằm ở tương lai khi chạy trước 10 giờ sáng.


## 13/09/2026 — Danh sách và gợi ý nhắc OA

- Tab DANH SÁCH chỉ phiếu chưa hủy / chưa hoàn thành; Phiếu mới theo tiến độ Đơn mới (waiting/new), không giới hạn ngày lập; Đang đặt = ordering + crafting; Hàng sẵn sàng = ready. Báo cáo vẫn tra cứu toàn bộ lịch sử theo bộ lọc.
- THÔNG BÁO tách gợi ý và hàng đợi đã soạn. Gợi ý gồm hàng sẵn sàng hoặc đến/quá hẹn. Mốc tham chiếu đầu = hẹn + 7 ngày; sau một lần OA xác nhận, lần kế tiếp không sớm hơn cả hẹn + 7 và lần gửi trước + 7 ngày. Không tự gửi vì xuất hiện trong gợi ý.
- X bỏ gợi ý theo target + phiếu + ngày đang xem, + tìm/thêm các phiếu còn mở. Các thao tác này chỉ ghi DepositEvent, không xóa phiếu hay đổi quỹ. Tin đang chờ / đang gửi / chưa rõ kết quả không được gợi ý lại. Phiếu đã gửi trong ngày cũng không gợi ý lại.
- Chọn tối đa 200 phiếu, một mẫu OA, giờ chung tùy chọn; Xem trước từng tin -> xác nhận lưu/lên lịch. Máy chủ xác thực lại phiếu, SĐT, nội dung, phiên bản mẫu. Mẫu chưa duyệt giữ waiting_template. Duyệt mẫu không tự phát lại lịch cũ: cần lên lịch lại.
- Description.zalo là mốc của lần nhắc tiếp theo, ví dụ {"zalo":{"2026-09-20":"nhắc lần 2"}}. Lịch sử tin sent trong MySQL mới là bằng chứng OA đã xác nhận. Không tăng số lần cho bản soạn / lỗi / uncertain.
- Sau gửi thành công, outbox reminder_zalo giữ mốc trong MySQL; job tiếp theo đồng bộ Description qua gateway cố định (chỉ Description + TrnDateTime_Upd), giữ ghi chú / thông tin JSON cũ và giới hạn 500 UTF-16 units. Không gọi TRN_DATCOC_Upd hay thủ tục quỹ. Không mở kênh SQL tùy ý.
- Lỗi kết nối / Description đầy: giữ outbox, thử đồng bộ lại sau 1 giờ, tuyệt đối không gửi lại OA. CRUD phiếu giữ khóa zalo hiện có. Không sửa các phiếu thật hoặc gửi OA trong kiểm thử triển khai.
- Hồi quy: 172 tests ĐẶT-CỌC / đối chiếu mã hàng trên SQLite; provider và gateway ghi được mock trong các test mới. UI kiểm tra tab Phiếu mới, danh sách gợi ý, chọn nhiều phiếu và popup + trên bản chạy.


### 13/09/2026 — Khoảng ngày hẹn trên tab phiếu

DANH SÁCH / Phiếu mới / Đang đặt / Hàng sẵn sàng dùng start–end để lọc promise_date, bao gồm hai đầu mút. Bỏ trống cả hai không giới hạn ngày (bao gồm phiếu chưa hẹn); một đầu trống là khoảng mở. Phiếu mới lấy mọi phiếu trạng thái Đơn mới (waiting/new), sau đó lọc thêm ngày hẹn. Chuyển tab không xóa khoảng ngày. Bộ lọc Công việc / Nguồn hàng / Của tôi nằm trực tiếp trên thanh lọc. Tab THÔNG BÁO giữ bộ lọc lịch gửi riêng; báo cáo giữ lọc ngày lập qua date_field=created. Kiểm tra 80 tests liên quan đạt.


### 13/09/2026 — Sửa Phiếu mới theo tiến độ

Yêu cầu mới thay quy tắc theo ngày lập: tab new lấy waiting + new (cùng nhãn Đơn mới), kể cả phiếu cũ từ Mobile và webapp. Không lấy phiếu đã chuyển tiến độ khác dù lập hôm nay. Đối chiếu thực tế không bộ lọc: Danh sách 75 = Đơn mới 33 + Đang đặt 1 + Hàng sẵn sàng 41; 51 kiểm thử liên quan đạt, gồm ma trận toàn bộ tiến độ và khoảng ngày hẹn trên cả 4 tab. THÔNG BÁO dùng logic gợi ý riêng.
