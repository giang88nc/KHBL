from django.db import migrations, models
import django.db.models.deletion

class Migration(migrations.Migration):
    dependencies=[('pos','0033_mobile_identity_scope')]
    operations=[
        migrations.AlterField(model_name='moneyflowpayment',name='flow',field=models.ForeignKey(to='pos.moneyflow',on_delete=django.db.models.deletion.CASCADE,related_name='payment_instructions',null=True,blank=True)),
        migrations.AddField(model_name='moneyflowpayment',name='origin',field=models.CharField(max_length=16,default='business',db_index=True)),
        migrations.AddField(model_name='moneyflowpayment',name='note',field=models.CharField(max_length=500,default='',blank=True)),
        migrations.AlterField(model_name='moneyflowbankreceipt',name='flow',field=models.ForeignKey(to='pos.moneyflow',on_delete=django.db.models.deletion.PROTECT,related_name='bank_receipts',null=True,blank=True)),
        migrations.AddField(model_name='moneyflowbankreceipt',name='payment',field=models.ForeignKey(to='pos.moneyflowpayment',on_delete=django.db.models.deletion.PROTECT,related_name='standalone_receipts',null=True,blank=True)),
    ]
