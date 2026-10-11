from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("redeem", "0003_redemption_ip"),
    ]

    operations = [
        migrations.AddField(
            model_name="redemption",
            name="duration_days",
            field=models.PositiveIntegerField(blank=True, null=True, verbose_name="有效时长（天）"),
        ),
    ]
