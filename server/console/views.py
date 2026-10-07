import json

from django.contrib import messages
from django.contrib.auth import get_user_model, login
from django.contrib.auth.decorators import user_passes_test
from django.core.paginator import Paginator
from django.db import transaction
from django.http import Http404, HttpResponse
from django.shortcuts import redirect, render
from django.urls import reverse
from django.utils.http import url_has_allowed_host_and_scheme
from django.views.decorators.http import require_POST

from activation import services as activation_services
from keys import services as keys_services
from keys.errors import ServiceError
from redeem import services as redeem_services

from .forms import IssueCodesForm, ProductCreateForm, ProductEditForm, SetupForm

PAGE_SIZE = 50
NOT_FOUND = {"PRODUCT_NOT_FOUND", "BATCH_NOT_FOUND", "ACTIVATION_NOT_FOUND", "REDEMPTION_NOT_FOUND"}

admin_required = user_passes_test(lambda u: u.is_active and u.is_superuser)


def setup(request):
    if get_user_model().objects.filter(is_superuser=True).exists():
        return redirect("console:login")
    form = SetupForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        user = get_user_model().objects.create_superuser(
            form.cleaned_data["username"], password=form.cleaned_data["password"]
        )
        login(request, user, backend="django.contrib.auth.backends.ModelBackend")
        return redirect("console:products")
    return render(request, "console/setup.html", {"form": form})


def _call(func, *args, **kwargs):
    try:
        return func(*args, **kwargs)
    except ServiceError as exc:
        if exc.code in NOT_FOUND:
            raise Http404(exc.message)
        raise


def _first_error(form):
    for errors in form.errors.values():
        return errors[0]
    return "输入有误"


def _safe_next(request, fallback):
    next_url = request.POST.get("next", "")
    if url_has_allowed_host_and_scheme(next_url, allowed_hosts={request.get_host()}):
        return redirect(next_url)
    return fallback


def _in_use_counts():
    return {
        "device": activation_services.count_in_use_by_product(),
        "count": redeem_services.count_in_use_by_product(),
    }


@admin_required
def product_list(request):
    counts = {
        "device": activation_services.count_by_product(),
        "count": redeem_services.count_by_product(),
    }
    in_use = _in_use_counts()
    rows = [
        (p, counts[p.kind].get(p.code, 0), not in_use[p.kind].get(p.code, 0)) for p in keys_services.list_products()
    ]
    return render(request, "console/product_list.html", {"rows": rows})


@admin_required
@require_POST
def product_delete(request, pk):
    product = _call(keys_services.get_product, pk)
    with transaction.atomic():
        if _in_use_counts()[product.kind].get(product.code, 0):
            messages.error(request, f"{product.name} 还有使用中的卡密，不能删除；可在编辑中禁用")
            return redirect("console:products")
        activation_services.delete_by_product(product.code)
        redeem_services.delete_by_product(product.code)
        keys_services.delete_product(pk)
    messages.success(request, f"已删除 {product.name}（{product.code}）")
    return redirect("console:products")


@admin_required
def product_create(request):
    form = ProductCreateForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        data = form.cleaned_data
        if data["kind"] == "count":
            data.update(allow_transfer=False, transfer_penalty_hours=0)
        try:
            product = keys_services.create_product(**data)
        except ServiceError as exc:
            form.add_error(None, exc.message)
        else:
            return redirect("console:keys", pk=product.id)
    return render(request, "console/product_form.html", {"form": form, "title": "新建软件"})


@admin_required
def product_edit(request, pk):
    product = _call(keys_services.get_product, pk)
    initial = {
        "name": product.name,
        "allow_transfer": product.allow_transfer,
        "transfer_penalty_hours": product.transfer_penalty_hours,
        "disabled": product.disabled,
    }
    form = ProductEditForm(request.POST or None, initial=initial)
    if product.kind == "count":
        del form.fields["allow_transfer"], form.fields["transfer_penalty_hours"]
    if request.method == "POST" and form.is_valid():
        data = {**initial, **form.cleaned_data}
        try:
            keys_services.update_product(pk, **data)
        except ServiceError as exc:
            form.add_error(None, exc.message)
        else:
            messages.success(request, "已保存")
            return redirect("console:products")
    return render(
        request,
        "console/product_form.html",
        {"form": form, "title": f"编辑 {product.name}（{product.code}）", "product": product},
    )


@admin_required
def key_list(request, pk):
    product = _call(keys_services.get_product, pk)
    query = request.GET.get("q", "").strip()
    if product.kind == "count":
        records = redeem_services.search(product.code, query)
    else:
        records = activation_services.search(product.code, query)
    page = Paginator(records, PAGE_SIZE).get_page(request.GET.get("page"))
    return render(
        request,
        "console/key_list.html",
        {
            "product": product,
            "page": page,
            "query": query,
            "issue_form": IssueCodesForm(),
            "batches": keys_services.list_batches(pk),
        },
    )


@admin_required
@require_POST
def key_issue(request, pk):
    product = _call(keys_services.get_product, pk)
    form = IssueCodesForm(request.POST)
    if not form.is_valid():
        messages.error(request, _first_error(form))
        return redirect("console:keys", pk=pk)
    if product.kind == "count":
        duration_days, uses = None, form.cleaned_data["uses"]
    else:
        duration_days, uses = form.cleaned_data["duration_days"], None
    try:
        batch, codes = keys_services.issue_codes(pk, duration_days, form.cleaned_data["count"], uses)
    except ServiceError as exc:
        messages.error(request, exc.message)
        return redirect("console:keys", pk=pk)
    return render(
        request,
        "console/result.html",
        {"product": product, "title": f"已签发 {len(codes)} 个卡密", "codes": codes, "batch": batch},
    )


@admin_required
def key_export(request, pk):
    product = _call(keys_services.get_product, pk)
    batch_id = request.GET.get("batch", "") or None
    content = _call(keys_services.export_codes_csv, pk, batch_id)
    filename = f"codes-{product.code}-{batch_id}.csv" if batch_id else f"codes-{product.code}.csv"
    response = HttpResponse("\ufeff" + content, content_type="text/csv; charset=utf-8")
    response["Content-Disposition"] = f'attachment; filename="{filename}"'
    return response


@admin_required
def activation_detail(request, pk):
    activation, logs = _call(activation_services.get_detail, pk)
    product = keys_services.get_product_by_code(activation.product_code)
    return render(
        request,
        "console/activation_detail.html",
        {"activation": activation, "product": product, "logs": logs},
    )


@admin_required
def redemption_detail(request, code):
    usage, records = _call(redeem_services.get_code_detail, code)
    product = keys_services.get_product_by_code(usage.product_code)
    return render(
        request,
        "console/redemption_detail.html",
        {"usage": usage, "product": product, "records": records},
    )


@admin_required
@require_POST
def activation_toggle(request, pk):
    activation = _call(activation_services.toggle_disabled, pk)
    messages.success(request, f"激活记录已{'禁用' if activation.disabled else '取消禁用'}")
    product = keys_services.get_product_by_code(activation.product_code)
    return _safe_next(request, redirect("console:keys", pk=product.id))


@admin_required
def batch_list(request, pk):
    product = _call(keys_services.get_product, pk)
    return render(
        request, "console/batch_list.html", {"product": product, "batches": keys_services.list_batches(pk)}
    )


@admin_required
@require_POST
def batch_disable(request, batch_id):
    batch = _call(keys_services.disable_batch, batch_id)
    messages.success(request, f"批次 {batch.batch_id} 已禁用")
    return redirect("console:batches", pk=batch.product_id)


@admin_required
def mcp_setup(request):
    mcp_url = request.build_absolute_uri(reverse("mcp"))
    config = json.dumps({"mcpServers": {"spark-auth": {"url": mcp_url}}}, indent=2)
    return render(request, "console/mcp_setup.html", {"mcp_url": mcp_url, "config": config})
