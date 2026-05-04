from functools import wraps

from django.conf import settings
from django.contrib import messages
from django.core.paginator import Paginator
from django.db.models import Q
from django.shortcuts import render, redirect, get_object_or_404
from django.views.decorators.cache import never_cache
from django.views.decorators.http import require_http_methods

from .models import Buzon, Correo


# ─── Decoradores ───────────────────────────────────────────────────────────
def portal_login_required(view):
    """Redirige a login si no hay sesión activa."""
    @wraps(view)
    def wrapper(request, *args, **kwargs):
        if not request.session.get('buzon_email'):
            return redirect('login')
        return view(request, *args, **kwargs)
    return wrapper


def _allowed_emails() -> set[str]:
    """Set normalizado de emails autorizados a entrar al portal."""
    return {e.lower().strip() for e in settings.PORTAL_ALLOWED_EMAILS}


# ─── Vistas públicas ───────────────────────────────────────────────────────
def landing_view(request):
    if request.session.get('buzon_email'):
        return redirect('inbox')
    return render(request, 'correos/landing.html')


# ─── Autenticación ─────────────────────────────────────────────────────────
@never_cache
@require_http_methods(['GET', 'POST'])
def login_view(request):
    if request.session.get('buzon_email'):
        return redirect('inbox')

    if request.method == 'POST':
        email_ingresado = (request.POST.get('email') or '').strip().lower()

        # Validación básica de longitud para evitar payloads grandes
        if not email_ingresado or len(email_ingresado) > 254:
            messages.error(request, 'Correo inválido.')
            return render(request, 'correos/login.html', status=400)

        if email_ingresado not in _allowed_emails():
            # Mensaje genérico — no revelar si existe o no
            messages.error(request, 'Esta dirección no tiene acceso al archivo.')
            return render(request, 'correos/login.html', status=403)

        try:
            buzon = Buzon.objects.get(email=email_ingresado)
        except Buzon.DoesNotExist:
            messages.error(request, 'Tu buzón aún no ha sido importado. Contacta al administrador.')
            return render(request, 'correos/login.html', status=404)

        # Rotar la sesión al autenticar (mitiga session fixation)
        request.session.cycle_key()
        request.session['buzon_email'] = buzon.email
        request.session['buzon_id'] = buzon.id
        return redirect('inbox')

    return render(request, 'correos/login.html')


def logout_view(request):
    request.session.flush()
    return redirect('landing')


# ─── Vistas autenticadas ───────────────────────────────────────────────────
@portal_login_required
@never_cache
def inbox_view(request):
    email = request.session['buzon_email']
    buzon = get_object_or_404(Buzon, email=email)
    correos_qs = buzon.correos.all().order_by('-fecha')

    query = (request.GET.get('q') or '').strip()[:200]   # cap defensivo
    if query:
        correos_qs = correos_qs.filter(
            Q(asunto__icontains=query) |
            Q(remitente__icontains=query) |
            Q(cuerpo_texto__icontains=query)
        )

    paginator = Paginator(correos_qs, 50)
    page = paginator.get_page(request.GET.get('page', 1))

    return render(request, 'correos/inbox.html', {
        'buzon': buzon,
        'page': page,
        'query': query,
        'total': paginator.count,
    })


@portal_login_required
@never_cache
def detalle_view(request, correo_id):
    email = request.session['buzon_email']
    buzon = get_object_or_404(Buzon, email=email)
    correo = get_object_or_404(Correo, id=correo_id, buzon=buzon)

    return render(request, 'correos/detalle.html', {
        'buzon': buzon,
        'correo': correo,
    })
