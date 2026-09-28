from django.urls import path

from . import views

urlpatterns = [
    path("activate", views.activate, name="activate"),
    path("verify", views.verify, name="verify"),
]
