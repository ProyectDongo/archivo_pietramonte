"""
Context processor que inyecta datos del usuario logueado en cada template
del área autenticada.

  - usuario_actual:    instancia UsuarioPortal o None
  - buzones_visibles:  queryset de buzones que el usuario puede ver
  - buzon_actual:      Buzon actualmente seleccionado (o None)
"""
from .models import Buzon, UsuarioPortal


def portal(request):
    email = request.session.get('usuario_email') if hasattr(request, 'session') else None
    if not email:
        return {}

    try:
        usuario = UsuarioPortal.objects.get(email=email, activo=True)
    except UsuarioPortal.DoesNotExist:
        return {}

    visibles = usuario.buzones_visibles()
    buzon_actual = None
    bid = request.session.get('buzon_actual_id')
    if bid:
        try:
            buzon_actual = visibles.get(id=bid)
        except Buzon.DoesNotExist:
            pass

    return {
        'usuario_actual':    usuario,
        'buzones_visibles':  visibles,
        'buzon_actual':      buzon_actual,
    }
