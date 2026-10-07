from django.urls import include, path

from activation import mcp

from .health import healthz

urlpatterns = [
    path("healthz", healthz, name="healthz"),
    path("api/", include("activation.urls")),
    path("api/", include("redeem.urls")),
    path("mcp", mcp.endpoint, name="mcp"),
    path("", include("console.urls")),
]
