"""
Khai báo job nền KHBL — chạy trong tiến trình RIÊNG (manage.py run_scheduler),
KHÔNG nhúng vào web (nguyên tắc kế thừa KHJ: job không bao giờ chạy trùng 2 tiến trình).

- 02:00 hằng đêm : backup_pmv  (backup COPY_ONLY DB PMV trên PC KK + verify + dọn bản cũ)
  (KHJ HR backup lúc 01:30 cùng máy — né giờ nhau)
- 30 phút/lần    : check_pmv   (kiểm kết nối + vân tay version DB vendor → cảnh báo đổi schema)
- 5 phút/lần     : doi_soat_ck (đối soát tiền CK phiếu thâu với thông báo ngân hàng — thay cho nút bấm tay)
"""
from apscheduler.schedulers.blocking import BlockingScheduler
from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.interval import IntervalTrigger
from django.core.management import call_command


def _job_backup_pmv():
    # 09:30 & 21:30 (GĐ chốt 06/09/2026 — đêm KK/GIANG có thể tắt; giờ này cả 2 máy chắc chắn bật,
    # đứng SAU sync kho 09:00/21:00 30 phút để không cùng lúc đọc KK). Bước 2: backup kho lịch sử → ổ D.
    call_command("backup_pmv")
    try:
        call_command("backup_hist")
    except Exception as exc:  # kho lỗi không được làm mất bản KK vừa xong
        print(f"backup_hist LỖI: {exc}", flush=True)


def _job_check_pmv():
    call_command("check_pmv")


def _job_collect_behavior():
    call_command("collect_pmv_behavior")


def _job_sync_gold_bill():
    # Đối soát bảng gold_bill (MySQL) với KK cho đơn HÔM NAY — GĐ chốt 08/09/2026 (bắt đơn tạo/sửa/xóa từ PMVGoldRT)
    call_command("sync_gold_bill")


def _job_sync_money_flow():
    # Sổ IN/OUT chỉ đọc nguồn KHBL + KHCD; quét lại 3 ngày để bắt hoàn tác muộn + đọc KK bổ sung SĐT / tiền thối.
    import datetime
    today = datetime.date.today()
    call_command("sync_money_flow", d1=(today - datetime.timedelta(days=2)).isoformat(), d2=today.isoformat(), kk=True)


def _job_gan_ma_ck():
    # 22/09/2026: nhận diện mã chứng từ trong nội dung CK mới về → cột riêng KHBL (ma_chung_tu / loai_chung_tu) trên
    # bank_notifications. Skill nhan-dien-ma-chung-tu-ck. Chỉ ghi 3 cột mới, không đụng cột BANLE_V5 chép sang.
    from django.db import connection, transaction
    from apps.pos import ma_chung_tu_ck as MC
    with transaction.atomic(), connection.cursor() as cur:
        MC.gan_ma(cur, gioi_han=1000)


def _job_sync_money_flow_nhanh():
    # 21/09/2026: trang mobile Tạo QR cần HĐ gần realtime — chỉ HÔM NAY, chỉ MySQL (gold_bill + thau_nhom + khj_cd).
    import datetime
    today = datetime.date.today().isoformat()
    call_command("sync_money_flow", d1=today, d2=today)


def _job_doi_soat_ck():
    # Đối soát tiền CK của phiếu thâu với thông báo ngân hàng — GĐ chốt 10/09/2026: BỎ nút trên trang, máy chủ tự làm.
    # Soát 3 ngày gần nhất để bắt cả khoản chuyển trễ; luật nối vẫn y như bấm tay, không nới lỏng.
    call_command("doi_soat_ck", ngay=3)


def _job_deposit_work():
    call_command('process_deposit_work')


def _job_pawn_in():
    call_command('reconcile_pawn_in', apply=True)


def _job_sync_hist():
    # Đồng bộ KK → kho lịch sử PMV_KH2_HIST (incremental) — 09:00 & 21:00 (GĐ chốt 06/09/2026)
    call_command("sync_hist", sync=True)


def start():
    scheduler = BlockingScheduler(timezone="Asia/Ho_Chi_Minh")
    # misfire_grace_time=3600: máy bật muộn trong vòng 1h vẫn CHẠY BÙ thay vì bỏ lượt (lý do đổi giờ 02:00→09:30)
    scheduler.add_job(_job_backup_pmv, CronTrigger(hour="9,21", minute=30), name="Backup PMV + kho lịch sử 09:30 & 21:30",
                      max_instances=1, coalesce=True, misfire_grace_time=3600)
    # Check KK + vân tay version DB vendor: CẦU CHÌ chống ghi sai khi vendor nâng cấp — giữ, giãn 60'
    scheduler.add_job(_job_check_pmv, IntervalTrigger(minutes=60), name="Check PMV 60 phút")
    scheduler.add_job(_job_sync_gold_bill, IntervalTrigger(minutes=60), name="Đối soát gold_bill 60 phút",
                      max_instances=1, coalesce=True)
    # Job thu thập hành vi 2 phút ĐÃ TẮT (GĐ chốt 06/09/2026) — việc học đã xong. Giữ code + nút tay:
    # ĐÁNH DẤU TRƯỚC (tự bật trace) → thao tác PMV → ĐÁNH DẤU SAU (tự tắt trace) → HỌC → quy trình mới.
    # Bật lại định kỳ nếu cần: bỏ dấu # dòng dưới.
    # scheduler.add_job(_job_collect_behavior, IntervalTrigger(minutes=2), name="Thu thập hành vi PMV 2 phút",
    #                   max_instances=1, coalesce=True)
    scheduler.add_job(_job_sync_hist, CronTrigger(hour="9,21", minute=0), name="Sync kho lịch sử 09:00 & 21:00",
                      max_instances=1, coalesce=True)
    scheduler.add_job(_job_doi_soat_ck, IntervalTrigger(minutes=5), name="Đối soát CK phiếu thâu 5 phút",
                      max_instances=1, coalesce=True)
    scheduler.add_job(_job_deposit_work, IntervalTrigger(minutes=1), name="Đối soát cọc & lịch OA 1 phút",
                      max_instances=1, coalesce=True, misfire_grace_time=60)
    scheduler.add_job(_job_pawn_in, IntervalTrigger(seconds=30), name='Đối soát IN KHBL + KHCD',
                      max_instances=1, coalesce=True, misfire_grace_time=30)
    scheduler.add_job(_job_sync_money_flow, IntervalTrigger(minutes=5), name="Đồng bộ sổ IN/OUT KHBL + KHCD 5 phút (3 ngày + KK)",
                      max_instances=1, coalesce=True, misfire_grace_time=300)
    scheduler.add_job(_job_gan_ma_ck, IntervalTrigger(seconds=15), name="Nhận diện mã CK mới 15 giây",
                      max_instances=1, coalesce=True, misfire_grace_time=15)
    scheduler.add_job(_job_sync_money_flow_nhanh, IntervalTrigger(seconds=20), name="Đồng bộ sổ IN/OUT hôm nay 20 giây (MySQL)",
                      max_instances=1, coalesce=True, misfire_grace_time=20)
    # flush=True: stdout đổ vào logs/scheduler.log bị block-buffer, BlockingScheduler không bao giờ thoát
    # → banner nằm kẹt trong buffer, nhìn log tưởng chưa nạp job mới (đã dính 06/09/2026).
    print("KHBL scheduler khởi động: sync lịch sử 09:00/21:00 + backup PMV+kho 09:30/21:30 (bù 1h) + check KK 60' "
          "+ đối soát CK phiếu thâu 5' + đối soát cọc/lịch OA 1' + sổ IN/OUT 20s hôm nay + 5' 3 ngày. "
          "(Thu thập hành vi 2' đã tắt — dùng ĐÁNH DẤU tay.) Ctrl+C để dừng.", flush=True)
    scheduler.start()
