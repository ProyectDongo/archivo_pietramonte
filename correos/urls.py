from django.urls import path
from . import views

urlpatterns = [
    path('',              views.landing_view,  name='landing'),
    path('acceso/',       views.login_view,    name='login'),
    path('salir/',        views.logout_view,   name='logout'),
    path('bandeja/',      views.inbox_view,    name='inbox'),
    path('correo/<int:correo_id>/', views.detalle_view, name='detalle'),
]