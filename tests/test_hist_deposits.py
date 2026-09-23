from unittest.mock import MagicMock, patch
from django.test import SimpleTestCase
from apps.pmv import hist_config as C, hist_sync as H


class DepositHistoryTests(SimpleTestCase):
    def test_strategies(self):
        self.assertEqual(C._strategy('TRN_DATCOC',['TrnID'],True,[]),('snapshot',None))
        for table in C.DEPOSIT_CHILDREN:
            self.assertEqual(C._strategy(table,[],False,[]),('deposit_child',None))
        self.assertEqual(C._strategy('TRN_DATCOC_Log',[],False,[{'name':'ID'}]),('append','ID'))

    def test_children_atomic_live_keys_no_drop(self):
        src=MagicMock();src.__enter__.return_value=src
        dst=MagicMock();dst.__enter__.return_value=dst
        src.cursor.return_value.fetchall.side_effect=[[('LIVE',)],[('ORPHAN','item')]]
        meta={'table':'TRN_DATCOC_DT','columns':[{'name':'TrnID'},{'name':'ProductDesc'}]}
        with patch.object(H.G,'_connect_dich',return_value=src),patch.object(H.G,'_hist_connect',return_value=dst):
            self.assertEqual(H.sync_deposit_child(meta)['ins'],1)
        src.commit.assert_called_once();dst.commit.assert_called_once()
        commands=[c.args[0] for c in dst.cursor.return_value.execute.call_args_list]
        self.assertTrue(any('JOIN #deposit_keys' in s for s in commands))
        self.assertFalse(any('TRUNCATE' in s or 'DROP TABLE [TRN' in s for s in commands))
        keys=dst.cursor.return_value.executemany.call_args_list[0].args[1]
        self.assertEqual(set(keys),{('LIVE',),('ORPHAN',)})

    def test_source_failure_does_not_delete_history(self):
        src=MagicMock();src.__enter__.return_value=src
        src.cursor.return_value.execute.side_effect=RuntimeError('offline')
        with patch.object(H.G,'_connect_dich',return_value=src),patch.object(H.G,'_hist_connect') as dest:
            with self.assertRaises(RuntimeError):
                H.sync_deposit_child({'table':'TRN_DATCOC_DT','columns':[{'name':'TrnID'}]})
            dest.assert_not_called()
