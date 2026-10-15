from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("keys", "0003_count_kind_and_uses"),
    ]

    operations = [
        migrations.AddField(
            model_name="batch",
            name="name",
            field=models.CharField(blank=True, default="", max_length=50, verbose_name="批次名称"),
        ),
    ]
