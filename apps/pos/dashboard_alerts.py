"""Cảnh báo PMV chỉ đọc; cùng điều kiện cho số lượng và popup chi tiết.

08/09/2026: đối chiếu schema và proc duyệt nhập/xuất/chuyển quầy trên KK.
Không dùng PCheck (kiểm kê) để suy ra trạng thái duyệt sản phẩm.
"""
import logging

from django.http import Http404
from django.shortcuts import render
from django.views.decorators.http import require_GET

from apps.pmv.client import PmvClient

logger = logging.getLogger(__name__)
WAIT_BILL = "p.Status = 'W' AND p.IsDel = '0' AND p.CreatedDate < DATEADD(minute,-30,GETDATE())"
WAIT_STOCK = "p.Status = 'W'"
# Tên bảng/cột luôn từ cấu hình cố định, không lấy từ tham số request.
KINDS = {
    'product': ('TRN_PRODUCT_IN', 'Sản phẩm chưa duyệt', WAIT_STOCK, 'TrnDate', 'cam', 'Phiếu nhập chờ duyệt vào kho'),
    'ban': ('TRN_RT_BUYSELL', 'Bán hàng chờ thanh toán', WAIT_BILL, 'CreatedDate', 'do', 'Đơn nháp quá 30 phút'),
    'thau': ('TRN_RT_BUYGOLD', 'Phiếu thâu vào chưa hoàn tất', WAIT_BILL, 'CreatedDate', 'cam', 'Phiếu thu mua quá 30 phút'),
    'doi': ('TRN_RT_CHANGE', 'Phiếu đổi vàng chưa hoàn tất', WAIT_BILL, 'CreatedDate', 'cam', 'Phiếu đổi vàng quá 30 phút'),
    'xuat': ('TRN_PRODUCT_OUT', 'Phiếu xuất hàng chưa duyệt', WAIT_STOCK, 'TrnDate', 'cam', 'Phiếu xuất đang chờ duyệt'),
    'chuyen': ('TRN_PRODUCT_SECTION', 'Phiếu chuyển quầy chưa hoàn tất', WAIT_STOCK, 'TrnDate', 'cam', 'Phiếu chuyển quầy đang chờ xử lý'),
}


def summaries(c):
    results = []
    for key, (table, title, where, date_col, color, detail) in KINDS.items():
        row = c.query(f"SELECT COUNT(*) AS n, MIN(p.{date_col}) AS luc FROM {table} p WITH (NOLOCK) WHERE {where}")[0]
        if row['n']:
            results.append(dict(key=key, title=title, count=row['n'], luc=row['luc'], kind=color, detail=detail))
    return results


def details(c, key):
    table, _, where, _, _, _ = KINDS[key]
    if key == 'product':
        rows = c.query(
            "SELECT p.TrnID, p.TrnDate, p.TrnTime, p.ProductCode, p.ProductDesc, p.GoldCode, "
            "p.Quantity, p.TotalWeight, p.DiamondWeight, p.PriceUnit, p.UserID, p.Description "
            f"FROM {table} p WITH (NOLOCK) WHERE {where} ORDER BY p.TrnDate, p.TrnTime, p.TrnID")
        codes = {}
        for item in c.query(
            "SELECT d.TrnID, d.ProductCode FROM TRN_PRODUCT_IN_DT d WITH (NOLOCK) "
            "INNER JOIN TRN_PRODUCT_IN p WITH (NOLOCK) ON p.TrnID = d.TrnID "
            f"WHERE {where} ORDER BY d.TrnID, d.ProductCode"):
            if item['ProductCode']:
                codes.setdefault(item['TrnID'], []).append(item['ProductCode'])
        for item in rows:
            item['ma_hang'] = ', '.join(codes.get(item['TrnID'], [])) or item['ProductCode']
        return rows
    if key in ('ban', 'thau', 'doi'):
        amount = 'TotalAmount' if key == 'thau' else 'PayAmount'
        return c.query(
            f"SELECT p.TrnID, p.BillCode, p.TrnDate, p.TrnTime, p.CreatedDate, p.{amount} AS Amount, "
            "k.CustName, k.Phone FROM " + table + " p WITH (NOLOCK) "
            "LEFT JOIN I_CUSTOMER k WITH (NOLOCK) ON k.CustID = p.CustID "
            f"WHERE {where} ORDER BY p.CreatedDate, p.TrnID")
    return c.query(f"SELECT p.TrnID, p.BillCode, p.TrnDate, p.TrnTime, p.Status "
                   f"FROM {table} p WITH (NOLOCK) WHERE {where} ORDER BY p.TrnDate, p.TrnTime, p.TrnID")


@require_GET
def detail_view(request, key):
    if key not in KINDS:
        raise Http404
    context = dict(key=key, title=KINDS[key][1], condition=KINDS[key][5], bills=key in ('ban', 'thau', 'doi'))
    try:
        context['rows'] = details(PmvClient('kk', tag='dashboard_alert_detail'), key)
    except Exception:
        logger.exception('Không tải được cảnh báo PMV %s', key)
        context['error'] = 'Không đọc được dữ liệu từ máy KK. Vui lòng đóng và thử lại.'
    return render(request, 'pos/_dashboard_alert_detail.html', context)
