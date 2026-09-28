from django.urls import include, path

urlpatterns = [
    path("api/", include("activation.urls")),
    path("", include("console.urls")),
]
