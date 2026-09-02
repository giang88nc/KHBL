from django.urls import path

from . import views

app_name = "pmv"

urlpatterns = [
    path("", views.status, name="status"),
]
