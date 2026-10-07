from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("redeem", "0001_initial"),
    ]

    operations = [
        migrations.AddField(
            model_name="redemption",
            name="seq",
            field=models.PositiveIntegerField(default=1, verbose_name="第几次"),
            preserve_default=False,
        ),
        migrations.AddField(
            model_name="redemption",
            name="uses",
            field=models.PositiveIntegerField(default=1, verbose_name="可用次数"),
            preserve_default=False,
        ),
        migrations.AlterField(
            model_name="redemption",
            name="code",
            field=models.CharField(max_length=64, verbose_name="卡密"),
        ),
        migrations.AddConstraint(
            model_name="redemption",
            constraint=models.UniqueConstraint(fields=("code", "seq"), name="redemption_code_seq_unique"),
        ),
    ]
