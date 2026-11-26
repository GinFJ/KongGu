from django.urls import path
from storage import views

urlpatterns = [
    path("v1/register", views.register),
    path("v1/login", views.login),
    path("v1/logout", views.logout),
    path("v1/batches", views.batches),
    path("v1/batches/<uuid:batch_id>", views.batch),
]
