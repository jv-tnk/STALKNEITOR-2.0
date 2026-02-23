from django.contrib import admin
from django.conf import settings
from django.urls import path, include

_url_prefix = (getattr(settings, 'URL_PREFIX', '') or '').strip('/')

if _url_prefix:
    urlpatterns = [
        path(f'{_url_prefix}/admin/', admin.site.urls),
        path(f'{_url_prefix}/', include('core.urls')),
    ]
else:
    urlpatterns = [
        path('admin/', admin.site.urls),
        path('', include('core.urls')),
    ]
