from django.contrib.admin import site as admin_site
from django.urls import include, path


urlpatterns = [
    path("admin/", admin_site.urls),
    path("ai/", include("flare_ai.urls")),
    path("", include("lab.urls")),
]
