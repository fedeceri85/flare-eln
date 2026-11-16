from django.urls import path

from . import views


app_name = "flare_ai"

urlpatterns = [
    path("chat/stream/", views.chat_stream, name="chat_stream"),
    path("chat/reset/", views.reset_chat, name="reset_chat"),
]
