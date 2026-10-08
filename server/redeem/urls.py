from django.urls import path

from . import views

urlpatterns = [
    path("redeem/status", views.redeem_status, name="redeem_status"),
    path("redeem", views.redeem, name="redeem"),
]
