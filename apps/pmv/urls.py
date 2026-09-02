from django.urls import path

from . import views

app_name = "pmv"

urlpatterns = [
    path("", views.status, name="status"),
    path("so-sanh/", views.diff_view, name="diff"),
    path("so-sanh/chup/", views.snapshot_create, name="snapshot_create"),
    path("so-sanh/sync/", views.sync_now, name="sync_now"),
    path("hanh-vi/", views.behavior_view, name="behavior"),
    path("hanh-vi/trace/<str:action>/", views.trace_toggle, name="trace_toggle"),
    path("hanh-vi/thu-thap/", views.collect_now, name="collect_now"),
    path("so-sanh/xoa/<int:pk>/", views.snapshot_delete, name="snapshot_delete"),
]
