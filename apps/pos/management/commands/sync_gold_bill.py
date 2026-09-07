"""sync_gold_bill — đối soát gold_bill với KK cho đơn HÔM NAY (job 60' trong scheduler; chạy tay được).
Bắt đơn tạo/đổi/xóa từ PMVGoldRT mà web không thấy; đơn web ghi xuyên sẵn nên chỉ khớp lại mốc."""
from django.core.management.base import BaseCommand

from apps.pos import gold_bill as GB


class Command(BaseCommand):
    help = "Đối soát gold_bill ↔ KK (đơn hôm nay)"

    def handle(self, *a, **o):
        tk = GB.doi_soat_hom_nay()
        self.stdout.write(f"gold_bill đối soát hôm nay: KK {tk['kk']} đơn · mới {tk['moi']} · đổi {tk['doi']} · xóa {tk['xoa']}")
