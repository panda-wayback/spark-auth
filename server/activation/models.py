from django.core.validators import MinValueValidator
from django.db import models
from django.utils import timezone


class Activation(models.Model):
    code = models.CharField("卡密", max_length=64, unique=True)
    product_code = models.CharField("软件标识", max_length=64, db_index=True)
    device_hash = models.CharField("设备指纹", max_length=64)
    device_info = models.CharField("设备信息", max_length=255, blank=True)
    duration_days = models.PositiveIntegerField("有效时长（天）", validators=[MinValueValidator(1)])
    expires_at = models.DateTimeField("到期时间")
    disabled = models.BooleanField("禁用", default=False)
    activated_at = models.DateTimeField("激活时间")
    created_at = models.DateTimeField("创建时间", auto_now_add=True)

    class Meta:
        verbose_name = "激活记录"
        verbose_name_plural = "激活记录"
        ordering = ["-created_at", "-id"]

    def __str__(self):
        return f"{self.code} → {self.device_hash[:12]}"


class TransferLog(models.Model):
    activation = models.ForeignKey(
        Activation, verbose_name="激活记录", on_delete=models.PROTECT, related_name="transfer_logs"
    )
    old_device_hash = models.CharField("旧设备指纹", max_length=64)
    old_device_info = models.CharField("旧设备信息", max_length=255, blank=True)
    new_device_hash = models.CharField("新设备指纹", max_length=64)
    new_device_info = models.CharField("新设备信息", max_length=255, blank=True)
    penalty_hours = models.PositiveIntegerField("扣减时长（小时）")
    expires_before = models.DateTimeField("换设备前到期时间")
    expires_after = models.DateTimeField("换设备后到期时间")
    created_at = models.DateTimeField("换设备时间", default=timezone.now)

    class Meta:
        verbose_name = "换设备记录"
        verbose_name_plural = "换设备记录"
        ordering = ["-created_at", "-id"]

    def save(self, *args, **kwargs):
        if self.pk is not None:
            raise PermissionError("换设备记录只追加，不能修改")
        super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        raise PermissionError("换设备记录只追加，不能删除")
