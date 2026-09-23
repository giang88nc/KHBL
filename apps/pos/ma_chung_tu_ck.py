# -*- coding: utf-8 -*-
"""NHẬN DIỆN MÃ CHỨNG TỪ trong nội dung CHUYỂN KHOẢN (bank_notifications) — MỘT nguồn sự thật.

ĐẶC TẢ GĐ CHỐT 22/09/2026 (skill `.claude/skills/nhan-dien-ma-chung-tu-ck/SKILL.md`):
  · CHỈ DÙNG TRONG NGÀY — tra sổ luôn kèm ngày; khoản của ngày khác không tự gán.
  · Chia 2 chiều theo bank_notifications.direction:
      IN  (direction='in')  : 12 số  = HĐ mua bán + cọc (yymmdd + STT 6 số, vd 260921000232)
                              14 số  = cầm đồ (YYMM + phiếu 5 số + phiên 5 số, vd 26090178206824)
                              KHBL + 10 số = QR độc lập (giây epoch, vd KHBL1790054215)
      OUT (direction='out') : THANH TOAN TIEN VANG + 4 số = 4 số cuối bill_code HĐ ĐẦU (STT trong ngày) → thâu / đổi dư
                              THANH TOAN TIEN VANG 1 + 5 số = 5 số cuối MÃ PHIÊN cầm đồ (vd …VANG 101234) — CHƯA nối phiếu
  · CHỈ nhận dạng mới — các dạng cũ (QR BH/CD/DC/KH+12 ký tự, KH2Q…, TDC…, yymmddHHmm+CD/CT/KC, 'VANG 1-…')
    KHÔNG nhận diện nữa → "Không rõ nguồn". Hằng QR_DOC_LAP / MA_COC còn giữ CHỈ vì luồng đối soát
    mobile_qr / deposit_bank vẫn cần cho phiếu đã phát trước ngày đổi — không dùng trong nhan_dien.

HAI BƯỚC, TÁCH BẠCH:
  1. nhan_dien(mo_ta, huong)  — THUẦN, không DB → list[Ma(loai, ma, quy_tac)]
  2. tra_so(ma, ngay)         — tra sổ money_flow TRONG NGÀY (MySQL, không hỏi KK) → list[MoneyFlow]
Module chỉ trả lời "mã gì, loại gì, phiếu nào"; luật đối soát tiền (tài khoản, cửa sổ giờ, số tiền, is_check)
vẫn nằm ở từng luồng đối soát.
"""
import re
from dataclasses import dataclass

# ── IN ──
QR_MOI = re.compile(r'(?<![A-Z0-9])KHBL\d{10}(?!\d)')
# ── OUT ── (6 số = cầm đồ — log phiên KHCD; 4 số = thâu/đổi dư — tách bằng ĐỘ DÀI, ràng buộc không có chữ số liền sau)
OUT_CAM_DO = re.compile(r'TIEN\s+VANG\s+(\d{6})(?!\d)')   # KHCD ghi 6 số log phiên (22/09/2026: 006830, 101234…)
OUT_STT = re.compile(r'TIEN\s+VANG\s+(\d{4})(?!\d)')

# ── dạng CŨ: KHÔNG dùng trong nhan_dien; chỉ để luồng đối soát cũ xử lý nốt phiếu đã phát ──
QR_DOC_LAP = re.compile(r'(?<![A-Z0-9])(?:KH2Q[A-P]{20}|(?:CD|BH|DC|KH)[A-HJ-NP-Z][A-HJ-NP-Z2-9]{11})(?![A-Z0-9])')
MA_COC = re.compile(r'(?:TDC|TRC)\d{12}')

IN, OUT = 'in', 'out'
# Mã loại GHI vào loai_chung_tu (GĐ chốt 22/09/2026): HD · CD (cầm đồ — IN thu / OUT chi, phân bằng direction) · TV · QR
HD, CAM_DO, QR, STT, CHI_CD = 'HD', 'CD', 'QR', 'TV', 'CD'
TEN = {HD: 'Bán hàng / cọc', CAM_DO: 'Cầm đồ', QR: 'QR độc lập', STT: 'Thâu / đổi dư'}
TEN_DICH_VU = {'RETAIL': 'Bán hàng', 'DEPOSIT': 'Cọc', 'GOLD_BUY': 'Thâu vàng', 'PAWN': 'Cầm đồ', 'REDEEM': 'Chuộc đồ'}


@dataclass(frozen=True)
class Ma:
    loai: str        # IN: HD · CD · QR   —   OUT: TV · CD
    ma: str          # IN: 12/14 chữ số · 'KHBL'+10 số — OUT: yymmdd + 6 số (có ngày) hoặc 4 / 6 số trần (không ngày)
    quy_tac: str

    @property
    def ten(self):
        return TEN.get(self.loai, self.loai)


def nhan_dien(mo_ta, huong=IN, ngay=None):
    """Nội dung CK → list[Ma] không trùng, theo CHIỀU (bank_notifications.direction). THUẦN, không DB.

    OUT có `ngay` (ngày giao dịch) → mã đủ 12 số = yymmdd + 6 số sau "TIEN VANG" (GĐ chốt 22/09/2026):
        '…TIEN VANG 1014-…'   → TV 260921001014   (4 số STT đệm 0 thành 6 số — trùng khuôn số HĐ)
        '…TIEN VANG 101234-…' → CD 260921101234   (6 số log phiên cầm đồ KHCD, vd 006830)"""
    from .money_in import codes as so_chung_tu
    nd = str(mo_ta or '').upper()
    ra = []
    if str(huong or '').lower() == OUT:
        dau = ngay.strftime('%y%m%d') if ngay else ''
        for m in OUT_CAM_DO.findall(nd):
            ra.append(Ma(CHI_CD, dau + m if dau else m, 'TIEN VANG + 6 số phiên cầm đồ'))
        for m in OUT_STT.findall(nd):
            ra.append(Ma(STT, dau + m.zfill(6) if dau else m, 'TIEN VANG + 4 số cuối số HĐ đầu'))
    else:
        for m in QR_MOI.findall(nd):
            ra.append(Ma(QR, m, 'KHBL + 10 số'))
        from django.utils import timezone
        nam = timezone.localdate().year % 100
        for m in sorted(so_chung_tu(bo_so_dinh_chu(nd))):
            if int(m[:2]) not in (nam, nam - 1):    # năm vô lý (vd mã tham chiếu 130125… = "25/01/2013") → không phải chứng từ
                continue          # 12 số yymmdd hợp lệ · 14 số tháng hợp lệ (loại mã GD ngân hàng)
            ra.append(Ma(HD if len(m) == 12 else CAM_DO, m, '12 số yymmdd+STT' if len(m) == 12 else '14 số cầm đồ'))
    seen, out = set(), []
    for x in ra:
        if (x.loai, x.ma) not in seen:
            seen.add((x.loai, x.ma)); out.append(x)
    return out


def bo_so_dinh_chu(nd):
    """Bỏ dãy số DÍNH LIỀN SAU CHỮ CÁI — đó là mã giao dịch ngân hàng / ví, không phải mã chứng từ.

    Thực đo toàn bộ bank_notifications IN (22/09/2026): FT…(14 số) 2.962 · MOMO…(12) 302 · ZP…(12) 167 · TRC/TDC…(12) 230
    — chỉ 1 ca thật khách gõ dính "CK260611000354". ⚠ money_in.codes KHÔNG chặn: FT + năm + NGÀY THỨ trong năm
    (vd FT26093…, ngày 093) có "tháng" 09 hợp lệ → bị nhận là mã cầm đồ 14 số suốt khoảng tháng 1–5; ZP260930… bị nhận là HĐ.
    Tiền tố khách hay gõ (CK, HD) được tách ra giữ lại."""
    nd = re.sub(r'(?<![A-Z])(CK|HD)(?=\d{12}(?!\d))', r'\1 ', nd)
    return re.sub(r'(?<=[A-Z])\d+', ' ', nd)


def ma_duy_nhat(mo_ta, huong=IN, ngay=None):
    """Đúng 1 mã → Ma; 0 hoặc ≥2 mã → None (mơ hồ — KHÔNG tự gán, cùng nguyên tắc money_in)."""
    ds = nhan_dien(mo_ta, huong, ngay)
    return ds[0] if len(ds) == 1 else None


def gon(ma):
    """'26-09-21-000123' → '260921000123'."""
    return re.sub(r'[^0-9A-Z]', '', str(ma or '').upper())


def tra_so(ma, ngay):
    """Ma → list[MoneyFlow] TRONG NGÀY `ngay` (bắt buộc) — chỉ MySQL.
    HD → phiếu KHBL chiều IN (RETAIL/DEPOSIT) · CAM_DO → phiên KHCD chiều IN · STT → phiếu chiều OUT (thâu /
    đổi dư) có số HĐ đầu kết thúc bằng 4 số · QR / CHI_CD → [] (QR tra riêng bằng tra_qr; cầm đồ OUT nối sau)."""
    from .models import MoneyFlow
    qs = MoneyFlow.objects.filter(business_date=ngay, is_void=False)
    if ma.loai == HD:
        ma_gach = f'{ma.ma[:2]}-{ma.ma[2:4]}-{ma.ma[4:6]}-{ma.ma[6:]}'
        return list(qs.filter(direction='IN', source_system='KHBL', source_bill_code__in=[ma_gach, ma.ma]))
    if ma.loai == CAM_DO and len(ma.ma) == 14:          # CD chiều IN (14 số); CD chiều OUT chưa nối phiếu
        return list(qs.filter(direction='IN', source_system='KHCD', source_bill_code=ma.ma))
    if ma.loai == STT:                                  # so 4 số cuối — đúng cả dạng 4 số trần lẫn yymmdd+00xxxx
        return [f for f in qs.filter(direction='OUT', service__in=['GOLD_BUY', 'RETAIL'])
                if gon(str(f.source_bill_code).split(' ')[0])[-4:] == ma.ma[-4:]]
    return []


def tra_qr(ma, ngay):
    """QR độc lập 'KHBL…' → MoneyFlowPayment (origin standalone) tạo trong ngày, hoặc None."""
    import datetime as dt
    from django.utils import timezone
    from .models import MoneyFlowPayment
    dau = timezone.make_aware(dt.datetime.combine(ngay, dt.time.min))
    return MoneyFlowPayment.objects.filter(standalone_reference=ma.ma, created_at__gte=dau,
                                           created_at__lt=dau + dt.timedelta(days=1)).first()


def loai_that(ma, flows):
    """Tên loại cuối cùng sau khi tra sổ: theo service của phiếu (HD → Bán hàng/Cọc; STT → Thâu vàng/Đổi dư)."""
    if flows:
        f = flows[0]
        if ma.loai == STT and f.service == 'RETAIL':
            return 'Đổi dư'
        return TEN_DICH_VU.get(f.service, ma.ten)
    return ma.ten


# ═══════════════════════ GHI KẾT QUẢ VÀO bank_notifications (CỘT RIÊNG KHBL — GĐ chốt 22/09/2026) ═══════════════════════
# Ghi vào 3 cột MỚI chứ không ghi bill_code_raw: BANLE_V5 (bank_notification_mirror) chép đè MỌI cột nó biết mỗi lần
# webhook cập nhật — 3 cột này nó không biết nên không bao giờ bị đè; bill_code_raw giữ nguyên nghĩa cũ cho luồng cũ.
#   ma_chung_tu   : IN 12 số / 14 số / KHBL+10 · OUT yymmdd + 6 số (…VANG 1014 → 260921001014 · …VANG 101234 → 260921101234)
#   loai_chung_tu : HD · CD · TV · QR · NHIEU (≥2 mã, không tự gán) · '' (không có mã)
#   nhan_dien_luc : mốc đã nhận diện (NULL = chưa xử lý) — mỗi dòng xử lý MỘT lần.
COT_MOI = (('ma_chung_tu', 'VARCHAR(20) NULL'), ('loai_chung_tu', 'VARCHAR(8) NULL'), ('nhan_dien_luc', 'DATETIME NULL'))


def tao_cot(cur):
    """Thêm 3 cột + chỉ mục nếu chưa có — chạy lại bao nhiêu lần cũng an toàn (MySQL 8 ALGORITHM=INSTANT)."""
    cur.execute("SELECT column_name FROM information_schema.columns WHERE table_schema=DATABASE() "
                "AND table_name='bank_notifications'")
    co = {r[0].lower() for r in cur.fetchall()}
    them = [f'ADD COLUMN {ten} {kieu}' for ten, kieu in COT_MOI if ten not in co]
    if them:
        cur.execute('ALTER TABLE bank_notifications ' + ', '.join(them) + ', ALGORITHM=INSTANT')
    cur.execute("SELECT COUNT(*) FROM information_schema.statistics WHERE table_schema=DATABASE() "
                "AND table_name='bank_notifications' AND index_name='ix_bn_ma_chung_tu'")
    if not cur.fetchone()[0]:
        cur.execute('ALTER TABLE bank_notifications ADD INDEX ix_bn_ma_chung_tu (ma_chung_tu, loai_chung_tu)')
    return [t for t, _ in COT_MOI if t not in co]


def ket_qua(row):
    """Một dòng bank_notifications → (ma_chung_tu, loai_chung_tu). THUẦN (không tra sổ). Chỉ gán khi ĐÚNG 1 mã."""
    import datetime as dt
    t = row.get('transaction_time')
    try:
        ngay = t.date() if hasattr(t, 'date') else dt.date.fromisoformat(str(t)[:10])
    except ValueError:
        ngay = None
    ds = nhan_dien(row.get('description'), row.get('direction'), ngay)
    if not ds:
        return None, ''
    if len(ds) > 1:
        return None, 'NHIEU'
    return ds[0].ma, ds[0].loai


def gan_ma(cur, tu=None, gioi_han=5000, ghi=True, ids=None):
    """Nhận diện các dòng CHƯA xử lý (nhan_dien_luc IS NULL) → ghi 3 cột. `ids` = chỉ xử lý đúng các dòng này
    (vd vừa được upsert). UPDATE có điều kiện nhan_dien_luc IS NULL — 2 nơi chạy trùng cũng không ghi đè nhau."""
    from collections import Counter
    from django.utils import timezone
    dk, tham = ['nhan_dien_luc IS NULL'], []
    if tu:
        dk.append('transaction_time >= %s'); tham.append(str(tu))
    if ids:
        dk.append('id IN (' + ','.join(['%s'] * len(ids)) + ')'); tham += [int(i) for i in ids]
    cur.execute('SELECT id, direction, description, transaction_time FROM bank_notifications WHERE '
                + ' AND '.join(dk) + ' ORDER BY id LIMIT %s', tham + [gioi_han])
    rows = [dict(zip(('id', 'direction', 'description', 'transaction_time'), r)) for r in cur.fetchall()]
    dem = Counter()
    bay_gio = timezone.localtime().replace(tzinfo=None, microsecond=0)
    for r in rows:
        ma, loai = ket_qua(r)
        dem[loai or 'KHONG'] += 1
        if ghi:
            cur.execute('UPDATE bank_notifications SET ma_chung_tu=%s, loai_chung_tu=%s, nhan_dien_luc=%s '
                        'WHERE id=%s AND nhan_dien_luc IS NULL', [ma, loai, bay_gio, r['id']])
    dem['_so_dong'] = len(rows)
    return dem
