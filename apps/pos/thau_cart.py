"""
PHIẾU THÂU ĐANG LÀM DỞ — giữ trong Django session (cùng cách cart.py của màn Bán hàng).

GĐ chốt 08/09/2026 tối: trang Thâu vào cùng khung màn Bán hàng; PMVGoldRT chỉ cho Ins 1 dòng/1 phiếu
→ web cho NHIỀU DÒNG (nhiều loại vàng) cùng 1 khách: mỗi dòng = 1 TRN_RT_BUYGOLD, cả nhóm chốt chung
một lệnh CompleteMore 'A@B@' (app cũng gọi 2 mã một lúc). Nhóm ghi ở MySQL (models.ThauNhom) để mở lại / in 1 tờ.

Mọi giá trị ép về CHUỖI (session đi qua JSON). Dòng: gold · desc · unit · tong_tl · tl_hot · tl_vang · gia
(nghìn/chỉ hoặc nghìn/g theo unit) · kieu ('thau' = giá thâu vào · 'ban' = giá bán ra) · tien · trn_id · bill_code · upd.
"""
from decimal import Decimal

from apps.pmv import money as M

KEY = "phieu_thau"


def _chuoi(v):
    if isinstance(v, Decimal):
        return format(v, "f")
    return "" if v is None else str(v)


def _rong(emp=""):
    return {"lines": [], "cust": None, "emp": emp, "bu": "0", "bot": "0", "ghi_chu": "",
            "pay_method": "cash", "tien_mat": "", "ck_bank": "", "ck_stk": "", "ck_nd": "", "ck_ten": "", "status": "", "trn_ids": [], "bill_codes": [],
            "nhom_id": "", "ngay": "", "gio": "", "kieu_ui": "thau", "sua_lai": False}


def get(request):
    g = request.session.get(KEY)
    if not isinstance(g, dict):
        g = {}
    for k, v in _rong().items():
        g.setdefault(k, v)
    return g


def save(request, g):
    request.session[KEY] = g
    request.session.modified = True


def clear(request, giu_nv=True):
    cu = get(request)
    g = _rong(cu.get("emp", "") if giu_nv else "")
    g["kieu_ui"] = cu.get("kieu_ui") or "thau"
    save(request, g)


def dong(gold, desc, unit, tong_tl, tl_hot, gia, kieu, tien, trn_id="", bill_code="", upd=""):
    tong_tl, tl_hot = M.dec(tong_tl), M.dec(tl_hot)
    return {"gold": gold, "desc": desc or gold, "unit": (unit or "L").upper(), "tong_tl": _chuoi(tong_tl),
            "tl_hot": _chuoi(tl_hot), "tl_vang": _chuoi(tong_tl - tl_hot), "gia": _chuoi(M.dec(gia)),
            "kieu": "ban" if kieu == "ban" else "thau", "tien": _chuoi(M.dec(tien)),
            "trn_id": trn_id or "", "bill_code": bill_code or "", "upd": _chuoi(upd)}


def them_dong(request, d):
    g = get(request)
    g["lines"].append(d)
    save(request, g)
    return g


def xoa_dong(request, i):
    g = get(request)
    if 0 <= i < len(g["lines"]):
        g["lines"].pop(i)
        save(request, g)
    return g


def tong(request):
    return tong_cua(get(request))


def tong_cua(g):
    """Tổng kết phiếu thâu: tiệm trả = tiền vàng + bù − bớt; tách tiền mặt / CK như màn bán."""
    tien_vang = sum((M.dec(x["tien"]) for x in g["lines"]), M.D0)
    bu, bot = M.dec(g.get("bu")), M.dec(g.get("bot"))
    tra = M.tron_ngan(tien_vang + bu - bot)
    t = {"tien_vang": tien_vang, "bu": bu, "bot": bot, "khach_tra": tra, "so_dong": len(g["lines"]),
         "tl_vang": sum((M.dec(x["tl_vang"]) for x in g["lines"]), M.D0),
         "tl_hot": sum((M.dec(x["tl_hot"]) for x in g["lines"]), M.D0)}
    t["botle_goiy"] = [{"tien": v, "val": str(int(v))} for v in M.bot_le_goiy(tra + bot)] if tra > 0 else []
    t["pay_method"] = "bank" if g.get("pay_method") in ("bank", "card") else "cash"   # 08/09 tối: BỎ THẺ, chỉ tiền mặt / CK
    raw = g.get("tien_mat")
    if raw in (None, ""):
        cash = tra if t["pay_method"] == "cash" else M.D0
    else:
        cash = min(max(M.dec(raw), M.D0), tra) if tra > 0 else M.D0
    t["tien_mat"] = cash
    t["tien_ck"] = tra - cash
    return t


def phan_bo(g):
    """Chia BÙ/BỚT và CK cho từng dòng → list [(tien_dong, add_money, ck)] theo thứ tự dòng.
    Bù/bớt ròng gắn vào dòng có tiền LỚN NHẤT (1 dòng TRN_RT_BUYGOLD mang AddMoney);
    CK rót tuần tự từ dòng đầu cho tới hết. Tổng các dòng = tiệm trả."""
    t = tong_cua(g)
    tiens = [M.tron_ngan(M.dec(x["tien"])) for x in g["lines"]]
    if not tiens:
        return []
    rong = t["khach_tra"] - sum(tiens, M.D0)          # bù − bớt (+ lệch làm tròn)
    i_max = max(range(len(tiens)), key=lambda i: tiens[i])
    tong_dong = [tiens[i] + (rong if i == i_max else M.D0) for i in range(len(tiens))]
    con_ck = t["tien_ck"]
    out = []
    for i, td in enumerate(tong_dong):
        ck = min(con_ck, td) if con_ck > 0 and td > 0 else M.D0
        con_ck -= ck
        out.append((tiens[i], (rong if i == i_max else M.D0), ck, td))
    return out


def nap(request, rows, nhom=None, emp=""):
    """Dựng giỏ từ các dòng TRN_RT_BUYGOLD (bill.phieu_thau) — mở lại từ DANH SÁCH / sau THANH TOÁN."""
    g = _rong(emp)
    rows = [r for r in rows if r]
    if not rows:
        save(request, g)
        return g
    r0 = rows[0]
    g["trn_ids"] = [r["TrnID"] for r in rows]
    g["bill_codes"] = [r.get("BillCode") or r["TrnID"] for r in rows]
    g["status"] = "C" if all(r.get("Status") == "C" for r in rows) else "W"
    g["ngay"] = r0["TrnDate"].date().isoformat() if hasattr(r0.get("TrnDate"), "date") else str(r0.get("TrnDate") or "")[:10]
    g["gio"] = (r0.get("TrnTime") or "")[:5]
    g["emp"] = r0.get("EmpID") or emp
    if r0.get("CustID") and r0["CustID"] != "CU0000000000000":
        g["cust"] = {"id": r0["CustID"], "code": "", "name": r0.get("CustName") or "", "phone": r0.get("Phone") or ""}
    g["ghi_chu"] = r0.get("Notes") or ""
    add = sum((M.dec(r.get("AddMoney")) for r in rows), M.D0)
    g["bu"], g["bot"] = _chuoi(add if add > 0 else M.D0), _chuoi(-add if add < 0 else M.D0)
    ck = sum((-M.dec(r.get("CardPay")) for r in rows if M.dec(r.get("CardPay")) < 0), M.D0)
    tong_tt = sum((M.dec(r.get("TotalAmount")) for r in rows), M.D0)
    g["pay_method"] = "bank" if ck >= tong_tt and ck > 0 else "cash"
    g["tien_mat"] = _chuoi(tong_tt - ck) if 0 < ck < tong_tt else ""
    for r in rows:
        tien = M.dec(r.get("TotalAmount")) - M.dec(r.get("AddMoney"))
        g["lines"].append(dong(r.get("GoldCode"), r.get("GoldDesc"), r.get("WeightUnit"),
                               M.dec(r.get("GoldWeight")) + M.dec(r.get("DiamondWeight")), r.get("DiamondWeight"),
                               r.get("BuyRate"), "thau", tien, r["TrnID"], r.get("BillCode") or "", r.get("TrnDateTime_Upd")))
    if nhom is not None:
        g["nhom_id"] = str(nhom.pk)
        g["pay_method"] = nhom.pay_method or g["pay_method"]
        g["ck_bank"], g["ck_stk"], g["ck_nd"] = nhom.ck_bank or "", nhom.ck_stk or "", nhom.ck_nd or g["bill_codes"][0]
        g["ck_ten"] = nhom.ck_ten or ""
        for i, kieu in enumerate(nhom.kieu or []):
            if i < len(g["lines"]):
                g["lines"][i]["kieu"] = kieu
    save(request, g)
    return g
