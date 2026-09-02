"""URL gốc KHBL."""
from django.contrib import admin
from django.contrib.auth import views as auth_views
from django.contrib.auth.decorators import login_not_required
from django.urls import include, path

urlpatterns = [
    path("admin/", admin.site.urls),
    path(
        "dang-nhap/",
        login_not_required(auth_views.LoginView.as_view(template_name="accounts/login.html", redirect_authenticated_user=True)),
        name="login",
    ),
    path("dang-xuat/", auth_views.LogoutView.as_view(), name="logout"),
    path("", include("apps.pmv.urls")),
]
