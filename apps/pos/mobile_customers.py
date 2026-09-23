"""Resolve customer identity as a pair from the same customer record."""
import logging
from . import services, customer_phones


def hydrate_customers(flows):
    ids = sorted({f.customer_id for f in flows if f.customer_id and f.customer_id != services.WALK_IN})
    profiles = {}
    if ids:
        try:
            records = services.client('mobile-customers').query(
                'SELECT CustID, CustName, Phone, GhiChu2, GhiChu3 FROM I_CUSTOMER WITH (NOLOCK) WHERE CustID IN (' +
                ','.join(['?'] * len(ids)) + ')', tuple(ids))
            profiles = {r['CustID']: r for r in records}
        except Exception:
            logging.getLogger(__name__).exception('Mobile customer lookup unavailable')
    for flow in flows:
        profile = profiles.get(flow.customer_id)
        if profile:
            flow.customer_name = (profile.get('CustName') or '').strip() or flow.customer_name
            phone = next((customer_phones.phone_key(profile.get(k)) for k in ('Phone', 'GhiChu2', 'GhiChu3')
                          if customer_phones.valid(customer_phones.phone_key(profile.get(k)))), '')
            if phone:
                flow.phone_label = phone
        if not flow.customer_name:
            flow.customer_name = ('Khách lẻ' if not flow.customer_id or flow.customer_id == services.WALK_IN
                                  else 'Chưa tải được tên khách')
