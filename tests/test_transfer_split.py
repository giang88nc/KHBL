from decimal import Decimal
from types import SimpleNamespace
from django.test import SimpleTestCase
from apps.pos.money_flow_payment import validate_split, transfer_content
from apps.pos.vietqr import payload, parse


class TransferSplitTests(SimpleTestCase):
    def test_split_validation(self):
        self.assertEqual(validate_split(1000000, '750123', '249877'), (Decimal(750123), Decimal(249877)))
        for bank, cash in [('NaN', '0'), ('1000001', '-1'), ('750000', '0'), ('0', '1000000')]:
            with self.assertRaises(ValueError):
                validate_split(1000000, bank, cash)

    def test_qr_amount_and_reference(self):
        for code, expected in [('26-09-16-000198', '260916000198'), ('KH22609020839', 'KH22609020839')]:
            flow = SimpleNamespace(source_bill_code=code, direction='IN', source_system='KHBL')
            note = transfer_content(flow)
            self.assertEqual(note, expected)
            qr = parse(payload('ACB', '123456', 750123, note, exact_amount=True))
            self.assertTrue(qr['hop_le'])
            self.assertEqual(Decimal(str(qr['amount'])), Decimal(750123))
            self.assertEqual(qr['info'], expected)
