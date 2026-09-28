from django.core.validators import MinValueValidator
from django.db import models
from django.utils import timezone


class Product(models.Model):
    code = models.SlugField("标识", max_length=64, unique=True)
    name = models.CharField("名称", max_length=128)
    allow_transfer = models.BooleanField("允许换设备", default=False)
    transfer_penalty_hours = models.PositiveIntegerField(
        "换设备扣减时长（小时）", default=0, help_text="0 表示换设备不扣除时长；1 天填 24"
    )
    disabled = models.BooleanField("禁用", default=False)
    created_at = models.DateTimeField("创建时间", auto_now_add=True)

    class Meta:
        verbose_name = "软件（product）"
        verbose_name_plural = "软件（product）"
        ordering = ["code"]

    def __str__(self):
        return f"{self.name}（{self.code}）"

    @property
    def transfer_policy_label(self):
        if not self.allow_transfer:
            return "不允许换设备"
        if self.transfer_penalty_hours == 0:
            return "允许换设备，不扣时长"
        return f"允许换设备，每次扣 {self.transfer_penalty_hours} 小时"


class LicenseKey(models.Model):
    STATUS_DISABLED = "已禁用"
    STATUS_INACTIVE = "未激活"
    STATUS_ACTIVE = "有效"
    STATUS_EXPIRED = "已到期"

    key = models.CharField("key", max_length=128, unique=True)
    product = models.ForeignKey(
        Product, verbose_name="所属软件", on_delete=models.PROTECT, related_name="keys"
    )
    duration_days = models.PositiveIntegerField("有效时长（天）", validators=[MinValueValidator(1)])
    expires_at = models.DateTimeField("到期时间", null=True, blank=True)
    disabled = models.BooleanField("禁用", default=False)
    created_at = models.DateTimeField("创建时间", auto_now_add=True)

    class Meta:
        verbose_name = "key"
        verbose_name_plural = "key"
        ordering = ["-created_at", "-id"]

    def __str__(self):
        return self.key

    def is_expired(self, now=None):
        return self.expires_at is not None and self.expires_at <= (now or timezone.now())

    @property
    def status(self):
        if self.disabled:
            return self.STATUS_DISABLED
        if self.expires_at is None:
            return self.STATUS_INACTIVE
        if self.is_expired():
            return self.STATUS_EXPIRED
        return self.STATUS_ACTIVE


class Activation(models.Model):
    key = models.OneToOneField(
        LicenseKey, verbose_name="key", on_delete=models.CASCADE, related_name="activation"
    )
    device_hash = models.CharField("设备指纹", max_length=64)
    device_info = models.CharField("设备信息", max_length=255, blank=True)
    activated_at = models.DateTimeField("绑定时间")

    class Meta:
        verbose_name = "当前激活记录"
        verbose_name_plural = "当前激活记录"

    def __str__(self):
        return f"{self.key} → {self.device_hash[:12]}"


class TransferLog(models.Model):
    key = models.ForeignKey(
        LicenseKey, verbose_name="key", on_delete=models.PROTECT, related_name="transfer_logs"
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
