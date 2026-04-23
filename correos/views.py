from django.shortcuts import render, redirect, get_object_or_404
from django.contrib import messages
from django.core.paginator import Paginator
from django.db.models import Q
from .models import Buzon, Correo

EMAILS_VALIDOS = [
    'aledezma@pietramonte.cl',
    'cobranza@pietramonte.cl',
    'contacto@pietramonte.cl',
    'cpietrasanta@pietramonte.cl',
    'vpietrasanta@pietramonte.cl',
    'ralbornoz@pietramonte.cl',
]


def login_view(request):
    if request.session.get('buzon_email'):
        return redirect('inbox')

    if request.method == 'POST':
        email_ingresado = request.POST.get('email', '').strip().lower()

        if email_ingresado not in EMAILS_VALIDOS:
            messages.error(request, 'Esta dirección no tiene acceso al archivo.')
            return render(request, 'correos/login.html')

        try:
            buzon = Buzon.objects.get(email=email_ingresado)
        except Buzon.DoesNotExist:
            messages.error(request, 'Tu buzón aún no ha sido importado. Contacta al administrador.')
            return render(request, 'correos/login.html')

        request.session['buzon_email'] = buzon.email
        request.session['buzon_id'] = buzon.id
        return redirect('inbox')

    return render(request, 'correos/login.html')


def logout_view(request):
    request.session.flush()
    return redirect('login')


def inbox_view(request):
    email = request.session.get('buzon_email')
    if not email:
        return redirect('login')

    buzon = get_object_or_404(Buzon, email=email)
    correos_qs = buzon.correos.all().order_by('-fecha')

    query = request.GET.get('q', '').strip()
    if query:
        correos_qs = correos_qs.filter(
            Q(asunto__icontains=query) |
            Q(remitente__icontains=query) |
            Q(cuerpo_texto__icontains=query)
        )

    paginator = Paginator(correos_qs, 50)
    page_number = request.GET.get('page', 1)
    page = paginator.get_page(page_number)

    return render(request, 'correos/inbox.html', {
        'buzon': buzon,
        'page': page,
        'query': query,
        'total': correos_qs.count(),
    })


def detalle_view(request, correo_id):
    email = request.session.get('buzon_email')
    if not email:
        return redirect('login')

    buzon = get_object_or_404(Buzon, email=email)
    correo = get_object_or_404(Correo, id=correo_id, buzon=buzon)

    return render(request, 'correos/detalle.html', {
        'buzon': buzon,
        'correo': correo,
    })


def landing_view(request):
    if request.session.get('buzon_email'):
        return redirect('inbox')
    return render(request, 'correos/landing.html')
