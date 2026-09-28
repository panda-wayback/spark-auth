from django.contrib import messages
from django.contrib.auth.decorators import user_passes_test
from django.core.paginator import Paginator
from django.db.models import Count
from django.http import HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.utils.http import url_has_allowed_host_and_scheme
from django.views.decorators.http import require_POST

from keys import services
from keys.errors import ServiceError
from keys.models import LicenseKey, Product

from .forms import GenerateForm, ImportForm, ProductCreateForm, ProductEditForm

PAGE_SIZE = 50

admin_required = user_passes_test(lambda u: u.is_active and u.is_superuser)


def _first_error(form):
    for errors in form.errors.values():
        return errors[0]
    return "输入有误"


@admin_required
def product_list(request):
    products = Product.objects.annotate(key_count=Count("keys"))
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
    keys = (
        product.keys.select_related("activation")
        .annotate(transfer_count=Count("transfer_logs"))
        .order_by("-created_at", "-id")
    )
    if query:
        keys = keys.filter(key__icontains=query)
    page = Paginator(keys, PAGE_SIZE).get_page(request.GET.get("page"))
    return render(
        request,
        "console/key_list.html",
        {
            "product": product,
            "page": page,
            "query": query,
            "generate_form": GenerateForm(),
            "import_form": ImportForm(),
        },
    )


@admin_required
@require_POST
def key_generate(request, pk):
    product = get_object_or_404(Product, pk=pk)
    form = GenerateForm(request.POST)
    if not form.is_valid():
        messages.error(request, _first_error(form))
        return redirect("console:keys", pk=pk)
    try:
        created = services.generate_keys(product, form.cleaned_data["duration_days"], form.cleaned_data["count"])
    except ServiceError as exc:
        messages.error(request, exc.message)
        return redirect("console:keys", pk=pk)
    return render(
        request,
        "console/result.html",
        {"product": product, "title": f"已生成 {len(created)} 个 key", "keys": [k.key for k in created]},
    )


@admin_required
@require_POST
def key_import(request, pk):
    product = get_object_or_404(Product, pk=pk)
    form = ImportForm(request.POST, request.FILES)
    if not form.is_valid():
        messages.error(request, _first_error(form))
        return redirect("console:keys", pk=pk)
    try:
        created = services.import_keys(product, form.cleaned_data["duration_days"], form.cleaned_data["file"].read())
    except ServiceError as exc:
        if exc.code != "IMPORT_DUPLICATE":
            messages.error(request, exc.message)
            return redirect("console:keys", pk=pk)
        return render(
            request,
            "console/result.html",
            {"product": product, "title": exc.message, "error": True, "keys": exc.details},
        )
    messages.success(request, f"已导入 {len(created)} 个 key")
    return redirect("console:keys", pk=pk)


@admin_required
def key_export(request, pk):
    product = get_object_or_404(Product, pk=pk)
    response = HttpResponse("\ufeff" + services.export_keys_csv(product), content_type="text/csv; charset=utf-8")
    response["Content-Disposition"] = f'attachment; filename="keys-{product.code}.csv"'
    return response


@admin_required
def key_detail(request, pk):
    lic = get_object_or_404(LicenseKey.objects.select_related("product", "activation"), pk=pk)
    return render(
        request,
        "console/key_detail.html",
        {"lic": lic, "product": lic.product, "logs": lic.transfer_logs.all()},
    )


@admin_required
@require_POST
def key_toggle(request, pk):
    lic = get_object_or_404(LicenseKey, pk=pk)
    lic.disabled = not lic.disabled
    lic.save(update_fields=["disabled"])
    messages.success(request, f"{lic.key} 已{'禁用' if lic.disabled else '取消禁用'}")
    next_url = request.POST.get("next", "")
    if url_has_allowed_host_and_scheme(next_url, allowed_hosts={request.get_host()}):
        return redirect(next_url)
    return redirect("console:keys", pk=lic.product_id)
