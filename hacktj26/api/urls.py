from django.urls import path
from . import views

urlpatterns = [
    path("images", views.upload_images,    name="upload_images"),
    path("price",  views.analyse_and_price, name="analyse_and_price"),
]
