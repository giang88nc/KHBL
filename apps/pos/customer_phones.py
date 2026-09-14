"""Ba số liên hệ của PMV; cùng quy tắc khi nhập, đối chiếu và tìm kiếm."""
import re

COLUMNS = ("Phone", "GhiChu2", "GhiChu3")
KEYS = ("phone", "phone2", "phone3")
SEPARATORS = (" ", ".", "(", ")", "-", "\t", "\r", "\n", "\u00a0")


def phone_key(value):
    return str(value or "").translate(str.maketrans("", "", "".join(SEPARATORS)))


def valid(value):
    return bool(re.fullmatch(r"[0-9]{10}", value))


def expression(column):
    for char in SEPARATORS:
        column = "REPLACE(" + column + ", '" + char + "', '')"
    return column


def exact_sql(alias=""):
    prefix = alias + "." if alias else ""
    return "(" + " OR ".join(expression(prefix + c) + "=?" for c in COLUMNS) + ")"


def values(row):
    return {phone_key(row.get(c)) for c in COLUMNS} - {""}


def search(q, alias="c"):
    """10 số: một hồ sơ cố định; CCCD đầy đủ: mọi dòng khớp; khác: tìm gần đúng."""
    number = phone_key(q)
    if valid(number):
        # Không thay đổi khách cũ trùng số. Ưu tiên số chính, SĐT2, SĐT3 rồi CustID.
        order = ("CASE WHEN " + expression("p.Phone") + "=? THEN 0 WHEN "
                 + expression("p.GhiChu2") + "=? THEN 1 ELSE 2 END, p.CustID")
        return (alias + ".CustID = (SELECT TOP 1 p.CustID FROM I_CUSTOMER p WITH (NOLOCK) "
                "WHERE p.CustID<>? AND " + exact_sql("p") + " ORDER BY " + order + ")",
                ("CU0000000000000", number, number, number, number, number))
    if re.fullmatch(r"(?:[0-9]{9}|[0-9]{12})", number):
        return expression(alias + ".CMND") + "=?", (number,)
    cols = (*COLUMNS, "CMND", "CustName", "CustCode")
    return "(" + " OR ".join(alias + "." + c + " LIKE ?" for c in cols) + ")", ("%" + q + "%",) * len(cols)
