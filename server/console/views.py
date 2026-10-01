import json

from django.contrib import messages
from django.contrib.auth.decorators import user_passes_test
from django.core.paginator import Paginator
from django.db.models import Count, Q
from django.http import HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils.http import url_has_allowed_host_and_scheme
from django.views.decorators.http import require_POST

from keys import services
from keys.errors import ServiceError
from keys.models import Activation, Product, SigningKey

from . import hosts
from .forms import IssueCodesForm, ProductCreateForm, ProductEditForm

PAGE_SIZE = 50

admin_required = user_passes_test(lambda u: u.is_active and u.is_superuser)


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
    products = Product.objects.annotate(active_count=Count("activations"))
    return render(request, "console/product_list.html", {"products": products})


@admin_required
def product_create(request):
    form = ProductCreateForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        product = form.save()
        return redirect("console:keys", pk=product.pk)
    return render(request, "console/product_form.html", {"form": form, "title": "新建软件"})


@admin_required
def product_edit(request, pk):
    product = get_object_or_404(Product, pk=pk)
    form = ProductEditForm(request.POST or None, instance=product)
    if request.method == "POST" and form.is_valid():
        form.save()
        messages.success(request, "已保存")
        return redirect("console:products")
    return render(
        request, "console/product_form.html", {"form": form, "title": f"编辑 {product}", "product": product}
    )


@admin_required
def key_list(request, pk):
    product = get_object_or_404(Product, pk=pk)
    query = request.GET.get("q", "").strip()
    activations = product.activations.annotate(transfer_count=Count("transfer_logs"))
    if query:
        activations = activations.filter(
            Q(code__icontains=query) | Q(device_info__icontains=query) | Q(device_hash__icontains=query)
        )
    page = Paginator(activations.order_by("-created_at", "-id"), PAGE_SIZE).get_page(request.GET.get("page"))
    return render(
        request,
        "console/key_list.html",
        {
            "product": product,
            "page": page,
            "query": query,
            "issue_form": IssueCodesForm(),
            "signing_keys": product.signing_keys.all(),
        },
    )


@admin_required
@require_POST
def key_issue(request, pk):
    product = get_object_or_404(Product, pk=pk)
    form = IssueCodesForm(request.POST)
    if not form.is_valid():
        messages.error(request, _first_error(form))
        return redirect("console:keys", pk=pk)
    try:
        signing_key, codes = services.issue_codes(
            product, form.cleaned_data["duration_days"], form.cleaned_data["count"]
        )
    except ServiceError as exc:
        messages.error(request, exc.message)
        return redirect("console:keys", pk=pk)
    return render(
        request,
        "console/result.html",
        {"product": product, "title": f"已签发 {len(codes)} 个卡密", "codes": codes, "signing_key": signing_key},
    )


@admin_required
def key_export(request, pk):
    product = get_object_or_404(Product, pk=pk)
    batch = request.GET.get("batch", "")
    if batch:
        signing_keys = [get_object_or_404(SigningKey, key_id=batch, product=product)]
        filename = f"codes-{product.code}-{batch}.csv"
    else:
        signing_keys = product.signing_keys.order_by("created_at", "id")
        filename = f"codes-{product.code}.csv"
    content = services.export_codes_csv(signing_keys)
    response = HttpResponse("\ufeff" + content, content_type="text/csv; charset=utf-8")
    response["Content-Disposition"] = f'attachment; filename="{filename}"'
    return response


@admin_required
def activation_detail(request, pk):
    activation = get_object_or_404(Activation.objects.select_related("product"), pk=pk)
    return render(
        request,
        "console/activation_detail.html",
        {"activation": activation, "product": activation.product, "logs": activation.transfer_logs.all()},
    )


@admin_required
@require_POST
def activation_toggle(request, pk):
    activation = get_object_or_404(Activation, pk=pk)
    activation.disabled = not activation.disabled
    activation.save(update_fields=["disabled"])
    messages.success(request, f"激活记录已{'禁用' if activation.disabled else '取消禁用'}")
    return _safe_next(request, redirect("console:keys", pk=activation.product_id))


@admin_required
def signing_key_list(request, pk):
    product = get_object_or_404(Product, pk=pk)
    return render(
        request, "console/signing_key_list.html", {"product": product, "signing_keys": product.signing_keys.all()}
    )


@admin_required
@require_POST
def signing_key_disable(request, pk):
    signing_key = get_object_or_404(SigningKey, pk=pk)
    if not signing_key.disabled:
        signing_key.disabled = True
        signing_key.save(update_fields=["disabled"])
        messages.success(request, f"批次 {signing_key.key_id} 已禁用")
    return redirect("console:signing_keys", pk=signing_key.product_id)


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
