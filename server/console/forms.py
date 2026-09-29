from django import forms

from keys.models import Product

PRODUCT_HELP_TEXTS = {
    "code": "接入方软件激活时上报的软件标识，只能用字母、数字、- 和 _；创建后不能修改",
    "name": "只在后台显示，可随时修改",
    "allow_transfer": "不勾选时，卡密绑定第一台设备后不能换到其它设备",
    "disabled": "勾选后该软件所有卡密立即失效：禁止新激活，已激活的设备校验失败；取消勾选即恢复",
}

DURATION_HELP_TEXT = "从首次激活开始计算；未激活的卡密不会过期"


class ProductCreateForm(forms.ModelForm):
    class Meta:
        model = Product
        fields = ["code", "name", "allow_transfer", "transfer_penalty_hours"]
        help_texts = PRODUCT_HELP_TEXTS


class ProductEditForm(forms.ModelForm):
    class Meta:
        model = Product
        fields = ["name", "allow_transfer", "transfer_penalty_hours", "disabled"]
        help_texts = PRODUCT_HELP_TEXTS


class IssueCodesForm(forms.Form):
    count = forms.IntegerField(label="数量", initial=1)
    duration_days = forms.IntegerField(label="有效天数", min_value=1, initial=30, help_text=DURATION_HELP_TEXT)
