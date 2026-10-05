from django import forms

DURATION_HELP_TEXT = "从首次激活开始计算；未激活的卡密不会过期"


class ProductEditForm(forms.Form):
    name = forms.CharField(label="名称", help_text="只在后台显示，可随时修改")
    allow_transfer = forms.BooleanField(
        label="允许换设备", required=False, help_text="不勾选时，卡密绑定第一台设备后不能换到其它设备"
    )
    transfer_penalty_hours = forms.IntegerField(
        label="换设备扣减时长（小时）", initial=0, help_text="0 表示换设备不扣除时长；1 天填 24"
    )
    disabled = forms.BooleanField(
        label="禁用",
        required=False,
        help_text="勾选后该软件所有卡密立即失效：禁止新激活，已激活的设备校验失败；取消勾选即恢复",
    )


class ProductCreateForm(ProductEditForm):
    code = forms.CharField(
        label="标识", help_text="接入方软件激活时上报的软件标识，只能用字母、数字、- 和 _；创建后不能修改"
    )
    disabled = None
    field_order = ["code", "name", "allow_transfer", "transfer_penalty_hours"]


class IssueCodesForm(forms.Form):
    count = forms.IntegerField(label="数量", initial=1)
    duration_days = forms.IntegerField(label="有效天数", min_value=1, initial=30, help_text=DURATION_HELP_TEXT)
