from django.apps import AppConfig


class KeysConfig(AppConfig):
    name = "keys"
    verbose_name = "激活管理"

    def ready(self):
        from django.db.backends.signals import connection_created

        def _sqlite_wal(sender, connection, **kwargs):
            if connection.vendor != "sqlite":
                return
            with connection.cursor() as cursor:
                cursor.execute("PRAGMA journal_mode=WAL;")
                cursor.execute("PRAGMA busy_timeout=30000;")

        connection_created.connect(_sqlite_wal)
