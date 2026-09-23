"""Provision approved per-employee, passkey-only IN/QR accounts. Dry-run by default."""
from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand
from django.db import connection, transaction
from apps.pos.models import MobileEmployeeIdentity
from apps.pos.mobile_auth import resolve_identity


class Command(BaseCommand):
    help='Tạo tài khoản mobile riêng từ nhân viên KHJ đang hoạt động có passkey và mã PMV.'

    def add_arguments(self, parser):
        parser.add_argument('--apply',action='store_true')

    def handle(self,*args,**options):
        with connection.cursor() as c:
            c.execute('SELECT e.id,e.full_name,e.employee_pmv,COUNT(p.id) FROM khj_hr.employees e '
                'JOIN khj_hr.employee_portal_passkeys p ON p.employee_id=e.id AND p.revoked_at IS NULL '
                'WHERE e.status=%s GROUP BY e.id,e.full_name,e.employee_pmv ORDER BY e.id',['ACTIVE'])
            rows=c.fetchall()
        created=blocked=existing=0
        for pk,name,emp,nkeys in rows:
            emp=str(emp or '').strip()
            if nkeys!=1 or not emp:
                blocked+=1
                self.stdout.write(f'BLOCKED KHJ {pk}: {name} — cần mã PMV và đúng một passkey')
                continue
            username=f'khj_{pk}'
            with transaction.atomic():
                if MobileEmployeeIdentity.objects.filter(employee_id=pk).exists():
                    existing+=1
                    continue
                if get_user_model().objects.filter(username=username).exists():
                    blocked+=1
                    self.stdout.write(f'BLOCKED KHJ {pk}: username đã tồn tại, không ghi đè')
                    continue
                self.stdout.write(f'{"CREATE" if options["apply"] else "PLAN"} {username}: {name} | {emp}')
                if options['apply']:
                    resolve_identity({'employee_id':pk,'full_name':name,'employee_pmv':emp})
                created+=1
        self.stdout.write(f'{"CREATED" if options["apply"] else "PLANNED"}={created} EXISTING={existing} BLOCKED={blocked}')
