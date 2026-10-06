import os

from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand, CommandError


class Command(BaseCommand):
    help = "当环境变量 SPARK_AUTH_ADMIN_USERNAME、SPARK_AUTH_ADMIN_PASSWORD 都设置时创建超级管理员"

    def handle(self, *args, **options):
        username = os.environ.get("SPARK_AUTH_ADMIN_USERNAME", "")
        password = os.environ.get("SPARK_AUTH_ADMIN_PASSWORD", "")
        if not username and not password:
            return
        if not username or not password:
            raise CommandError(
                "必须同时设置 SPARK_AUTH_ADMIN_USERNAME 和 SPARK_AUTH_ADMIN_PASSWORD，或两个都不设置"
            )
        User = get_user_model()
        if User.objects.filter(username=username).exists():
            return
        User.objects.create_superuser(username, password=password)
        self.stdout.write(f'已创建超级管理员 "{username}"')
