from django.test import TestCase

from apps.pos import gold_bill as GB
from apps.pos.models import GoldBill


class GoldBillTombstoneTests(TestCase):
    def test_deleted_live_invoice_does_not_reappear_from_history(self):
        GoldBill.objects.create(trn_id='T-DELETED',is_del=True)
        GoldBill.objects.create(trn_id='T-ACTIVE',is_del=False)
        rows=[{'TrnID':'T-DELETED','Status':'W'},{'TrnID':'T-ACTIVE','Status':'C'},
              {'TrnID':'T-HISTORY-ONLY','Status':'C'}]
        self.assertEqual([r['TrnID'] for r in GB.bo_phieu_da_xoa(rows)],
                         ['T-ACTIVE','T-HISTORY-ONLY'])

    def test_empty_rows_need_no_query_result(self):
        self.assertEqual(GB.bo_phieu_da_xoa([]),[])
