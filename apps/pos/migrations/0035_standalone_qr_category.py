from django.db import migrations, models

class Migration(migrations.Migration):
    dependencies=[('pos','0034_standalone_qr')]
    operations=[
        migrations.AddField(model_name='moneyflowpayment',name='qr_category',field=models.CharField(max_length=2,blank=True,default='')),
        migrations.AddField(model_name='moneyflowpayment',name='standalone_reference',field=models.CharField(max_length=16,unique=True,null=True,blank=True)),
    ]
