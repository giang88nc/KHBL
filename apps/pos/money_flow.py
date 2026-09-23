"""Sổ dòng tiền GĐ1: chỉ đọc nguồn, ghi bản chiếu MySQL ``money_flow``.

Không hàm nào trong module này gọi proc ghi PMV/KHCD. Việc đồng bộ có tính
idempotent; chạy lại chỉ cập nhật dòng cùng khóa nguồn.
"""
import datetime as dt
from decimal import Decimal, InvalidOperation

from django.db import connection
from django.utils import timezone

from .models import GoldBill, MoneyFlow, ThauNhom, ThauPaymentLink


SERVICES = {
    "RETAIL": "Bán / đổi",
    "GOLD_BUY": "Thâu vàng",
    "DEPOSIT": "Đặt hàng / cọc",
    "PAWN": "Cầm đồ",
    "REDEEM": "Chuộc đồ",
}

KHCD_OPERATIONS = {
    0: "Hủy phiên", 1: "Cầm mới", 2: "Cầm thêm", 3: "Trả bớt",
    4: "Gia hạn", 5: "Chuộc đồ", 6: "Thanh lý", 7: "Báo mất",
    8: "Mở khóa báo mất",
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
            payment_status=None, source_system="KHBL"):
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
    # A later projection refresh must not replace verified exact-VND amounts
    # with planned/rounded cache allocations.
    if direction==MoneyFlow.IN:
        from django.db.models import Sum
        from .models import MoneyFlowBankReceipt
        verified=MoneyFlowBankReceipt.objects.filter(flow__source_system=source_system,
            flow__source_type=source_type,flow__source_id=str(source_id),status='applied').aggregate(n=Sum('amount'))['n']
        if verified is not None:
            if verified<=amount:
                defaults.update(bank_amount=verified,cash_amount=amount-verified,
                    payment_status=MoneyFlow.VOID if is_void else MoneyFlow.CONFIRMED if verified==amount else MoneyFlow.PARTIAL)
            else:defaults['payment_status']=MoneyFlow.REVIEW
    # Metadata is populated by the source projector, not the legacy bill cache.
    existing = MoneyFlow.objects.filter(source_system=source_system,service=service,
        source_type=source_type,source_id=str(source_id),flow_role='settlement').first()
    if existing:
        for key in ('mobile','tender'):
            if key in (existing.source_snapshot or {}) and key not in defaults['source_snapshot']:
                defaults['source_snapshot'][key] = existing.source_snapshot[key]
    if existing and 'mobile' in (existing.source_snapshot or {}):
        defaults['customer_id'], defaults['customer_name'] = existing.customer_id, existing.customer_name
    return MoneyFlow.objects.update_or_create(
        source_system=source_system, service=service, source_type=source_type,
        source_id=str(source_id), flow_role="settlement", defaults=defaults)


def sync_gold_bills(d1=None, d2=None, trn_ids=None):
    qs = GoldBill.all_objects.all().order_by("id")
    if d1:
        qs = qs.filter(trn_date__gte=d1)
    if d2:
        qs = qs.filter(trn_date__lte=d2)
    if trn_ids:     # 19/09/2026: chiếu lại ĐÚNG hóa đơn vừa chốt (TẠO QR màn Bán hàng) — job 2 phút không truyền, y cũ
        qs = qs.filter(trn_id__in=list(trn_ids))
    # Các TBG có nhóm do sync_thau_groups sở hữu; chỉ giữ TBG cũ không có nhóm.
    # Quét thau_nhom CHỈ khi gặp TBG (21/09/2026): ghi ngay 1 HĐ bán/cọc không phải đọc cả bảng.
    grouped = None
    count = 0
    for bill in qs.iterator(chunk_size=300):
        if bill.bill_kind == "deposit":
            plan = bill.payment_plan or {}
            cash, bank = dec(plan.get("cash")), dec(plan.get("bank"))
            amount = cash + bank
            if amount <= 0:
                MoneyFlow.objects.filter(source_system="KHBL", service="DEPOSIT",
                    source_type="gold_bill_deposit", source_id=bill.trn_id).update(is_void=True,payment_status=MoneyFlow.VOID)
                continue
            explicit = {"matched": MoneyFlow.CONFIRMED, "partial": MoneyFlow.PARTIAL,
                        "review": MoneyFlow.REVIEW}.get(plan.get("bank_status"), "")
            _upsert(service="DEPOSIT", source_type="gold_bill_deposit", source_id=bill.trn_id,
                    direction=MoneyFlow.IN, amount=amount, cash=cash, bank=bank,
                    business_date=bill.trn_date, source_status=bill.status, is_void=bill.is_del or bill.fulfilment=='cancelled' or bill.status=='D',
                    bill_code=bill.bill_code, trn_ids=[bill.trn_id], customer_id=bill.cust_id,
                    customer_name=bill.cust_name, payment_status=payment_state(
                        bill.status, bill.is_del, abs(bank), explicit=explicit),
                    snapshot={"bill_kind": bill.bill_kind, "bank_status": plan.get("bank_status", "")})
        elif bill.trn_id.startswith("TBG"):
            if grouped is None:
                grouped = set(t for ids in ThauNhom.objects.values_list("trn_ids", flat=True) for t in (ids or []))
            if bill.trn_id in grouped:
                continue
            _upsert(service="GOLD_BUY", source_type="gold_bill_ungrouped", source_id=bill.trn_id,
                    direction=MoneyFlow.OUT, amount=bill.tong, cash=bill.tien_mat, bank=bill.tien_ck,
                    business_date=bill.trn_date, source_status=bill.status, is_void=bill.is_del,
                    bill_code=bill.bill_code, trn_ids=[bill.trn_id], customer_id=bill.cust_id,
                    customer_name=bill.cust_name, snapshot={"nguon": bill.nguon})
        else:
            if dec(bill.tong) == 0 and bill.is_del:
                MoneyFlow.objects.filter(source_system="KHBL", service="RETAIL",
                    source_type="gold_bill_retail", source_id=bill.trn_id).update(is_void=True,payment_status=MoneyFlow.VOID)
                continue
            if dec(bill.tong) == 0:     # ĐỔI NGANG (GĐ chốt 22/09/2026): không thu tiền nhưng hóa đơn vẫn SỐNG — KHÔNG đánh VOID
                _upsert(service="RETAIL", source_type="gold_bill_retail", source_id=bill.trn_id,
                        direction=MoneyFlow.IN, amount=0, cash=0, bank=0, business_date=bill.trn_date,
                        source_status=bill.status, is_void=False, bill_code=bill.bill_code,
                        trn_ids=[bill.trn_id], customer_id=bill.cust_id, customer_name=bill.cust_name,
                        payment_status=MoneyFlow.CONFIRMED if bill.status == "C" else MoneyFlow.WAITING,
                        snapshot={"nguon": bill.nguon, "pay_method": bill.pay_method, "doi_ngang": True})
                count += 1
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
    # Chỉ nhóm PHIẾU THÂU. Nhóm bán-đổi dư (nghiep_vu='doi', 19/09/2026) là chính hóa đơn bán đã có dòng RETAIL
    # từ gold_bill — chiếu thêm ở đây thành GOLD_BUY sẽ đếm tiền trả khách hai lần.
    qs = ThauNhom.objects.filter(nghiep_vu=ThauNhom.THAU).order_by("id")
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
                          "gold_bill_active": len(active), "status_basis": "latest_group",
                          "bank_code": group.ck_bank or "", "bank_account": group.ck_stk or "",
                          "bank_owner": group.ck_ten or "", "transfer_content": group.ck_nd or ""})
        count += 1
    return count


def _khcd_rows(d1=None, d2=None):
    """Chỉ SELECT schema KHCD; mỗi dòng là một payment của một phiên."""
    where, params = [], []
    if d1:
        where.append("l.happened_at >= %s")
        params.append(d1.isoformat())
    if d2:
        where.append("l.happened_at < %s")
        params.append((d2 + dt.timedelta(days=1)).isoformat())
    clause = " WHERE " + " AND ".join(where) if where else ""
    sql = """
        SELECT l.id log_id,l.loan_id,l.operation_id,l.happened_at,l.request_key,l.note,l.employee_id,l.actor_legacy,
               l.principal_change,l.interest,l.extra_amount,l.discount_amount,l.reverses_log_id,
               n.sku,n.cust_id,n.phone,n.loan_state,n.version,n.customer_snapshot,
               p.id payment_id,p.channel,p.direction,p.amount,p.cashPay,p.cardPay,p.payment_ref,p.reconciliation_state,p.bank_snapshot
        FROM khj_cd.cd_loan_logs l
        JOIN khj_cd.cd_loans n ON n.id=l.loan_id
        JOIN khj_cd.cd_payments p ON p.log_id=l.id
    """ + clause + " ORDER BY l.id,p.id"
    with connection.cursor() as cur:
        cur.execute(sql, params)
        names = [c[0] for c in cur.description]
        return [dict(zip(names, row)) for row in cur.fetchall()]


def _json(value):
    import json
    if isinstance(value, dict):
        return value
    try:
        parsed = json.loads(value or "{}")
        return parsed if isinstance(parsed, dict) else {}
    except (TypeError, ValueError):
        return {}


def sync_khcd(d1=None, d2=None):
    """Chiếu phiên tiền KHCD vào money_flow; không ghi bất kỳ bảng KHCD nào."""
    raw = _khcd_rows(d1, d2)
    from . import services
    try:
        staff_names = {str(e['EmpID']).strip(): e['EmpName'] for e in services.nhan_vien_ban()}
    except Exception:
        staff_names = {}
    sessions = {}
    for row in raw:
        sessions.setdefault(row["log_id"], {"head": row, "payments": []})["payments"].append(row)
    seen = set()
    for log_id, data in sessions.items():
        head, payments = data["head"], data["payments"]
        directions = {p["direction"] for p in payments}
        direction = next(iter(directions)) if len(directions) == 1 else MoneyFlow.IN
        cash = sum((dec(p.get('cashPay')) if p.get('cashPay') is not None else
                    dec(p['amount']) if p['channel'] == 'CASH' else Decimal(0) for p in payments), Decimal(0))
        bank = sum((dec(p.get('cardPay')) if p.get('cardPay') is not None else
                    dec(p['amount']) if p['channel'] == 'BANK' else Decimal(0) for p in payments), Decimal(0))
        total = sum((dec(p['amount']) for p in payments), Decimal(0))
        states = {str(p["reconciliation_state"] or "").upper() for p in payments}
        operation = int(head["operation_id"])
        is_void = operation == 0 or bool(states) and states <= {"REVERSED", "VOID"}
        if is_void:
            payment_status = MoneyFlow.VOID
        elif len(directions) != 1 or cash + bank != total:
            payment_status = MoneyFlow.REVIEW
        elif bank and states & {"MATCHED", "CONFIRMED", "RECONCILED"}:
            payment_status = MoneyFlow.CONFIRMED
        elif bank and 'PARTIAL' in states:
            payment_status = MoneyFlow.PARTIAL
        elif bank:
            payment_status = MoneyFlow.WAITING
        else:
            payment_status = MoneyFlow.RECORDED
        customer = _json(head["customer_snapshot"])
        bank_snapshots = [_json(p.get("bank_snapshot")) for p in payments]
        bank_snapshot = next((item for item in bank_snapshots if item), {})
        customer_name = (customer.get("name") or customer.get("CustName") or
                         customer.get("customer_name") or "")
        service = "PAWN" if direction == MoneyFlow.OUT else "REDEEM"
        from .payment_reference import pawn_reference
        ref=pawn_reference(head['happened_at'],head['loan_id'],log_id)
        _upsert(source_system="KHCD", service=service, source_type="cd_loan_log",
                source_id=log_id, group_id=head["loan_id"], direction=direction,
                amount=total, cash=cash, bank=bank,
                business_date=head["happened_at"].date(), source_status=KHCD_OPERATIONS.get(operation, str(operation)),
                is_void=is_void, bill_code=ref if direction==MoneyFlow.IN else head["sku"], trn_ids=[], customer_id=head["cust_id"],
                customer_name=customer_name, payment_status=payment_status,
                snapshot={"mobile": {"happened_at":head['happened_at'].isoformat(),
                          "employee_pmv":str(head.get('employee_id') or '').strip(),
                          "employee_name":staff_names.get(str(head.get('employee_id') or '').strip(),''),
                          "phone":head['phone'],"basis":"cd_loan_logs"},
                          "operation_id": operation, "operation": KHCD_OPERATIONS.get(operation, str(operation)),
                          "loan_state": head["loan_state"], "loan_version": head["version"],
                          "payment_ids": [p["payment_id"] for p in payments],
                          "receipt_sku": head['sku'],
                          "payment_ref": next((p.get('payment_ref') for p in payments if p.get('payment_ref')), ''),
                          "reconciliation_states": sorted(states), "phone": head["phone"],
                          "bank_snapshot": bank_snapshot})
        seen.add(str(log_id))
    # Phiên có thể bị xóa trong cửa sổ hoàn tác 5 phút. Giữ dấu vết
    # money_flow nhưng loại khỏi tổng, không để dòng mồ côi tiếp tục có hiệu lực.
    stale = MoneyFlow.objects.filter(source_system="KHCD", source_type="cd_loan_log")
    if d1:
        stale = stale.filter(business_date__gte=d1)
    if d2:
        stale = stale.filter(business_date__lte=d2)
    stale = stale.exclude(source_id__in=seen)
    for flow in stale:
        flow.is_void = True
        flow.source_status = "Nguồn đã hoàn tác"
        flow.payment_status = MoneyFlow.VOID
        flow.source_snapshot = {**(flow.source_snapshot or {}), "missing_from_source": True}
        flow.save(update_fields=["is_void", "source_status", "payment_status", "source_snapshot", "synced_at"])
    return len(sessions)


_KHOA_SYNC = __import__("threading").Lock()


def sync(d1=None, d2=None, kk=False):
    """Cập nhật sổ phụ từ nguồn có sẵn; tuyệt đối không ghi nguồn.

    kk=False (job nhanh 20 giây): CHỈ MySQL. kk=True (job 5 phút): thêm lượt đọc KK bổ sung SĐT + tiền thối lại.
    Khóa trong tiến trình: job nhanh và job chậm cùng scheduler không chạy chồng lên nhau."""
    with _KHOA_SYNC:
        return _sync(d1, d2, kk)


def project_now(trn_id):
    """Chiếu NGAY 1 hóa đơn KHBL (bán / cọc) vào money_flow sau khi gold_bill vừa ghi — chỉ MySQL.
    Lỗi không được làm hỏng thao tác bán hàng: job 20 giây là lưới an toàn."""
    import logging
    try:
        sync_gold_bills(trn_ids=[trn_id])
        from .mobile_projection import refresh
        refresh(trn_ids=[trn_id])
    except Exception:
        logging.getLogger(__name__).exception("money_flow: chưa chiếu ngay được %s (job 20 giây sẽ bù)", trn_id)


def _sync(d1=None, d2=None, kk=False):
    result = {"gold_bill": sync_gold_bills(d1, d2), "thau_nhom": sync_thau_groups(d1, d2)}
    try:
        result["khcd"] = sync_khcd(d1, d2)
    except Exception as exc:
        # Trang quản lý vẫn phải xem được bản sync cuối khi KHCD tạm dừng.
        import logging
        logging.getLogger(__name__).exception("Không đồng bộ được money_flow từ KHCD")
        result["khcd_error"] = str(exc)[:240]
    from .mobile_projection import refresh, enrich_from_kk
    result['mobile_metadata'] = refresh(d1, d2)
    if kk:
        try:
            result['mobile_kk'] = enrich_from_kk(d1, d2)
        except Exception as exc:
            import logging
            logging.getLogger(__name__).exception("Không bổ sung được SĐT/tiền thối từ KK")
            result['mobile_kk_error'] = str(exc)[:240]
    return result
