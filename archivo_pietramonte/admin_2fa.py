"""
2FA para el admin de Django (auth.User → AdminTOTP).

Tres endpoints:
  - /admin-prefix/2fa/setup/       primer setup, muestra QR
  - /admin-prefix/2fa/verify/      challenge en cada login
  - /admin-prefix/2fa/codigos/     muestra los recovery codes una sola vez

Y `Admin2FAMiddleware` que intercepta cualquier acceso al admin si la
sesión no tiene `admin_2fa_ok=True`.
"""
from __future__ import annotations

import time

from django.conf import settings
from django.contrib import messages
from django.contrib.admin.views.decorators import staff_member_required
from django.http import HttpResponse
from django.shortcuts import redirect, render
from django.utils import timezone
from django.views.decorators.cache import never_cache
from django.views.decorators.http import require_http_methods, require_POST

from correos import totp as totp_helpers
from correos.models import AdminTOTP, IntentoLogin, hash_ip


# Tras setup: ventana de 30 min para descargar PDF / imprimir / confirmar.
RECOVERY_DISPLAY_TTL = 30 * 60


def _ip_real(request) -> str:
    fwd = request.META.get('HTTP_X_FORWARDED_FOR', '')
    if fwd:
        return fwd.split(',')[0].strip()
    return request.META.get('REMOTE_ADDR', '')


def _admin_index_url() -> str:
    return '/' + settings.ADMIN_URL_PATH


def _get_or_create_totp(user) -> AdminTOTP:
    obj, _ = AdminTOTP.objects.get_or_create(user=user)
    return obj


def _identidad(user) -> str:
    return user.email or user.username


def _log(request, user, motivo: str, exito: bool) -> None:
    try:
        IntentoLogin.objects.create(
            ip_hash=hash_ip(_ip_real(request)),
            user_agent=(request.META.get('HTTP_USER_AGENT') or '')[:500],
            email_intentado=_identidad(user)[:254],
            motivo=motivo,
            exito=exito,
        )
    except Exception:
        pass


# ─── Setup ─────────────────────────────────────────────────────────────────
@never_cache
@require_http_methods(['GET', 'POST'])
@staff_member_required
def admin_2fa_setup_view(request):
    totp_obj = _get_or_create_totp(request.user)
    if totp_obj.totp_activo:
        return redirect('admin_2fa_verify')

    secret = request.session.get('admin_setup_secret')
    if not secret:
        secret = totp_helpers.generar_secret()
        request.session['admin_setup_secret'] = secret

    if request.method == 'POST':
        codigo = request.POST.get('codigo') or ''
        if not totp_helpers.verificar_totp(secret, codigo, valid_window=1):
            _log(request, request.user, 'totp_fail', False)
            messages.error(request, 'Código inválido. Verificá la hora del teléfono.')
            return _render_setup(request, secret)

        codes_planos = totp_helpers.generar_recovery_codes_planos()
        totp_obj.totp_secret = secret
        totp_obj.totp_activo = True
        totp_obj.recovery_codes_hash = totp_helpers.hashear_codes(codes_planos)
        totp_obj.totp_ultimo_codigo = totp_helpers.normalizar_codigo_totp(codigo)
        totp_obj.ultima_2fa_ok = timezone.now()
        totp_obj.save()

        _log(request, request.user, 'totp_setup', True)
        _log(request, request.user, 'totp_ok', True)

        request.session.pop('admin_setup_secret', None)
        request.session['admin_2fa_ok'] = True
        request.session['admin_2fa_at'] = int(time.time())
        request.session['admin_recovery_codes_a_mostrar']    = codes_planos
        request.session['admin_recovery_codes_a_mostrar_at'] = int(time.time())
        return redirect('admin_2fa_recovery_codes')

    return _render_setup(request, secret)


def _render_setup(request, secret: str):
    identidad = _identidad(request.user)
    url = totp_helpers.url_otpauth(secret, identidad, issuer='Pietramonte Admin')
    return render(request, 'admin_2fa/setup.html', {
        'qr_svg':     totp_helpers.qr_svg(url),
        'secret':     secret,
        'identidad':  identidad,
    })


# ─── Verify ────────────────────────────────────────────────────────────────
@never_cache
@require_http_methods(['GET', 'POST'])
@staff_member_required
def admin_2fa_verify_view(request):
    totp_obj = _get_or_create_totp(request.user)
    if not totp_obj.totp_activo:
        return redirect('admin_2fa_setup')

    if request.method == 'POST':
        modo = (request.POST.get('modo') or 'totp').lower()
        codigo = request.POST.get('codigo') or ''
        ok = False

        if modo == 'recovery':
            ok, nueva_lista = totp_helpers.consumir_recovery_code(
                list(totp_obj.recovery_codes_hash or []), codigo,
            )
            if ok:
                totp_obj.recovery_codes_hash = nueva_lista
                _log(request, request.user, 'recovery_used', True)
            else:
                _log(request, request.user, 'recovery_inval', False)
        else:
            if totp_helpers.verificar_totp(
                totp_obj.totp_secret, codigo,
                ultimo_usado=totp_obj.totp_ultimo_codigo,
            ):
                totp_obj.totp_ultimo_codigo = totp_helpers.normalizar_codigo_totp(codigo)
                ok = True
                _log(request, request.user, 'totp_ok', True)
            else:
                _log(request, request.user, 'totp_fail', False)

        if not ok:
            messages.error(request, 'Código incorrecto.')
            return render(request, 'admin_2fa/verify.html', {
                'modo': modo,
                'recovery_count': len(totp_obj.recovery_codes_hash or []),
            }, status=400)

        totp_obj.ultima_2fa_ok = timezone.now()
        totp_obj.save()
        request.session['admin_2fa_ok'] = True
        request.session['admin_2fa_at'] = int(time.time())
        return redirect(_admin_index_url())

    return render(request, 'admin_2fa/verify.html', {
        'modo':           request.GET.get('modo', 'totp'),
        'recovery_count': len(totp_obj.recovery_codes_hash or []),
    })


# ─── Recovery codes (display + PDF + confirmar) ────────────────────────────
def _codes_admin_de_sesion(request) -> list[str] | None:
    codes = request.session.get('admin_recovery_codes_a_mostrar')
    at    = request.session.get('admin_recovery_codes_a_mostrar_at', 0)
    if not codes:
        return None
    try:
        if int(time.time()) - int(at) > RECOVERY_DISPLAY_TTL:
            request.session.pop('admin_recovery_codes_a_mostrar', None)
            request.session.pop('admin_recovery_codes_a_mostrar_at', None)
            return None
    except (TypeError, ValueError):
        return None
    return list(codes)


@staff_member_required
@never_cache
def admin_2fa_recovery_codes_view(request):
    codes = _codes_admin_de_sesion(request)
    if not codes:
        return redirect(_admin_index_url())
    return render(request, 'admin_2fa/recovery_codes.html', {
        'codes':           codes,
        'admin_index_url': _admin_index_url(),
    })


@staff_member_required
@require_POST
def admin_2fa_recovery_confirmar_view(request):
    request.session.pop('admin_recovery_codes_a_mostrar', None)
    request.session.pop('admin_recovery_codes_a_mostrar_at', None)
    return redirect(_admin_index_url())


@staff_member_required
@never_cache
def admin_2fa_recovery_pdf_view(request):
    codes = _codes_admin_de_sesion(request)
    if not codes:
        return redirect(_admin_index_url())
    pdf_bytes = totp_helpers.pdf_recovery_codes(codes, _identidad(request.user))
    resp = HttpResponse(pdf_bytes, content_type='application/pdf')
    resp['Content-Disposition'] = (
        'attachment; filename="recovery_codes_pietramonte_admin.pdf"'
    )
    resp['X-Content-Type-Options'] = 'nosniff'
    return resp


# ─── Middleware ────────────────────────────────────────────────────────────
class Admin2FAMiddleware:
    """
    Exige 2FA para cualquier URL bajo ADMIN_URL_PATH excepto:
      - login/  logout/  2fa/setup/  2fa/verify/  2fa/codigos/
    Cuando el usuario está autenticado pero no completó 2FA, redirige a
    setup (si no tiene TOTP) o verify (si lo tiene).
    """
    def __init__(self, get_response):
        self.get_response = get_response
        self.admin_prefix = '/' + settings.ADMIN_URL_PATH
        # Sufijos relativos al prefix que deben pasar sin 2FA
        self.bypass_suffixes = (
            'login/',
            'logout/',
            '2fa/setup/',
            'jsi18n/',          # admin internal, lo suele pedir antes del index
        )
        # Sufijos que también deben pasar pero solo si el user ya intentó setup
        self.bypass_suffixes_post_setup = (
            '2fa/verify/',
            '2fa/codigos/',
            '2fa/codigos/pdf/',
            '2fa/codigos/confirmar/',
        )

    def __call__(self, request):
        path = request.path
        if not path.startswith(self.admin_prefix):
            return self.get_response(request)

        for suf in self.bypass_suffixes + self.bypass_suffixes_post_setup:
            if path == self.admin_prefix + suf:
                return self.get_response(request)

        user = getattr(request, 'user', None)
        if not user or not user.is_authenticated:
            # No logueado → que el admin lo redirija a su login normal.
            return self.get_response(request)

        if request.session.get('admin_2fa_ok'):
            return self.get_response(request)

        # Falta 2FA: setup o verify según si ya configuró antes.
        try:
            totp_obj = AdminTOTP.objects.get(user=user)
            if totp_obj.totp_activo:
                return redirect(self.admin_prefix + '2fa/verify/')
        except AdminTOTP.DoesNotExist:
            pass
        return redirect(self.admin_prefix + '2fa/setup/')
