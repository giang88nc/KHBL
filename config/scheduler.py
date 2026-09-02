"""
Khai báo job nền KHBL — chạy trong tiến trình RIÊNG (manage.py run_scheduler),
KHÔNG nhúng vào web (nguyên tắc kế thừa KHJ: job không bao giờ chạy trùng 2 tiến trình).

- 02:00 hằng đêm : backup_pmv  (backup COPY_ONLY DB PMV trên PC KK + verify + dọn bản cũ)
  (KHJ HR backup lúc 01:30 cùng máy — né giờ nhau)
- 30 phút/lần    : check_pmv   (kiểm kết nối + vân tay version DB vendor → cảnh báo đổi schema)
"""
from apscheduler.schedulers.blocking import BlockingScheduler
from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.interval import IntervalTrigger
from django.core.management import call_command


def _job_backup_pmv():
    call_command("backup_pmv")


def _job_check_pmv():
    call_command("check_pmv")


def _job_collect_behavior():
    call_command("collect_pmv_behavior")


def start():
    scheduler = BlockingScheduler(timezone="Asia/Ho_Chi_Minh")
    scheduler.add_job(_job_backup_pmv, CronTrigger(hour=2, minute=0), name="Backup PMV 02:00")
    scheduler.add_job(_job_check_pmv, IntervalTrigger(minutes=30), name="Check PMV 30 phút")
    scheduler.add_job(_job_collect_behavior, IntervalTrigger(minutes=2), name="Thu thập hành vi PMV 2 phút",
                      max_instances=1, coalesce=True)
    print("KHBL scheduler khởi động: backup PMV 02:00 + check PMV 30 phút + hành vi PMV 2 phút. Ctrl+C để dừng.")
    scheduler.start()
