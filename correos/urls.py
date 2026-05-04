from django.urls import path

from . import views

# URLs del portal:
# - "/"           landing pública
# - "/intranet/"  acceso oculto (sin link público en el landing salvo el candadito del footer)
# - "/intranet/bandeja/"        bandeja del usuario logueado
# - "/intranet/correo/<id>/"    detalle de un correo
urlpatterns = [
    path('',                                views.landing_view, name='landing'),
    path('intranet/',                       views.login_view,   name='login'),
    path('intranet/salir/',                 views.logout_view,  name='logout'),
    path('intranet/bandeja/',               views.inbox_view,   name='inbox'),
    path('intranet/correo/<int:correo_id>/', views.detalle_view, name='detalle'),
]
