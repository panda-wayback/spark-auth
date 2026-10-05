from django.core.validators import MinValueValidator
from django.db import models
from django.utils import timezone


class Product(models.Model):
    code = models.SlugField("标识", max_length=64, unique=True)
    name = models.CharField("名称", max_length=128)
    allow_transfer = models.BooleanField("允许换设备", default=False)
    transfer_penalty_hours = models.PositiveIntegerField("换设备扣减时长（小时）", default=0)
    disabled = models.BooleanField("禁用", default=False)
    created_at = models.DateTimeField("创建时间", auto_now_add=True)

    class Meta:
        verbose_name = "软件（product）"
        verbose_name_plural = "软件（product）"
        ordering = ["code"]

    def __str__(self):
        return f"{self.name}（{self.code}）"


class Batch(models.Model):
    batch_id = models.CharField("批次标识", max_length=10, unique=True)
    secret = models.CharField("批次随机值", max_length=32)
    product = models.ForeignKey(Product, verbose_name="所属软件", on_delete=models.PROTECT, related_name="batches")
    duration_days = models.PositiveIntegerField("卡密有效时长（天）", validators=[MinValueValidator(1)])
    count = models.PositiveIntegerField("生成数量")
    disabled = models.BooleanField("禁用", default=False)
    created_at = models.DateTimeField("生成时间", default=timezone.now)

    class Meta:
        verbose_name = "卡密批次"
        verbose_name_plural = "卡密批次"
        ordering = ["-created_at", "-id"]

    def __str__(self):
        return f"{self.batch_id}（{self.product.code}）"
