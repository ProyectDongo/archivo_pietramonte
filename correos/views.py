import hmac
import mimetypes
import time
from datetime import timedelta
from functools import wraps

from django.conf import settings
from django.contrib import messages
from django.core.cache import cache
from django.core.paginator import Paginator
from django.db.models import Count, Q
from django.db.models.functions import TruncMonth
from django.http import FileResponse, Http404, HttpResponseBadRequest, JsonResponse
from django.shortcuts import render, redirect, get_object_or_404
from django.utils import timezone
from django.views.decorators.cache import never_cache
from django.views.decorators.http import require_http_methods, require_POST

from . import captcha
from .models import Adjunto, Buzon, Correo, Etiqueta, IntentoLogin, UsuarioPortal, hash_ip


# ─── Helpers ───────────────────────────────────────────────────────────────
def _get_ip(request) -> str:
    """Toma la IP real considerando que Cloudflare/Tunnel mete X-Forwarded-For."""
    fwd = request.META.get('HTTP_X_FORWARDED_FOR', '')
    if fwd:
        return fwd.split(',')[0].strip()
    return request.META.get('REMOTE_ADDR', '')


def _ua(request) -> str:
    return (request.META.get('HTTP_USER_AGENT') or '')[:500]


def _allowed_emails() -> set[str]:
    return {e.lower().strip() for e in settings.PORTAL_ALLOWED_EMAILS}


def _safe_email_eq(a: str, b: str) -> bool:
    """Comparación de emails resistente a timing attacks (paranoia)."""
    return hmac.compare_digest(a.lower().strip(), b.lower().strip())


def portal_login_required(view):
    @wraps(view)
    def wrapper(request, *args, **kwargs):
        if not request.session.get('usuario_email'):
            return redirect('login')
        return view(request, *args, **kwargs)
    return wrapper


# ─── Helpers de sesión multi-buzón ─────────────────────────────────────────
def _usuario_actual(request) -> UsuarioPortal | None:
    """Devuelve el UsuarioPortal de la sesión o None si la sesión es inválida."""
    email = request.session.get('usuario_email')
    if not email:
        return None
    try:
        return UsuarioPortal.objects.get(email=email, activo=True)
    except UsuarioPortal.DoesNotExist:
        request.session.flush()
        return None


def _buzon_actual(request, usuario: UsuarioPortal) -> Buzon | None:
    """
    Devuelve el Buzón "actualmente seleccionado" para este usuario.
    Si la sesión apunta a uno al que ya no tiene acceso, cae en el primero
    visible. Si no tiene ninguno visible, devuelve None.
    """
    visibles = usuario.buzones_visibles()
    buzon_id = request.session.get('buzon_actual_id')

    if buzon_id:
        try:
            return visibles.get(id=buzon_id)
        except Buzon.DoesNotExist:
            pass     # Lost access → fallback

    # Toma el primero disponible y lo deja como activo
    primero = visibles.first()
    if primero:
        request.session['buzon_actual_id'] = primero.id
        request.session['buzon_actual_email'] = primero.email
    return primero


# ─── Rate limiting (por IP, contra el cache de Django) ─────────────────────
RL_VENTANA_SEG = 15 * 60      # 15 minutos
RL_MAX_FALLOS  = 5            # tras 5 fallos, bloquea


def _rl_key(ip_h: str) -> str:
    return f'rl:login:{ip_h}'


def _rl_intento(ip_h: str, exito: bool):
    """Reinicia el contador en éxito; suma 1 en fallo."""
    if exito:
        cache.delete(_rl_key(ip_h))
        return
    n = cache.get(_rl_key(ip_h), 0) + 1
    cache.set(_rl_key(ip_h), n, RL_VENTANA_SEG)


def _rl_bloqueado(ip_h: str) -> bool:
    return cache.get(_rl_key(ip_h), 0) >= RL_MAX_FALLOS


# ─── Logging de intentos (para ML futuro) ──────────────────────────────────
def _log_intento(request, ip_h: str, email: str, motivo: str, exito: bool,
                 tiempo_ms: int = 0, captcha_cat: str = '', honeypot: bool = False):
    try:
        IntentoLogin.objects.create(
            ip_hash=ip_h,
            user_agent=_ua(request),
            email_intentado=email[:254],
            captcha_categoria=captcha_cat[:30],
            tiempo_ms=max(0, min(tiempo_ms, 10**8)),
            honeypot_lleno=honeypot,
            exito=exito,
            motivo=motivo,
        )
    except Exception:
        # Nunca bloquear el flujo de login por un fallo de logging
        pass


# ─── Vistas públicas ───────────────────────────────────────────────────────
def landing_view(request):
    if request.session.get('usuario_email'):
        return redirect('inbox')
    return render(request, 'correos/landing.html')


def healthz_view(request):
    """
    Healthcheck para Coolify / Cloudflare / monitoring externos.
    Responde 200 'ok' sin tocar BD para que sea rapidísimo y no
    consuma recursos en cada chequeo.
    """
    from django.http import HttpResponse
    return HttpResponse('ok', content_type='text/plain')


# ─── Login ─────────────────────────────────────────────────────────────────
ERROR_GENERICO = 'No fue posible iniciar sesión. Verifica tus datos e intenta de nuevo.'


@never_cache
@require_http_methods(['GET', 'POST'])
def login_view(request):
    if request.session.get('usuario_email'):
        return redirect('inbox')

    ip_h = hash_ip(_get_ip(request))

    # ─── Rate limit a nivel app (la otra capa la pone Cloudflare) ─────────
    if _rl_bloqueado(ip_h):
        _log_intento(request, ip_h, '', motivo='throttled', exito=False)
        messages.error(request, 'Demasiados intentos. Espera unos minutos antes de volver a intentar.')
        return render(request, 'correos/login.html', {
            'challenge': captcha.generar_challenge(),
            'page_loaded_at': int(time.time() * 1000),
        }, status=429)

    if request.method == 'GET':
        return render(request, 'correos/login.html', {
            'challenge': captcha.generar_challenge(),
            'page_loaded_at': int(time.time() * 1000),
        })

    # ─── POST ─────────────────────────────────────────────────────────────
    email = (request.POST.get('email') or '').strip().lower()
    password = request.POST.get('password') or ''
    honeypot = (request.POST.get('website') or '').strip()      # campo trampa
    page_loaded_at = request.POST.get('page_loaded_at') or '0'
    captcha_token = request.POST.get('captcha_token') or ''
    captcha_seleccion = request.POST.getlist('captcha_seleccion[]')

    try:
        tiempo_ms = max(0, int(time.time() * 1000) - int(page_loaded_at))
    except (TypeError, ValueError):
        tiempo_ms = 0

    # Función helper para fallar con respuesta UNIFORME (anti-enumeración)
    def fallo(motivo: str, captcha_cat: str = ''):
        _rl_intento(ip_h, exito=False)
        _log_intento(request, ip_h, email, motivo=motivo, exito=False,
                     tiempo_ms=tiempo_ms, captcha_cat=captcha_cat,
                     honeypot=bool(honeypot))
        messages.error(request, ERROR_GENERICO)
        return render(request, 'correos/login.html', {
            'challenge': captcha.generar_challenge(),
            'page_loaded_at': int(time.time() * 1000),
            'last_email': email[:254],
        }, status=400)

    # 1. Honeypot — bots tienden a rellenar TODO. Humanos no ven el campo.
    if honeypot:
        return fallo('honeypot')

    # 2. Tiempo mínimo razonable de un humano (esquiva submit instantáneo).
    if tiempo_ms < captcha.MIN_TIEMPO_HUMANO_MS:
        return fallo('muy_rapido')

    # 3. Validación básica de email + presencia de password.
    if not email or '@' not in email or len(email) > 254 or not password:
        return fallo('email_invalido')

    # 4. Captcha (firma HMAC + match).
    try:
        cat = captcha.verificar(captcha_token, captcha_seleccion)
    except captcha.CaptchaError:
        return fallo('captcha_fail')

    # 5. Usuario existe + activo + password correcto.
    #    Hacemos check_password contra un hash dummy si el usuario no existe
    #    para que el tiempo de respuesta sea similar (anti-timing-enumeration).
    try:
        usuario = UsuarioPortal.objects.get(email=email)
        if not usuario.activo:
            # Igual hace check_password (timing-safe), pero falla con motivo correcto
            usuario.check_password(password)
            return fallo('usuario_inactivo', captcha_cat=cat)
        if not usuario.check_password(password):
            return fallo('password_invalida', captcha_cat=cat)
    except UsuarioPortal.DoesNotExist:
        # Run check_password on a known hash for timing parity
        UsuarioPortal(password_hash='pbkdf2_sha256$600000$dummy$dummy').check_password(password)
        return fallo('email_no_lista', captcha_cat=cat)

    # 6. Tiene al menos un buzón visible (o es admin → ve todos).
    primer_buzon = usuario.buzones_visibles().first()
    if primer_buzon is None:
        return fallo('buzon_inexist', captcha_cat=cat)

    # ─── Éxito ────────────────────────────────────────────────────────────
    usuario.ultimo_login = timezone.now()
    usuario.save(update_fields=['ultimo_login'])
    request.session.cycle_key()
    request.session['usuario_email']      = usuario.email
    request.session['usuario_es_admin']   = usuario.es_admin
    request.session['buzon_actual_id']    = primer_buzon.id
    request.session['buzon_actual_email'] = primer_buzon.email
    _rl_intento(ip_h, exito=True)
    _log_intento(request, ip_h, email, motivo='exito', exito=True,
                 tiempo_ms=tiempo_ms, captcha_cat=cat)
    return redirect('inbox')


@require_POST
def logout_view(request):
    """Logout solo por POST (anti-CSRF: nadie puede desloguearte vía <img>)."""
    request.session.flush()
    return redirect('landing')


@require_http_methods(['GET'])
def captcha_regenerar(request):
    """Endpoint AJAX para refrescar el challenge sin recargar la página."""
    return JsonResponse(captcha.generar_challenge())


# ─── Vistas autenticadas ───────────────────────────────────────────────────
def _stats_de(buzon: Buzon) -> dict:
    """
    Calcula métricas para el header del inbox (queries optimizadas, una sola pasada).
    """
    qs = buzon.correos.all()
    ahora = timezone.now()
    hace_30d = ahora - timedelta(days=30)
    hace_6m  = ahora - timedelta(days=183)

    total = qs.count()

    # Top 5 remitentes (por dominio o nombre completo, lo más frecuente)
    top = (qs.exclude(remitente='')
             .values('remitente')
             .annotate(n=Count('id'))
             .order_by('-n')[:5])
    top_remitentes = [
        {'remitente': r['remitente'][:60], 'n': r['n']}
        for r in top
    ]

    # Actividad mensual últimos 6 meses (para mini-gráfico)
    mensual = (qs.filter(fecha__gte=hace_6m)
                 .annotate(mes=TruncMonth('fecha'))
                 .values('mes')
                 .annotate(n=Count('id'))
                 .order_by('mes'))
    chart = [(m['mes'], m['n']) for m in mensual if m['mes']]
    chart_max = max((c[1] for c in chart), default=1)

    return {
        'total':             total,
        'recientes_30d':     qs.filter(fecha__gte=hace_30d).count(),
        'con_adjuntos':      qs.filter(tiene_adjunto=True).count(),
        'fecha_mas_reciente': qs.order_by('-fecha').values_list('fecha', flat=True).first(),
        'fecha_mas_antigua':  qs.exclude(fecha__isnull=True).order_by('fecha').values_list('fecha', flat=True).first(),
        'top_remitentes':    top_remitentes,
        'chart':             chart,
        'chart_max':         chart_max,
    }


@portal_login_required
@never_cache
def inbox_view(request):
    usuario = _usuario_actual(request)
    if not usuario:
        return redirect('login')
    buzon = _buzon_actual(request, usuario)
    if not buzon:
        request.session.flush()
        messages.error(request, 'No tienes buzones asignados. Contacta al administrador.')
        return redirect('login')

    correos_qs = buzon.correos.all().prefetch_related('etiquetas').order_by('-fecha')

    # ─── Filtros ─────────────────────────────────────────────────────────
    query = (request.GET.get('q') or '').strip()[:200]
    if query:
        correos_qs = correos_qs.filter(
            Q(asunto__icontains=query) |
            Q(remitente__icontains=query) |
            Q(cuerpo_texto__icontains=query)
        )

    solo_destacados = request.GET.get('destacado') == '1'
    if solo_destacados:
        correos_qs = correos_qs.filter(destacado=True)

    etiqueta_actual = None
    try:
        etiqueta_id = int(request.GET.get('etiqueta') or 0)
        if etiqueta_id:
            etiqueta_actual = buzon.etiquetas.get(id=etiqueta_id)
            correos_qs = correos_qs.filter(etiquetas=etiqueta_actual)
    except (ValueError, Etiqueta.DoesNotExist):
        pass

    paginator = Paginator(correos_qs, 50)
    page = paginator.get_page(request.GET.get('page', 1))

    hay_filtros_activos = bool(query or solo_destacados or etiqueta_actual)

    return render(request, 'correos/inbox.html', {
        'buzon': buzon,
        'page': page,
        'query': query,
        'total': paginator.count,
        'stats': _stats_de(buzon) if not hay_filtros_activos else None,
        'buzones_visibles': usuario.buzones_visibles(),
        'etiquetas_disponibles': buzon.etiquetas.all().order_by('nombre'),
        'etiqueta_actual': etiqueta_actual,
        'solo_destacados': solo_destacados,
        'cant_destacados': buzon.correos.filter(destacado=True).count(),
        'hay_filtros_activos': hay_filtros_activos,
    })


@portal_login_required
@never_cache
def detalle_view(request, correo_id):
    usuario = _usuario_actual(request)
    if not usuario:
        return redirect('login')
    correo = get_object_or_404(Correo, id=correo_id)
    if not usuario.puede_ver(correo.buzon):
        raise Http404
    # Si el usuario abrió un correo de OTRO buzón al que tiene acceso,
    # cambia la "vista actual" a ese buzón
    if correo.buzon_id != request.session.get('buzon_actual_id'):
        request.session['buzon_actual_id']    = correo.buzon.id
        request.session['buzon_actual_email'] = correo.buzon.email

    return render(request, 'correos/detalle.html', {
        'buzon': correo.buzon,
        'correo': correo,
        'buzones_visibles': usuario.buzones_visibles(),
    })


@portal_login_required
@require_POST
def cambiar_buzon_view(request):
    """
    Cambia el buzón "actualmente seleccionado" del usuario.
    Verifica que tenga acceso. POST-only con CSRF (no se puede gatillar via <img>).
    """
    usuario = _usuario_actual(request)
    if not usuario:
        return redirect('login')

    try:
        buzon_id = int(request.POST.get('buzon_id') or 0)
    except (TypeError, ValueError):
        return HttpResponseBadRequest('buzon_id inválido')

    try:
        buzon = usuario.buzones_visibles().get(id=buzon_id)
    except Buzon.DoesNotExist:
        raise Http404

    request.session['buzon_actual_id']    = buzon.id
    request.session['buzon_actual_email'] = buzon.email
    return redirect('inbox')


@portal_login_required
@never_cache
@require_http_methods(['GET', 'POST'])
def cambiar_password_view(request):
    """
    Permite al usuario logueado cambiar su propia contraseña.
    Requiere conocer la actual + cumplir AUTH_PASSWORD_VALIDATORS.
    """
    from django.contrib.auth.password_validation import validate_password
    from django.core.exceptions import ValidationError as DjValError

    usuario = _usuario_actual(request)
    if not usuario:
        return redirect('login')

    if request.method == 'GET':
        return render(request, 'correos/cambiar_password.html')

    actual = request.POST.get('actual') or ''
    nueva  = request.POST.get('nueva') or ''
    nueva2 = request.POST.get('nueva2') or ''

    errores = []

    if not usuario.check_password(actual):
        errores.append('La contraseña actual no es correcta.')
    if nueva != nueva2:
        errores.append('Las contraseñas nuevas no coinciden.')
    if nueva == actual and nueva:
        errores.append('La nueva contraseña debe ser distinta de la actual.')

    if not errores:
        try:
            validate_password(nueva, user=usuario)
        except DjValError as e:
            errores.extend(e.messages)

    if errores:
        for err in errores:
            messages.error(request, err)
        return render(request, 'correos/cambiar_password.html', status=400)

    usuario.set_password(nueva)
    usuario.save(update_fields=['password_hash'])
    # Rotar sesión por buenas prácticas tras cambio sensible
    request.session.cycle_key()
    messages.success(request, 'Contraseña actualizada correctamente.')
    return redirect('inbox')


@portal_login_required
@never_cache
def adjunto_view(request, adjunto_id):
    """
    Sirve un adjunto al usuario logueado, SOLO si pertenece a un correo
    de uno de SUS buzones visibles.
    """
    usuario = _usuario_actual(request)
    if not usuario:
        return redirect('login')

    adjunto = get_object_or_404(Adjunto, id=adjunto_id)
    if not usuario.puede_ver(adjunto.correo.buzon):
        # 404 (no 403) para no filtrar existencia de adjuntos ajenos
        raise Http404

    try:
        f = adjunto.archivo.open('rb')
    except FileNotFoundError:
        raise Http404('Archivo no encontrado en disco')

    # Inline solo para tipos seguros (PDF, imágenes). El resto fuerza descarga
    # para evitar que un .html adjunto se ejecute como página servida desde
    # nuestro propio dominio (XSS).
    disposition = 'inline' if adjunto.es_seguro_inline else 'attachment'

    response = FileResponse(
        f,
        content_type=adjunto.mime_type or 'application/octet-stream',
        as_attachment=(disposition == 'attachment'),
        filename=adjunto.nombre_original,
    )
    # Refuerza nosniff y no permitir que se sirva en frames de terceros
    response['X-Content-Type-Options'] = 'nosniff'
    response['Content-Security-Policy'] = "default-src 'none'; sandbox"
    return response


@portal_login_required
@never_cache
def correo_preview_view(request, correo_id):
    """
    Devuelve el fragment HTML del cuerpo del correo, para inyectar en el panel
    derecho del split view del inbox vía fetch().
    """
    if not request.headers.get('X-Requested-With') == 'fetch':
        return redirect('detalle', correo_id=correo_id)

    usuario = _usuario_actual(request)
    if not usuario:
        return redirect('login')

    correo = get_object_or_404(Correo, id=correo_id)
    if not usuario.puede_ver(correo.buzon):
        raise Http404

    return render(request, 'correos/_correo_preview.html', {'correo': correo})


# ─── AJAX: organización del archivo ────────────────────────────────────────
def _correo_si_visible(request, correo_id):
    """Helper común: devuelve (usuario, correo) o levanta Http404 si no aplica."""
    usuario = _usuario_actual(request)
    if not usuario:
        raise Http404
    correo = get_object_or_404(Correo, id=correo_id)
    if not usuario.puede_ver(correo.buzon):
        raise Http404
    return usuario, correo


@portal_login_required
@require_POST
def toggle_destacado_view(request, correo_id):
    """POST → invierte el flag destacado del correo. Devuelve JSON."""
    _, correo = _correo_si_visible(request, correo_id)
    correo.destacado = not correo.destacado
    correo.save(update_fields=['destacado'])
    return JsonResponse({'destacado': correo.destacado})


@portal_login_required
@require_POST
def actualizar_notas_view(request, correo_id):
    """POST notas=... → guarda las notas (max 5000)."""
    _, correo = _correo_si_visible(request, correo_id)
    notas = (request.POST.get('notas') or '')[:5000]
    correo.notas = notas
    correo.save(update_fields=['notas'])
    return JsonResponse({'ok': True, 'notas': notas})


@portal_login_required
@require_POST
def asignar_etiqueta_view(request, correo_id):
    """
    POST etiqueta_id → asigna la etiqueta al correo.
    POST etiqueta_id + accion=quitar → la quita.
    La etiqueta debe pertenecer al MISMO buzón del correo.
    """
    _, correo = _correo_si_visible(request, correo_id)
    try:
        etiqueta_id = int(request.POST.get('etiqueta_id') or 0)
    except (TypeError, ValueError):
        return HttpResponseBadRequest('etiqueta_id inválido')
    accion = request.POST.get('accion', 'asignar')

    try:
        etiqueta = correo.buzon.etiquetas.get(id=etiqueta_id)
    except Etiqueta.DoesNotExist:
        raise Http404

    if accion == 'quitar':
        correo.etiquetas.remove(etiqueta)
        asignada = False
    else:
        correo.etiquetas.add(etiqueta)
        asignada = True

    return JsonResponse({
        'asignada': asignada,
        'etiqueta': {'id': etiqueta.id, 'nombre': etiqueta.nombre, 'color': etiqueta.color},
    })


@portal_login_required
@require_POST
def crear_etiqueta_view(request):
    """
    POST nombre=... color=... → crea una etiqueta nueva en el buzón actual
    (el usuario debe tener acceso a ese buzón).
    """
    usuario = _usuario_actual(request)
    if not usuario:
        return redirect('login')

    buzon = _buzon_actual(request, usuario)
    if not buzon:
        raise Http404

    nombre = (request.POST.get('nombre') or '').strip()[:40]
    color  = (request.POST.get('color') or '#C80C0F').strip()
    if not nombre:
        return HttpResponseBadRequest('nombre requerido')

    # color debe estar en la paleta válida
    paleta_valida = {c for c, _ in Etiqueta.PALETA}
    if color not in paleta_valida:
        color = '#C80C0F'

    etiqueta, creada = Etiqueta.objects.get_or_create(
        buzon=buzon, nombre=nombre,
        defaults={'color': color},
    )
    if not creada and etiqueta.color != color:
        etiqueta.color = color
        etiqueta.save(update_fields=['color'])

    return JsonResponse({
        'creada': creada,
        'etiqueta': {'id': etiqueta.id, 'nombre': etiqueta.nombre, 'color': etiqueta.color},
    })
