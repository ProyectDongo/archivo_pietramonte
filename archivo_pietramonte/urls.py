from django.conf import settings
from django.contrib import admin
from django.urls import include, path

from taller import admin_views as taller_admin

from . import admin_2fa

# Encabezado del admin (lo que se ve en /admin/)
admin.site.site_header  = 'Pietramonte · Administración'
admin.site.site_title   = 'Pietramonte Admin'
admin.site.index_title  = 'Gestión — Bóveda Pietramonte'

urlpatterns = [
    # 2FA del admin — registrado ANTES de admin.site.urls para que matchee primero.
    path(settings.ADMIN_URL_PATH + '2fa/setup/',             admin_2fa.admin_2fa_setup_view,              name='admin_2fa_setup'),
    path(settings.ADMIN_URL_PATH + '2fa/verify/',            admin_2fa.admin_2fa_verify_view,             name='admin_2fa_verify'),
    path(settings.ADMIN_URL_PATH + '2fa/codigos/',           admin_2fa.admin_2fa_recovery_codes_view,     name='admin_2fa_recovery_codes'),
    path(settings.ADMIN_URL_PATH + '2fa/codigos/pdf/',       admin_2fa.admin_2fa_recovery_pdf_view,       name='admin_2fa_recovery_pdf'),
    path(settings.ADMIN_URL_PATH + '2fa/codigos/confirmar/', admin_2fa.admin_2fa_recovery_confirmar_view, name='admin_2fa_recovery_confirmar'),

    # Panel custom de Agenda (taller). DEBE ir antes que admin.site.urls
    # para que matchee primero. Lo protege Admin2FAMiddleware + permisos.
    path(settings.ADMIN_URL_PATH + 'agenda/',
         taller_admin.panel_agenda_view,
         name='panel_agenda'),
    path(settings.ADMIN_URL_PATH + 'agenda/<int:reserva_id>/confirmar-llamada/',
         taller_admin.confirmar_llamada_view,
         name='confirmar_llamada'),

    # Admin en URL ofuscada (configurable en .env vía ADMIN_URL_PATH).
    # En producción ponerlo detrás de Cloudflare Access además.
    path(settings.ADMIN_URL_PATH, admin.site.urls),

    path('', include('taller.urls')),
    path('', include('correos.urls')),
]
