from django.conf import settings
from django.contrib import admin
from django.urls import include, path

# Encabezado del admin (lo que se ve en /admin/)
admin.site.site_header  = 'Pietramonte · Administración'
admin.site.site_title   = 'Pietramonte Admin'
admin.site.index_title  = 'Gestión del archivo de correos'

urlpatterns = [
    # Admin en URL ofuscada (configurable en .env vía ADMIN_URL_PATH).
    # En producción ponerlo detrás de Cloudflare Access además.
    path(settings.ADMIN_URL_PATH, admin.site.urls),

    path('', include('correos.urls')),
]
