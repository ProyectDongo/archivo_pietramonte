"""
Middleware propio mínimo — sin dependencias externas.

  SecurityHeadersMiddleware:
    - Content-Security-Policy (defensa en profundidad contra XSS).
    - Permissions-Policy (deshabilita APIs sensibles del navegador).
    - Cross-Origin-Resource-Policy.
    - CSP estricta para todo el sitio EXCEPTO el admin de Django,
      que necesita inline-script/style por su diseño legacy.
"""

from django.conf import settings


# CSP estricta: público + portal + adjuntos.
# - Sin 'unsafe-inline' para script-src.
# - 'unsafe-inline' en style-src queda por compatibilidad con backdrop-filter
#   y algunos estilos inyectados por Django. Si se quiere endurecer en el futuro,
#   se puede pasar a hashes/nonces.
_CSP_DEFAULT = (
    "default-src 'self'; "
    "script-src 'self'; "
    "style-src 'self' 'unsafe-inline' https://fonts.googleapis.com; "
    "font-src 'self' https://fonts.gstatic.com; "
    "img-src 'self' data: blob:; "
    "connect-src 'self'; "
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
