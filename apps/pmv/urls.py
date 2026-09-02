from django.urls import path

from . import views

app_name = "pmv"

urlpatterns = [
    path("", views.status, name="status"),
    path("so-sanh/", views.diff_view, name="diff"),
    path("so-sanh/chup/", views.snapshot_create, name="snapshot_create"),
    path("so-sanh/xoa/<int:pk>/", views.snapshot_delete, name="snapshot_delete"),
]
