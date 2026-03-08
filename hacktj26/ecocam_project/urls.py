from django.conf import settings
from django.conf.urls.static import static
from django.urls import path, include
from api.views import page_home, page_product, page_about

urlpatterns = [
    path("",         page_home,    name="home"),
    path("product/", page_product, name="product"),
    path("about/",   page_about,   name="about"),
    path("api/",     include("api.urls")),
] + static(settings.MEDIA_URL,  document_root=settings.MEDIA_ROOT) \
  + static(settings.STATIC_URL, document_root=settings.STATICFILES_DIRS[0])
