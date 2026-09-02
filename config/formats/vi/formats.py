"""
Định dạng VN cho template (FORMAT_MODULE_PATH). Không có file này thì Django in
'Ngày 03 tháng 9 năm 2026' và số thực ra '1234,5' — bẫy KHJ đã dính 2 lần.
USE_THOUSAND_SEPARATOR vẫn để False: số nhúng vào data-*/value= phải là số trần,
chấm nghìn do CSS/JS lo (xem .khbl-money).
"""
DATE_FORMAT = "d/m/Y"
DATETIME_FORMAT = "d/m/Y H:i:s"
SHORT_DATE_FORMAT = "d/m/Y"
SHORT_DATETIME_FORMAT = "d/m/Y H:i"
TIME_FORMAT = "H:i:s"
DECIMAL_SEPARATOR = ","
THOUSAND_SEPARATOR = "."
NUMBER_GROUPING = 3
FIRST_DAY_OF_WEEK = 1
DATE_INPUT_FORMATS = ["%d/%m/%Y", "%Y-%m-%d"]
