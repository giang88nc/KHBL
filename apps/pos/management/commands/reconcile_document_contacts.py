"""Read-only PMV verification then apply only the local contact snapshot."""
from django.core.management.base import BaseCommand, CommandError
from apps.pos import document_contacts as DC
from apps.pos.models import DocumentContactWrite
from apps.pmv.client import PmvClient


class Command(BaseCommand):
    help = 'Liệt kê yêu cầu liên hệ chưa xong; --apply TOKEN chỉ đối soát, không gửi lại giao dịch PMV.'

    def add_arguments(self, parser):
        parser.add_argument('--apply')

    def handle(self, *args, **options):
        token=options['apply']
        if not token:
            for row in DocumentContactWrite.objects.filter(status='pending').order_by('id'):
                self.stdout.write(f'{row.token} {row.source_type} {row.source_id or "CHUA_NHAN_ID"} {row.created_at}')
            return
        try: row=DocumentContactWrite.objects.get(token=token,status='pending')
        except DocumentContactWrite.DoesNotExist: raise CommandError('Không có yêu cầu đang chờ.')
        tables={'KHBL_BUYSELL':'TRN_RT_BUYSELL','KHBL_BUYGOLD':'TRN_RT_BUYGOLD','KHBL_DEPOSIT':'TRN_DATCOC'}
        if not row.source_id or row.source_type not in tables:
            raise CommandError('Chưa có ID trả về từ PMV; cần đối soát thủ công. Không tự ghép hoặc tạo phiếu.')
        c=PmvClient(row.target,tag='reconcile_document_contacts')
        found=c.query('SELECT TrnID,CustID,BillCode FROM '+tables[row.source_type]+' WITH (NOLOCK) WHERE TrnID=?',(row.source_id,))
        if len(found)!=1 or found[0]['CustID']!=row.snapshot['cust_id']:
            raise CommandError('Phiếu nguồn hoặc CustID không khớp. Chưa cập nhật liên hệ.')
        DC.finish(row,found[0].get('BillCode'))
        self.stdout.write('Đã khôi phục liên hệ của '+row.source_id+'; không thay đổi tiền/trạng thái phiếu.')
