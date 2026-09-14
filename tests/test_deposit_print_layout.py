"""Layout persistence/permissions only; SQLite test DB, no financial writes."""
import json
from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse
from apps.pmv.models import PmvState, UserModuleAccess
from apps.pos import deposit_print_layout as L, gdb_layout as G


class DepositPrintLayoutTests(TestCase):
    def setUp(self):
        self.user=get_user_model().objects.create_user('print-admin',is_superuser=True)
        self.client.force_login(self.user)
        self.url=reverse('pmv:deposit_print_config')

    def post(self,layout):
        return self.client.post(self.url,json.dumps({'layout':layout}),content_type='application/json')

    def test_deposit_save_and_reset_do_not_change_sales_layout(self):
        original=G.defaults() if hasattr(G,'defaults') else G.mac_dinh()
        original['_in']['dx']=2
        G.save(original)
        sales=PmvState.get(G.KEY)
        self.assertEqual(L.load()['_in']['dx'],2)
        layout=L.defaults();layout['employee']['top']=58
        response=self.post(layout)
        self.assertEqual(response.status_code,200)
        self.assertEqual(L.load()['employee']['top'],58)
        self.assertEqual(PmvState.get(G.KEY),sales)
        self.assertEqual(self.client.post(self.url,json.dumps({'reset':True}),content_type='application/json').status_code,200)
        self.assertEqual(L.load(),L.defaults())
        self.assertEqual(PmvState.get(G.KEY),sales)

    def test_invalid_values_cannot_replace_saved_layout(self):
        L.save(L.defaults());before=PmvState.get(L.KEY)
        for key,field,value in [('items','left',80),('items','fs',float('nan')),
                                ('_in','ty_le',float('inf')),('_in','dx','abc'),('_in','kho','bad')]:
            with self.subTest(key=key,field=field):
                layout=L.defaults();layout[key][field]=value
                self.assertEqual(self.post(layout).status_code,400)
                self.assertEqual(PmvState.get(L.KEY),before)

    def test_view_permission_cannot_save(self):
        self.user.is_superuser=False;self.user.save()
        self.assertEqual(self.client.get(self.url).status_code,403)
        grant=UserModuleAccess.objects.create(user=self.user,module='HE_THONG',can_view=True)
        self.assertEqual(self.client.get(self.url).status_code,200)
        self.assertEqual(self.post(L.defaults()).status_code,403)
        grant.can_edit=True;grant.save()
        self.assertEqual(self.post(L.defaults()).status_code,200)

    def test_configuration_and_sample_render_actual_print_template(self):
        response=self.client.get(self.url)
        self.assertTemplateUsed(response,'pos/deposit_print_config.html')
        self.assertContains(response,'dc-layout-state')
        self.assertContains(response,'Mẫu in CỌC')
        sample=self.client.get(self.url,{'sample':'1','configure':'1'})
        self.assertTemplateUsed(sample,'pos/dat_coc_print.html')
        self.assertContains(sample,'class="store-deposit"',count=2)
        self.assertContains(sample,'class="store-employee"',count=2)
        self.assertContains(sample,'class="customer-employee"')
        self.assertNotContains(sample,'customer-signatures')
        self.assertEqual(sample['Cache-Control'],'private, no-store')
        self.assertEqual(sample['X-Frame-Options'],'SAMEORIGIN')
        real=self.client.get(self.url,{'trn_id':'TDC260900000006'})
        self.assertContains(real,'TDC260900000006/view/?print=1&amp;configure=1')
        self.assertEqual(self.client.get(self.url,{'trn_id':'../../bad'}).status_code,400)
