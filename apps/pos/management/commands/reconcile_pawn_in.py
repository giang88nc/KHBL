import datetime as dt
from django.core.management.base import BaseCommand
from django.utils import timezone
from apps.pos.models import MoneyFlowPayment, MoneyFlowSourceWrite
from apps.pos.money_in import reconcile


class Command(BaseCommand):
    help = 'Đối soát IN KHBL/KHCD; chỉ phân bổ tiền nguồn, không ghi quỹ/kho.'

    def add_arguments(self, parser):
        parser.add_argument('--apply', action='store_true', help='Ghi nguồn và chứng từ; mặc định chỉ liệt kê phạm vi.')

    def handle(self, *args, **options):
        day = timezone.localdate()
        qs = MoneyFlowPayment.objects.filter(method='BANK', status__in=['ready','success'],
            flow__direction='IN', flow__is_void=False, flow__business_date__range=(day-dt.timedelta(days=1),day))
        seen=set()
        for instruction in qs.iterator():
            if instruction.flow_id in seen:continue
            seen.add(instruction.flow_id)
            if not options['apply']:
                self.stdout.write(f'flow={instruction.flow_id}; QR={instruction.pk}; reference={instruction.transfer_content}')
                continue
            try:
                result = reconcile(instruction.flow_id)
                if result['status'] != 'waiting':
                    self.stdout.write(f'flow={instruction.flow_id}: {result}')
            except Exception as exc:
                self.stderr.write(f'flow={instruction.flow_id}: {exc}')
        if options['apply']:
            for pk in MoneyFlowSourceWrite.objects.filter(status='pending').exclude(flow_id__in=seen).values_list('flow_id',flat=True).distinct():
                try:reconcile(pk)
                except Exception as exc:self.stderr.write(f'pending flow={pk}: {exc}')
