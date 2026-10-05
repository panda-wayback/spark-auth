import json

from django.contrib import messages
from django.contrib.auth.decorators import user_passes_test
from django.core.paginator import Paginator
from django.http import Http404, HttpResponse
from django.shortcuts import redirect, render
from django.urls import reverse
from django.utils.http import url_has_allowed_host_and_scheme
from django.views.decorators.http import require_POST

from activation import services as activation_services
from keys import services as keys_services
from keys.errors import ServiceError

from . import hosts
from .forms import IssueCodesForm, ProductCreateForm, ProductEditForm

PAGE_SIZE = 50
NOT_FOUND = {"PRODUCT_NOT_FOUND", "BATCH_NOT_FOUND", "ACTIVATION_NOT_FOUND"}

admin_required = user_passes_test(lambda u: u.is_active and u.is_superuser)


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


@admin_required
def product_list(request):
    counts = activation_services.count_by_product()
    rows = [(p, counts.get(p.code, 0)) for p in keys_services.list_products()]
    return render(request, "console/product_list.html", {"rows": rows})


@admin_required
def product_create(request):
    form = ProductCreateForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        try:
            product = keys_services.create_product(**form.cleaned_data)
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
    if request.method == "POST" and form.is_valid():
        try:
            keys_services.update_product(pk, **form.cleaned_data)
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
    activations = activation_services.search(product.code, query)
    page = Paginator(activations, PAGE_SIZE).get_page(request.GET.get("page"))
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
    try:
        batch, codes = keys_services.issue_codes(pk, form.cleaned_data["duration_days"], form.cleaned_data["count"])
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
def allowed_hosts(request):
    if request.method == "POST":
        try:
            host = hosts.add_host(request.POST.get("host", ""))
            messages.success(request, f"已允许 {host}")
        except ServiceError as exc:
            messages.error(request, exc.message)
        return redirect("console:allowed_hosts")
    public_ip = None
    if request.GET.get("detect"):
        try:
            public_ip = hosts.public_ip()
        except ServiceError as exc:
            messages.error(request, exc.message)
        else:
            if hosts.is_allowed(public_ip):
                messages.success(request, f"公网 IP {public_ip} 已在允许列表中")
                public_ip = None
    return render(
        request,
        "console/allowed_hosts.html",
        {
            "builtin_hosts": hosts.builtin_hosts(),
            "file_hosts": hosts.file_hosts(),
            "candidates": hosts.candidates(),
            "public_ip": public_ip,
            "current_domain": hosts.request_domain(request),
        },
    )


@admin_required
@require_POST
def allowed_host_delete(request):
    host = request.POST.get("host", "")
    try:
        hosts.remove_host(host, hosts.request_domain(request))
        messages.success(request, f"已删除 {host}")
    except ServiceError as exc:
        messages.error(request, exc.message)
    return redirect("console:allowed_hosts")


@admin_required
def mcp_setup(request):
    mcp_url = request.build_absolute_uri(reverse("mcp"))
    config = json.dumps({"mcpServers": {"spark-auth": {"url": mcp_url}}}, indent=2)
    return render(request, "console/mcp_setup.html", {"mcp_url": mcp_url, "config": config})
