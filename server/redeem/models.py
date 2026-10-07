from django.db import models


class Redemption(models.Model):
    code = models.CharField("卡密", max_length=64)
    product_code = models.CharField("软件标识", max_length=64, db_index=True)
    seq = models.PositiveIntegerField("第几次")
    uses = models.PositiveIntegerField("可用次数")
    redeemed_at = models.DateTimeField("核销时间")
    ip = models.GenericIPAddressField("核销 IP", null=True, blank=True)

    class Meta:
        verbose_name = "核销记录"
        verbose_name_plural = "核销记录"
        ordering = ["-redeemed_at", "-id"]
        constraints = [models.UniqueConstraint(fields=["code", "seq"], name="redemption_code_seq_unique")]

    def __str__(self):
        return f"{self.code} #{self.seq}"

    def save(self, *args, **kwargs):
        if self.pk is not None:
            raise PermissionError("核销记录只追加，不能修改")
        super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        raise PermissionError("核销记录只追加，不能删除")
