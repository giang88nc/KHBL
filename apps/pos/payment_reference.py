"""Stable transfer reference; never replaces the pawn receipt SKU."""
import re


def pawn_reference(day, loan_id, log_id):
    if not 0 < int(loan_id) <= 99999 or not 0 < int(log_id) <= 99999:
        raise ValueError('Mã phiên vượt giới hạn 5 chữ số; cần nâng phiên bản mã đối soát.')
    return day.strftime('%y%m') + f'{int(loan_id):05d}{int(log_id):05d}'


def contains_reference(description, reference):
    # Numeric boundaries prevent matching a shorter ID inside another ID.
    # Accept hyphens/spaces only at the two declared separators, not arbitrary digits.
    if not re.fullmatch(r'\d{14}', reference):
        return False
    pattern = r'(?<!\d)' + reference[:4] + r'[- ]?' + reference[4:9] + r'[- ]?' + reference[9:] + r'(?!\d)'
    return bool(re.search(pattern, description or ''))
