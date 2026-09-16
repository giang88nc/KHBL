import datetime as dt

from django.core.management.base import BaseCommand, CommandError

from apps.pos.money_flow import sync


class Command(BaseCommand):
    help = "Đồng bộ sổ dòng tiền GĐ1 từ bảng nguồn, không ghi PMV/KHCD"

    def add_arguments(self, parser):
        parser.add_argument("--from", dest="d1")
        parser.add_argument("--to", dest="d2")

    def handle(self, *args, **options):
        try:
            d1 = dt.date.fromisoformat(options["d1"]) if options.get("d1") else None
            d2 = dt.date.fromisoformat(options["d2"]) if options.get("d2") else None
        except ValueError as exc:
            raise CommandError("Ngày phải có dạng YYYY-MM-DD") from exc
        if d1 and d2 and d1 > d2:
            raise CommandError("--from không được sau --to")
        result = sync(d1, d2)
        self.stdout.write(self.style.SUCCESS(
            f"money_flow: gold_bill {result['gold_bill']} · thau_nhom {result['thau_nhom']}"))

