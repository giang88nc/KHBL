"""CHECK GOLD V4 — SỔ HÀNG LẤY RA KHỎI TỦ + dòng GIAO DỊCH realtime (GĐ chốt 23/09/2026).

    https://127.0.0.1:8100/banle/bao-cao/check-gold/     (trang đứng riêng, KHÔNG đăng nhập, không menu)

KHÔNG phải trang kiểm hàng (GĐ chốt 23/09): quầy quét mã khi LẤY món ra khỏi tủ để bán (chủ yếu vàng 9999),
quét lại đúng mã đó khi TRẢ món về tủ. Dựng lại từ bản V3 của BANLE_V5 (/check-gold/) nhưng GOM VỀ KHBL:
  · nhật ký quét nằm ở MySQL 8 khj_bl — model GoldCheckEntry (bảng gold_check_entry), KHÔNG còn pmv_report;
  · dữ liệu máy KK đọc qua PmvClient (CHỈ ĐỌC, qua gateway có audit);
  · chuyển khoản đọc khj_bl.bank_notifications (webhook V2 ghi) — cùng nguồn với CHECK BILL.

Khác V3 (sửa các điểm yếu của bản cũ):
  · sửa hóa đơn (như V3) đi qua cổng gateway.pmv_billsell_edit: khóa ghi, kiểm trigger, ràng TM+CK = tổng, audit;
  · bỏ SSE while-True (mỗi tab giữ 1 luồng, ~5 truy vấn/2 giây) → làm mới 5 giây/lần và CHỈ đọc lại khi chữ ký đổi;
  · thay khối "còn thiếu" bằng dòng GIAO DỊCH realtime đọc khj_bl.money_flow (kiểu bảng bình luận livestream);
  · gỡ mã khỏi danh sách = ĐÁNH DẤU (còn truy vết) thay vì xóa cứng; unique (ngày, mã SP) chặn 2 máy quét cùng lúc.
"""
import datetime as dt
import logging
import re

from django.db import IntegrityError, connection
from django.utils import timezone

from apps.pmv import money as M
from .models import GoldCheckEntry

log = logging.getLogger(__name__)

MA_SP = re.compile(r'^[0-9][A-Z](?:[0-9]{6}|[0-9]{8})$')     # 1 số + 1 chữ hoa + 6 hoặc 8 số (y luật V3)
N9999 = 'N9999'
TT_NHAN = {'I': 'Trong kho', 'O': 'Đã xuất', 'S': 'Đã bán'}
TRANG_THAI_HD = {'C': 'Hoàn thành', 'W': 'Đang chờ', 'D': 'Hủy', 'R': 'Trả lại'}
DICH_VU = {'RETAIL': 'Bán hàng', 'GOLD_BUY': 'Thâu vàng', 'DEPOSIT': 'Đặt cọc', 'PAWN': 'Cầm đồ', 'REDEEM': 'Chuộc đồ'}
DA_DOI_SOAT = 'confirmed'               # tiền chuyển khoản đã đối soát khớp (money_flow.payment_status)
TT_TIEN = {'waiting': 'Chờ tiền', 'recorded': 'Đã ghi', 'partial': 'Thiếu', 'confirmed': 'Đủ tiền',
           'review': 'Cần kiểm', 'void': 'Đã hủy'}


class LoiQuet(Exception):
    """Lỗi nghiệp vụ hiện thẳng cho người quét."""


def chi(value):
    """Trọng lượng KK lưu theo đơn vị chi × 100 → đổi ra CHI (2 số lẻ)."""
    return round(float(M.dec(value)) / 100, 2)


def loai_ma(raw):
    """('sp', mã) · ('hd', mã HĐ dạng 26-09-23-000096) · (None, '') — cùng luật nhận mã của V3."""
    ma = str(raw or '').strip().upper()
    if MA_SP.match(ma):
        return 'sp', ma
    so = re.sub(r'\D', '', ma)
    if len(so) >= 7:
        return 'hd', f'{so[:2]}-{so[2:4]}-{so[4:6]}-{so[6:].zfill(6)}'
    return None, ''


def nhan_vien_ds(c):
    return c.query("SELECT EmpID, EmpName FROM T_EMPLOYEE WITH (NOLOCK) "
                   "WHERE Active=1 AND LTRIM(RTRIM(EmpName))<>'' ORDER BY EmpName")


def _trang_thai_sp(c, codes):
    """{mã SP: {tt, ngay_xuat, khach, nv_ban}} — T_PRODUCT + lượt bán gần nhất (CHỈ ĐỌC máy KK)."""
    codes = [x for x in {str(v or '').strip().upper() for v in codes} if x]
    ra = {}
    for i in range(0, len(codes), 200):
        lo = codes[i:i + 200]
        rows = c.query(
            "SELECT p.ProductCode, p.Status AS TT, p.SellOutDate AS NgayXuat, ISNULL(c.CustName,'') AS CustName, "
            "ISNULL(e.EmpName,'') AS EmpName FROM T_PRODUCT p WITH (NOLOCK) "
            "OUTER APPLY (SELECT TOP 1 bs.CustID, bs.EmpID FROM TRN_RT_BUYSELL_SELL s WITH (NOLOCK) "
            "  JOIN TRN_RT_BUYSELL bs WITH (NOLOCK) ON bs.TrnID = s.TrnID "
            "  WHERE s.ProductCode = p.ProductCode ORDER BY bs.TrnDate DESC, bs.TrnTime DESC, bs.TrnID DESC) ban "
            "LEFT JOIN I_CUSTOMER c WITH (NOLOCK) ON c.CustID = ban.CustID "
            "LEFT JOIN T_EMPLOYEE e WITH (NOLOCK) ON e.EmpID = ban.EmpID "
            f"WHERE UPPER(p.ProductCode) IN ({','.join('?' * len(lo))})", tuple(lo))
        for r in rows:
            ra[str(r['ProductCode']).strip().upper()] = {
                'tt': str(r['TT'] or '').strip().upper(), 'ngay_xuat': r['NgayXuat'],
                'khach': r['CustName'], 'nv_ban': r['EmpName']}
    return ra


def danh_sach(c, ngay):
    """Danh sách đã kiểm trong ngày + trạng thái sản phẩm đọc LẠI từ KK (I/O/S)."""
    rows = list(GoldCheckEntry.objects.filter(ngay=ngay, go_luc__isnull=True))
    tt = _trang_thai_sp(c, [r.product_code for r in rows]) if rows else {}
    ra = []
    for r in rows:
        t = tt.get(r.product_code, {})
        ra.append({'id': r.id, 'ma': r.product_code, 'vang': r.gold_code, 'ten': r.product_desc,
                   'chi': chi(r.total_weight), 'nv': r.nhan_vien, 'quet_luc': timezone.localtime(r.quet_luc),
                   'tt': t.get('tt', ''), 'tt_nhan': TT_NHAN.get(t.get('tt', ''), 'Không thấy'),
                   'ngay_xuat': t.get('ngay_xuat'), 'khach': t.get('khach', ''), 'nv_ban': t.get('nv_ban', '')})
    return ra


# ── NHỊP SỐNG CỦA THẺ GIAO DỊCH — bảng GĐ chốt 26/09/2026 (thay hẳn cặp 3 phút/10 phút ngày 23/09) ──
#   Tiền VÀO (bán · cọc · chuộc đồ):  chờ quầy xác nhận tiền mặt 30p · CÒN NỢ KHÁCH TIỀN THỐI 3p ·
#                                     chờ đối soát CK 30p · hết việc 30s.
#   Tiền RA (thâu · đổi dư · cầm đồ): còn tiền mặt phải đưa tận tay 10p · chờ đối soát CK 30p · xong 30s.
#   Phiếu ĐÃ HỦY: 30s.
#   ⚠ THỨ TỰ XÉT quan trọng (GĐ chốt 26/09/2026 tối, sau phiếu chuộc đồ 26090170307034 — TM 284.000 +
#   CK 4.500.000, khách đưa 294.000 nên còn thối 10.000): TIỀN THỐI XÉT TRƯỚC CHỜ CK. Trả lại tiền cho
#   khách đang đứng ở quầy là việc gấp; phần chuyển khoản chưa khớp vẫn theo dõi tiếp bên CHECK BILL.
SONG_CHO = 30 * 60                      # chờ xác nhận tiền mặt · chờ đối soát chuyển khoản
SONG_DUA = 10 * 60                      # tiền RA thuần tiền mặt: còn phải đưa tận tay khách
SONG_THOI = 3 * 60                      # tiền VÀO đã nhận đủ, còn nợ khách tiền thối
SONG_XONG = 30                          # hết việc, hoặc phiếu đã hủy — rời bảng sau 30 giây
NGUONG_GON = 15 * 60                    # chờ quá 15 phút thì thẻ THU GỌN còn 2 dòng (GĐ chốt 26/09/2026)
TRAN_THE = 150                          # số dòng money_flow mới nhất xét mỗi nhịp — 60 cũ sát trần khi nhịp 30 phút
                                        # (đo 24/09 giờ 20: 54 dòng/giờ), GĐ chốt 27/09/2026
NOI_BAT = 2 * 60                        # "Thối …" vừa được xác nhận thì nhấp nháy cỡ lớn trong 2 phút (24/09/2026)
TRAN_POPUP = 200                        # popup Phiếu bán / Phiếu thâu / Cầm đồ vẽ tối đa 200 dòng mới nhất (27/09/2026)
# Màu theo nhóm nghiệp vụ: tiền VÀO tiệm (bán hàng · chuộc đồ · cọc) vàng kim · tiền RA (thâu vàng · cầm đồ) đỏ
NHOM_MAU = {'RETAIL': 'vao', 'REDEEM': 'vao', 'DEPOSIT': 'vao', 'GOLD_BUY': 'ra', 'PAWN': 'ra'}


def _gio_vn(value):
    """Mọi mốc thời gian về GIỜ VN dạng KHÔNG tz để so sánh được với nhau (DB lưu giờ VN)."""
    if not value:
        return None
    if isinstance(value, str):
        try:
            value = dt.datetime.fromisoformat(value[:19])
        except ValueError:
            return None
    return timezone.localtime(value).replace(tzinfo=None) if timezone.is_aware(value) else value


def _luc_giao_dich(happened_at, created_at):
    """Giờ giao dịch — ưu tiên mốc lúc quầy thao tác, không có thì lấy giờ tạo dòng."""
    return _gio_vn(happened_at) or _gio_vn(created_at)


def _ten_nhan_vien(rows):
    """{id dòng: tên NV}. Phiếu bán/cọc có sẵn tên trong snapshot; PHIẾU THÂU (nhóm ThauNhom) chỉ có emp_id
    nên tra thêm qua cache danh sách nhân viên (services.nhan_vien_ban, 5 phút) — không hỏi KK mỗi nhịp."""
    from . import services as S
    from .models import ThauNhom
    can = {r['id']: r['source_group_id'] or r['source_id'] for r in rows
           if r['source_type'] == 'thau_nhom' and not ((r['source_snapshot'] or {}).get('mobile') or {}).get('employee_name')}
    if not can:
        return {}
    nhom = {str(g.pk): (g.emp_id or '').strip()
            for g in ThauNhom.objects.filter(pk__in=[v for v in can.values() if str(v).isdigit()]).only('id', 'emp_id')}
    try:
        ten = {str(e['EmpID']).strip(): e['EmpName'] for e in S.nhan_vien_ban()}
    except Exception:
        log.exception('CHECK GOLD: không đọc được danh sách nhân viên')
        ten = {}
    return {pk: ten.get(nhom.get(str(v), ''), '') for pk, v in can.items()}


CUA_SO_CK_RA = dt.timedelta(hours=1)    # khoản chi phải rời tài khoản trong 1 GIỜ kể từ lúc lập phiếu


MAU_BILL = re.compile(r'^(\d{2})-(\d{2})-(\d{2})-(\d{6})$')


def _ma_tu_bill(bill_code):
    """'26-09-26-000207' → '260926000207'; nhóm nhiều phiếu '26-09-26-000171 (+1)' lấy phiếu đầu.

    Chỉ nhận ĐÚNG khuôn bill_code yy-mm-dd-nnnnnn. Mã TRN của máy KK (TBG260900001270) bỏ chữ ra cũng
    thành 12 số nhưng KHÔNG phải mã chứng từ chuyển khoản — nhận nhầm là đối soát sai phiếu."""
    m = MAU_BILL.match(str(bill_code or '').split(' (')[0].strip())
    return ''.join(m.groups()) if m else ''


def _gio_bank(v):
    """transaction_time của bank_notifications là CHUỖI 'YYYY-MM-DD HH:MM:SS' (cột varchar) → datetime ngây thơ."""
    if isinstance(v, dt.datetime):
        return v.replace(tzinfo=None)
    try:
        return dt.datetime.strptime(str(v or '')[:19], '%Y-%m-%d %H:%M:%S')
    except ValueError:
        return None


def _ck_ra_da_chuyen(rows):
    """{id dòng money_flow} của phiếu THÂU đã thấy khoản CHI khớp trong sổ ngân hàng.

    ⚠ ĐỐI SOÁT TẠM THỜI (GĐ chốt 26/09/2026). money_flow / money_flow_payment chưa ghi nhận khoản chi của
    phiếu thâu, còn đường cũ (thau_payment_link) bị luật tranh chấp chặn: một phiếu cũ trùng ĐUÔI 4 số mà
    không cần chuyển khoản đồng nào vẫn bị tính là tranh chấp, nên phiếu khớp mã đầy đủ không được tự nối
    (26/09 có 3 nhóm kẹt, 217.722.000 ₫). Ở đây CHỈ ĐỌC bank_notifications và không ghi gì, chuẩn đối soát:

        · direction = out (tiền rời tài khoản tiệm);
        · ma_chung_tu == bill_code của phiếu bỏ hết dấu gạch (26-09-26-000207 → 260926000207);
        · số tiền đúng bằng phần chuyển khoản của phiếu;
        · giao dịch xảy ra trong vòng 1 GIỜ kể từ lúc lập phiếu.

    Đây là lớp HIỂN THỊ của CHECK GOLD, không thay thế đối soát chính thức bên Thâu vào.
    """
    can = {}
    for r in rows:
        if (str(r['direction']).upper() != 'OUT' or r['service'] != 'GOLD_BUY' or r['is_void']
                or r['payment_status'] == DA_DOI_SOAT or M.dec(r['bank_amount']) <= 0):
            continue
        ma = _ma_tu_bill(r['source_bill_code'])
        if ma:
            can.setdefault(ma, []).append(r)
    if not can:
        return set()
    moc = [_luc_giao_dich((((r['source_snapshot'] or {}) if isinstance(r['source_snapshot'], dict) else {})
                           .get('mobile') or {}).get('happened_at'), r['created_at'])
           for ds in can.values() for r in ds]
    moc = [m for m in moc if m]
    if not moc:
        return set()
    with connection.cursor() as cur:
        cur.execute("SELECT ma_chung_tu, trans_amount, transaction_time FROM bank_notifications "
                    "WHERE LOWER(direction)='out' AND trans_amount > 0 AND transaction_time >= %s "
                    "AND transaction_time < %s AND ma_chung_tu IN (%s)"
                    % ('%s', '%s', ','.join(['%s'] * len(can))),
                    [min(moc).replace(tzinfo=None), (max(moc) + CUA_SO_CK_RA).replace(tzinfo=None), *can])
        bang = cur.fetchall()
    ra = set()
    for ma, tien, luc_bank in bang:
        luc_bank = _gio_bank(luc_bank)
        if luc_bank is None:
            continue
        for r in can.get(str(ma or '').strip(), []):
            snap = r['source_snapshot'] if isinstance(r['source_snapshot'], dict) else {}
            luc = _luc_giao_dich((snap.get('mobile') or {}).get('happened_at'), r['created_at'])
            if luc is None or M.dec(tien) != M.dec(r['bank_amount']):
                continue
            if luc.replace(tzinfo=None) <= luc_bank <= (luc + CUA_SO_CK_RA).replace(tzinfo=None):
                ra.add(r['id'])
    return ra


class _SoMoc:
    """Sổ MỐC của thẻ GIAO DỊCH — bảng gold_check_moc (GĐ chốt 27/09/2026), đọc 1 lần + ghi 1 lần mỗi nhịp.

    Thay hai chỗ "đúng chức năng nhưng không đúng ý": mốc "đã xong" trước nằm trong bộ nhớ tiến trình (RESET là
    mất), mốc nhấp nháy trước lấy money_flow.synced_at (job chiếu lại mọi dòng vài giây/lần nên luôn là "vừa").

    Luật chung cho cả hai mốc: phiếu ĐANG được theo dõi mà việc vừa bật lên → mốc = BÂY GIỜ; phiếu CHECK GOLD mới
    nhìn thấy LẦN ĐẦU mà việc đã có sẵn và giao dịch không còn mới (> 60 giây) → mốc = CHÍNH LÚC GIAO DỊCH, để
    phiếu cũ không "sống lại" trên bảng. Việc tắt → xoá mốc. Mọi mốc trả ra là giờ VN không tz (như _gio_vn).
    """
    COT = ('xong_luc', 'noi_bat_luc')

    def __init__(self, ids):
        from .models import GoldCheckMoc
        self.Mo = GoldCheckMoc
        self.co = {m.flow_id: m for m in GoldCheckMoc.objects.filter(flow_id__in=list(ids))} if ids else {}
        self.moi, self.doi = {}, {}

    def moc(self, fid, cot, bat, bay_gio, tuoi):
        lan_dau = fid not in self.co
        m = self.co.get(fid) or self.moi.get(fid)
        if m is None:
            m = self.moi[fid] = self.Mo(flow_id=fid)       # có dòng = CHECK GOLD đã từng thấy phiếu này
        cu = _gio_vn(getattr(m, cot))
        if not bat:
            if cu is not None:
                setattr(m, cot, None)
                self._doi(fid, m)
            return None
        if cu is None:
            cu = bay_gio - dt.timedelta(seconds=tuoi) if lan_dau and tuoi > 60 else bay_gio
            setattr(m, cot, timezone.make_aware(cu))
            self._doi(fid, m)
        return cu

    def _doi(self, fid, m):
        if fid in self.co:
            self.doi[fid] = m

    def luu(self):
        try:
            if self.moi:
                self.Mo.objects.bulk_create(list(self.moi.values()), ignore_conflicts=True)
            if self.doi:
                self.Mo.objects.bulk_update(list(self.doi.values()), list(self.COT))
        except Exception:                                   # sổ mốc hỏng không được làm sập bảng giao dịch
            log.exception('CHECK GOLD: không ghi được sổ mốc gold_check_moc')


def _bam_the(the):
    """Dấu vân tay NỘI DUNG hiển thị của một thẻ (bỏ các trường chạy theo đồng hồ: con · noi_bat · gon).
    Trang chỉ nhận lại trọn thẻ khi dấu này đổi — kể cả đổi do đối soát tạm qua sổ ngân hàng, nơi money_flow
    không đổi gì nên cột ky (chữ ký tiền) đứng yên."""
    import json
    import zlib
    goc = {k: v for k, v in the.items() if k not in ('con', 'noi_bat', 'gon', 'h')}
    return '%08x' % zlib.crc32(json.dumps(goc, sort_keys=True, default=str).encode('utf-8'))


def _ten_dich_vu(r):
    """Tên nghiệp vụ trên thẻ. Phiên CẦM ĐỒ (KHCD) ghi rõ ĐÚNG nghiệp vụ của phiên — Cầm mới · Cầm thêm · Trả bớt ·
    Gia hạn · Chuộc đồ… (lấy từ source_status, sync_khcd ghi tên theo KHCD_OPERATIONS) — GĐ chốt 27/09/2026.
    Trước đây chỉ theo chiều tiền: mọi phiên tiền VÀO đều ghi "Chuộc đồ", kể cả phiên gia hạn đóng lãi."""
    if r.get('source_type') == 'cd_loan_log' and r.get('source_status'):
        return 'Cầm đồ · ' + str(r['source_status']).strip()
    return DICH_VU.get(r['service'], r['service'])


def giao_dich(ngay, tu_id=0):
    """DÒNG GIAO DỊCH trong ngày — khj_bl.money_flow (KHÔNG hỏi KK). tu_id > 0 chỉ lấy dòng mới hơn.

    Trang hiện kiểu bảng bình luận livestream: dòng mới trượt vào đầu bảng, dòng vừa đổi tiền/trạng thái thì nháy.
    Chữ ký của mỗi dòng gồm các cột GĐ chốt phải theo dõi: expected/cash/bank/cus_cash/cus_change/payment_status.
    """
    from .models import MoneyFlow
    qs = MoneyFlow.objects.filter(business_date=ngay)
    if tu_id:
        qs = qs.filter(id__gt=tu_id)
    rows = list(qs.order_by('-id').values(
        'id', 'service', 'direction', 'source_type', 'source_group_id', 'source_bill_code', 'source_id',
        'customer_name', 'expected_amount', 'cash_amount', 'bank_amount', 'cus_cash', 'cus_change',
        'payment_status', 'is_void', 'source_snapshot', 'created_at', 'synced_at', 'source_status')[:TRAN_THE])
    nv_thau = _ten_nhan_vien(rows)
    ck_ra_xong = _ck_ra_da_chuyen(rows)          # đối soát tạm thời cho tiền RA của phiếu thâu
    so_moc = _SoMoc(r['id'] for r in rows)
    bay_gio = _gio_vn(timezone.now())
    ra = []
    for r in rows:
        snap = r['source_snapshot'] or {}
        if isinstance(snap, str):
            import json
            try:
                snap = json.loads(snap)
            except ValueError:
                snap = {}
        mobile = snap.get('mobile') or {}
        luc = _luc_giao_dich(mobile.get('happened_at'), r['created_at'])
        ra_tien = str(r['direction']).upper() == 'OUT'
        # GĐ chốt 25/09/2026 — phiếu TIỀN RA chỉ phải ĐƯA KHÁCH phần TIỀN MẶT: trả hết bằng chuyển khoản
        # (TM = 0, CK = cả phiếu) thì quầy không đưa đồng nào ra tay, "Đưa khách" = 0 và phiếu xong tại quầy.
        dua_khach = M.dec(r['cash_amount']) if ra_tien else M.dec(0)
        # Phiếu có tiền mặt phải thu mà chưa ai xác nhận (cus_cash + cus_change = 0) — quầy còn phải xử lý
        cho_tien_mat = (not ra_tien and M.dec(r['cash_amount']) > 0
                        and M.dec(r['cus_cash']) + M.dec(r['cus_change']) == 0)
        # NHỊP SỐNG (bảng GĐ chốt 26/09/2026 — xem chú thích ở khối hằng SONG_*)
        ck, thoi, huy = M.dec(r['bank_amount']), M.dec(r['cus_change']), bool(r['is_void'])
        # có chuyển khoản mà ngân hàng chưa khớp (phiếu thâu còn xét thêm sổ ngân hàng — _ck_ra_da_chuyen)
        cho_ck = ck > 0 and r['payment_status'] != DA_DOI_SOAT and r['id'] not in ck_ra_xong
        if huy:
            song, xong = SONG_XONG, True
        elif ra_tien:
            # Tiền RA không có bước "xác nhận đã đưa khách": phiếu thuần tiền mặt sống 10 phút rồi tự đi.
            if cho_ck:
                song, xong = SONG_CHO, False
            elif ck > 0:
                song, xong = SONG_XONG, True
            else:
                song, xong = SONG_DUA, False
        elif cho_tien_mat:
            song, xong = SONG_CHO, False
        elif thoi > 0:                       # còn nợ khách tiền thối — gấp hơn cả việc chờ đối soát CK
            song, xong = SONG_THOI, False
        elif cho_ck:
            song, xong = SONG_CHO, False
        else:
            song, xong = SONG_XONG, True
        tuoi = (bay_gio - luc).total_seconds() if luc else 0
        # Còn tiền THỐI cho khách · còn TIỀN MẶT phải ĐƯA khách → nhấp nháy đúng 2 phút kể từ lúc việc đó BẮT ĐẦU
        # (mốc trong sổ gold_check_moc — trước đây lấy synced_at nên thực tế nháy suốt đời thẻ).
        luc_noi = so_moc.moc(r['id'], 'noi_bat_luc', (thoi > 0 or dua_khach > 0) and not huy, bay_gio, tuoi)
        noi_bat = round(max(0.0, NOI_BAT - (bay_gio - luc_noi).total_seconds()), 1) if luc_noi else 0.0
        # Phiếu KHÔNG có đồng tiền mặt nào (cả tiền ra lẫn tiền vào): dòng 4 nói thẳng việc còn lại là
        # chuyển khoản, thay cho "Đưa khách 0" / "Nhận 0 · Thối 0" vô nghĩa (GĐ chốt 25–26/09/2026).
        nhan_ck = ''
        if ck > 0 and M.dec(r['cash_amount']) == 0 and not huy:
            nhan_ck = 'Chờ chuyển khoản' if cho_ck else 'Đã chuyển khoản'
        # VIỆC CÒN LẠI gói trong vài chữ — chỉ hiện ở THẺ THU GỌN, nơi không đủ chỗ cho 4 dòng đầy đủ
        # (GĐ chốt 26/09/2026: phiếu hỗn hợp TM + CK đã nhận tiền mặt thì ghi "Chờ CK", không ghi "Thối 0").
        if huy:
            viec = 'Đã hủy'
        elif cho_tien_mat:
            viec = 'Chưa xác nhận TM'
        elif thoi > 0:
            viec = 'Thối ' + M.money_vn(thoi, '')
        elif cho_ck:
            viec = 'Chờ CK'
        elif ra_tien and dua_khach > 0:
            viec = 'Đưa khách ' + M.money_vn(dua_khach, '')
        else:
            viec = 'Xong'
        # Thẻ XONG đếm 30 giây kể từ LÚC TRỞ THÀNH xong — phiếu nằm chờ đối soát cả tiếng, vừa khớp xong
        # vẫn được 30 giây báo "HĐ XONG" rồi mới đi (mốc bền qua RESET — xem _SoMoc).
        con = song - tuoi
        luc_xong = so_moc.moc(r['id'], 'xong_luc', xong, bay_gio, tuoi)
        if luc_xong is not None:
            con = SONG_XONG - (bay_gio - luc_xong).total_seconds()
        if con <= 0:
            continue
        ra.append({
            'id': r['id'], 'gio': luc.strftime('%H:%M') if luc else '',
            'dich_vu': _ten_dich_vu(r), 'nhom': NHOM_MAU.get(r['service'], 'vao'),
            'nv': mobile.get('employee_name') or nv_thau.get(r['id']) or mobile.get('employee_pmv') or '',
            'ma': r['source_bill_code'] or r['source_id'], 'khach': r['customer_name'] or 'Khách lẻ',
            'tong': M.dec(r['expected_amount']), 'tien_mat': M.dec(r['cash_amount']), 'ck': M.dec(r['bank_amount']),
            'khach_dua': M.dec(r['cus_cash']), 'thoi_lai': M.dec(r['cus_change']),
            'dua_khach': dua_khach,                      # tiền RA: phần TIỀN MẶT thật sự đưa tận tay khách
            'tt': r['payment_status'],
            'tt_nhan': 'HĐ XONG' if xong else TT_TIEN.get(r['payment_status'], r['payment_status']),
            'huy': bool(r['is_void']), 'ra_tien': ra_tien, 'cho_tm': cho_tien_mat,
            'nhan_ck': nhan_ck,                          # nhãn thay "Đưa khách" khi phiếu ra trả hết bằng CK
            # còn chờ thì chữ đỏ + lớp cg-gd__cho (JS rải từng chữ để nhấp nhô); xong rồi thì chữ xám, đứng yên
            'nhan_ck_lop': 'cg-gd__nhan' if nhan_ck == 'Đã chuyển khoản' else 'cg-gd__do cg-gd__cho',
            'song': song, 'con': round(con, 1),          # vạch thời gian còn lại dưới đáy thẻ
            # Việc chờ lâu chỉ cần hai dòng nhắc; thẻ mới và thẻ vừa xong vẫn hiện đủ 4 dòng
            'gon': (not xong) and tuoi > NGUONG_GON,
            'viec': viec, 'viec_lop': 'cg-gd__nhan' if viec in ('Xong', 'Đã hủy') else 'cg-gd__do',
            'noi_bat': noi_bat,                          # giây còn lại của hiệu ứng "Thối …"/"Đưa khách" (0 = tắt)
            'ky': '|'.join(str(x) for x in (r['expected_amount'], r['cash_amount'], r['bank_amount'],
                                            r['cus_cash'], r['cus_change'], r['payment_status'], r['is_void'])),
        })
        ra[-1]['h'] = _bam_the(ra[-1])                # trang chỉ nhận lại trọn thẻ khi dấu này đổi
    so_moc.luu()
    return ra


def sua_hoa_don(c, trn_id, bill_code, posted):
    """Sửa hóa đơn bán trên KK — mọi lần ghi đều đi qua CỔNG AN TOÀN có sẵn, không mở đường ghi mới:

      ① khách · NV bán · TK nhận CK · nội dung CK · tách TIỀN MẶT / CHUYỂN KHOẢN → gateway.pmv_billsell_edit
         (ràng buộc CashPay + CardPay = PayAmount, không âm, khóa ghi, kiểm trigger, đọc lại, audit);
      ② THÔNG TIN ĐƠN Desc3 (kênh · social · khách gửi · đơn cưới + ngày cưới · ghi chú) + TIỀN KHÁCH ĐƯA →
         gateway.pmv_invoice_update — ĐÚNG thuật toán mobile_invoice.changes (GĐ chốt 29/09/2026): giữ nguyên
         các khóa Desc3 khác (lịch sử khách lấy hàng…), trả lại khách tự tính = khách đưa − tiền mặt;
      ③ NV HỖ TRỢ — máy KK không có chỗ, ghi vào sổ KHBL gold_bill.emp_sup_id.
    Kiểm HẾT trước rồi mới ghi, để form sai ở ② không làm ① đã lên KK nửa chừng."""
    from apps.pmv import gateway
    from . import mobile_invoice as MI
    hd = hoa_don(c, bill_code)
    if not hd or str(hd['TrnID']).strip() != str(trn_id).strip():
        raise LoiQuet('Hóa đơn vừa thay đổi trên KK. Mở lại rồi sửa.')
    doi = {}
    for cot, khoa in (('CustID', 'cust_id'), ('EmpID', 'emp_id'), ('Desc4', 'desc4'), ('Desc5', 'desc5')):
        giu = str(posted.get(khoa) or '').strip()
        if giu != str(hd.get(cot) or '').strip():
            doi[cot] = giu
    tong = M.dec(hd['PayAmount'])
    # ô tiền trên form hiện dạng 39.540.000 — bỏ dấu chấm nghìn trước khi đọc (M.dec không hiểu dấu chấm)
    tien_mat, ck = (M.dec(re.sub(r'[^0-9-]', '', str(posted.get(k) or '')) or 0) for k in ('cash_pay', 'card_pay'))
    if tien_mat + ck != tong:
        raise LoiQuet(f'Tiền mặt + chuyển khoản phải bằng tổng thanh toán {M.money_vn(tong)}.')
    if tien_mat < 0 or ck < 0:
        raise LoiQuet('Tiền mặt và chuyển khoản không được âm.')
    if tien_mat != M.dec(hd['CashPay']) or ck != M.dec(hd['CardPay']):
        doi['CashPay'], doi['CardPay'] = tien_mat, ck

    # ② Desc3 + tiền khách — chỉ khi form gửi kèm (form SỬA luôn gửi 'channel'); kiểm trước trên bản dự kiến
    d3_post, d3_doi = None, False
    if 'channel' in posted:
        if tong < 0:
            raise LoiQuet('Hóa đơn tiệm trả khách (tổng âm) chưa sửa được thông tin đơn ở đây.')
        d3_post = MI.retail_post(posted)
        d3_post['tender'] = re.sub(r'\D', '', d3_post.get('tender') or '') or '0'
        try:
            goc = MI.load_retail(trn_id, bill_code)
            du_kien = {**goc, 'CashPay': tien_mat, 'CardPay': ck}
            sl = MI.retail_quantity(trn_id) if d3_post.get('is_wedding') == '1' else None
            moi = MI.changes('gold_bill_retail', du_kien, d3_post, bill_quantity=sl)
        except ValueError as exc:
            raise LoiQuet(str(exc))
        d3_doi = (MI.document(moi.get('Desc3')) != MI.document(goc.get('Desc3'))
                  or any(M.dec(moi[k]) != M.dec(goc.get(k)) for k in ('TienKhachTraThuc', 'TienTraLai') if k in moi))

    # ③ NV hỗ trợ
    sup = str(posted.get('emp_sup_id') or '').strip() if 'emp_sup_id' in posted else None
    sup_doi = sup is not None and sup != str(hd.get('emp_sup_id') or '').strip()
    if sup_doi and not hd.get('co_so_khbl'):
        raise LoiQuet('Hóa đơn này không có trong sổ KHBL (lập trên PMV) — chưa lưu được NV hỗ trợ.')

    if not doi and not d3_doi and not sup_doi:
        return 'Không có thay đổi nào.'
    da = []
    try:
        if doi:
            gateway.pmv_billsell_edit(trn_id, doi)
            da += sorted(doi)
        if d3_doi:
            goc = MI.load_retail(trn_id, bill_code)          # đọc lại SAU khi tách tiền để dấu (stamp) khớp
            gateway.pmv_invoice_update('gold_bill_retail', str(goc['TrnID']), str(goc['BillCode']).strip(),
                                       MI.stamp(goc), d3_post)
            da.append('thông tin đơn')
        if sup_doi:
            from .models import GoldBill
            GoldBill.all_objects.filter(trn_id=trn_id).update(emp_sup_id=sup)
            da.append('NV hỗ trợ')
    except Exception as exc:
        raise LoiQuet((f'Đã lưu {", ".join(da)} nhưng ' if da else '') + str(exc))
    return f'✓ Đã lưu hóa đơn {bill_code}: ' + ', '.join(da)


def quet(c, raw, nhan_vien, ngay):
    """Quét 1 mã. Trả (kiểu, dữ liệu, tin nhắn, mức) — mức: ok · canh_bao. Lỗi nghiệp vụ ném LoiQuet."""
    kieu, ma = loai_ma(raw)
    if not kieu:
        raise LoiQuet('Mã không đúng dạng mã sản phẩm hay mã hóa đơn.')
    if kieu == 'hd':
        hd = hoa_don(c, ma)
        if not hd:
            raise LoiQuet(f'Không tìm thấy hóa đơn {ma} trên máy KK.')
        return 'hd', hd, f'Đã mở hóa đơn {ma}.', 'ok'
    cu = GoldCheckEntry.objects.filter(ngay=ngay, product_code=ma, go_luc__isnull=True).first()
    if cu:      # quét lần 2 = GỠ khỏi danh sách (y V3), nhưng giữ dòng để truy vết
        cu.go_luc, cu.go_boi = timezone.now(), (nhan_vien or '')[:150]
        cu.save(update_fields=['go_luc', 'go_boi'])
        return 'sp', None, f'Đã gỡ {ma} khỏi danh sách kiểm.', 'canh_bao'
    rows = c.query("SELECT TOP 1 GoldCode, ProductCode, ProductDesc, TotalWeight, Status, SellOutDate "
                   "FROM T_PRODUCT WITH (NOLOCK) WHERE UPPER(ProductCode)=?", (ma,))
    if not rows:
        raise LoiQuet(f'Không tìm thấy sản phẩm {ma} trên máy KK.')
    sp = rows[0]
    tt = str(sp['Status'] or '').strip().upper()
    if tt in ('O', 'S'):
        ngay_xuat = sp['SellOutDate']
        raise LoiQuet(f'{ma} — {TT_NHAN[tt]}'
                      + (f' ngày {ngay_xuat:%d/%m/%Y}' if hasattr(ngay_xuat, 'strftime') else '')
                      + ', không còn trong kho.')
    vang = str(sp['GoldCode'] or '').strip()
    try:
        GoldCheckEntry.objects.update_or_create(
            ngay=ngay, product_code=ma,
            defaults={'gold_code': vang, 'product_desc': str(sp['ProductDesc'] or '').strip(),
                      'total_weight': M.dec(sp['TotalWeight']), 'nhan_vien': (nhan_vien or '')[:150],
                      'go_luc': None, 'go_boi': ''})
    except IntegrityError:
        raise LoiQuet(f'{ma} vừa được máy khác quét — tải lại danh sách.')
    if vang.upper() != N9999:
        return 'sp', None, f'✓ {ma} · {vang or "?"} — KHÔNG thuộc vàng 9999, vẫn ghi vào danh sách kiểm.', 'canh_bao'
    return 'sp', None, f'✓ {ma} · {chi(sp["TotalWeight"])} chỉ · {str(sp["ProductDesc"] or "").strip()}', 'ok'


def go_dong(pk, nguoi):
    """Nút ✕ trên bảng — gỡ 1 dòng khỏi danh sách kiểm (vẫn giữ dòng)."""
    row = GoldCheckEntry.objects.filter(pk=pk, go_luc__isnull=True).first()
    if not row:
        return ''
    row.go_luc, row.go_boi = timezone.now(), (nguoi or '')[:150]
    row.save(update_fields=['go_luc', 'go_boi'])
    return row.product_code


# ─────────────────────────── hóa đơn ───────────────────────────
def hoa_don(c, bill_code):
    """Chi tiết 1 hóa đơn bán (header + hàng bán ra + hàng thu vào) — CHỈ ĐỌC."""
    rows = c.query(
        "SELECT TOP 1 b.TrnID, b.BillCode, b.TrnDate, b.TrnTime, b.CustID, ISNULL(c.CustName,'') AS CustName, "
        "ISNULL(c.Phone,'') AS Phone, ISNULL(c.CMND,'') AS CMND, ISNULL(c.Address,'') AS Address, "
        "b.SellTotalAmount, b.BuyTotalAmount, b.TotalAmount, b.Discount, b.PayAmount, b.Status, b.IsDel, "
        "ISNULL(b.CashPay,0) AS CashPay, ISNULL(b.CardPay,0) AS CardPay, b.Desc3, b.Desc4, b.Desc5, b.CreatedDate, "
        "ISNULL(b.TienKhachTraThuc,0) AS TienKhachTraThuc, ISNULL(b.TienTraLai,0) AS TienTraLai, "
        "ISNULL(b.TaskPriceAdd,0) AS TaskPriceAdd, ISNULL(b.TienCoc,0) AS TienCoc, "
        "b.EmpID, ISNULL(e.EmpName,'') AS EmpName FROM TRN_RT_BUYSELL b WITH (NOLOCK) "
        "LEFT JOIN I_CUSTOMER c WITH (NOLOCK) ON c.CustID = b.CustID "
        "LEFT JOIN T_EMPLOYEE e WITH (NOLOCK) ON e.EmpID = b.EmpID "
        "WHERE b.BillCode=? ORDER BY b.TrnDate DESC, b.TrnTime DESC, b.TrnID DESC", (bill_code,))
    if not rows:
        return None
    h = dict(rows[0])
    h['trang_thai'] = TRANG_THAI_HD.get(str(h['Status'] or '').strip().upper(), 'Không rõ')
    h['da_huy'] = str(h['IsDel'] or '0') != '0'
    trn = str(h['TrnID']).strip()
    ban = c.query("SELECT ProductCode, ProductDesc, GoldCode, TotalWeight, PriceUnit, TaskPrice, SellAmount "
                  "FROM TRN_RT_BUYSELL_SELL WITH (NOLOCK) WHERE TrnID=?", (trn,))
    # Dẻ khách đưa vào: ĐỔI NGANG khi tính theo GIÁ BÁN RA đang hiệu lực lúc lập hóa đơn, còn lại là ĐỔI THÂU —
    # cùng luật với popup Vàng thâu (ds_vang_thau), GĐ chốt 27/09/2026.
    thu = c.query(
        "SELECT bg.GoldCode, ISNULL(g.GoldDesc,'') AS GoldDesc, bg.GoldWeight, bg.DiamondWeight, bg.TotalGoldWeight, "
        "bg.BuyRate, bg.BuyAmount, CASE WHEN bg.BuyRate >= ISNULL(x.SellRate, 999999999999) THEN 1 ELSE 0 END AS Ngang "
        "FROM TRN_RT_BUYSELL_BUYGOLD bg WITH (NOLOCK) LEFT JOIN I_GOLD g WITH (NOLOCK) ON g.GoldCode = bg.GoldCode "
        "OUTER APPLY (SELECT TOP 1 r.SellRate FROM ("
        "  SELECT GoldCcy, RateDate, RateTime, SellRate FROM I_XRATE WITH (NOLOCK) "
        "  UNION ALL SELECT GoldCcy, RateDate, RateTime, SellRate FROM I_XRATE_HIST WITH (NOLOCK)) r "
        "  WHERE r.GoldCcy = bg.GoldCode "
        "  AND DATEADD(second, DATEDIFF(second, 0, CAST(r.RateTime AS datetime)), r.RateDate) <= ? "
        "  ORDER BY r.RateDate DESC, r.RateTime DESC) x "
        "WHERE bg.TrnID=?", (h.get('CreatedDate'), trn))
    h['ban'] = [dict(r, chi=chi(r['TotalWeight'])) for r in ban]
    h['thu'] = [dict(r, chi=chi(r['TotalGoldWeight'] or r['GoldWeight']), chi_vang=chi(r['GoldWeight']),
                     chi_hot=chi(r['DiamondWeight']), ngang=bool(r['Ngang'])) for r in thu]
    h['doi_ngang'] = [r for r in h['thu'] if r['ngang']]
    h['doi_thau'] = [r for r in h['thu'] if not r['ngang']]
    h['pay_abs'] = abs(M.dec(h['PayAmount']))
    h['them'] = _desc3(h.get('Desc3'))
    h['d3'] = _desc3_dict(h.get('Desc3'))
    tm, ck = M.dec(h['CashPay']) != 0, M.dec(h['CardPay']) != 0
    h['phuong_thuc'] = 'Tiền mặt + CK' if tm and ck else ('Chuyển khoản' if ck else ('Tiền mặt' if tm else 'Không thu tiền'))
    h['tl_ban'] = round(sum(r['chi'] for r in h['ban']), 2)
    h['tl_thu'] = round(sum(r['chi'] for r in h['thu']), 2)
    # NV HỖ TRỢ không có chỗ trên máy KK — KHBL giữ ở gold_bill.emp_sup_id (hóa đơn lập trên KHBL)
    h['nv_ho_tro'], h['emp_sup_id'], h['co_so_khbl'] = '', '', False
    try:
        from . import services as S
        from .models import GoldBill
        dong = GoldBill.all_objects.filter(trn_id=trn).values_list('emp_sup_id', flat=True)
        h['co_so_khbl'] = dong.exists()
        sup = dong.first()
        h['emp_sup_id'] = str(sup or '').strip()
        if sup:
            ten = {str(e['EmpID']).strip(): e['EmpName'] for e in S.nhan_vien_ban()}
            h['nv_ho_tro'] = ten.get(str(sup).strip(), str(sup).strip())
    except Exception:
        log.exception('CHECK GOLD: không đọc được NV hỗ trợ của %s', trn)
    return h


# ─────────────────────────── 3 danh sách nhanh trong ngày ───────────────────────────
def _desc3_dict(raw):
    """Desc3 → dict các ô của form SỬA (kênh · social · khách gửi · đơn cưới · ngày cưới · ghi chú)."""
    import json
    try:
        d = json.loads(str(raw or '').strip() or '{}')
    except ValueError:
        return {'loi': True, 'channel': 'store', 'note': str(raw or '')}
    d = d if isinstance(d, dict) else {}
    loai = d.get('type') or []
    return {'loi': False,
            'channel': d.get('channel') or ('online' if 'Đơn online' in loai else 'store'),
            'social': d.get('social') or '', 'customer_hold': d.get('customer_hold') is True,
            'wedding': d.get('wedding') is True or 'Đơn cưới' in loai,
            'NgayCuoi': str(d.get('NgayCuoi') or '')[:10], 'note': d.get('note') or ''}


def _desc3(raw):
    """Desc3 của hóa đơn = JSON app ghi (kênh bán · đơn cưới · khách gửi hàng · ghi chú) → [(nhãn, giá trị)] chỉ
    những mục CÓ nội dung (GĐ chốt 29/09/2026). Không phải JSON thì trả nguyên văn làm "Ghi chú"."""
    import json
    s = str(raw or '').strip()
    if not s:
        return []
    try:
        d = json.loads(s)
    except ValueError:
        return [('Ghi chú', s)]
    if not isinstance(d, dict):
        return [('Ghi chú', s)]
    ra = []
    kenh = d.get('channel')
    if kenh:
        ra.append(('Kênh', 'Online · ' + (d.get('social') or 'Online') if kenh == 'online'
                   else ('Tại tiệm' if kenh == 'store' else str(kenh))))
    loai = [str(x) for x in (d.get('type') or []) if x]
    if loai:
        ra.append(('Loại đơn', ', '.join(loai)))
    if d.get('wedding'):
        if d.get('NgayCuoi'):
            try:
                ra.append(('Ngày cưới', dt.date.fromisoformat(str(d['NgayCuoi'])[:10]).strftime('%d/%m/%Y')))
            except ValueError:
                ra.append(('Ngày cưới', str(d['NgayCuoi'])))
        if d.get('wedding_quantity'):
            ra.append(('Số món cưới', str(d['wedding_quantity'])))
        if d.get('wedding_bill_total'):
            ra.append(('Tổng đơn cưới', M.money_vn(d['wedding_bill_total'])))
    if d.get('customer_hold') is True:
        ra.append(('Khách gửi hàng', 'Đang gửi tại tiệm'))
    if str(d.get('note') or '').strip():
        ra.append(('Ghi chú', str(d['note']).strip()))
    return ra


def ds_chuyen_khoan(ngay):
    """CK trong ngày — khj_bl.bank_notifications (KHÔNG hỏi KK), kèm mã chứng từ skill đã tách."""
    with connection.cursor() as cur:
        cur.execute("SELECT transaction_time, direction, trans_amount, bank_name, bank_number, ma_chung_tu, "
                    "loai_chung_tu, description FROM bank_notifications "
                    "WHERE transaction_time >= %s AND transaction_time < %s "
                    "ORDER BY transaction_time DESC, id DESC",
                    [ngay.isoformat(), (ngay + dt.timedelta(days=1)).isoformat()])
        cot = [c[0] for c in cur.description]
        rows = [dict(zip(cot, r)) for r in cur.fetchall()]
    for r in rows:
        r['gio'] = str(r['transaction_time'] or '')[11:16]
        r['vao'] = str(r['direction']).lower() == 'in'
    vao = sum((M.dec(r['trans_amount']) for r in rows if r['vao']), M.D0)
    ra = sum((M.dec(r['trans_amount']) for r in rows if not r['vao']), M.D0)
    return rows, {'so': len(rows), 'vao': vao, 'ra': ra, 'rong': vao - ra}


def ds_phieu_thau(c, ngay):
    rows = c.query(
        "SELECT g.TrnID, g.BillCode, g.TrnTime, ISNULL(c.CustName,'') AS CustName, ISNULL(e.EmpName,'') AS EmpName, "
        "g.TotalAmount, ISNULL(g.CardPay,0) AS CardPay, g.Status, g.IsDel FROM TRN_RT_BUYGOLD g WITH (NOLOCK) "
        "LEFT JOIN I_CUSTOMER c WITH (NOLOCK) ON c.CustID=g.CustID "
        "LEFT JOIN T_EMPLOYEE e WITH (NOLOCK) ON e.EmpID=g.EmpID "
        "WHERE g.CreatedDate >= ? AND g.CreatedDate < ? ORDER BY g.BillCode DESC",
        (ngay.isoformat(), (ngay + dt.timedelta(days=1)).isoformat()))
    return rows, {'so': len(rows), 'tong': sum((M.dec(r['TotalAmount']) for r in rows), M.D0),
                  'ck': sum((M.dec(r['CardPay']) for r in rows), M.D0)}


def ds_phieu_ban(c, ngay):
    rows = c.query(
        "SELECT b.TrnID, b.BillCode, b.TrnTime, ISNULL(c.CustName,'') AS CustName, ISNULL(e.EmpName,'') AS EmpName, "
        "b.PayAmount, ISNULL(b.CashPay,0) AS CashPay, ISNULL(b.CardPay,0) AS CardPay, b.Status, b.IsDel "
        "FROM TRN_RT_BUYSELL b WITH (NOLOCK) LEFT JOIN I_CUSTOMER c WITH (NOLOCK) ON c.CustID=b.CustID "
        "LEFT JOIN T_EMPLOYEE e WITH (NOLOCK) ON e.EmpID=b.EmpID "
        "WHERE b.CreatedDate >= ? AND b.CreatedDate < ? ORDER BY b.BillCode DESC",
        (ngay.isoformat(), (ngay + dt.timedelta(days=1)).isoformat()))
    return rows, {'so': len(rows), 'tong': sum((M.dec(r['PayAmount']) for r in rows), M.D0),
                  'tien_mat': sum((M.dec(r['CashPay']) for r in rows), M.D0),
                  'ck': sum((M.dec(r['CardPay']) for r in rows), M.D0)}


CD_NGHIEP_VU = {0: 'Hủy phiên', 1: 'Cầm mới', 2: 'Cầm thêm', 3: 'Trả bớt', 4: 'Gia hạn',
                5: 'Chuộc đồ', 6: 'Thanh lý', 7: 'Báo mất', 8: 'Mở khóa báo mất'}
CD_DA_KHOP = {'MATCHED', 'CONFIRMED', 'RECONCILED'}          # cd_payments.reconciliation_state đã khớp ngân hàng


def _cam_do_sql(ngay):
    """Phiên cầm đồ trong ngày — đọc THẲNG khj_cd (CHỈ ĐỌC, không ghi gì sang KHCD).

    Một dòng cd_loan_logs = một phiên làm việc với khách; tiền của phiên nằm ở cd_payments (có thể nhiều
    dòng: vừa tiền mặt vừa chuyển khoản) nên gom sẵn trong bảng dẫn xuất, giới hạn đúng log của ngày.
    """
    d1, d2 = ngay.isoformat(), (ngay + dt.timedelta(days=1)).isoformat()
    with connection.cursor() as cur:
        cur.execute(
            "SELECT l.id, l.happened_at, l.operation_id, l.employee_name, l.interest, l.principal_change, "
            "       l.principal_after, l.note, n.sku, n.phone, n.customer_snapshot, n.loan_state, "
            "       COALESCE(t.tong,0) tong, COALESCE(t.tien_mat,0) tien_mat, COALESCE(t.ck,0) ck, "
            "       t.chieu, t.doi_soat "
            "FROM khj_cd.cd_loan_logs l "
            "JOIN khj_cd.cd_loans n ON n.id = l.loan_id "
            "LEFT JOIN (SELECT p.log_id, SUM(p.amount) tong, "
            "                  SUM(COALESCE(p.cashPay, CASE WHEN p.channel='CASH' THEN p.amount ELSE 0 END)) tien_mat, "
            "                  SUM(COALESCE(p.cardPay, CASE WHEN p.channel='BANK' THEN p.amount ELSE 0 END)) ck, "
            "                  GROUP_CONCAT(DISTINCT p.direction) chieu, "
            "                  GROUP_CONCAT(DISTINCT p.reconciliation_state) doi_soat "
            "           FROM khj_cd.cd_payments p JOIN khj_cd.cd_loan_logs l2 ON l2.id = p.log_id "
            "           WHERE l2.happened_at >= %s AND l2.happened_at < %s GROUP BY p.log_id) t ON t.log_id = l.id "
            "WHERE l.happened_at >= %s AND l.happened_at < %s ORDER BY l.id DESC",
            [d1, d2, d1, d2])
        cot = [c[0] for c in cur.description]
        return [dict(zip(cot, r)) for r in cur.fetchall()]


def _gom_cam_do(rows):
    """Dựng dòng hiển thị + thống kê cho popup CẦM ĐỒ (tách khỏi SQL để kiểm được không cần khj_cd)."""
    import json
    ra = []
    tong = {'so': 0, 'chi': M.D0, 'thu': M.D0, 'tien_mat': M.D0, 'ck': M.D0, 'lai': M.D0, 'cho_ck': 0}
    for r in rows:
        snap = r.get('customer_snapshot') or {}
        if isinstance(snap, (str, bytes)):
            try:
                snap = json.loads(snap or '{}')
            except ValueError:
                snap = {}
        if not isinstance(snap, dict):
            snap = {}
        tt_ck = {s.strip().upper() for s in str(r.get('doi_soat') or '').split(',') if s.strip()}
        chieu = {s.strip().upper() for s in str(r.get('chieu') or '').split(',') if s.strip()}
        ra_tien = 'OUT' in chieu                       # tiệm đưa tiền cho khách (cầm mới · cầm thêm)
        nghiep_vu = int(r.get('operation_id') or 0)
        huy = nghiep_vu == 0 or bool(tt_ck & {'REVERSED', 'VOID'})
        tien, tien_mat, ck = M.dec(r['tong']), M.dec(r['tien_mat']), M.dec(r['ck'])
        if ck <= 0:
            nhan, lop = ('Tiền mặt', 'I') if tien > 0 else ('—', 'I')
        elif tt_ck & CD_DA_KHOP:
            nhan, lop = 'Đã khớp', 'S'
        elif 'PARTIAL' in tt_ck:
            nhan, lop = 'Một phần', 'O'
        else:
            nhan, lop = 'Chờ đối soát', 'O'
        if ck > 0 and not (tt_ck & CD_DA_KHOP) and not huy:
            tong['cho_ck'] += 1
        luc = timezone.localtime(r['happened_at']) if timezone.is_aware(r['happened_at']) else r['happened_at']
        ra.append({
            'id': r['id'], 'gio': luc.strftime('%H:%M'), 'ma': r.get('sku') or f"#{r['id']}",
            'nghiep_vu': CD_NGHIEP_VU.get(nghiep_vu, str(nghiep_vu)),
            'khach': snap.get('name') or snap.get('CustName') or snap.get('customer_name') or 'Khách lẻ',
            'dt': r.get('phone') or '', 'nv': r.get('employee_name') or '',
            'ra_tien': ra_tien, 'tong': tien, 'tien_mat': tien_mat, 'ck': ck,
            'lai': M.dec(r.get('interest')), 'goc_con': M.dec(r.get('principal_after')),
            'doi_soat': nhan, 'doi_soat_lop': 'x' if huy else lop, 'huy': huy, 'ghi_chu': r.get('note') or '',
        })
        if huy:
            continue
        tong['so'] += 1
        tong['lai'] += M.dec(r.get('interest'))
        tong['tien_mat'] += tien_mat
        tong['ck'] += ck
        tong['chi' if ra_tien else 'thu'] += tien
    return ra, tong


def ds_cam_do(ngay):
    """Danh sách + thống kê phiên cầm đồ trong ngày (KHCD, chỉ đọc)."""
    return _gom_cam_do(_cam_do_sql(ngay))


def _gom_loai_vang(rows, dem='SoMon', tl='TL', tien='Tien'):
    """Dòng theo loại vàng + dòng tổng (trọng lượng KK lưu ×100 nên đổi ra CHI ngay ở đây)."""
    ra = [{'ma': str(r['GoldCode'] or '').strip() or '—', 'ten': (r['GoldDesc'] or '').strip(),
           'so_mon': int(r[dem] or 0), 'chi': chi(r[tl]), 'tien': M.dec(r[tien])} for r in rows]
    tong = {'loai': len(ra), 'so_mon': sum(x['so_mon'] for x in ra),
            'chi': round(sum(x['chi'] for x in ra), 2), 'tien': sum((x['tien'] for x in ra), M.D0)}
    # Vàng THÂU / ĐỔI (GĐ chốt 27/09/2026): tách TL vàng · TL hột; ĐƠN GIÁ = tổng tiền ÷ TL VÀNG (đồng/chỉ) —
    # máy KK tính tiền trên TL vàng, hột không được trả tiền nên không chia cho tổng TL.
    if rows and 'TLVang' in rows[0]:
        for x, r in zip(ra, rows):
            x['chi_vang'], x['chi_hot'] = chi(r['TLVang']), chi(r['TLHot'])
            x['don_gia'] = _don_gia(x['tien'], x['chi_vang'])
        tong['chi_vang'] = round(sum(x['chi_vang'] for x in ra), 2)
        tong['chi_hot'] = round(sum(x['chi_hot'] for x in ra), 2)
        tong['don_gia'] = _don_gia(tong['tien'], tong['chi_vang'])
    return ra, tong


def _don_gia(tien, chi_vang):
    """Đồng / chỉ vàng, làm tròn đồng; không có TL vàng thì 0."""
    from decimal import ROUND_HALF_UP, Decimal
    if not chi_vang:
        return M.D0
    return (M.dec(tien) / Decimal(str(chi_vang))).quantize(Decimal(1), rounding=ROUND_HALF_UP)


def ds_vang_ban(c, ngay):
    """VÀNG BÁN RA trong ngày, gom theo loại vàng — TRN_RT_BUYSELL_SELL, bỏ hóa đơn đã hủy (CHỈ ĐỌC)."""
    rows = c.query(
        "SELECT s.GoldCode, ISNULL(g.GoldDesc,'') AS GoldDesc, COUNT(*) AS SoMon, "
        "SUM(ISNULL(s.TotalWeight,0)) AS TL, SUM(ISNULL(s.SellAmount,0)) AS Tien "
        "FROM TRN_RT_BUYSELL_SELL s WITH (NOLOCK) "
        "JOIN TRN_RT_BUYSELL b WITH (NOLOCK) ON b.TrnID = s.TrnID "
        "LEFT JOIN I_GOLD g WITH (NOLOCK) ON g.GoldCode = s.GoldCode "
        "WHERE b.CreatedDate >= ? AND b.CreatedDate < ? AND ISNULL(b.IsDel,'0') = '0' "
        "GROUP BY s.GoldCode, g.GoldDesc ORDER BY SUM(ISNULL(s.SellAmount,0)) DESC",
        (ngay.isoformat(), (ngay + dt.timedelta(days=1)).isoformat()))
    return _gom_loai_vang(rows)


# Nhóm TUỔI VÀNG trên header popup Vàng thâu (GĐ chốt 27/09/2026): hàng bán + dẻ cùng tuổi gộp một nhóm
NHOM_TUOI = (('610', ('18K', 'D18K')), ('980', ('24K', 'D24K')), ('9999', ('N9999', 'D9999')))


def _nhom_tuoi(*bang):
    """Tổng TL / TL vàng theo nhóm tuổi 610 · 980 · 9999 (+ 'Khác' nếu có loại ngoài 3 nhóm) trên các bảng đưa vào."""
    gom = {}
    for ds in bang:
        for x in ds:
            ten = next((t for t, ma in NHOM_TUOI if x['ma'].upper() in {m.upper() for m in ma}), 'Khác')
            g = gom.setdefault(ten, {'ten': ten, 'chi': 0.0, 'chi_vang': 0.0})
            g['chi'] += x['chi']
            g['chi_vang'] += x.get('chi_vang', x['chi'])
    thu_tu = [t for t, _ in NHOM_TUOI] + ['Khác']
    return [dict(g, chi=round(g['chi'], 2), chi_vang=round(g['chi_vang'], 2))
            for t in thu_tu for g in [gom.get(t)] if g]


def ds_vang_thau(c, ngay):
    """VÀNG THÂU trong ngày, tách BA bảng (CHỈ ĐỌC, bỏ phiếu/hóa đơn đã hủy):

    · THÂU       — phiếu thâu độc lập TRN_RT_BUYGOLD (khách chỉ bán vàng, tiệm chi tiền);
    · ĐỔI - THÂU — vàng cũ trong hóa đơn bán, tính theo GIÁ THÂU;
    · ĐỔI - NGANG — vàng cũ trong hóa đơn bán, tính theo GIÁ BÁN RA của đúng loại đó (đổi ngang cùng tuổi).

    Máy KK không có cột đánh dấu đổi ngang (GĐ chốt 27/09/2026 tách theo giá): dòng TRN_RT_BUYSELL_BUYGOLD có
    BuyRate ≥ GIÁ BÁN RA niêm yết của loại đó ĐANG HIỆU LỰC lúc lập hóa đơn → đổi ngang (đúng luật
    money.chia_doi_ngang: phần đổi ngang định giá bán ra, phần dư định giá thâu). Giá hiệu lực = dòng mới nhất của
    I_XRATE (bảng giá hiện hành) ∪ I_XRATE_HIST có ngày giờ ≤ lúc lập hóa đơn — lịch sử KK không ghi đủ mọi lần
    đổi giá nên phải gộp cả bảng hiện hành. Không tìm được giá bán → coi là THÂU.
    """
    kh = (ngay.isoformat(), (ngay + dt.timedelta(days=1)).isoformat())
    thau = c.query(
        "SELECT t.GoldCode, ISNULL(g.GoldDesc,'') AS GoldDesc, COUNT(*) AS SoMon, "
        "SUM(ISNULL(t.GoldWeight,0) + ISNULL(t.DiamondWeight,0)) AS TL, SUM(ISNULL(t.GoldWeight,0)) AS TLVang, "
        "SUM(ISNULL(t.DiamondWeight,0)) AS TLHot, SUM(ISNULL(t.TotalAmount,0)) AS Tien "
        "FROM TRN_RT_BUYGOLD t WITH (NOLOCK) "
        "LEFT JOIN I_GOLD g WITH (NOLOCK) ON g.GoldCode = t.GoldCode "
        "WHERE t.CreatedDate >= ? AND t.CreatedDate < ? AND ISNULL(t.IsDel,'0') = '0' "
        "GROUP BY t.GoldCode, g.GoldDesc ORDER BY SUM(ISNULL(t.TotalAmount,0)) DESC", kh)
    kieu = ("CASE WHEN bg.BuyRate >= ISNULL(x.SellRate, 999999999999) THEN 'ngang' ELSE 'thau' END")
    doi = c.query(
        "SELECT bg.GoldCode, ISNULL(g.GoldDesc,'') AS GoldDesc, " + kieu + " AS Kieu, COUNT(*) AS SoMon, "
        "SUM(ISNULL(bg.TotalGoldWeight, ISNULL(bg.GoldWeight,0) + ISNULL(bg.DiamondWeight,0))) AS TL, "
        "SUM(ISNULL(bg.GoldWeight,0)) AS TLVang, SUM(ISNULL(bg.DiamondWeight,0)) AS TLHot, "
        "SUM(ISNULL(bg.BuyAmount,0)) AS Tien "
        "FROM TRN_RT_BUYSELL_BUYGOLD bg WITH (NOLOCK) "
        "JOIN TRN_RT_BUYSELL b WITH (NOLOCK) ON b.TrnID = bg.TrnID "
        "LEFT JOIN I_GOLD g WITH (NOLOCK) ON g.GoldCode = bg.GoldCode "
        "OUTER APPLY (SELECT TOP 1 r.SellRate FROM ("
        "  SELECT GoldCcy, RateDate, RateTime, SellRate FROM I_XRATE WITH (NOLOCK) "
        "  UNION ALL SELECT GoldCcy, RateDate, RateTime, SellRate FROM I_XRATE_HIST WITH (NOLOCK)) r "
        "  WHERE r.GoldCcy = bg.GoldCode "
        "  AND DATEADD(second, DATEDIFF(second, 0, CAST(r.RateTime AS datetime)), r.RateDate) <= b.CreatedDate "
        "  ORDER BY r.RateDate DESC, r.RateTime DESC) x "
        "WHERE b.CreatedDate >= ? AND b.CreatedDate < ? AND ISNULL(b.IsDel,'0') = '0' "
        "GROUP BY bg.GoldCode, g.GoldDesc, " + kieu + " ORDER BY SUM(ISNULL(bg.BuyAmount,0)) DESC", kh)
    d_thau, t_thau = _gom_loai_vang(thau)
    d_dt, t_dt = _gom_loai_vang([r for r in doi if r['Kieu'] != 'ngang'])
    d_dn, t_dn = _gom_loai_vang([r for r in doi if r['Kieu'] == 'ngang'])
    ba = (t_thau, t_dt, t_dn)
    return ({'thau': d_thau, 'doi_thau': d_dt, 'doi_ngang': d_dn},
            {'thau': t_thau, 'doi_thau': t_dt, 'doi_ngang': t_dn,
             'so_mon': sum(t['so_mon'] for t in ba),
             'chi': round(sum(t['chi'] for t in ba), 2),
             'tien': sum((t['tien'] for t in ba), M.D0),
             # header 3 dòng (GĐ chốt 27/09/2026): tổng thâu · tổng đổi (thâu + ngang) · TỔNG cả ba
             'nhom_thau': _nhom_tuoi(d_thau), 'nhom_doi': _nhom_tuoi(d_dt, d_dn),
             'nhom': _nhom_tuoi(d_thau, d_dt, d_dn)})


def chu_ky(ngay):
    """Chữ ký NHẸ của ngày (chỉ MySQL) — trang chỉ tải lại bảng khi chữ ký đổi, thay SSE 2 giây của V3."""
    with connection.cursor() as cur:
        cur.execute("SELECT COUNT(*), COALESCE(MAX(id),0), COALESCE(MAX(go_luc),'') FROM gold_check_entry WHERE ngay=%s",
                    [ngay])
        a = cur.fetchone()
        # Các cột GĐ chốt phải theo dõi: expected/cash/bank/cus_cash/cus_change/payment_status (+ id mới, + hủy)
        cur.execute("SELECT COUNT(*), COALESCE(MAX(id),0), COALESCE(SUM(expected_amount),0), "
                    "COALESCE(SUM(cash_amount),0), COALESCE(SUM(bank_amount),0), COALESCE(SUM(cus_cash),0), "
                    "COALESCE(SUM(cus_change),0), COALESCE(SUM(is_void),0), "
                    "SUM(CASE WHEN payment_status='confirmed' THEN 1 ELSE 0 END), "
                    "SUM(CASE WHEN payment_status='partial' THEN 1 ELSE 0 END), "
                    "SUM(CASE WHEN payment_status='waiting' THEN 1 ELSE 0 END), "
                    "SUM(CASE WHEN payment_status='review' THEN 1 ELSE 0 END), "
                    "SUM(CASE WHEN payment_status='void' THEN 1 ELSE 0 END) "
                    "FROM money_flow WHERE business_date=%s", [ngay])
        b = cur.fetchone()
    return '|'.join(str(x) for x in (*a, *b))


# ─────────────────────────── views ───────────────────────────
# TRANG ĐỨNG RIÊNG (GĐ chốt 23/09/2026): KHÔNG đăng nhập, KHÔNG menu — @login_not_required vượt LoginRequiredMiddleware.
from django.contrib.auth.decorators import login_not_required       # noqa: E402
from django.http import HttpResponse, JsonResponse                 # noqa: E402
from django.shortcuts import render                                 # noqa: E402
from django.views.decorators.http import require_GET, require_POST  # noqa: E402

from .check_bill import _nguoi                                      # noqa: E402  (cùng cách xác định "người thao tác")


def _ngay(request):
    raw = (request.GET.get('d') or request.POST.get('d') or '').strip()
    try:
        return dt.date.fromisoformat(raw[:10])
    except ValueError:
        return timezone.localdate()


def _nv(request):
    return (request.GET.get('nv') or request.POST.get('nv') or '').strip()


def _khoi(request, ngay, nv, tin='', muc='', hd=None):
    """Khối làm việc: thanh QUÉT + bảng HÀNG ĐÃ LẤY RA + dòng GIAO DỊCH (hoặc panel hóa đơn vừa quét).

    Thanh quét nằm TRONG khối (GĐ chốt 27/09/2026) nên mọi đường dựng lại khối đều phải kèm danh sách
    nhân viên, không riêng trang đầu."""
    from . import services as S
    ctx = {'ngay': ngay, 'nv': nv, 'tin': tin, 'muc': muc, 'hd': hd, 'hom_nay': timezone.localdate(),
           'hd_nhom_doi': [('Đổi ngang vàng', hd.get('doi_ngang') or []), ('Đổi thâu vào', hd.get('doi_thau') or [])]
           if hd else []}
    # Danh sách nhân viên đổi rất thưa mà khối này dựng lại sau MỖI lượt quét → nhớ 5 phút cho đỡ phiền máy KK
    from django.core.cache import cache
    ds_nv = cache.get('cg_nhan_vien')
    if ds_nv is None:
        try:
            ds_nv = nhan_vien_ds(S.client('check_gold'))
        except Exception:
            log.exception('CHECK GOLD: không đọc được danh sách nhân viên')
            ds_nv = []
        cache.set('cg_nhan_vien', ds_nv, 300)
    ctx['nhan_vien_ds'] = ds_nv
    try:
        ctx.update(rows=danh_sach(S.client('check_gold'), ngay), gd=giao_dich(ngay), chu_ky=chu_ky(ngay))
    except Exception as exc:
        log.exception('CHECK GOLD: không đọc được dữ liệu ngày %s', ngay)
        from .customer import error_message
        ctx['loi'] = error_message(exc)
    return ctx


@login_not_required
@require_GET
def trang(request):
    from . import services as S
    ngay, nv = _ngay(request), _nv(request)
    ctx = _khoi(request, ngay, nv)
    tep = 'pos/_check_gold_khoi.html' if request.headers.get('HX-Request') else 'pos/check_gold.html'
    return render(request, tep, ctx)


@login_not_required
@require_POST
def quet_view(request):
    from . import services as S
    ngay, nv = _ngay(request), _nv(request)
    hd = None
    try:
        kieu, du_lieu, tin, muc = quet(S.client('check_gold'), request.POST.get('ma', ''), nv, ngay)
        hd = du_lieu if kieu == 'hd' else None
    except LoiQuet as exc:
        tin, muc = str(exc), 'loi'
    except Exception as exc:
        log.exception('CHECK GOLD: quét lỗi')
        from .customer import error_message
        tin, muc = error_message(exc), 'loi'
    return render(request, 'pos/_check_gold_khoi.html', _khoi(request, ngay, nv, tin, muc, hd))


@login_not_required
@require_POST
def go_view(request, pk):
    ngay, nv = _ngay(request), _nv(request)
    ma = go_dong(pk, _nguoi(request))
    tin = f'Đã gỡ {ma} khỏi danh sách kiểm.' if ma else 'Dòng đã được gỡ trước đó.'
    return render(request, 'pos/_check_gold_khoi.html', _khoi(request, ngay, nv, tin, 'canh_bao'))


def _the_json(g):
    return {**g, 'tong': str(g['tong']), 'tien_mat': str(g['tien_mat']), 'ck': str(g['ck']),
            'khach_dua': str(g['khach_dua']), 'thoi_lai': str(g['thoi_lai']), 'dua_khach': str(g['dua_khach'])}


@login_not_required
@require_GET
def theo_doi(request):
    """Nhịp 2 giây (chỉ MySQL): CHỮ KÝ + danh sách thẻ, nhưng chỉ gửi TRỌN nội dung của thẻ VỪA ĐỔI.

    Trang gửi kèm `biet=id:h,id:h…` (thẻ đang có trên màn hình + dấu vân tay). Trả về:
        ds   — [[id, h], …] đủ mọi thẻ còn sống, đúng thứ tự mới → cũ (thẻ thiếu trong ds = rời bảng);
        moi  — trọn nội dung CHỈ những thẻ trang chưa có hoặc có dấu khác;
        gon  — id các thẻ đang ở dạng thu gọn (đổi theo đồng hồ, không theo nội dung).
    Trước đây gửi trọn mọi thẻ mỗi 2 giây (~640 byte/thẻ → 54 thẻ ≈ 35 KB/2 giây/máy), GĐ chốt 27/09/2026.
    Trang chỉ tải lại bảng HÀNG ĐÃ LẤY RA (có hỏi KK) khi phần chữ ký của bảng đó đổi — tiết kiệm máy KK."""
    ngay = _ngay(request)
    ky = chu_ky(ngay)
    biet = dict(x.split(':', 1) for x in (request.GET.get('biet') or '').split(',') if ':' in x)
    gd = giao_dich(ngay)
    return JsonResponse({'chu_ky': ky, 'doi': ky != (request.GET.get('ky') or ''),
                         'ds': [[g['id'], g['h']] for g in gd],
                         'moi': [_the_json(g) for g in gd if biet.get(str(g['id'])) != g['h']],
                         'gon': [g['id'] for g in gd if g['gon']]})


def _ds_tk(c, hd):
    """Danh sách TK nhận CK cho form SỬA. ⚠ TK đang ghi trên hóa đơn (Desc4) mà không còn trong I_BankCard đang bật
    vẫn phải có trong danh sách và được chọn sẵn — thiếu nó thì ô chọn rơi về "Không có" và bấm LƯU là XÓA MẤT
    Desc4 (thực đo 29/09/2026: HĐ 26-09-29-000215 ghi 50361307, danh sách không có)."""
    ds = list(c.query("SELECT NumberBank, AccName, BankName FROM I_BankCard WITH (NOLOCK) WHERE Active=1 "
                      "ORDER BY NumberBank DESC"))
    tk = str((hd or {}).get('Desc4') or '').strip()
    if tk and all(str(b['NumberBank']).strip() != tk for b in ds):
        ds.insert(0, {'NumberBank': tk, 'AccName': '', 'BankName': 'TK đang ghi trên hóa đơn'})
    return ds


def _ds_nv(c, hd):
    """Danh sách NV cho form SỬA — cùng lý do với _ds_tk: NV bán / NV hỗ trợ đang ghi trên hóa đơn mà nay đã nghỉ
    (không còn Active) vẫn phải có trong ô chọn, không thì bấm LƯU sẽ âm thầm đổi sang người khác."""
    ds = list(nhan_vien_ds(c))
    co = {str(e['EmpID']).strip() for e in ds}
    for ma, ten in ((str((hd or {}).get('EmpID') or '').strip(), (hd or {}).get('EmpName')),
                    (str((hd or {}).get('emp_sup_id') or '').strip(), (hd or {}).get('nv_ho_tro'))):
        if ma and ma not in co:
            ds.insert(0, {'EmpID': ma, 'EmpName': f'{ten or ma} (đã nghỉ)'})
            co.add(ma)
    return ds


@login_not_required
@require_GET
def sua_form(request, bill_code):
    """Popup SỬA HÓA ĐƠN (như V3): khách · nhân viên · ngân hàng · ghi chú CK · tách tiền mặt/chuyển khoản."""
    from . import services as S
    c = S.client('check_gold')
    try:
        hd = hoa_don(c, bill_code)
        nv = _ds_nv(c, hd)
        bank = _ds_tk(c, hd)
    except Exception as exc:
        log.exception('CHECK GOLD: mở popup sửa hóa đơn lỗi')
        from .customer import error_message
        return render(request, 'pos/_check_gold_sua.html', {'loi': error_message(exc), 'ngay': _ngay(request)})
    if not hd:
        return render(request, 'pos/_check_gold_sua.html', {'loi': f'Không tìm thấy hóa đơn {bill_code}.',
                                                            'ngay': _ngay(request)})
    from .mobile_invoice import SOCIALS
    return render(request, 'pos/_check_gold_sua.html', {'hd': hd, 'nhan_vien_ds': nv, 'bank_ds': bank,
                                                        'ngay': _ngay(request), 'nv': _nv(request),
                                                        'social_ds': SOCIALS})


@login_not_required
@require_POST
def sua_luu(request, bill_code):
    """Lưu popup sửa hóa đơn → ghi KK qua cổng an toàn. Lỗi thì giữ popup, không ghi gì."""
    from . import services as S
    ngay, nv = _ngay(request), _nv(request)
    c = S.client('check_gold')
    try:
        tin = sua_hoa_don(c, request.POST.get('trn_id', ''), bill_code, request.POST)
    except LoiQuet as exc:
        hd = hoa_don(c, bill_code)
        from .mobile_invoice import SOCIALS
        return render(request, 'pos/_check_gold_sua.html', {'hd': hd, 'loi': str(exc), 'ngay': ngay, 'nv': nv,
                                                            'nhan_vien_ds': _ds_nv(c, hd), 'social_ds': SOCIALS,
                                                            'bank_ds': _ds_tk(c, hd)})
    except Exception as exc:
        log.exception('CHECK GOLD: lưu hóa đơn lỗi')
        from .customer import error_message
        return render(request, 'pos/_check_gold_sua.html', {'hd': hoa_don(c, bill_code), 'loi': error_message(exc),
                                                            'ngay': ngay, 'nv': nv, 'nhan_vien_ds': [], 'bank_ds': []})
    ra = render(request, 'pos/_check_gold_khoi.html', _khoi(request, ngay, nv, tin, 'ok', hoa_don(c, bill_code)))
    ra.content = b'<div id="cg-modal" hx-swap-oob="true"></div>' + ra.content       # đóng popup
    return ra


@login_not_required
@require_GET
def tim_khach(request):
    """Gợi ý khách hàng cho popup sửa (>= 2 ký tự) — chỉ đọc."""
    from . import services as S
    q = (request.GET.get('q') or '').strip()
    if len(q) < 2:
        return JsonResponse({'rows': []})
    try:
        rows = S.tim_khach(q, limit=10)
    except Exception:
        log.exception('CHECK GOLD: tìm khách lỗi')
        return JsonResponse({'rows': []})
    return JsonResponse({'rows': [{'id': r.get('CustID'), 'ten': r.get('CustName') or '',
                                   'dt': r.get('Phone') or '', 'cccd': r.get('CMND') or ''} for r in rows]})


@login_not_required
@require_GET
def hoa_don_view(request, bill_code):
    from . import services as S
    ngay, nv = _ngay(request), _nv(request)
    try:
        hd = hoa_don(S.client('check_gold'), bill_code)
    except Exception as exc:
        log.exception('CHECK GOLD: đọc hóa đơn lỗi')
        from .customer import error_message
        return render(request, 'pos/_check_gold_khoi.html', _khoi(request, ngay, nv, error_message(exc), 'loi'))
    if not hd:
        return render(request, 'pos/_check_gold_khoi.html',
                      _khoi(request, ngay, nv, f'Không tìm thấy hóa đơn {bill_code}.', 'loi'))
    return render(request, 'pos/_check_gold_khoi.html', _khoi(request, ngay, nv, f'Hóa đơn {bill_code}.', 'ok', hd))


@login_not_required
@require_GET
def manifest(request):
    """Manifest để lưu trang ra màn hình chính (điện thoại/Windows) với đúng biểu tượng GĐ cấp."""
    from django.templatetags.static import static
    return JsonResponse({
        'name': 'CHECK GOLD · Kim Hạnh 2', 'short_name': 'CHECK GOLD',
        'description': 'Hàng lấy ra khỏi tủ + dòng giao dịch realtime',
        'start_url': '/banle/bao-cao/check-gold/', 'display': 'standalone',
        'background_color': '#FDFCF9', 'theme_color': '#C9A02C',
        'icons': [{'src': static('img/check-gold-32.png'), 'sizes': '32x32', 'type': 'image/png'},
                  {'src': static('img/check-gold.png'), 'sizes': '100x100', 'type': 'image/png'},
                  {'src': static('img/check-gold-180.png'), 'sizes': '180x180', 'type': 'image/png',
                   'purpose': 'any maskable'}]})


@login_not_required
@require_GET
def popup(request, kieu):
    """Danh sách nhanh trong ngày: chuyển khoản · phiếu thâu · phiếu bán · cầm đồ (KHCD) ·
    vàng bán theo loại · vàng thâu + đổi theo loại."""
    from . import services as S
    ngay = _ngay(request)
    try:
        if kieu == 'ck':
            rows, tong = ds_chuyen_khoan(ngay)
        elif kieu == 'thau':
            rows, tong = ds_phieu_thau(S.client('check_gold'), ngay)
        elif kieu == 'ban':
            rows, tong = ds_phieu_ban(S.client('check_gold'), ngay)
        elif kieu == 'cam':
            rows, tong = ds_cam_do(ngay)                    # KHCD, không hỏi máy KK
        elif kieu == 'vang_ban':
            rows, tong = ds_vang_ban(S.client('check_gold'), ngay)
        elif kieu == 'vang_thau':
            rows, tong = ds_vang_thau(S.client('check_gold'), ngay)
        else:
            return HttpResponse('')
    except Exception as exc:
        log.exception('CHECK GOLD: popup %s lỗi', kieu)
        from .customer import error_message
        return render(request, 'pos/_check_gold_popup.html', {'kieu': kieu, 'ngay': ngay, 'loi': error_message(exc)})
    # Ngày đông (300+ phiếu) thì chỉ VẼ 200 dòng mới nhất cho nhẹ — tổng tiền vẫn tính trên TẤT CẢ
    if kieu in ('ban', 'thau', 'cam') and len(rows) > TRAN_POPUP:
        tong['tat_ca'] = len(rows)
        rows = rows[:TRAN_POPUP]
    ctx = {'kieu': kieu, 'ngay': ngay, 'rows': rows, 'tong': tong}
    if kieu == 'vang_thau':
        ctx['tuoi_dong'] = [('Tổng thâu', tong['nhom_thau']), ('Tổng đổi', tong['nhom_doi']), ('TỔNG', tong['nhom'])]
    return render(request, 'pos/_check_gold_popup.html', ctx)
