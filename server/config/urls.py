from django.urls import include, path

from activation import mcp

urlpatterns = [
    path("api/", include("activation.urls")),
    path("mcp", mcp.endpoint, name="mcp"),
    path("", include("console.urls")),
]
