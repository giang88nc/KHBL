r"""
QUY TRÌNH CHUẨN BÁN HÀNG — bản FULL (GĐ chốt 05/09/2026), seed cho `manage.py seed_quy_trinh`.

Gốc số liệu: 4 thao tác THẬT trên PMVGoldRT máy KK ngày 05/09/2026 (trace + so từng dòng KK ↔ sandbox):
  05:57 bán 1 món TRB260900000604 · 06:19–06:22 tạo đơn rồi xóa (605) ·
  06:51–07:03 bán 2 món + tạo khách mới trong đơn + hủy thanh toán → về chờ → xóa (606) ·
  06:48 xóa đơn đã thanh toán (604). Phụ lục số liệu: docs/LUONG_BAN_HANG_PMV_20260905.md.

6 điểm GĐ chốt 05/09/2026 (ghi trong ghi chú từng bước, nhãn "Cần kiểm" = KHBL phải sửa theo):
  1. Ins NGAY món đầu (như app) → SP bị khóa P-008 mọi máy; kèm nút XÓA đơn bỏ dở + nhắc đơn W treo quá 30 PHÚT (GĐ chốt 05/09)
  2. Xóa đơn ĐÃ THANH TOÁN = 2 bước rõ ràng, 2 lần xác nhận
  3. BirthDate rỗng → gửi 01/01/1900 (app gửi ngày hôm nay = sai dữ liệu)
  4. Dẻ vụn đi theo MÃ ĐƠN: Upd/Del đơn là dẻ đi theo, xóa đơn xóa luôn
  5. (để trống) → HoaDonDienTu_UpdateMa giữ "cần kiểm sandbox"
  6. Giữ mã BAN_HANG · (7) sửa đơn đã thanh toán = NHÁNH (pha 6 → 2/3 → 4), không pha riêng
"""
from .models import PmvProcessStep

L, K, R, B = (PmvProcessStep.Khbl.LAM, PmvProcessStep.Khbl.KIEM, PmvProcessStep.Khbl.RIENG, PmvProcessStep.Khbl.BO)
KHOP, CHUA, LECH = PmvProcessStep.DoiChieu.KHOP, PmvProcessStep.DoiChieu.CHUA, PmvProcessStep.DoiChieu.LECH

# (title, proc, ghi/đọc, lặp ×N, tham số quan trọng, bảng đổi, KHBL, đối chiếu, ghi chú)
BAN_HANG = {
    "code": "BAN_HANG",
    "name": "Bán hàng — FULL: nhiều SP + dẻ vụn → ĐƠN CHỜ → DUYỆT → ĐÃ THANH TOÁN · khách có sẵn/tạo mới · hủy thanh toán · xóa đơn · chống trùng",
    "description": (
        "Vòng đời đơn bán lẻ trên PMVGoldRT đo thật 05/09/2026 (4 thao tác, 18+ bảng, so từng dòng KK ↔ sandbox). "
        "TRẠNG THÁI ĐƠN: chưa có → W (ĐƠN CHỜ, hiện trong DS chờ ngay từ món đầu) → C (ĐÃ THANH TOÁN) ⇄ hủy thanh toán về W → xóa → chỉ còn trong *_Log. "
        "TRẠNG THÁI SP: I (trong kho) → nằm trong dòng đơn W (T_PRODUCT vẫn I nhưng proc quét CHẶN P-008 'đang chờ duyệt mua bán' trên mọi máy) → S khi DUYỆT → về I khi hủy thanh toán / tự do khi xóa đơn. "
        "Tiền: SellAmount = tròn(GoldReal/100 × SellRate × 1000 + TaskPrice × 1000) · BuyAmount(dẻ) = tròn(GW/100 × BuyRate × 1000 × Pct/100) · "
        "Pay = (ΣBán − ΣDẻ) − Bớt + CôngThêm + VàngThêm − Cọc. Nhánh 'sửa đơn đã thanh toán' = pha 6 → pha 2/3 → pha 4."
    ),
    "source": (
        "Trace KK 05/09/2026: 05:56:53–05:58:41 (TRB260900000604 bán 1 món) · 06:19–06:22 (605 tạo rồi xóa) · "
        "06:51–07:03 (606: 2 món + I_CUSTOMER_Ins + T_TILL_TXN_Del '0' → W + Del) · 06:48 (604: T_TILL_TXN_Del '1' + Del) · "
        "đơn nhân viên bán 05/09: 607 (tạo khách giữa đơn, GĐ học DON_MOI) · 608 (nhiều món, HD_FULL) · 612 (có DẺ D18K 135 ly). "
        "Gộp 6 bản GĐ tự học (BAN_1 · HD_FULL · DON_MOI · HUY_HOA_DON · HUY_THANH_TOAN · KHACH_HANG_MOI). "
        "Định nghĩa proc đọc từ sys.sql_modules (T_PRODUCT_GetByCodeForSell dòng 219 P-008; TRN_RT_BUYSELL_Del B-001/B-002; T_TILL_TXN_Del hoàn T_PRODUCT→I dòng 1886/2096). "
        "Phụ lục số liệu: docs/LUONG_BAN_HANG_PMV_20260905.md. GĐ chốt 6 điểm 05/09/2026 (xem docstring quy_trinh_ban_hang.py)."
    ),
    "phases": [
        ("Điều kiện trước khi bán",
         "Két của user đang mở · bảng giá I_XRATE có giá loại vàng · user gắn EmpID/TillID/ShopID (sys_users).", [
            ("Két đang mở của người bán", "", "", False, "TillID = két user đang mở (vd TIL260500000001); két chưa mở → không vào sổ quỹ được", "", L, KHOP,
             "KHBL lấy từ sys_users.till_id (manage.py sync_pmv_users)"),
            ("Bảng giá có giá bán/giá dẻ", "", "đọc", False, "I_XRATE ShopID='' · SellRate theo GoldCcy · BuyRate cho mã dẻ D18K/D24K/D9999…", "", L, KHOP,
             "cache 5s services.bang_gia; app poll I_XRATE_GetAll liên tục"),
        ]),
        ("Khách hàng — có sẵn · tạo mới · vãng lai",
         "Chọn khách bất cứ lúc nào trước DUYỆT (mỗi lần đổi = 1 Upd). Không chọn = vãng lai CU0000000000000.", [
            ("Tìm khách (SĐT · tên · CCCD · mã)", "", "đọc", True, "app: I_CUSTOMER_Lst @p_LoaiGD='SRT' · KHBL: SELECT I_CUSTOMER LIKE (tên có dấu nháy làm vỡ proc Lst)", "", L, KHOP, ""),
            ("Tạo khách MỚI ngay trong đơn", "I_CUSTOMER_Ins", "ghi", False,
             "p_CustID='' · p_CustCode='' · p_CustName · p_Phone · p_Address · p_CMND · p_BirthDate dd/MM/yyyy · p_Gender 1=Nam/0=Nữ · p_Active='1' · TuDongNangHang='1' · p_Image=NULL · @MaGD · CustType (VIP/VVIP/CANHBAO/trống) — mã khách do SYS_CodeMasters_Gen cấp",
             "I_CUSTOMER +1 · I_GIAODICH_KHACHHANG +1 · SHOP_CUSTOMER +1 · SYS_CODEMASTERS (I_CUSTOMER) · I_DIEMTICHLUY +1 (đo 06:51–06:53); T_CUSTOMER_DEBT tạo lúc DUYỆT",
             K, CHUA,
             "Đo thật 06:52:13 khách 'Anh Abc' → CU2609000000192. ⚠ App gửi BirthDate = NGÀY HÔM NAY khi để trống (sai dữ liệu) — GĐ CHỐT 3: KHBL gửi 01/01/1900 khi rỗng. Popup tạo khách mở ngay trong màn bán (đã có _khach_form + quét QR CCCD)"),
            ("Khởi tạo điểm tích lũy cho khách mới", "I_DiemTichLuy_InsFromGT", "ghi", False, "@p_CustID = mã khách vừa tạo → bên trong EXEC I_DiemTichLuy_Ins",
             "I_DIEMTICHLUY +1", K, CHUA,
             "App gọi NGAY sau I_CUSTOMER_Ins (đo 06:52 CU…192, 07:23 CU…193, 07:26 CU…194 — bản BAN_1/KHACH_HANG_MOI/DON_MOI anh học). KHBL chưa gọi → thêm vào popup tạo khách. (App còn nạp form bằng SYS_LOADCOMBO I_CUSTOMER_GROUP + I_GIAODICH_Lst — KHBL không cần)"),
            ("Gắn / đổi khách trên đơn", "TRN_RT_BUYSELL_Upd", "ghi", True, "p_CustID = CustID thật hoặc CU0000000000000 · p_TrnDateTime_Upd = mốc Get liền trước",
             "TRN_RT_BUYSELL.CustID", L, KHOP, "đo 05:57:14 (604) và 06:52:16 (606)"),
        ]),
        ("Gom hàng vào ĐƠN CHỜ (Status W)",
         "GĐ CHỐT 1: tạo đơn (Ins) NGAY khi quét món đầu như app → SP bị khóa P-008 trên mọi máy ngay lập tức. Mỗi lần thêm/bỏ SP, thêm/bỏ dẻ = 1 Upd (kèm Get lấy mốc). GĐ CHỐT 4: dẻ vụn đi theo MÃ ĐƠN — Upd/Del đơn là dẻ đi theo.", [
            ("Quét tem / gõ mã SP", "T_PRODUCT_GetByCodeForSell", "đọc", True,
             "p_ProductCode · p_TaskPrice=0 · p_ShopID='' · p_CheckRealSL=1 · p_TillID=két · p_CustID='' · p_ShopID_XRate='' (⚠ '' mới có SellRate) · p_RutGon='0'",
             "", L, KHOP,
             "Lỗi vendor in NGUYÊN VĂN: P-002 không tồn tại · P-017 đã xuất/mượn · P-008 'Mã hàng <mã> đang chờ duyệt mua bán' (SP đang nằm trong đơn W/C bất kỳ máy nào — proc dòng 219) · P-013 quầy không thuộc két · P-004 hết số lượng"),
            ("Tạo ĐƠN CHỜ ngay món đầu", "TRN_RT_BUYSELL_Ins", "ghi", False,
             "p_TrnID='' · p_TrnDate dd/MM/yyyy · p_TrnTime · p_CustID vãng lai · p_SellTotalAmount = p_TotalAmount = p_PayAmount = SellAmount món đầu · p_Status='W' · p_CreatedBy · p_EmpID (NV mặc định của user) · p_ShopID · p_TillID · p_BanLeBanSi='BL' · XML <TRN_RT_BUYSELL_SELL> 1 dòng · 37 tham số",
             "TRN_RT_BUYSELL +1 (W) · TRN_RT_BUYSELL_SELL +1 · SYS_CODEMASTERS TRB+1 (TrnID) · SYS_BILL_COUNTER (BillCode yy-MM-dd-00000n)",
             K, CHUA,
             "GĐ CHỐT 1: KHBL phải đổi từ 'gom giỏ session, ghi khi THANH TOÁN' sang Ins ngay món đầu (bill.py + views ban_quet). TrnID/BillCode do proc cấp — web không tự sinh; app gửi p_TrnDateTime_Upd_GDN = giờ hiện tại (KHBL 1900 — kiểm cột đích)"),
            ("Đọc lại lấy mốc khóa lạc quan", "TRN_RT_BUYSELL_Get", "đọc", True, "p_TrnID · p_ShopID_XRate='' → header/lines/old_gold + TrnDateTime_Upd", "", L, KHOP,
             "mốc đổi sau MỌI bước ghi — đọc lại giữa các bước (luật 2 bill.py)"),
            ("Quét thêm SP #2..n", "TRN_RT_BUYSELL_Upd", "ghi", True,
             "XML dòng hàng TRỌN BỘ (60 cột từ Get + dòng mới từ GetByCodeForSell) · p_SellTotalAmount/p_TotalAmount/p_PayAmount tính lại · p_TrnDateTime_Upd = mốc",
             "TRN_RT_BUYSELL_SELL +1/món · TRN_RT_BUYSELL (tiền)", L, KHOP, "đo 06:51:48 (606: món 2 → SellTotal 23.195.000). KHBL gửi 42 cột — so dòng SELL với app trên sandbox"),
            ("Thêm / bỏ DẺ VỤN (vàng cũ khách đưa)", "TRN_RT_BUYSELL_Upd", "ghi", True,
             "dòng <TRN_RT_BUYSELL_BUYGOLD> cùng XML: GoldCode dẻ (D18K…) · GoldDesc · PriceUnit · TotalGoldWeight (ly) · DiamondWeight · GoldWeight = TL − hột · BuyRate (nghìn) · BuyAmount = tròn(GW/100 × BuyRate × 1000 × Pct/100) · PercentValue 100 · Dirty 0 · TruDoTrenChi 0 · GCatNi=GoldCode · p_BuyTotalAmount = Σ dẻ",
             "TRN_RT_BUYSELL_BUYGOLD ±n (DB lưu CcyRate=1 — vì thế KHÔNG dùng cột này, dùng hằng 1000) · TRN_RT_BUYSELL (BuyTotalAmount, TotalAmount = Sell − Buy)",
             L, KHOP,
             "GĐ CHỐT 4: dẻ đi theo mã đơn (Upd/Del kéo theo → sang _BUYGOLD_Log khi xóa). ĐO THẬT đơn 612 (26-09-05-000011, nhân viên bán 05/09): XML app gửi đúng 13 cột = bill.dong_doi(): D18K · TotalGoldWeight 135 · DiamondWeight 9 · GoldWeight 126 · BuyRate 8850 · BuyAmount 11.151.000 = 126/100 × 8850 × 1000 ✓ · PercentValue 100 · Dirty/TruDoTrenChi 0 · GCatNi=D18K; đầu phiếu Pay = 12.507.000 − 11.151.000 − 6.000 = 1.350.000 ✓"),
            ("Bỏ 1 SP khỏi đơn chờ", "TRN_RT_BUYSELL_Upd", "ghi", True, "XML thiếu dòng bị bỏ · tiền tính lại", "TRN_RT_BUYSELL_SELL −1", L, CHUA,
             "SP tự do ngay (P-008 hết chặn). KHBL ban_xoa hiện chỉ sửa session → sau CHỐT 1 phải thành Upd"),
        ]),
        ("Tính tiền", "Mọi ô tiền sửa = 1 Upd. Pay = (ΣBán − ΣDẻ) − Bớt + CôngThêm + VàngThêm − Cọc (khớp 100% hóa đơn thật 604: 11.473.000 − 3.000 + 30.000 = 11.500.000).", [
            ("Bớt · công thêm · vàng thêm · cọc", "TRN_RT_BUYSELL_Upd", "ghi", True,
             "p_Discount · p_TaskPriceAdd · p_Add (vàng thêm) · p_TienCoc · p_PayAmount = Total − Discount + TaskPriceAdd + Add − TienCoc · p_Description ghi chú",
             "TRN_RT_BUYSELL (Discount, TaskPriceAdd, AddMoney, TienCoc, PayAmount, TrnDateTime_Upd)", L, KHOP,
             "AddMoney/TienCoc chưa từng phát sinh trong 18.441 HĐ — dấu +/− theo GĐ chốt 03/09; TrnTime trong DB GIỮ giờ lúc Ins dù Upd gửi giờ mới"),
            ("Đổi nhân viên bán", "TRN_RT_BUYSELL_Upd", "ghi", False, "p_EmpID", "TRN_RT_BUYSELL.EmpID", L, KHOP, "đo 05:57 (EMP150400000001 → EMP250800000001)"),
        ]),
        ("DUYỆT — thanh toán (W → C)", "Khối ghi lớn nhất. Xong là đơn nằm trong DS ĐÃ THANH TOÁN, SP sang S, kho giảm, két tăng.", [
            ("Chốt hóa đơn", "TRN_RT_BUYSELL_Complete", "ghi", False, "p_TrnID · p_UserID · p_ThuHo='0'",
             "TRN_RT_BUYSELL Status W→C · T_PRODUCT I→S + SellTrnID/SellTrnType=SRT/SellOutDate · T_PRODUCT_TRACKING +1/món (CRDR −, TrnRefID = phiếu nhập gốc) · I_GOLD_BAL quầy −Qty/−TL/−tem · TonKho ±1 dòng ngày/quầy/tuổi · TRN_DAILY_LOG +4/món (PROD_WEI/GOLDWEI/QTY/STAMPWEI) · SYS_CODEMASTERS LOG · I_CUSTOMER.LastTradingDate · T_CUSTOMER_DEBT (tạo/LastModify) · I_LICHSUTICHLUYDIEM +1 · I_DIEMTICHLUY · RetailLog 'Complete_success_TinhTrangC'",
             L, KHOP, "proc con: SYS_CodeMasters_Gen · TRN_DAILY_LOG_Ins · I_LichSuTLD_Ins · I_QuyDiemTichLuy · I_DiemTichLuy_Ins · fun_GetHS · spQuyDiemTL"),
            ("Kiểm chưa có dòng sổ quỹ", "T_TILL_TXN_DETAIL_GetByTrnID", "đọc", False, "p_TrnID", "", L, CHUA,
             "KHBL chưa gọi — thêm để bước chốt idempotent (đã có sổ quỹ thì không Proc lần 2)"),
            ("Vào sổ quỹ (két)", "T_TILL_TXN_Proc", "ghi", False, "p_TrnIDs · p_TillID · p_UserID",
             "T_TILL_TXN +1 (TTX…, TrnType RT · TrnCode SRT · Status P · TrnTotalAmount = PayAmount) · T_TILL_TXN_DETAIL +1 · T_TILL_BAL InFlow/TillBal += PayAmount · SYS_CODEMASTERS TTX",
             L, KHOP, "proc con TRN_TILL_TXN_Ins"),
            ("Kiểm 6 điểm sau DUYỆT", "", "đọc", False,
             "TRN_RT_BUYSELL.Status='C' · T_PRODUCT.Status='S' mọi món · T_TILL_TXN có dòng TrnRefID · T_TILL_BAL tăng đúng PayAmount · I_GOLD_BAL giảm đúng TL · TRN_DAILY_LOG +4/món",
             "", L, CHUA, "đưa vào bill.chot() + smoke_ban_hang; hiện KHBL chỉ kiểm Status='C'"),
        ]),
        ("Sau duyệt", "Việc phụ của app: tin nhắn Zalo, số liệu HĐĐT, in phiếu, làm mới DS.", [
            ("Gửi ZNS Zalo (2 mẫu)", "SMS_Check", "đọc", False,
             "p_TrnCode='SRT' → PHONE_LIST_Get · SYS_SMS_SYSTEM_GetByTrnCode · SYS_SMS_SRT_GetInfo · SYS_Notification_Templates_Check · tbc_TinNhan_Ins ×2 · tbc_TinNhan_Upd ×2",
             "tbc_TinNhan +2", B, CHUA, "cả 2 tin lỗi 'Lỗi SMS Service' ngay trên app — GĐ chốt KHBL không làm SMS/Zalo"),
            ("Cập nhật số liệu HĐĐT (không phát hành)", "HoaDonDienTu_UpdateMa", "ghi", False,
             "@LoaiGiaoDich='SRT' · @TrnID · mã HĐĐT/số HĐ/GUID/json TRỐNG · @input = XML dòng hàng có GiaHDDT = Pay ÷ chỉ · DiscountAmount = TaskPriceAdd − Discount · AmountAfterDiscount · ThanhTienHDDT · @TotalAmountHDDT",
             "TRN_RT_BUYSELL_SELL DonGiaHDDT/AmountHDDT (?) · TRN_RT_BUYSELL TotalAmountHDDT (?)", K, CHUA,
             "GĐ để trống mục 5 → giữ CẦN KIỂM: xác định trên sandbox proc nào ghi DonGiaHDDT/AmountHDDT; nếu là proc này thì KHBL dựng XML giống app"),
            ("Đọc lại + khuyến mãi", "TRN_RT_BUYSELL_Get", "đọc", False, "p_TrnID → rồi TRN_PROMOTION_LOG_Lst", "", L, KHOP, "KHBL đọc lại đối chiếu mã hàng + tiền (luật 2)"),
            ("In hóa đơn / giấy đảm bảo", "rptSRT_PrintBill", "đọc", False, "p_TrnID · @xemtruoc='0'", "RetailLog '0 - TinhTrangC'", R, CHUA,
             "KHBL in giấy đảm bảo riêng; muốn có vết 'đã in' trong RetailLog thì gọi proc này (cần duyệt vào PROC_READ_ALLOW)"),
            ("Làm mới DS chờ / DS đã thanh toán", "TRN_RT_BUYSELL_Lst", "đọc", False, "p_FromDate/p_ToDate dd/MM/yyyy · p_Status='W' (chờ) / 'C' (đã thanh toán)", "", R, KHOP,
             "KHBL: poll hoa_don_nhip 3s + popup DANH SÁCH (⚠ bỏ TOP 100/200 — audit 05/09)"),
        ]),
        ("HỦY THANH TOÁN (C → W, đơn về DS chờ)",
         "Đo thật 06:58:30 (606): app dùng @p_Type='0' → đơn về W, hoàn TOÀN BỘ pha 4. Sửa đơn đã thanh toán = pha này → pha 2/3 → pha 4 lại (nhánh, GĐ chốt 7).", [
            ("Xem sổ quỹ đang chờ của đơn", "T_TILL_TXN_Lst", "đọc", False, "p_TrnFromDate/p_TrnToDate · p_Status='P'", "", R, CHUA, "app gọi trước và sau khi hủy để làm mới màn sổ quỹ"),
            ("Hủy thanh toán → về ĐƠN CHỜ", "T_TILL_TXN_Del", "ghi", False,
             "p_TrnRefID · pType='SRT' · pCongNoBanLe=0 · p_UserUpd · p_TrnDateTime_Upd = mốc hiện tại của đơn · p_TrnDateTime_Upd_GDN=1900 · **p_Type='0'**",
             "TRN_RT_BUYSELL Status C→W + TRN_RT_BUYSELL_Log +1 · TRN_RT_BUYSELL_SELL_Log +n · T_PRODUCT S→I (SellTrnID null) · T_PRODUCT_TRACKING −n · I_GOLD_BAL hoàn · TonKho hoàn · TRN_DAILY_LOG −4/món · T_TILL_TXN −1 → T_TILL_TXN_Log · T_TILL_TXN_DETAIL −1 → _DETAIL_Log · T_TILL_BAL −PayAmount · I_LICHSUTICHLUYDIEM −1 · I_CUSTOMER · ErrorLog +1 (vết kết nối vendor tự ghi, KHÔNG phải lỗi)",
             K, LECH,
             "⚠ KHBL bill.mo_lai đang dùng p_Type='1' và tài liệu 4c ghi ''0' không ăn gì' — SAI: '0' không ăn gì chỉ khi đơn còn W (không có gì để hoàn). Proc không rẽ nhánh theo p_Type với SRT, chỉ ghi giá trị vào *_Log. ĐỔI sang '0' để giống app (isLuuTam)"),
        ]),
        ("XÓA ĐƠN (→ chỉ còn trong *_Log)",
         "Đơn W xóa thẳng; đơn C phải HỦY THANH TOÁN trước (proc trả B-002). Dòng hàng + dẻ đi theo đơn sang bảng Log (GĐ chốt 4). SP tự do quét lại ngay; mã TRB/BillCode không tái dùng.", [
            ("Xóa đơn chờ (kể cả đơn bỏ dở)", "TRN_RT_BUYSELL_Del", "ghi", False,
             "p_TrnID · p_UserUpd · p_TrnDateTime_Upd = mốc · p_TrnDateTime_Upd_GDN=1900 · p_LogDel='0'",
             "TRN_RT_BUYSELL −1 → TRN_RT_BUYSELL_Log +1 (isLuuTam=1, DelUserID) · TRN_RT_BUYSELL_SELL −n → _SELL_Log +n · TRN_RT_BUYSELL_BUYGOLD −n → _BUYGOLD_Log · (TRN_GIAODICHNHANH, I_DOIQUA nếu có)",
             L, KHOP,
             "đo 06:22:30 (605) và 07:03:53 (606). Lỗi: B-001 không có đơn · B-002 đơn còn C. GĐ CHỐT 1: KHBL cần nút XÓA đơn bỏ dở (đã có ban_huy) + nhắc đơn W treo quá N giờ (pha 8)"),
            ("Xóa đơn ĐÃ THANH TOÁN — 2 bước, 2 xác nhận", "T_TILL_TXN_Del", "ghi", False,
             "bước 1: T_TILL_TXN_Del p_Type='1' (hủy thanh toán) → xác nhận · bước 2: TRN_RT_BUYSELL_Del với mốc MỚI (đọc lại sau bước 1) → xác nhận",
             "= pha 6 + bước trên", K, CHUA,
             "đo 06:48:49–50 (604): app chuỗi tự động 2 proc cách 1 giây. GĐ CHỐT 2: KHBL bắt người dùng làm 2 bước rõ ràng với 2 lần xác nhận — việc hệ trọng. Mốc khóa đổi sau bước 1 → phải Get lại"),
        ]),
        ("Kiểm soát trùng & đơn treo",
         "Vendor chặn sẵn P-008 trên mọi máy/user. KHBL bổ sung thông điệp có địa chỉ và dọn đơn treo (GĐ chốt 1).", [
            ("Vendor chặn SP đang trong đơn", "T_PRODUCT_GetByCodeForSell", "đọc", False, "→ rc=−1 · ErrorCode P-008 · ErrorDesc 'Mã hàng <mã> đang chờ duyệt mua bán'", "", L, KHOP,
             "proc dòng 219: EXISTS trong TRN_RT_BUYSELL ⋈ TRN_RT_BUYSELL_SELL — không phân biệt W/C, máy, user"),
            ("Báo rõ đơn nào đang giữ SP + nút MỞ", "", "đọc", False,
             "SELECT b.TrnID, b.BillCode, b.Status, b.CreatedBy, b.TillID, b.CreatedDate FROM TRN_RT_BUYSELL_SELL s JOIN TRN_RT_BUYSELL b ON b.TrnID=s.TrnID WHERE s.ProductCode=? (NOLOCK)",
             "", R, CHUA, "hiện 'đang trong đơn chờ 26-09-05-000002 · user · két · giờ'; nếu CreatedBy = user đang đăng nhập → nút MỞ đơn đó"),
            ("Nhắc đơn W treo quá 30 phút", "", "đọc", False, "SELECT TRN_RT_BUYSELL Status='W' AND CreatedDate < now − 30 phút (NOLOCK) → badge trên màn bán + popup DANH SÁCH", "", R, CHUA,
             "GĐ CHỐT 1 + chốt 05/09: N = 30 PHÚT. Hành động: MỞ tiếp hoặc XÓA (pha 7)"),
        ]),
    ],
}
