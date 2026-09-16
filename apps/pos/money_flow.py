"""Sổ dòng tiền GĐ1: chỉ đọc nguồn, ghi bản chiếu MySQL ``money_flow``.

Không hàm nào trong module này gọi proc ghi PMV/KHCD. Việc đồng bộ có tính
idempotent; chạy lại chỉ cập nhật dòng cùng khóa nguồn.
"""
import datetime as dt
from decimal import Decimal, InvalidOperation

from django.db import transaction
from django.db.models import Q
from django.utils import timezone

from .models import GoldBill, MoneyFlow, ThauNhom, ThauPaymentLink


SERVICES = {
    "RETAIL": "Bán / đổi",
    "GOLD_BUY": "Thâu vàng",
    "DEPOSIT": "Đặt hàng / cọc",
    "PAWN": "Cầm đồ",
    "REDEEM": "Chuộc đồ",
}


def dec(value):
    try:
        return Decimal(str(value or 0))
    except (InvalidOperation, TypeError, ValueError):
        return Decimal(0)


def payment_state(source_status, is_void, bank, bank_matched=Decimal(0), explicit=""):
    if is_void:
        return MoneyFlow.VOID
    if explicit in {MoneyFlow.WAITING, MoneyFlow.PARTIAL, MoneyFlow.CONFIRMED, MoneyFlow.REVIEW}:
        return explicit
    if source_status not in {"C", "P", "COMPLETED"}:
        return MoneyFlow.WAITING
    if bank > 0:
        if bank_matched >= bank:
            return MoneyFlow.CONFIRMED
        if bank_matched > 0:
            return MoneyFlow.PARTIAL
        return MoneyFlow.WAITING
    # GĐ1 chưa có phiếu xác nhận tiền mặt riêng; chỉ dám nói nguồn đã ghi.
    return MoneyFlow.RECORDED


def _upsert(*, service, source_type, source_id, direction, amount, cash, bank,
            business_date, source_status, is_void=False, bill_code="", trn_ids=None,
            group_id="", customer_id="", customer_name="", snapshot=None,
            payment_status=None):
    amount, cash, bank = abs(dec(amount)), abs(dec(cash)), abs(dec(bank))
    defaults = {
        "direction": direction, "source_group_id": str(group_id or ""),
        "source_bill_code": bill_code or "", "source_trn_ids": list(trn_ids or []),
        "customer_id": customer_id or "", "customer_name": (customer_name or "")[:200],
        "expected_amount": amount, "cash_amount": cash, "bank_amount": bank,
        "business_date": business_date or timezone.localdate(), "source_status": source_status or "",
        "payment_status": payment_status or payment_state(source_status, is_void, bank),
        "is_void": bool(is_void), "source_snapshot": snapshot or {},
    }
    return MoneyFlow.objects.update_or_create(
        source_system="KHBL", service=service, source_type=source_type,
        source_id=str(source_id), flow_role="settlement", defaults=defaults)


def sync_gold_bills(d1=None, d2=None):
    qs = GoldBill.all_objects.all().order_by("id")
    if d1:
        qs = qs.filter(trn_date__gte=d1)
    if d2:
        qs = qs.filter(trn_date__lte=d2)
    # Các TBG có nhóm do sync_thau_groups sở hữu; chỉ giữ TBG cũ không có nhóm.
    grouped = set(t for ids in ThauNhom.objects.values_list("trn_ids", flat=True) for t in (ids or []))
    count = 0
    for bill in qs.iterator(chunk_size=300):
        if bill.bill_kind == "deposit":
            plan = bill.payment_plan or {}
            cash, bank = dec(plan.get("cash")), dec(plan.get("bank"))
            amount = cash + bank
            if amount <= 0:
                MoneyFlow.objects.filter(source_system="KHBL", service="DEPOSIT",
                    source_type="gold_bill_deposit", source_id=bill.trn_id).delete()
                continue
            explicit = {"matched": MoneyFlow.CONFIRMED, "partial": MoneyFlow.PARTIAL,
                        "review": MoneyFlow.REVIEW}.get(plan.get("bank_status"), "")
            _upsert(service="DEPOSIT", source_type="gold_bill_deposit", source_id=bill.trn_id,
                    direction=MoneyFlow.IN, amount=amount, cash=cash, bank=bank,
                    business_date=bill.trn_date, source_status=bill.status, is_void=bill.is_del,
                    bill_code=bill.bill_code, trn_ids=[bill.trn_id], customer_id=bill.cust_id,
                    customer_name=bill.cust_name, payment_status=payment_state(
                        bill.status, bill.is_del, abs(bank), explicit=explicit),
                    snapshot={"bill_kind": bill.bill_kind, "bank_status": plan.get("bank_status", "")})
        elif bill.trn_id.startswith("TBG"):
            if bill.trn_id in grouped:
                continue
            _upsert(service="GOLD_BUY", source_type="gold_bill_ungrouped", source_id=bill.trn_id,
                    direction=MoneyFlow.OUT, amount=bill.tong, cash=bill.tien_mat, bank=bill.tien_ck,
                    business_date=bill.trn_date, source_status=bill.status, is_void=bill.is_del,
                    bill_code=bill.bill_code, trn_ids=[bill.trn_id], customer_id=bill.cust_id,
                    customer_name=bill.cust_name, snapshot={"nguon": bill.nguon})
        else:
            if dec(bill.tong) == 0:
                MoneyFlow.objects.filter(source_system="KHBL", service="RETAIL",
                    source_type="gold_bill_retail", source_id=bill.trn_id).delete()
                continue
            direction = MoneyFlow.OUT if dec(bill.tong) < 0 else MoneyFlow.IN
            _upsert(service="RETAIL", source_type="gold_bill_retail", source_id=bill.trn_id,
                    direction=direction, amount=bill.tong, cash=bill.tien_mat,
                    bank=dec(bill.tien_ck) + dec(bill.tien_the), business_date=bill.trn_date,
                    source_status=bill.status, is_void=bill.is_del, bill_code=bill.bill_code,
                    trn_ids=[bill.trn_id], customer_id=bill.cust_id, customer_name=bill.cust_name,
                    snapshot={"nguon": bill.nguon, "pay_method": bill.pay_method})
        count += 1
    return count


def sync_thau_groups(d1=None, d2=None):
    qs = ThauNhom.objects.all().order_by("id")
    # MySQL của tiệm từng trả rỗng với created_at__date; lọc bằng nửa khoảng.
    if d1:
        start = dt.datetime.combine(d1, dt.time.min)
        if timezone.is_aware(timezone.now()):
            start = timezone.make_aware(start)
        qs = qs.filter(created_at__gte=start)
    if d2:
        end = dt.datetime.combine(d2 + dt.timedelta(days=1), dt.time.min)
        if timezone.is_aware(timezone.now()):
            end = timezone.make_aware(end)
        qs = qs.filter(created_at__lt=end)
    latest_for = {}
    # Thanh toán lại sau SỬa tạo nhóm mới; nhóm cũ là phiên bản bị thay thế.
    for pk, trn_ids in ThauNhom.objects.order_by("id").values_list("pk", "trn_ids"):
        for trn_id in trn_ids or []:
            latest_for[trn_id] = pk
    count = 0
    for group in qs.iterator(chunk_size=200):
        ids = list(group.trn_ids or [])
        bills = list(GoldBill.objects.filter(trn_id__in=ids).order_by("id"))
        active = [b for b in bills if not b.is_del]
        superseded = bool(ids) and not any(latest_for.get(trn_id) == group.pk for trn_id in ids)
        # gold_bill thâu có thể bị job retail đánh is_del do nó không đọc TRN_RT_BUYGOLD;
        # không dùng cờ đó để kết luận hủy. Nhóm mới nhất là bản chốt quan sát được.
        source_status = "VOID" if superseded else "C"
        is_void = superseded
        bank = abs(dec(group.tien_ck))
        # Lọc giao nhau trong Python: tương thích cả MySQL thật lẫn SQLite test;
        # bảng link nhỏ và chỉ đọc các dòng còn hiệu lực.
        links = [link for link in ThauPaymentLink.objects.filter(active_notification_id__isnull=False)
                 if set(ids) & set(link.trn_ids or [])]
        matched = sum((dec(link.amount) for link in links), Decimal(0))
        day = next((b.trn_date for b in active if b.trn_date), None) or timezone.localtime(group.created_at).date()
        bill_codes = list(group.bill_codes or [])
        bill_label = (bill_codes[0] + (f" (+{len(bill_codes)-1})" if len(bill_codes) > 1 else "")) if bill_codes else ""
        _upsert(service="GOLD_BUY", source_type="thau_nhom", source_id=group.pk,
                direction=MoneyFlow.OUT, amount=abs(dec(group.tien_mat)) + bank,
                cash=group.tien_mat, bank=bank, business_date=day, source_status=source_status,
                is_void=is_void, bill_code=bill_label, trn_ids=ids, group_id=group.pk,
                customer_id=group.cust_id, customer_name=group.cust_name,
                payment_status=payment_state(source_status, is_void, bank, matched),
                snapshot={"matched_bank": str(matched), "pay_method": group.pay_method,
                          "gold_bill_active": len(active), "status_basis": "latest_group"})
        count += 1
    return count


@transaction.atomic
def sync(d1=None, d2=None):
    """Cập nhật sổ phụ từ nguồn có sẵn; tuyệt đối không ghi nguồn."""
    return {"gold_bill": sync_gold_bills(d1, d2), "thau_nhom": sync_thau_groups(d1, d2)}
