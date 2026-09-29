# -*- coding: utf-8 -*-
"""CHECK BILL V2 — kiểm hóa đơn giấy cuối ngày (GĐ chốt 21/09/2026, nâng cấp từ BANLE_V5 /dashboard/check_bill).

Quy trình: quét mã vạch từng tờ hóa đơn → đối chiếu TRN_RT_BUYSELL trên máy KK → ghi sổ `check_bill` (khj_bl).
Trang tự tính: HĐ còn thiếu (có trên KK mà chưa quét), HĐ đã kiểm nhưng nay KK báo hủy.
Nút "Dò phiếu bán" (21/09/2026): 2 bảng — HĐ còn thiếu kèm PayAmount | CashPay | CardPay | C.Lệch, và CK TRONG NGÀY
lấy từ money_flow IN đã XÁC NHẬN nhận tiền thành công (cùng nguồn "đã nhận" của trang mobile — amounts_by_flow).
Nút "Dò cầm đồ" (29/09/2026): 2 bảng — GIAO DỊCH CẦM ĐỒ trong ngày (khj_cd.cd_loan_logs, dùng chung hàm của CHECK
GOLD) và CK CẦM ĐỒ (cd_payments phần chuyển khoản, đối chiếu báo có bank_notifications loại CD theo mã chứng từ).
Không hỏi máy KK nên vẫn chạy khi KK trục trặc.

Khác bản cũ, CÓ CHỦ Ý:
  · Sổ nằm ở khj_bl (bảng riêng KHBL), không ghi pmv_report @3306.
  · KHÔNG ghi cờ `is_scan` vào bank_notifications — bảng CK đọc money_flow, không đánh dấu nguồn.
  · Ngày làm việc = CreatedDate (cùng quy tắc trang BÁO CÁO); chỉ chứng từ thật IsDel='0' AND Status='C'.
  · Mỗi hóa đơn chỉ kiểm 1 lần (unique trn_id) — quét trùng báo rõ ai đã kiểm lúc nào.
Phân loại giữ đúng bản cũ: Tổng ≤ 0 → ĐỔI BÙ · CK = 0 → TIỀN MẶT · CK = Tổng → CHUYỂN KHOẢN · còn lại → TM + CK.
"""
import datetime as dt
import logging
import re
from decimal import Decimal

from django.db import IntegrityError, connection
from django.http import HttpResponse
from django.utils import timezone

from apps.pmv import money as M
from .models import CheckBill

log = logging.getLogger(__name__)
NHOM = [(CheckBill.TM, 'TIỀN MẶT', 'tm'), (CheckBill.CK, 'CHUYỂN KHOẢN', 'ck'),
        (CheckBill.TMCK, 'TIỀN MẶT + CK', 'tmck'), (CheckBill.DB, 'ĐỔI BÙ', 'db')]
COT = ("b.TrnID, b.BillCode, b.PayAmount, b.CashPay, b.CardPay, ISNULL(b.TienKhachTraThuc,0) AS ThuKhach, b.CreatedDate, b.TrnTime, "
       "e.EmpName, c.CustName FROM TRN_RT_BUYSELL b WITH (NOLOCK) "
       "LEFT JOIN T_EMPLOYEE e WITH (NOLOCK) ON e.EmpID=b.EmpID "
       "LEFT JOIN I_CUSTOMER c WITH (NOLOCK) ON c.CustID=b.CustID ")


class LoiKiem(Exception):
    """Lỗi nghiệp vụ hiện thẳng cho người quét (mã sai, không có trên KK, đã kiểm…)."""


def chuan_ma(raw):
    """'260921123' / '260921000123' / '26-09-21-000123' → '26-09-21-000123'. Mã vạch máy quét = 12 chữ số."""
    so = re.sub(r'\D', '', str(raw or ''))
    if len(so) < 7 or len(so) > 12:
        raise LoiKiem('Mã hóa đơn không hợp lệ — cần dạng yymmdd + số thứ tự (vd 260921000123).')
    try:
        dt.date(2000 + int(so[:2]), int(so[2:4]), int(so[4:6]))
    except ValueError:
        raise LoiKiem('Mã hóa đơn sai ngày (6 số đầu phải là yymmdd).')
    return f'{so[:2]}-{so[2:4]}-{so[4:6]}-{so[6:].zfill(6)}'


def ma_gon(bill_code):
    return str(bill_code or '').replace('-', '').strip()


def phan_loai(tong, ck):
    if tong <= 0:
        return CheckBill.DB
    if ck == 0:
        return CheckBill.TM
    return CheckBill.CK if ck == tong else CheckBill.TMCK


def _gio(row):
    d = row.get('CreatedDate')
    return d if isinstance(d, dt.datetime) else None


def _dong(row):
    tong, ck = M.dec(row.get('PayAmount')), M.dec(row.get('CardPay'))
    tm = M.dec(row.get('CashPay')) if row.get('CashPay') is not None else tong - ck
    cash = M.dec(row.get('CashPay'))
    return dict(pay=tong, cash=cash, card=ck, lech=tong - cash - ck, thu_khach=M.dec(row.get('ThuKhach')),
                trn_id=str(row['TrnID']).strip(), bill_code=str(row['BillCode']).strip(), tong=tong,
                tien_mat=tm, chuyen_khoan=ck, kieu=phan_loai(tong, ck), lap_luc=_gio(row),
                nhan_vien=str(row.get('EmpName') or '').strip(), khach=str(row.get('CustName') or '').strip())


def ck_nhan(ma_list):
    """{mã gọn: (tổng tiền CK VÀO, số lần)} từ khj_bl.bank_notifications — chỉ đọc.
    22/09/2026: khớp theo ma_chung_tu (skill nhan-dien-ma-chung-tu-ck), KHÔNG còn bill_code_raw của V1 — cột cũ chỉ
    bắt dạng "12 số + khoảng trắng + 6 số" nên bỏ sót phần lớn CK QR (ngân hàng báo 27 triệu thay vì ~568 triệu)."""
    ma_list = [m for m in {ma_gon(x) for x in ma_list} if m]
    if not ma_list:
        return {}
    with connection.cursor() as cur:
        cur.execute("SELECT ma_chung_tu, COALESCE(SUM(trans_amount),0), COUNT(*) FROM bank_notifications "
                    "WHERE direction='in' AND ma_chung_tu IN (" + ','.join(['%s'] * len(ma_list)) + ") "
                    "GROUP BY ma_chung_tu", ma_list)
        return {r[0]: (M.dec(r[1]), int(r[2])) for r in cur.fetchall()}


def hd_trong_ngay(c, ngay):
    dau = ngay.isoformat()
    cuoi = (ngay + dt.timedelta(days=1)).isoformat()
    rows = c.query("SELECT " + COT + "WHERE b.IsDel='0' AND b.Status='C' AND b.CreatedDate >= ? AND b.CreatedDate < ? "
                   "ORDER BY b.BillCode", (dau, cuoi))
    return [_dong(r) for r in rows]


def kiem(c, raw, nguoi):
    """Quét 1 mã → ghi sổ. Trả CheckBill vừa ghi; lỗi nghiệp vụ ném LoiKiem."""
    ma = chuan_ma(raw)
    rows = c.query("SELECT " + COT + "WHERE b.BillCode=? AND b.IsDel='0' AND b.Status='C'", (ma,))
    if not rows:
        raise LoiKiem(f'Không tìm thấy hóa đơn {ma} trên máy KK (chưa chốt, đã hủy hoặc sai mã).')
    if len(rows) > 1:
        raise LoiKiem(f'Mã {ma} trùng {len(rows)} hóa đơn trên KK — cần kiểm tra tay.')
    d = _dong(rows[0])
    cu = CheckBill.objects.filter(trn_id=d['trn_id']).first()
    if cu:
        raise LoiKiem(f'Hóa đơn {ma} ĐÃ KIỂM lúc {timezone.localtime(cu.kiem_luc):%H:%M %d/%m} bởi {cu.nguoi_kiem}.')
    nhan, lan = ck_nhan([d['bill_code']]).get(ma_gon(d['bill_code']), (Decimal(0), 0))
    ngay = (d['lap_luc'] or timezone.localtime()).date()
    try:
        so = {k: v for k, v in d.items() if k not in ('pay', 'cash', 'card', 'lech', 'thu_khach')}   # cột chỉ để hiển thị, sổ không lưu
        return CheckBill.objects.create(ngay=ngay, ck_nhan=nhan, ck_so_lan=lan, nguoi_kiem=nguoi[:150], **so)
    except IntegrityError:
        raise LoiKiem(f'Hóa đơn {ma} vừa được người khác kiểm — tải lại trang.')


def bang_ngay(c, ngay, do=False):
    """Toàn bộ số liệu 1 ngày cho trang: KPI · 4 nhóm đã kiểm · còn thiếu · CK chưa gắn HĐ · HĐ nay đã hủy."""
    kk = hd_trong_ngay(c, ngay)
    kk_theo_trn = {r['trn_id']: r for r in kk}
    da = list(CheckBill.objects.filter(ngay=ngay).order_by('-kiem_luc'))
    da_trn = {x.trn_id for x in da}
    nhan = ck_nhan([r['bill_code'] for r in kk] + [x.bill_code for x in da])
    for x in da:
        x.ck_nhan_moi, x.ck_so_lan_moi = nhan.get(ma_gon(x.bill_code), (Decimal(0), 0))
        x.ck_lech = x.chuyen_khoan > 0 and x.ck_nhan_moi != x.chuyen_khoan
        x.da_huy = x.trn_id not in kk_theo_trn
    thieu = [r for r in kk if r['trn_id'] not in da_trn]
    for r in thieu:
        r['ck_nhan'] = nhan.get(ma_gon(r['bill_code']), (Decimal(0), 0))[0]
    nhom = []
    for ma, nhan_nhom, css in NHOM:
        dong = [x for x in da if x.kieu == ma]
        nhom.append(dict(ma=ma, nhan=nhan_nhom, css=css, dong=dong, so=len(dong),
                         tong=sum((x.tong for x in dong), Decimal(0)),
                         tm=sum((x.tien_mat for x in dong), Decimal(0)),
                         ck=sum((x.chuyen_khoan for x in dong), Decimal(0))))
    so_kk = len(kk)
    so_da = sum(1 for x in da if not x.da_huy)
    kpi = dict(so_kk=so_kk, so_da=so_da, so_thieu=len(thieu), phan_tram=round(so_da * 100 / so_kk) if so_kk else 0,
               # 22/09/2026 (GĐ chốt): 3 thẻ tổng = cộng THẲNG cột KK, chỉ phần > 0 (HĐ đổi dư âm không trừ vào)
               tong=sum((r['pay'] for r in kk if r['pay'] > 0), Decimal(0)),
               tm=sum((r['cash'] for r in kk if r['cash'] > 0), Decimal(0)),
               ck=sum((r['card'] for r in kk if r['card'] > 0), Decimal(0)),
               ck_nhan=sum((nhan.get(ma_gon(r['bill_code']), (Decimal(0), 0))[0] for r in kk), Decimal(0)),
               # 22/09/2026 (GĐ chốt): thêm Σ Đổi dư (HĐ tiệm trả khách, PayAmount < 0) · Σ Nhận của khách (TienKhachTraThuc)
               doi_du_so=sum(1 for r in kk if r['pay'] < 0),
               doi_du_tien=sum((-r['pay'] for r in kk if r['pay'] < 0), Decimal(0)),
               thu_khach=sum((r['thu_khach'] for r in kk if r['thu_khach'] > 0), Decimal(0)),
               thieu_tien=sum((r['tong'] for r in thieu), Decimal(0)))
    kpi['ck_lech'] = kpi['ck'] - kpi['ck_nhan']
    ra = dict(kpi=kpi, nhom=nhom, thieu=thieu, huy=[x for x in da if x.da_huy], do=do)
    if do:
        kpi['thieu_lech'] = sum(1 for r in thieu if r['lech'])
        ra['ck'] = ck_trong_ngay(ngay, {x.bill_code for x in da}, kk)
        kpi['ck_tong'] = sum((r['tien_ck'] for r in ra['ck']), Decimal(0))
        kpi['ck_khong_ro'] = sum(1 for r in ra['ck'] if r['loai'] == 'khong_ro')
        kpi['ck_chua_xn'] = sum(1 for r in ra['ck'] if not r['checked'])
    return ra


def _ten_khach(snap):
    """Tên khách trong cd_loans.customer_snapshot (JSON) — cùng các khóa mà popup cầm đồ CHECK GOLD đọc."""
    import json
    if isinstance(snap, (str, bytes)):
        try:
            snap = json.loads(snap or '{}')
        except ValueError:
            snap = {}
    snap = snap if isinstance(snap, dict) else {}
    return snap.get('name') or snap.get('CustName') or snap.get('customer_name') or ''


def ck_cam_do(ngay):
    """CK CẦM ĐỒ trong ngày (GĐ chốt 29/09/2026) — CHỈ ĐỌC khj_cd + bank_notifications.

    Mỗi phần CHUYỂN KHOẢN của một phiên (cd_payments.cardPay, bản cũ channel='BANK') là 1 dòng, đối chiếu với báo
    có/báo nợ ngân hàng có ma_chung_tu = payment_ref (skill tách mã gắn loại 'CD'):
        khop    — ngân hàng đã báo ĐÚNG số tiền, đúng chiều;
        lech    — có báo nhưng số tiền khác;
        cho     — chưa thấy báo nào.
        ghi_tm  — ngân hàng có chuyển khoản cho phiên nhưng KHCD ghi phiên là TIỀN MẶT (cardPay = 0);
        la      — báo có/báo nợ loại CD không dò ra phiên nào (khoản lạ cần dò tay).
    """
    from .check_gold import CD_NGHIEP_VU
    d1, d2 = ngay.isoformat(), (ngay + dt.timedelta(days=1)).isoformat()
    tien_ck = "COALESCE(p.cardPay, CASE WHEN p.channel='BANK' THEN p.amount ELSE 0 END)"
    with connection.cursor() as cur:
        cur.execute(
            "SELECT p.id, l.happened_at, l.operation_id, l.employee_name, n.sku, n.phone, n.customer_snapshot, "
            "p.direction, " + tien_ck + " AS ck, p.reconciliation_state, p.payment_ref "
            "FROM khj_cd.cd_payments p JOIN khj_cd.cd_loan_logs l ON l.id = p.log_id "
            "JOIN khj_cd.cd_loans n ON n.id = l.loan_id "
            "WHERE l.happened_at >= %s AND l.happened_at < %s AND " + tien_ck + " > 0 "
            "ORDER BY l.happened_at DESC, p.id DESC", [d1, d2])
        cot = [c[0] for c in cur.description]
        phien = [dict(zip(cot, r)) for r in cur.fetchall()]
        cur.execute("SELECT id, direction, trans_amount, ma_chung_tu, transaction_time, description FROM bank_notifications "
                    "WHERE loai_chung_tu = 'CD' AND transaction_time >= %s AND transaction_time < %s", [d1, d2])
        cot = [c[0] for c in cur.description]
        bao = [dict(zip(cot, r)) for r in cur.fetchall()]
    ten = {r['id']: _ten_khach(r['customer_snapshot']) for r in phien}
    theo_ma = {}
    for b in bao:
        theo_ma.setdefault(str(b['ma_chung_tu'] or '').strip(), []).append(b)
    ra, da_gan = [], set()
    for r in phien:
        ma = str(r['payment_ref'] or '').strip()
        huong = 'in' if str(r['direction']).upper() == 'IN' else 'out'
        ung = [b for b in theo_ma.get(ma, []) if str(b['direction']).lower() == huong]
        da_gan.update(b['id'] for b in ung)
        ck = M.dec(r['ck'])
        nhan = sum((M.dec(b['trans_amount']) for b in ung), Decimal(0))
        trang_thai = 'cho' if not ung else ('khop' if nhan == ck else 'lech')
        luc = r['happened_at']
        ra.append(dict(gio=luc.strftime('%H:%M') if hasattr(luc, 'strftime') else str(luc)[11:16],
                       ma_ct=ma, sku=r['sku'] or '', nghiep_vu=CD_NGHIEP_VU.get(int(r['operation_id'] or 0), ''),
                       ra_tien=huong == 'out', khach=ten.get(r['id']) or 'Khách lẻ', nv=r['employee_name'] or '',
                       ck=ck, nhan=nhan, gio_bao=str(ung[0]['transaction_time'])[11:16] if ung else '',
                       khcd=str(r['reconciliation_state'] or ''), trang_thai=trang_thai))
    # Báo có/báo nợ CD còn lại: mã chứng từ = yymmdd + id phiên (6 số, vd 260929007147) — dò ngược ra phiên. Thực đo
    # 29/09/2026: 3 phiên CẦM MỚI 17–200 triệu tiệm đã CHUYỂN KHOẢN cho khách nhưng KHCD ghi cả phiên là TIỀN MẶT
    # (cardPay = 0) → đường khớp theo payment_ref ở trên bỏ sót, đúng loại lỗi mà màn dò phải bắt được.
    con = [b for b in bao if b['id'] not in da_gan]
    tien_to = ngay.strftime('%y%m%d')
    log_ids = {int(str(b['ma_chung_tu'])[-6:]) for b in con
               if str(b['ma_chung_tu'] or '').startswith(tien_to) and str(b['ma_chung_tu'])[-6:].isdigit()}
    phien_log = {}
    if log_ids:
        with connection.cursor() as cur:
            cur.execute("SELECT l.id, l.operation_id, l.employee_name, n.sku, n.customer_snapshot "
                        "FROM khj_cd.cd_loan_logs l JOIN khj_cd.cd_loans n ON n.id = l.loan_id "
                        "WHERE l.id IN (" + ','.join(['%s'] * len(log_ids)) + ")", list(log_ids))
            phien_log = {r[0]: r for r in cur.fetchall()}
    la = []
    for b in con:
        ma = str(b['ma_chung_tu'] or '')
        pl = phien_log.get(int(ma[-6:])) if ma.startswith(tien_to) and ma[-6:].isdigit() else None
        la.append(dict(gio=str(b['transaction_time'])[11:16], ma_ct=ma,
                       sku=(pl[3] if pl else '') or '', nghiep_vu=CD_NGHIEP_VU.get(int(pl[1] or 0), '') if pl else '',
                       ra_tien=str(b['direction']).lower() == 'out', khach=_ten_khach(pl[4]) if pl else '',
                       nv=(pl[2] if pl else '') or '', ck=Decimal(0), nhan=M.dec(b['trans_amount']),
                       gio_bao=str(b['transaction_time'])[11:16], khcd='',
                       trang_thai='ghi_tm' if pl else 'la', mo_ta=b['description'] or ''))
    return ra + la


def do_cam_do(ngay):
    """Khối DÒ CẦM ĐỒ: giao dịch trong ngày (hàm dùng chung với popup CHECK GOLD) + CK cầm đồ + số tổng.

    THU KHÁCH (29/09/2026) = tiền mặt khách đưa thực mà quầy đã xác nhận — cột money_flow.cus_cash của dòng chiếu
    phiên KHCD (source_type cd_loan_log, source_id = id phiên), cùng nguồn "Nhận …" của thẻ CHECK GOLD. Chỉ có
    nghĩa với phiên tiền VÀO; phiên tiệm chi ra để trống."""
    from collections import Counter
    from .check_gold import ds_cam_do
    from .models import MoneyFlow
    gd, tong = ds_cam_do(ngay)
    thu = dict(MoneyFlow.objects.filter(source_system='KHCD', source_type='cd_loan_log',
                                        source_id__in=[str(r['id']) for r in gd]).values_list('source_id', 'cus_cash'))
    for r in gd:
        r['thu_khach'] = None if r['ra_tien'] else M.dec(thu.get(str(r['id'])))
    tong['thu_khach'] = sum((r['thu_khach'] for r in gd if r['thu_khach'] and not r['huy']), Decimal(0))
    tong['theo_nv'] = Counter(r['nghiep_vu'] for r in gd if not r['huy']).most_common()
    ck = ck_cam_do(ngay)
    tong.update(ck_so=sum(1 for r in ck if r['trang_thai'] not in ('la', 'ghi_tm')),
                ck_tien=sum((r['ck'] for r in ck), Decimal(0)),
                ck_cho=sum(1 for r in ck if r['trang_thai'] in ('cho', 'lech')),
                ck_la=sum(1 for r in ck if r['trang_thai'] == 'la'),
                ck_ghi_tm=sum(1 for r in ck if r['trang_thai'] == 'ghi_tm'),
                ck_ngan_hang=sum((r['nhan'] for r in ck), Decimal(0)))
    return {'cd_gd': gd, 'cd_tong': tong, 'cd_ck': ck}


def _ck_ngan_hang(ngay):
    """Mọi khoản CK VÀO trong ngày ở khj_bl.bank_notifications (giờ VN) — chỉ đọc."""
    with connection.cursor() as cur:
        cur.execute("SELECT id, transaction_time, trans_amount, bank_name, bank_number, acc_name, description, ma_chung_tu, loai_chung_tu "
                    "FROM bank_notifications WHERE direction='in' AND transaction_time >= %s AND transaction_time < %s "
                    "ORDER BY transaction_time, id", [ngay, ngay + dt.timedelta(days=1)])
        cols = [c[0] for c in cur.description]
        return [dict(zip(cols, r)) for r in cur.fetchall()]


DICH_VU = {'RETAIL': 'Bán hàng', 'GOLD_BUY': 'Thâu vàng', 'DEPOSIT': 'Cọc', 'PAWN': 'Cầm đồ', 'REDEEM': 'Chuộc đồ'}


def ck_trong_ngay(ngay, ma_da_kiem=(), kk=()):
    """CK VÀO TRONG NGÀY (GĐ chốt 22/09/2026) — DS lấy THẲNG bank_notifications direction='in' trong ngày, chỉ MySQL.

    Mỗi khoản 1 dòng, hiển thị đơn giản: giờ · Mã HĐ (= ma_chung_tu do skill tách, kèm loại) · người CK · số tiền · checked.
    checked = khoản đã được XÁC NHẬN vào một phiếu money_flow IN trong ngày (cùng nguồn "đã nhận" của trang mobile
    receipts_by_flow: khóa id bank_notifications; phiếu KHCD so theo mã chứng từ) — còn lại là uncheck."""
    from .models import MoneyFlow
    from .mobile_receipts import receipts_by_flow
    flows = list(MoneyFlow.objects.filter(direction=MoneyFlow.IN, is_void=False, business_date=ngay).order_by('id'))
    rec = receipts_by_flow(flows) if flows else {}
    da_dung, ma_xac_nhan = set(), set()
    for f in flows:
        tien = {k: v for k, v in (rec.get(f.pk) or {}).items() if v > 0}
        if tien:
            da_dung.update(int(k) for k in tien if not str(k).startswith('cd:'))
            if any(str(k).startswith('cd:') for k in tien):
                ma_xac_nhan.add(ma_gon(f.source_bill_code or f.source_id))
    co_ck_kk = {ma_gon(r['bill_code']) for r in kk if r.get('card') and r['card'] > 0}   # 23/09/2026: HĐ đã ghi CardPay trên KK
    ra = []
    for n in _ck_ngan_hang(ngay):
        t = n['transaction_time']      # cột này là CHUỖI 'YYYY-MM-DD HH:MM:SS' trong bank_notifications (có nơi là datetime)
        ma = n.get('ma_chung_tu') or ''
        checked = n['id'] in da_dung or (bool(ma) and (ma in ma_xac_nhan or ma in co_ck_kk))
        ra.append(dict(id=n['id'], gio=t.strftime('%H:%M') if hasattr(t, 'strftime') else str(t or '')[11:16],
                       ma=ma, loai_ma=n.get('loai_chung_tu') or '', tien_ck=M.dec(n['trans_amount']),
                       khach=(n.get('acc_name') or '').strip(), mo_ta=n['description'] or '',
                       tk_nhan=' '.join(x for x in ((n.get('bank_name') or '').strip(), str(n.get('bank_number') or '').strip()) if x),
                       checked=checked, loai='xac_nhan' if checked else ('chua_xac_nhan' if ma else 'khong_ro')))
    return ra


# ─────────────────────────── views ───────────────────────────
# TRANG ĐỨNG RIÊNG (GĐ chốt 21/09/2026): KHÔNG đăng nhập, KHÔNG menu — @login_not_required vượt LoginRequiredMiddleware.
# Không kiểm quyền; "người kiểm" = tài khoản nếu máy đang đăng nhập KHBL, không thì IP máy quét (Caddy gửi X-Forwarded-For).
from django.contrib.auth.decorators import login_not_required
from django.views.decorators.http import require_GET, require_POST, require_http_methods


def _ngay(request, key='d'):
    try:
        return dt.date.fromisoformat(str(request.GET.get(key) or request.POST.get(key) or '')[:10])
    except ValueError:
        return timezone.localdate()


def _nguoi(request):
    user = getattr(request, 'user', None)
    if user is not None and user.is_authenticated:
        return user.username
    ip = (request.META.get('HTTP_X_FORWARDED_FOR') or request.META.get('REMOTE_ADDR') or '').split(',')[0].strip()
    return 'máy ' + (ip or '?')


def _do(request):
    return (request.GET.get('do') or request.POST.get('do')) == '1'


def _cd(request):
    """Cờ DÒ CẦM ĐỒ — cùng cách giữ trạng thái với cờ dò phiếu bán."""
    return (request.GET.get('cd') or request.POST.get('cd')) == '1'


def _render(request, ngay, tin=None, loi=None, full=False):
    from django.shortcuts import render
    from . import services as S
    ctx = {'ngay': ngay, 'hom_nay': timezone.localdate(), 'tin': tin, 'loi_quet': loi, 'do': _do(request),
           'cd': _cd(request)}
    if ctx['do'] and ctx['cd']:                   # hai chế độ dò loại trừ nhau (GĐ chốt 29/09/2026) — ưu tiên cầm đồ
        ctx['do'] = False
    try:
        ctx.update(bang_ngay(S.client('check_bill'), ngay, do=ctx['do']))
    except Exception as exc:                      # KK trục trặc: báo rõ, sổ đã kiểm vẫn còn nguyên
        log.exception('CHECK BILL: không đọc được KK ngày %s', ngay)
        ctx['loi'] = _loi_kk(exc)
    if ctx['cd']:                                 # dò cầm đồ đọc khj_cd + MySQL, KHÔNG phụ thuộc máy KK
        try:
            ctx.update(do_cam_do(ngay))
        except Exception as exc:
            log.exception('CHECK BILL: dò cầm đồ lỗi ngày %s', ngay)
            ctx['cd_loi'] = str(exc)
    tep = 'pos/check_bill.html' if full else 'pos/_check_bill_so_lieu.html'
    return render(request, tep, ctx)


@login_not_required
@require_GET
def trang(request):
    return _render(request, _ngay(request), full=not request.headers.get('HX-Request'))


@login_not_required
@require_POST
def quet(request):
    from . import services as S
    ngay = _ngay(request)
    try:
        cb = kiem(S.client('check_bill'), request.POST.get('ma', ''), _nguoi(request))
        tin = f'✓ {cb.bill_code} · {cb.get_kieu_display()} · {M.money_vn(cb.tong)}'
        if cb.ngay != ngay:
            tin += f' — hóa đơn của ngày {cb.ngay:%d/%m/%Y}, đã ghi vào đúng ngày đó'
        return _render(request, ngay, tin=tin)
    except LoiKiem as exc:
        return _render(request, ngay, loi=str(exc))
    except Exception as exc:
        log.exception('CHECK BILL: quét lỗi')
        return _render(request, ngay, loi=_loi_kk(exc))


@login_not_required
@require_POST
def xoa(request, pk):
    cb = CheckBill.objects.filter(pk=pk).first()
    ngay = cb.ngay if cb else _ngay(request)
    if cb:
        log.info('CHECK BILL: %s bỏ kiểm %s (kiểm bởi %s)', _nguoi(request), cb.bill_code, cb.nguoi_kiem)
        cb.delete()
    return _render(request, ngay, tin=f'Đã bỏ kiểm {cb.bill_code} — quét lại khi cần.' if cb else None)


@login_not_required
@require_POST
def kiem_lai(request):
    ngay = _ngay(request)
    n, _ = CheckBill.objects.filter(ngay=ngay).delete()
    log.warning('CHECK BILL: %s KIỂM LẠI ngày %s — xóa %s dòng', _nguoi(request), ngay, n)
    return _render(request, ngay, tin=f'Đã xóa {n} hóa đơn đã kiểm của ngày {ngay:%d/%m/%Y} — bắt đầu quét lại.')


def _loi_kk(exc):
    from .customer import error_message
    return error_message(exc)


# ─────── SỬA MÃ CHỨNG TỪ CỦA 1 KHOẢN CK + GHI TIỀN LÊN KK (GĐ chốt 23/09/2026) ───────
def ck_mot_dong(pk):
    """1 dòng bank_notifications tiền VÀO — cho popup sửa mã."""
    with connection.cursor() as cur:
        cur.execute("SELECT id, transaction_time, trans_amount, bank_name, bank_number, description, ma_chung_tu "
                    "FROM bank_notifications WHERE id=%s AND direction='in'", [pk])
        row = cur.fetchone()
    return dict(zip(('id', 'gio', 'tien', 'bank_name', 'bank_number', 'mo_ta', 'ma'), row)) if row else None


def sua_ma_va_ghi_kk(c, pk, ma_nhap, nguoi):
    """Ghi mã chứng từ cho khoản CK rồi phân bổ CardPay / CashPay của ĐÚNG hóa đơn đó trên KK.

    Mã nhận mọi kiểu gõ: '26-09-23-000096' / '260923000096'. CardPay ghi = TỔNG mọi khoản CK VÀO mang cùng mã
    (một HĐ có thể nhận nhiều lần chuyển), CashPay = PayAmount − CardPay. Ghi qua gateway pmv_in_allocate —
    cùng đường ghi với màn Mobile: có khóa ghi, kiểm trigger, đọc lại đối chiếu, audit."""
    from apps.pmv import gateway
    ma = ma_gon(ma_nhap)
    if len(ma) != 12 or not ma.isdigit():
        raise LoiKiem('Mã chứng từ phải là 12 chữ số, ví dụ 26-09-23-000096.')
    dong = ck_mot_dong(pk)
    if not dong:
        raise LoiKiem('Không tìm thấy khoản chuyển khoản này.')
    bill_code = f'{ma[:2]}-{ma[2:4]}-{ma[4:6]}-{ma[6:]}'
    rows = c.query("SELECT TrnID, PayAmount FROM TRN_RT_BUYSELL WITH (NOLOCK) WHERE BillCode=? AND IsDel='0' AND Status='C'", (bill_code,))
    if len(rows) != 1:
        raise LoiKiem(f'Không tìm thấy duy nhất hóa đơn {bill_code} đã chốt trên KK.')
    trn_id, tong = str(rows[0]['TrnID']).strip(), M.dec(rows[0]['PayAmount'])
    with connection.cursor() as cur:
        cur.execute("UPDATE bank_notifications SET ma_chung_tu=%s, loai_chung_tu='HD', nhan_dien_luc=%s WHERE id=%s",
                    [ma, timezone.localtime().replace(tzinfo=None, microsecond=0), pk])
        cur.execute("SELECT COALESCE(SUM(trans_amount),0) FROM bank_notifications WHERE direction='in' AND ma_chung_tu=%s", [ma])
        ck = M.dec(cur.fetchone()[0])
    if ck <= 0 or ck > tong:
        raise LoiKiem(f'Tiền CK {M.money_vn(ck)} không hợp lệ với hóa đơn {bill_code} ({M.money_vn(tong)}) — kiểm tra lại mã.')
    snap = gateway.pmv_in_snapshot('RETAIL', bill_code, trn_id)
    gateway.pmv_in_allocate('RETAIL', snap, ck)
    log.info('CHECK BILL: %s gán CK #%s → %s, ghi KK CardPay=%s CashPay=%s', nguoi, pk, bill_code, ck, tong - ck)
    return f'✓ {bill_code} · CardPay {M.money_vn(ck)} · CashPay {M.money_vn(tong - ck)}'


@login_not_required
@require_http_methods(['GET', 'POST'])
def ck_sua(request, pk):
    from django.shortcuts import render
    from . import services as S
    ngay = _ngay(request)
    if request.method == 'GET':
        dong = ck_mot_dong(pk)
        if not dong:
            return HttpResponse('')
        return render(request, 'pos/_check_bill_ck_modal.html', {'ck': dong, 'ngay': ngay})
    try:
        tin = sua_ma_va_ghi_kk(S.client('check_bill'), pk, request.POST.get('ma', ''), _nguoi(request))
    except LoiKiem as exc:
        dong = ck_mot_dong(pk)
        return render(request, 'pos/_check_bill_ck_modal.html', {'ck': dong, 'ngay': ngay, 'loi': str(exc),
                                                                 'ma_nhap': request.POST.get('ma', '')})
    except Exception as exc:
        log.exception('CHECK BILL: ghi KK từ popup sửa mã lỗi')
        return render(request, 'pos/_check_bill_ck_modal.html', {'ck': ck_mot_dong(pk), 'ngay': ngay, 'loi': _loi_kk(exc),
                                                                 'ma_nhap': request.POST.get('ma', '')})
    ra = _render(request, ngay, tin=tin)
    ra.content = b'<div id="cb-modal" hx-swap-oob="true"></div>' + ra.content     # đóng popup
    return ra
