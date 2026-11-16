from django.urls import include, path


urlpatterns = [
    path("ai/", include("flare_ai.urls")),
    path("", include("lab.urls")),
]
