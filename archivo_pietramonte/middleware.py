"""
Middleware propio mínimo — sin dependencias externas.

  SecurityHeadersMiddleware:
    - Content-Security-Policy (defensa en profundidad contra XSS).
    - Permissions-Policy (deshabilita APIs sensibles del navegador).
    - Cross-Origin-Resource-Policy.
    - CSP estricta para todo el sitio EXCEPTO el admin de Django,
      que necesita inline-script/style por su diseño legacy.

  AdminLoginRateLimitMiddleware:
    - Rate-limit de intentos de login al admin Django (django.contrib.auth).
      El portal tiene su propio rate-limit en login_view, pero el admin usa
      el flujo built-in que no lo tiene. Sin esto, brute-force ilimitado del
      password admin (aunque 2FA TOTP cierra la puerta igual, no hay razón
      para dejar el password expuesto a fuerza bruta).
"""
import logging

from django.conf import settings
from django.core.cache import cache
from django.http import HttpResponse


logger = logging.getLogger('archivo_pietramonte.middleware')


# CSP estricta: público + portal + adjuntos.
# - Sin 'unsafe-inline' para script-src.
# - 'unsafe-inline' en style-src queda por compatibilidad con backdrop-filter
#   y algunos estilos inyectados por Django. Si se quiere endurecer en el futuro,
#   se puede pasar a hashes/nonces.
# - challenges.cloudflare.com habilitado para Turnstile (login + agendamiento
#   público). Es el endpoint oficial de Cloudflare; carga un widget en iframe
#   y verifica el token. Sin esto la CSP bloqueaba el script y el captcha era
#   invisible para el usuario.
_CSP_DEFAULT = (
    "default-src 'self'; "
    # Cloudflare:
    #  - challenges.cloudflare.com → widget Turnstile (login + agendar)
    #  - static.cloudflareinsights.com → Cloudflare Web Analytics (RUM beacon
    #    auto-inyectado por el proxy de CF si está activo en el panel)
    "script-src 'self' https://challenges.cloudflare.com https://static.cloudflareinsights.com; "
    "style-src 'self' 'unsafe-inline' https://fonts.googleapis.com; "
    "font-src 'self' https://fonts.gstatic.com; "
    "img-src 'self' data: blob:; "
    "connect-src 'self' https://challenges.cloudflare.com https://cloudflareinsights.com; "
    "frame-src 'self' https://challenges.cloudflare.com; "  # 'self' para PDF/audio inline en adj-viewer
    "frame-ancestors 'none'; "
    "form-action 'self'; "
    "base-uri 'self'; "
    "object-src 'none';"
)

# CSP relajada SOLO para Django admin: usa muchos style="" inline y eventos
# inline tipo onload. NO se aplica a ninguna otra URL.
_CSP_ADMIN = (
    "default-src 'self'; "
    "script-src 'self' 'unsafe-inline' 'unsafe-eval'; "
    "style-src 'self' 'unsafe-inline'; "
    "img-src 'self' data:; "
    "connect-src 'self'; "
    "frame-ancestors 'none'; "
    "form-action 'self'; "
    "base-uri 'self'; "
    "object-src 'none';"
)

_PERMISSIONS_POLICY = (
    "accelerometer=(), "
    "camera=(), "
    "geolocation=(), "
    "gyroscope=(), "
    "magnetometer=(), "
    "microphone=(), "
    "payment=(), "
    "usb=()"
)


class SecurityHeadersMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response
        self.csp_default = getattr(settings, 'CONTENT_SECURITY_POLICY', _CSP_DEFAULT)
        # Prefijo del admin (con '/' al inicio para hacer match contra request.path)
        self.admin_prefix = '/' + settings.ADMIN_URL_PATH

    def __call__(self, request):
        response = self.get_response(request)
        ctype = response.get('Content-Type', '')
        if 'text/html' not in ctype:
            return response

        if request.path.startswith(self.admin_prefix):
            response.setdefault('Content-Security-Policy', _CSP_ADMIN)
        else:
            response.setdefault('Content-Security-Policy', self.csp_default)

        response.setdefault('Permissions-Policy', _PERMISSIONS_POLICY)
        response.setdefault('Cross-Origin-Resource-Policy', 'same-origin')
        return response


# ─── Brute-force admin login ───────────────────────────────────────────────
ADMIN_LOGIN_RL_VENTANA = 15 * 60      # 15 min
ADMIN_LOGIN_RL_MAX     = 8            # 8 intentos fallidos por IP en la ventana


class AdminLoginRateLimitMiddleware:
    """
    Bloquea intentos de POST al login del admin Django si una IP supera
    8 fallos en 15 minutos. Reusa el cache backend (Redis si REDIS_URL,
    sino LocMemCache).

    Detecta el "fallo" mirando el status_code de la respuesta: si el
    POST a /<admin>/login/ devuelve 200, el form falló y se quedó en la
    misma página (Django re-renderiza con error). Si devolvió 302 →
    autenticación OK, no contamos.

    El portal del usuario (login_view en correos/views.py) ya tiene su
    propio rate-limit; este middleware sólo cubre la URL del admin que
    usa el flujo built-in de django.contrib.auth.
    """

    def __init__(self, get_response):
        self.get_response = get_response
        self.admin_login_path = '/' + settings.ADMIN_URL_PATH + 'login/'

    def __call__(self, request):
        # Solo POST al endpoint exacto del login admin
        es_admin_login = (
            request.method == 'POST'
            and request.path == self.admin_login_path
        )
        if not es_admin_login:
            return self.get_response(request)

        # Importamos local para evitar ciclos (correos.views importa middleware indirectamente)
        from correos.views import _get_ip
        from correos.models import hash_ip
        ip_h = hash_ip(_get_ip(request))
        key = f'rl:admin_login:{ip_h}'

        n_fallos = cache.get(key, 0)
        if n_fallos >= ADMIN_LOGIN_RL_MAX:
            logger.warning('Bloqueo brute-force admin ip_hash=%s fallos=%d', ip_h[:12], n_fallos)
            resp = HttpResponse(
                'Demasiados intentos de login. Esperá 15 minutos antes de volver a intentar.',
                status=429,
                content_type='text/plain; charset=utf-8',
            )
            resp['Retry-After'] = str(ADMIN_LOGIN_RL_VENTANA)
            return resp

        response = self.get_response(request)

        # 302 (redirect) = login OK → reset. 200 (re-render del form) = fallo.
        if response.status_code == 302:
            cache.delete(key)
        else:
            cache.set(key, n_fallos + 1, ADMIN_LOGIN_RL_VENTANA)

        return response
