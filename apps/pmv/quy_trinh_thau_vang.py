r"""
QUY TRÌNH CHUẨN THÂU VÀNG — `THAU_VANG` = GỘP 2 quy trình GĐ học 08/09/2026 (THAU_VAO 11 bước · HUY_THAU_VAO 5 bước)
thành 1 vòng đời Thêm & Xóa, cùng khung với BAN_HANG (W → C → hủy TT → xóa). Seed: `manage.py seed_quy_trinh --ma THAU_VANG`.

Gốc số liệu: trace máy QQ 08/09/2026 07:17–07:26 (TBG…370/371 thanh toán rồi hủy · 373 tiền mặt · 372/391/395 CK do
nhân viên thâu thật) + thân 10 proc vendor đọc từ sandbox (Ins/Upd/Complete/CompleteMore/Del, TRN_TILL_TXN_Ins/_Upd,
T_TILL_TXN_Proc/_Del nhánh BRT, CARDPAY_Ins). Phân tích đầy đủ: DE_XUAT_THAU_VANG (08/09/2026, GĐ duyệt 3 chốt):
  1. Làm theo khung 6 pha; BỎ bước Upd thừa của app ngay sau Ins (chỉ Upd khi người dùng SỬA phiếu chờ).
  2. Tổng tiền = TotalAmount; có CHUYỂN KHOẢN → ghi CardPay (âm) + CashPay (âm) qua CARDPAY_Ins với DS thẻ RỖNG
     (proc chỉ UPDATE 2 cột + TRN_TILL_TXN_Upd sửa dòng VND két) — KHÔNG sinh dòng TRN_RT_BUYGOLD_CardPay.
  3. Phiếu in RIÊNG khổ máy in bill 110mm (thiết kế `_thau_phieu.html`), không dùng lại tờ Giấy đảm bảo.
"""
from .models import PmvProcessStep

L, K, R, B = (PmvProcessStep.Khbl.LAM, PmvProcessStep.Khbl.KIEM, PmvProcessStep.Khbl.RIENG, PmvProcessStep.Khbl.BO)
KHOP, CHUA, LECH = PmvProcessStep.DoiChieu.KHOP, PmvProcessStep.DoiChieu.CHUA, PmvProcessStep.DoiChieu.LECH

# (title, proc, ghi/đọc, lặp ×N, tham số quan trọng, bảng đổi, KHBL, đối chiếu, ghi chú)
THAU_VANG = {
    "code": "THAU_VANG",
    "name": "Thâu vàng — FULL: lập phiếu chờ → THANH TOÁN (tiền mặt / CK) → in 110mm · hủy thanh toán · xóa phiếu",
    "description": (
        "Vòng đời phiếu thâu độc lập TRN_RT_BUYGOLD trên PMVGoldRT, đo thật 08/09/2026 và đối chiếu thân proc. "
        "TRẠNG THÁI: chưa có → W (phiếu CHỜ, BillCode sinh NGAY lúc Ins vì TaoSoHDKhiThanhToan=0) → C (ĐÃ THANH TOÁN: "
        "vàng vào két, tiền ra két, cộng điểm, LastTradingDate) ⇄ hủy thanh toán về W → xóa CỨNG (không IsDel; vết chỉ còn ở "
        "*_Log do T_TILL_TXN_Del ghi — phiếu W chưa từng thanh toán bị xóa KHÔNG có log vendor, web tự ghi audit). "
        "Tiền: TotalAmount = tròn(GoldWeight(ly, đã trừ hột) ÷ 100 × BuyRate(nghìn/chỉ) × 1.000 × %tuổi ÷ 100) + AddMoney; "
        "hột không tính tiền nhưng vào két (Amount_Total). Thanh toán CK: CardPay = −CK, CashPay = −tiền mặt, dòng VND két = tiền mặt."
    ),
    "source": (
        "GỘP 2 quy trình GĐ học 08/09/2026: THAU_VAO (seq 2336761→2337597, TBG260900000373 tiền mặt) + HUY_THAU_VAO "
        "(seq 2335072→2335879, hủy 2 phiếu 370/371 đã C). Đối chiếu thêm dòng KK thật 372/391/395 (CK: CashPay=0, CardPay=−tổng, "
        "dòng VND két = 0, có TRN_RT_BUYGOLD_CardPay do app) và thân proc sandbox. GĐ duyệt 3 chốt 08/09/2026 (docstring)."
    ),
    "phases": [
        ("Điều kiện trước khi thâu",
         "Két/EmpID/TillID/ShopID của user (sys_users) · giá THÂU lấy từ MySQL gold_prices (như bán) · khách có sẵn / tạo mới / vãng lai.",
         [
             ("Tài khoản gắn két + NV + tiệm", "", "", False,
              "TillID = két user (TIL…); rỗng → CHẶN vì TRN_TILL_TXN_Ins để TillID NULL tới khi T_TILL_TXN_Proc", "—", L, KHOP,
              "KHBL: _phien(request) — thiếu till_id/user_id/shop_id thì không cho LƯU/THANH TOÁN"),
             ("Giá thâu theo mã dẻ", "", "đọc", False,
              "BuyRate D18K/D24K/D9999… — KHBL: MySQL gold_prices quy về nghìn/chỉ (services.loai_vang_thau)", "—", L, KHOP,
              "App đọc I_XRATE; web ép giá MySQL lúc lưu, không nhận giá tay"),
             ("Khách: có sẵn · tạo mới · vãng lai", "I_CUSTOMER_Ins", "ghi", False,
              "Tạo mới = quy trình THEM_KHACH (I_CUSTOMER_Ins → I_DiemTichLuy_InsFromGT); không chọn = CU0000000000000 (không tích điểm)",
              "I_CUSTOMER, I_DIEMTICHLUY", L, KHOP, "Popup ＋ THÊM dùng chung với DS khách (khachSaved → gắn vào phiếu)"),
         ]),
        ("Lập PHIẾU CHỜ (Status W)",
         "GĐ CHỐT 1: Ins khi bấm LƯU NHÁP / THANH TOÁN (không Ins ngầm lúc đang tính thử). BỎ bước Upd thừa của app ngay sau Ins.",
         [
             ("Lập phiếu", "TRN_RT_BUYGOLD_Ins", "ghi", False,
              "p_TrnID='' · p_TrnDate/p_TrnTime (proc BỎ, dùng GETDATE KK) · p_EmpID · p_CustID · p_GoldCode · p_GoldWeight ly ĐÃ TRỪ hột · "
              "p_DiamondWeight · p_BuyRate nghìn/chỉ · p_PercentValue · p_TotalAmount · p_CustPay=TotalAmount · p_AddMoney · p_GoldAge=p_AgeChange='1' · "
              "p_Dirty/TruLai/TruGia/TruDoTrenChi=0 · p_GoldWeightChange=GoldWeight · p_TillID · p_PriceCcy='VND' · p_CcyRate='1000' · p_ShopID · "
              "p_CreatedBy=UserID · p_TrnID_GDN='' · p_SoHDTuNhap=''",
              "TRN_RT_BUYGOLD +1 (W) · SYS_CODEMASTERS (TrnID) · SYS_BILL_COUNTER (BillCode BRT)", L, KHOP,
              "Trả ErrCode/TrnID/ErrorDesc. Phiếu W bỏ dở vẫn chiếm 1 số hóa đơn (như app). bill.luu_thau"),
             ("Đọc lại lấy mốc khóa lạc quan", "", "đọc", True,
              "SELECT TrnDateTime_Upd, BillCode, TotalAmount FROM TRN_RT_BUYGOLD WHERE TrnID=? — kiểm tiền khớp", "—", L, KHOP,
              "bill.phieu_thau; Complete KHÔNG đổi mốc, Upd/T_TILL_TXN_Del CÓ đổi"),
             ("Sửa phiếu chờ (đổi TL/giá/khách/ghi chú)", "TRN_RT_BUYGOLD_Upd", "ghi", True,
              "Bộ tham số như Ins + p_TrnID · p_UserUpd · p_IsGiaoDichNhanh='0' · p_CreatedDate=NULL · p_TrnDateTime_Upd = mốc vừa đọc",
              "TRN_RT_BUYGOLD (TrnDateTime_Upd đổi)", L, KHOP,
              "goi_co_khoa + kiểm mốc đã đổi; phiếu C → vendor từ chối B-002. App gọi Upd THỪA ngay sau Ins — web bỏ (GĐ chốt 1)"),
         ]),
        ("THANH TOÁN (W → C): vàng vào két, tiền ra két",
         "Chuỗi app đo 07:25:39: CompleteMore → T_TILL_TXN_DETAIL_GetByTrnID → T_TILL_TXN_Proc. CK: chèn CARDPAY_Ins giữa 2 bước (GĐ chốt 2).",
         [
             ("Chốt phiếu", "TRN_RT_BUYGOLD_CompleteMore", "ghi", False,
              "@p_TrnIDs='TBG…@' (đuôi @) → lặp TRN_RT_BUYGOLD_Complete: Status='C' · TRN_TILL_TXN_Ins (T_TILL_TXN 'U' TillID NULL + DETAIL dẻ '+' TL/hột/dirty, "
              "VND '−' TotalAmount) · tích điểm (I_QuyDiemTichLuy → I_LichSuTLD_Ins → I_DiemTichLuy_Ins) · I_CUSTOMER.LastTradingDate=hôm nay",
              "TRN_RT_BUYGOLD (C) · T_TILL_TXN +1 · T_TILL_TXN_DETAIL +2 · I_LICHSUTICHLUYDIEM +1 · I_DIEMTICHLUY · I_CUSTOMER", L, KHOP,
              "Từ chối BRT-001 (đã C) / B-001 (không có phiếu). KHÔNG đổi TrnDateTime_Upd. bill.chot_thau"),
             ("Kiểm chứng sau chốt", "", "đọc", False,
              "Status='C' + có dòng T_TILL_TXN 'U' theo TrnRefID (app: T_TILL_TXN_DETAIL_GetByTrnID)", "—", L, KHOP, "không tin rc=0 trần"),
             ("Có CHUYỂN KHOẢN / thẻ → ghi CardPay + sửa dòng VND két", "CARDPAY_Ins", "ghi", False,
              "@p_TypeTrade='BRT' · @p_TrnID · @p_TillID · @p_TillTxnID (dòng 'U' vừa tạo) · @p_Amount=−CK · @p_AmountTra=−tiền mặt · "
              "@p_List='<NewDataSet/>' (DS thẻ RỖNG → không INSERT TRN_RT_BUYGOLD_CardPay) · @p_ProductIDs=NULL (⚠ '' sẽ rẽ nhánh SRT sai)",
              "TRN_RT_BUYGOLD (CashPay/CardPay) · T_TILL_TXN.TrnTotalAmount=tiền mặt · T_TILL_TXN_DETAIL VND=tiền mặt", L, KHOP,
              "GĐ chốt 2: KHÔNG cần dòng TRN_RT_BUYGOLD_CardPay. App CK thật (372): CashPay=0, CardPay=−tổng, VND két 0. Kiểm đọc lại CardPay + TrnTotalAmount"),
             ("Két nhận: vàng +, tiền −", "T_TILL_TXN_Proc", "ghi", False,
              "@p_TrnIDs='TBG…@' · @p_TillID=két user · @p_UserID → T_TILL_TXN/DETAIL Status 'P' + TillID · T_TILL_BAL dẻ +TL, VND −tiền mặt (tạo dòng két nếu chưa có)",
              "T_TILL_TXN · T_TILL_TXN_DETAIL · T_TILL_BAL", L, KHOP,
              "Từ chối BRT-002 nếu dòng két không còn 'U'. Không có khóa lạc quan → kiểm T_TILL_TXN.Status='P' & TillID"),
             ("Mẫu SMS", "SYS_Notification_Templates_Check", "đọc", False, "@p_TrnCode='BRT' → 0 dòng", "—", B, KHOP, "Tiệm không dùng SMS — bỏ"),
         ]),
        ("Sau chốt: in phiếu · danh sách hôm nay",
         "GĐ CHỐT 3: phiếu in RIÊNG khổ máy in bill 110mm (_thau_phieu.html), in THẲNG từ cửa sổ đang mở (#pos-in) như GĐB.",
         [
             ("Đọc phiếu để in / DS hôm nay", "TRN_RT_BUYGOLD_Lst", "đọc", True,
              "App: @p_TrnID='TBG…@' · KHBL: SELECT thẳng có NOLOCK (bill.phieu_thau, services.hoa_don_loc loai THAU)", "—", L, KHOP,
              "Phiếu 110mm: tiệm · số phiếu · ngày giờ · NV · khách · loại/TL/hột/tuổi/giá · tiền vàng · bù/bớt · TIỆM TRẢ · tiền mặt/CK · bằng chữ · 2 chữ ký"),
         ]),
        ("HỦY THANH TOÁN (C → W): hoàn két, phiếu về chờ",
         "Đo 07:23: T_TILL_TXN_Del BRT với mốc HIỆN TẠI của phiếu (= mốc lần Upd cuối vì Complete không đổi mốc).",
         [
             ("Hoàn két + về W", "T_TILL_TXN_Del", "ghi", False,
              "@p_TrnRefID · @pType='BRT' · @pCongNoBanLe=0 · @p_UserUpd · @p_TrnDateTime_Upd = mốc TRN_RT_BUYGOLD · @p_TrnDateTime_Upd_GDN='1900-01-01' · @p_Type='1'",
              "TRN_RT_BUYGOLD_Log +1 · T_TILL_TXN_Log/_DETAIL_Log +1 · T_TILL_BAL hoàn · T_TILL_TXN/DETAIL −1 · I_LICHSUTICHLUYDIEM (đảo điểm) · TRN_RT_BUYGOLD (W, mốc đổi)", L, KHOP,
              "Đã chạy thật (hoa_don_huy loại THAU → bill.mo_lai_thau, goi_co_khoa kiểm Status W). Mốc lệch → rc=0 mà không làm gì"),
         ]),
        ("XÓA PHIẾU (W → mất hẳn)",
         "Del xóa CỨNG TRN_RT_BUYGOLD + _DT; từ chối phiếu C (B-002) → phải qua pha 4 trước. Passcode + chỉ phiếu hôm nay (Admin: mọi ngày).",
         [
             ("Xóa phiếu chờ", "TRN_RT_BUYGOLD_Del", "ghi", False,
              "@p_TrnID · @p_UserUpd · @p_TrnDateTime_Upd = mốc MỚI (đọc lại sau pha 4) · @p_TrnDateTime_Upd_GDN='1900-01-01' · @p_TrnID_GDN=''",
              "TRN_RT_BUYGOLD −1 · TRN_RT_BUYGOLD_DT", L, KHOP,
              "Đã chạy thật (bill.huy_thau: tự mo_lai nếu C rồi Del, kiểm dòng mất). Phiếu W chưa từng C → vendor KHÔNG log → web canh_bao/audit"),
             ("Đọc lại sổ quỹ", "T_TILL_TXN_Lst", "đọc", False, "App đọc lại DS két sau hủy — KHBL kiểm bằng SELECT", "—", L, KHOP, "—"),
             ("Xóa cứng hàng loạt của vendor", "DEL_TRN_RT_BUYGOLD", "ghi", False,
              "Tắt constraint toàn DB (sp_msforeachtable NOCHECK), xóa T_TILL_TXN Status P", "—", B, KHOP, "CẤM — không bao giờ vào allowlist"),
         ]),
        ("Kiểm soát & bẫy",
         "Mốc khóa im lặng · TillID NULL · BillCode ăn số ngay ở Ins · không có TRN_DAILY_LOG/I_GOLD_BAL cho thâu (chỉ T_TILL_BAL) · giờ KK chậm ~37s.",
         [
             ("Phiếu chờ treo", "", "đọc", False, "DS hôm nay hiện phiếu W kèm nút MỞ/XÓA; không tự xóa", "—", L, KHOP, "—"),
             ("Khóa ghi vendor", "", "", False, "pmv_write_lock=1 (check_pmv thấy tbh_VersionDB đổi) → gateway chặn mọi bước ghi", "—", L, KHOP, "RULE 7"),
             ("Đối chiếu bảng sau 1 phiếu chốt", "", "đọc", False,
              "10 bảng: SYS_CODEMASTERS · SYS_BILL_COUNTER · TRN_RT_BUYGOLD · T_TILL_TXN · T_TILL_TXN_DETAIL · T_TILL_BAL · I_LICHSUTICHLUYDIEM · I_DIEMTICHLUY · I_CUSTOMER · ErrorLog (vết chạy, không phải lỗi)",
              "—", L, KHOP, "smoke_thau so số dòng/số dư két trước–sau trên sandbox; ĐÁNH DẤU trước/sau khi chạy thật lần đầu"),
         ]),
    ],
}
