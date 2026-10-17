from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("redeem", "0004_redemption_duration_days"),
    ]

    operations = [
        migrations.CreateModel(
            name="CodeState",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("code", models.CharField(max_length=64, unique=True, verbose_name="卡密")),
                ("product_code", models.CharField(db_index=True, max_length=64, verbose_name="软件标识")),
                ("disabled", models.BooleanField(default=False, verbose_name="禁用")),
            ],
            options={
                "verbose_name": "卡密状态",
                "verbose_name_plural": "卡密状态",
            },
        ),
    ]
