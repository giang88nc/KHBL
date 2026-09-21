from django.db import migrations, models


class Migration(migrations.Migration):
    """GĐ chốt 19/09/2026: ThauNhom gom mọi lần tiệm CHUYỂN KHOẢN CHO KHÁCH. Nhóm cũ đều là phiếu thâu nên
    mặc định 'thau' giữ nguyên nghĩa của các dòng đang có; không đổi dữ liệu nào khác.
    db_default giữ DEFAULT trong MySQL để tiến trình web cũ (chưa RESET) vẫn INSERT nhóm thâu được."""

    dependencies = [('pos', '0035_standalone_qr_category')]

    operations = [
        migrations.AddField(
            model_name='thaunhom', name='nghiep_vu',
            field=models.CharField(choices=[('thau', 'Thâu vào'), ('doi', 'Bán đổi dư'), ('camdo', 'Cầm đồ'),
                                            ('khac', 'Khác')],
                                   db_index=True, default='thau', db_default='thau', max_length=10,
                                   verbose_name='Nghiệp vụ')),
    ]
