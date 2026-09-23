"""Explicit, backed-up conversion of the one shared bank table approved by owner."""
import os
import subprocess
from pathlib import Path
from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from django.db import connection
from django.utils import timezone


class Command(BaseCommand):
    help='Backup bank_notifications, preserve schema/data and switch MyISAM to InnoDB.'

    def add_arguments(self,parser):parser.add_argument('--apply',action='store_true')

    def handle(self,*args,**options):
        if connection.vendor!='mysql':raise CommandError('MySQL only.')
        with connection.cursor() as c:
            c.execute("SELECT ENGINE FROM information_schema.TABLES WHERE TABLE_SCHEMA=DATABASE() AND TABLE_NAME='bank_notifications'")
            engine=c.fetchone()[0]
        self.stdout.write('bank_notifications: '+engine)
        if not options['apply']:return
        if engine not in ('MyISAM','InnoDB'):raise CommandError('Unexpected storage engine.')
        cfg=settings.DATABASES['default']
        folder=Path(settings.BASE_DIR)/'backups';folder.mkdir(exist_ok=True)
        path=folder/('bank_notifications_'+timezone.localtime().strftime('%Y%m%d_%H%M%S')+'.sql')
        env=os.environ.copy();env['MYSQL_PWD']=cfg['PASSWORD']
        command=[r'D:\PYTHON\mysql8\bin\mysqldump.exe','--host='+cfg['HOST'],'--port='+str(cfg['PORT']),
                 '--user='+cfg['USER'],'--lock-tables','--hex-blob','--no-tablespaces','--set-gtid-purged=OFF',cfg['NAME'],'bank_notifications']
        with path.open('xb') as output:
            result=subprocess.run(command,stdout=output,stderr=subprocess.PIPE,env=env,timeout=120)
        if result.returncode or path.stat().st_size<1024:raise CommandError('Backup failed; no ALTER executed.')
        with connection.cursor() as c:
            c.execute('SELECT COUNT(*),MAX(id) FROM bank_notifications');count,high=c.fetchone()
            c.execute('SET SESSION lock_wait_timeout=15')
            if engine=='MyISAM':c.execute('ALTER TABLE bank_notifications ENGINE=InnoDB')
            c.execute('SELECT COUNT(*) FROM bank_notifications WHERE id<=%s',[high])
            if c.fetchone()[0]!=count:raise CommandError('Row count changed; inspect backup before enabling reconciliation.')
            c.execute('SHOW INDEX FROM bank_notifications')
            if 'ix_money_in_lookup' not in {r[2] for r in c.fetchall()}:
                c.execute('ALTER TABLE bank_notifications ADD INDEX ix_money_in_lookup(direction,bank_number,transaction_time)')
            c.execute("SELECT ENGINE FROM information_schema.TABLES WHERE TABLE_SCHEMA=DATABASE() AND TABLE_NAME='bank_notifications'")
            if c.fetchone()[0]!='InnoDB':raise CommandError('Engine verification failed.')
        self.stdout.write(f'Verified InnoDB; preserved {count} existing rows. Backup: {path}')
