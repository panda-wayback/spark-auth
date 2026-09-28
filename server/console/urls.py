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
    path("login/", auth_views.LoginView.as_view(template_name="console/login.html"), name="login"),
    path("logout/", auth_views.LogoutView.as_view(), name="logout"),
    path("password/", PasswordChangeView.as_view(), name="password"),
    path("", views.product_list, name="products"),
    path("products/new/", views.product_create, name="product_create"),
    path("products/<int:pk>/", views.key_list, name="keys"),
    path("products/<int:pk>/edit/", views.product_edit, name="product_edit"),
    path("products/<int:pk>/generate/", views.key_generate, name="key_generate"),
    path("products/<int:pk>/import/", views.key_import, name="key_import"),
    path("products/<int:pk>/export/", views.key_export, name="key_export"),
    path("keys/<int:pk>/", views.key_detail, name="key_detail"),
    path("keys/<int:pk>/toggle/", views.key_toggle, name="key_toggle"),
]
