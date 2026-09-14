from pathlib import Path
from django.core.management.base import BaseCommand, CommandError
from apps.pos.customer_bridge import Application


class Command(BaseCommand):
    help='Private loopback customer API reusing KHBL services for KHCD.'

    def add_arguments(self,parser):
        parser.add_argument('--key-file',required=True)
        parser.add_argument('--port',type=int,default=18202)
        parser.add_argument('--target',choices=['kk','sandbox'],default='kk')

    def handle(self,*args,**options):
        from waitress import serve
        key=Path(options['key_file']).read_text().strip()
        if len(key)<48:raise CommandError('Service key too short.')
        serve(Application(key,options['target']),host='127.0.0.1',port=options['port'],threads=4,
              max_request_body_size=112*1024*1024,clear_untrusted_proxy_headers=True)
