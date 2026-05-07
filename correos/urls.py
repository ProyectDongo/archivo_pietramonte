from django.urls import path

from . import views

# URLs del portal:
# - "/"           landing pública
# - "/intranet/"  acceso oculto (sin link público en el landing salvo el candadito del header)
# - "/intranet/captcha/"  AJAX para regenerar el captcha
# - "/intranet/salir/"    logout (solo POST)
# - "/intranet/bandeja/"          bandeja del usuario logueado
# - "/intranet/correo/<id>/"      detalle de un correo
urlpatterns = [
    path('healthz',                          views.healthz_view,      name='healthz'),
    path('',                                 views.landing_view,      name='landing'),
    path('intranet/',                        views.login_view,        name='login'),
    path('intranet/captcha/',                views.captcha_regenerar,    name='captcha_regenerar'),
    path('intranet/cambiar-password/',       views.cambiar_password_view, name='cambiar_password'),
    path('intranet/2fa/setup/',              views.setup_2fa_view,        name='setup_2fa'),
    path('intranet/2fa/verify/',             views.verify_2fa_view,       name='verify_2fa'),
    path('intranet/2fa/codigos/',            views.mostrar_recovery_codes_view, name='mostrar_recovery_codes'),
    path('intranet/2fa/codigos/confirmar/',  views.confirmar_recovery_codes_view, name='confirmar_recovery_codes'),
    path('intranet/2fa/codigos/pdf/',        views.descargar_recovery_pdf_view, name='descargar_recovery_pdf'),
    path('intranet/2fa/regenerar/',          views.regenerar_recovery_codes_view, name='regenerar_recovery_codes'),
    path('intranet/buzon/cambiar/',          views.cambiar_buzon_view,    name='cambiar_buzon'),
    path('intranet/salir/',                  views.logout_view,       name='logout'),
    path('intranet/bandeja/',                views.inbox_view,        name='inbox'),
    path('intranet/correo/<int:correo_id>/preview/',     views.correo_preview_view,   name='correo_preview'),
    path('intranet/correo/<int:correo_id>/destacar/',    views.toggle_destacado_view, name='toggle_destacado'),
    path('intranet/correo/<int:correo_id>/leido/',       views.toggle_leido_view,     name='toggle_leido'),
    path('intranet/correo/<int:correo_id>/notas/',       views.actualizar_notas_view, name='actualizar_notas'),
    path('intranet/correo/<int:correo_id>/etiqueta/',    views.asignar_etiqueta_view, name='asignar_etiqueta'),
    path('intranet/correo/<int:correo_id>/reenviar/',    views.reenviar_correo_view,  name='reenviar_correo'),
    path('intranet/correo/<int:correo_id>/responder/',   views.responder_correo_view, name='responder_correo'),
    path('intranet/correo/<int:correo_id>/',             views.detalle_view,          name='detalle'),
    path('intranet/adjunto/<int:adjunto_id>/',           views.adjunto_view,          name='adjunto'),
    path('intranet/buzon/etiqueta-nueva/',               views.crear_etiqueta_view,   name='crear_etiqueta'),
]
