from django.core.management.base import BaseCommand
from django.db import close_old_connections
from apps.pmv.client import PmvClient
from apps.pos.deposit_money import process_queue
from apps.pos.deposit_messages import send_due


class Command(BaseCommand):
    help='Đối soát tiền cọc đã yêu cầu và xử lý lịch OA đến hạn, theo đích dữ liệu hiện tại'

    def handle(self,*args,**options):
        close_old_connections()
        try:
            target=PmvClient().target
            money=process_queue(target)
            messages=send_due(target)
            self.stdout.write(f'ĐẶT-CỌC ({target}): {money} yêu cầu tiền hoàn tất; {messages} tin được OA tiếp nhận.')
        finally:
            close_old_connections()
