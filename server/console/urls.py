from django.contrib.auth import views as auth_views
from django.contrib.messages.views import SuccessMessageMixin
from django.urls import path, reverse_lazy

from . import views

app_name = "console"


class PasswordChangeView(SuccessMessageMixin, auth_views.PasswordChangeView):
    template_name = "console/password_change.html"
    success_url = reverse_lazy("console:products")
    success_message = "密码已修改"


urlpatterns = [
    path("setup/", views.setup, name="setup"),
    path("login/", auth_views.LoginView.as_view(template_name="console/login.html"), name="login"),
    path("logout/", auth_views.LogoutView.as_view(), name="logout"),
    path("password/", PasswordChangeView.as_view(), name="password"),
    path("", views.product_list, name="products"),
    path("products/new/", views.product_create, name="product_create"),
    path("products/<int:pk>/", views.key_list, name="keys"),
    path("products/<int:pk>/edit/", views.product_edit, name="product_edit"),
    path("products/<int:pk>/issue/", views.key_issue, name="key_issue"),
    path("products/<int:pk>/export/", views.key_export, name="key_export"),
    path("products/<int:pk>/batches/", views.batch_list, name="batches"),
    path("activations/<int:pk>/", views.activation_detail, name="activation_detail"),
    path("activations/<int:pk>/toggle/", views.activation_toggle, name="activation_toggle"),
    path("batches/<str:batch_id>/disable/", views.batch_disable, name="batch_disable"),
    path("mcp-setup/", views.mcp_setup, name="mcp_setup"),
]
