"""
Settings de Django para Archivo de Correos Pietramonte.

Variables sensibles se leen de .env (ver .env.example).
En producción, asegúrate de:
  - DEBUG=False
  - SECRET_KEY rotada
  - ALLOWED_HOSTS limitado a tu dominio
  - Acceso por HTTPS (Cloudflare Tunnel ya lo provee)
"""

from pathlib import Path
import os
import dj_database_url
from dotenv import load_dotenv

load_dotenv()

BASE_DIR = Path(__file__).resolve().parent.parent


# ─── Helpers ───────────────────────────────────────────────────────────────
def env_bool(key: str, default: bool = False) -> bool:
    raw = os.getenv(key)
    if raw is None:
        return default
    return raw.strip().lower() in ('1', 'true', 'yes', 'on')


def env_list(key: str, default: str = '') -> list[str]:
    raw = os.getenv(key, default)
    return [x.strip() for x in raw.split(',') if x.strip()]


# ─── Núcleo ────────────────────────────────────────────────────────────────
SECRET_KEY = os.getenv('SECRET_KEY')
if not SECRET_KEY:
    # Solo arrancar sin secreto en dev — fallar duro en prod
    if env_bool('DEBUG', False):
        SECRET_KEY = 'dev-insecure-key-do-not-use-in-production'
    else:
        raise RuntimeError(
            'SECRET_KEY no está definida. Configúrala en .env antes de arrancar.'
        )

DEBUG = env_bool('DEBUG', False)

ALLOWED_HOSTS = env_list('ALLOWED_HOSTS', 'localhost,127.0.0.1')


# ─── Aplicaciones ──────────────────────────────────────────────────────────
INSTALLED_APPS = [
    'django.contrib.admin',
    'django.contrib.auth',
    'django.contrib.contenttypes',
    'django.contrib.staticfiles',
    'django.contrib.messages',
    'django.contrib.sessions',
    'correos',
    'taller',
]

MIDDLEWARE = [
    'django.middleware.security.SecurityMiddleware',
    'whitenoise.middleware.WhiteNoiseMiddleware',
    'archivo_pietramonte.middleware.SecurityHeadersMiddleware',
    'django.contrib.sessions.middleware.SessionMiddleware',
    'django.middleware.common.CommonMiddleware',
    'django.middleware.csrf.CsrfViewMiddleware',
    'django.contrib.auth.middleware.AuthenticationMiddleware',
    # Exige 2FA en /admin-* — debe ir DESPUÉS de AuthenticationMiddleware.
    'archivo_pietramonte.admin_2fa.Admin2FAMiddleware',
    # Rate-limit del login admin (8 fallos/15min/IP). El portal tiene su propio
    # rate-limit en login_view; este middleware solo cubre /admin-*/login/.
    'archivo_pietramonte.middleware.AdminLoginRateLimitMiddleware',
    'django.contrib.messages.middleware.MessageMiddleware',
    'django.middleware.clickjacking.XFrameOptionsMiddleware',
]

ROOT_URLCONF = 'archivo_pietramonte.urls'

TEMPLATES = [
    {
        'BACKEND': 'django.template.backends.django.DjangoTemplates',
        'DIRS': [BASE_DIR / 'templates'],
        'APP_DIRS': True,
        'OPTIONS': {
            'context_processors': [
                'django.template.context_processors.request',
                'django.contrib.auth.context_processors.auth',
                'django.contrib.messages.context_processors.messages',
                'correos.context_processors.portal',
            ],
        },
    },
]


# ─── Validadores de password (Django built-in + nuestros) ───────────────────
# Aplican a UsuarioPortal cuando se crea/edita desde admin o desde
# "Cambiar contraseña" en el portal.
AUTH_PASSWORD_VALIDATORS = [
    {'NAME': 'django.contrib.auth.password_validation.UserAttributeSimilarityValidator',
     'OPTIONS': {'user_attributes': ('email',), 'max_similarity': 0.6}},
    {'NAME': 'django.contrib.auth.password_validation.MinimumLengthValidator',
     'OPTIONS': {'min_length': 10}},
    {'NAME': 'django.contrib.auth.password_validation.CommonPasswordValidator'},
    {'NAME': 'django.contrib.auth.password_validation.NumericPasswordValidator'},
]


# ─── Base de datos ─────────────────────────────────────────────────────────
# En producción: DATABASE_URL apunta a Postgres (servicio gestionado por Coolify).
# En desarrollo local sin DATABASE_URL: cae a SQLite en BASE_DIR/db.sqlite3.
DATABASES = {
    'default': dj_database_url.config(
        default=f'sqlite:///{BASE_DIR / "db.sqlite3"}',
        conn_max_age=600,
        conn_health_checks=True,
    )
}


# ─── Estáticos (whitenoise + collectstatic) ────────────────────────────────
STATIC_URL = '/static/'
STATIC_ROOT = BASE_DIR / 'staticfiles'
STATICFILES_DIRS = [BASE_DIR / 'static']
STORAGES = {
    'default': {'BACKEND': 'django.core.files.storage.FileSystemStorage'},
    'staticfiles': {'BACKEND': 'whitenoise.storage.CompressedManifestStaticFilesStorage'},
}


# ─── Sesiones ──────────────────────────────────────────────────────────────
SESSION_ENGINE = 'django.contrib.sessions.backends.db'
SESSION_DB_ALIAS = 'default'
SESSION_COOKIE_HTTPONLY = True
SESSION_COOKIE_SAMESITE = 'Lax'
# 8 horas — cubre una jornada de taller sin re-login. La sesión se renueva
# desde la última actividad significativa (login, cambiar buzón, etc.). Antes
# usábamos SESSION_SAVE_EVERY_REQUEST=True para renovar en CADA request, pero
# eso hace 1 UPDATE a django_session por hit (pesado en un inbox que recarga
# por cambio de pestaña). Con False solo escribimos cuando session.modified=True.
SESSION_COOKIE_AGE = 60 * 60 * 8
SESSION_SAVE_EVERY_REQUEST = False
SESSION_EXPIRE_AT_BROWSER_CLOSE = False


# ─── CSRF ──────────────────────────────────────────────────────────────────
CSRF_COOKIE_HTTPONLY = True
CSRF_COOKIE_SAMESITE = 'Lax'
# Detrás de Cloudflare Tunnel — el host visto por Django coincide con ALLOWED_HOSTS,
# pero los formularios HTTPS necesitan que CSRF acepte el origin del dominio público.
CSRF_TRUSTED_ORIGINS = [f'https://{h}' for h in ALLOWED_HOSTS if h not in ('localhost', '127.0.0.1')]


# ─── Endurecimiento (solo en producción) ───────────────────────────────────
if not DEBUG:
    # Cookies seguras
    SESSION_COOKIE_SECURE = True
    CSRF_COOKIE_SECURE = True

    # Cabeceras de seguridad
    SECURE_BROWSER_XSS_FILTER = True
    SECURE_CONTENT_TYPE_NOSNIFF = True
    SECURE_REFERRER_POLICY = 'strict-origin-when-cross-origin'
    X_FRAME_OPTIONS = 'DENY'

    # HSTS — el Tunnel termina TLS en Cloudflare, así que el tráfico interno
    # es HTTP plano; Cloudflare reescribe, pero igual avisamos al navegador.
    SECURE_HSTS_SECONDS = 60 * 60 * 24 * 30      # 30 días
    SECURE_HSTS_INCLUDE_SUBDOMAINS = True
    SECURE_HSTS_PRELOAD = False                  # activa solo cuando vayas a someter al preload list

    # Confiar en X-Forwarded-Proto que mete cloudflared
    SECURE_PROXY_SSL_HEADER = ('HTTP_X_FORWARDED_PROTO', 'https')


# ─── i18n ──────────────────────────────────────────────────────────────────
LANGUAGE_CODE = 'es-cl'
TIME_ZONE = 'America/Santiago'
USE_I18N = True
USE_TZ = True


# ─── Admin ─────────────────────────────────────────────────────────────────
# Ruta del admin DESDE .env, sin "/" inicial. Default ofuscado.
# En producción: detrás de Cloudflare Access además.
ADMIN_URL_PATH = (os.getenv('ADMIN_URL_PATH', 'admin-pm-staff').strip().strip('/') + '/')

# Login de Django (admin) usa estas URLs
LOGIN_URL  = '/' + ADMIN_URL_PATH + 'login/'
LOGIN_REDIRECT_URL = '/' + ADMIN_URL_PATH


# ─── Específico Pietramonte ────────────────────────────────────────────────
# Carpeta donde se guardarán los .mbox importados (NO va en git, ver .gitignore).
DATA_DIR = BASE_DIR / 'data'
DATA_DIR.mkdir(exist_ok=True)
MBOX_DIR = DATA_DIR / 'mbox'
MBOX_DIR.mkdir(parents=True, exist_ok=True)

# Adjuntos extraídos de los .mbox — NUNCA expuestos directamente, solo vía vista
# protegida con auth. NO va en git.
MEDIA_ROOT = DATA_DIR / 'adjuntos'
MEDIA_ROOT.mkdir(exist_ok=True)
MEDIA_URL  = '/media-internal/'   # nunca se sirve directo, solo vía adjunto_view

# Email para notificaciones operacionales (futuras: alertas, agendamientos).
# La allowlist real del portal es la tabla UsuarioPortal en BD.
PORTAL_ADMIN_EMAIL = os.getenv('PORTAL_ADMIN_EMAIL', 'soporte.dongo@gmail.com')


# ─── Email (SMTP) ──────────────────────────────────────────────────────────
# En dev por defecto: console backend (los emails se imprimen en stdout, no se mandan).
# En prod: setear EMAIL_BACKEND=django.core.mail.backends.smtp.EmailBackend en .env.
#
# Recetas:
#  - Gmail con App Password: smtp.gmail.com:587 TLS, EMAIL_HOST_USER=la cuenta real,
#    EMAIL_HOST_PASSWORD=los 16 chars del App Password (sin espacios).
#  - Para que el From sea un alias (agenda@pietramonte.cl, etc.), configurá ese alias
#    como "Send mail as" en la cuenta Gmail real y usá los EMAIL_*_FROM más abajo.
EMAIL_BACKEND       = os.getenv('EMAIL_BACKEND', 'django.core.mail.backends.console.EmailBackend')
EMAIL_HOST          = os.getenv('EMAIL_HOST', 'smtp.gmail.com')
EMAIL_PORT          = int(os.getenv('EMAIL_PORT', '587'))
EMAIL_USE_TLS       = env_bool('EMAIL_USE_TLS', True)
EMAIL_USE_SSL       = env_bool('EMAIL_USE_SSL', False)
EMAIL_HOST_USER     = os.getenv('EMAIL_HOST_USER', '')
EMAIL_HOST_PASSWORD = os.getenv('EMAIL_HOST_PASSWORD', '')
EMAIL_TIMEOUT       = int(os.getenv('EMAIL_TIMEOUT', '20'))     # segundos

DEFAULT_FROM_EMAIL  = os.getenv('DEFAULT_FROM_EMAIL', 'Pietramonte <noreply@pietramonte.cl>')
SERVER_EMAIL        = DEFAULT_FROM_EMAIL    # para los mails de error que Django dispara solo

# Aliases por tipo de notificación. Cada uno cae a DEFAULT_FROM_EMAIL si no está seteado.
# Los Reply-To apuntan a inboxes humanos (Cloudflare Email Routing los reenvía a Gmail).
EMAIL_AGENDA_FROM           = os.getenv('EMAIL_AGENDA_FROM',           DEFAULT_FROM_EMAIL)
EMAIL_COTIZACIONES_FROM     = os.getenv('EMAIL_COTIZACIONES_FROM',     DEFAULT_FROM_EMAIL)
EMAIL_REENVIO_FROM          = os.getenv('EMAIL_REENVIO_FROM',          DEFAULT_FROM_EMAIL)
EMAIL_REPLY_TO_AGENDA       = os.getenv('EMAIL_REPLY_TO_AGENDA',       '')
EMAIL_REPLY_TO_COTIZACIONES = os.getenv('EMAIL_REPLY_TO_COTIZACIONES', '')

# Listas de admins a notificar (CSV en env). Default: solo PORTAL_ADMIN_EMAIL.
ADMIN_NOTIFY_AGENDA       = env_list('ADMIN_NOTIFY_AGENDA',       PORTAL_ADMIN_EMAIL)
ADMIN_NOTIFY_COTIZACIONES = env_list('ADMIN_NOTIFY_COTIZACIONES', PORTAL_ADMIN_EMAIL)


# ─── Sync de Gmail vía IMAP (correos nuevos por label) ────────────────────
# Credenciales del IMAP de Gmail. Antes reusaba EMAIL_HOST_USER/PASSWORD del
# SMTP, pero cuando el outbound se mueve a otro proveedor (ej. Resend), las
# EMAIL_HOST_* dejan de servir para IMAP — hay que tener vars separadas.
# El comando `sincronizar_gmail` corre por cron cada N min, fetchea cada
# label de BuzonGmailLabel y mete los mensajes nuevos en su buzón.
GMAIL_IMAP_HOST = os.getenv('GMAIL_IMAP_HOST', 'imap.gmail.com')
GMAIL_IMAP_PORT = int(os.getenv('GMAIL_IMAP_PORT', '993'))
# GMAIL_IMAP_USER / GMAIL_IMAP_PASSWORD se leen en gmail_sync.py con fallback
# a EMAIL_HOST_USER / EMAIL_HOST_PASSWORD (compat con deploys viejos).
GMAIL_IMAP_USER     = os.getenv('GMAIL_IMAP_USER', '')
GMAIL_IMAP_PASSWORD = os.getenv('GMAIL_IMAP_PASSWORD', '')


# ─── Firmas de correo saliente ────────────────────────────────────────────
# URL absoluta del logo que se embebe en el HTML de las firmas. Tiene que ser
# accesible públicamente desde clientes externos (Gmail, Outlook). Subir el
# archivo a static/img/firma_logo.png y poner acá la URL completa.
# Si está vacío, las firmas se renderizan sin logo (solo texto).
FIRMA_LOGO_URL = os.getenv('FIRMA_LOGO_URL', '')

# Color de acento de las firmas (barra lateral, iconos en círculos). Cada
# deployment tiene su brand color: Pietramonte = rojo. Para otra empresa,
# overridear con env var (ej. BRAND_PRIMARY_COLOR=#1976D2 para azul).
BRAND_PRIMARY_COLOR = os.getenv('BRAND_PRIMARY_COLOR', '#C80C0F')

BRAND_COMPANY_NAME = os.getenv('BRAND_COMPANY_NAME', 'Pietramonte Automotriz')


# ─── Anti-bot del form público de reservas ────────────────────────────────
# Cloudflare Turnstile: gratis, invisible para humanos legítimos. Sacá las
# claves en https://dash.cloudflare.com/ → Turnstile → Add Site.
# El "Site Key" es público (va al HTML); el "Secret Key" se queda en el server.
# En dev, dejarlas vacías → la verificación se bypass-ea automáticamente.
TURNSTILE_SITE_KEY   = os.getenv('TURNSTILE_SITE_KEY', '')
TURNSTILE_SECRET_KEY = os.getenv('TURNSTILE_SECRET_KEY', '')

# Dominios extras a bloquear como desechables, sumados a la lista base bundlada.
# CSV en env. Útil cuando aparece un nuevo dominio temporal masivo.
DISPOSABLE_DOMAINS_EXTRA = env_list('DISPOSABLE_DOMAINS_EXTRA', '')


# ─── Render de correos HTML inbound ───────────────────────────────────────
# Permitir <img src="https://..."> externas en el cuerpo de correos
# recibidos (para que logos / branding / tablas con imágenes se vean tipo
# Gmail).
# Mitigaciones aplicadas en `render_correo_html`:
#   - referrerpolicy="no-referrer" en cada <img> (oculta nuestro dominio al sender)
#   - loading="lazy" (no descarga hasta que el user scrollea al correo)
# Setear False para volver al modo estricto (solo CID internos + data:image).
EMAIL_ALLOW_EXTERNAL_IMAGES = env_bool('EMAIL_ALLOW_EXTERNAL_IMAGES', True)


DEFAULT_AUTO_FIELD = 'django.db.models.BigAutoField'


# ─── Backup (Backblaze B2 vía rclone) ──────────────────────────────────────
# Credenciales en: Backblaze → Account → App Keys → Add a New App Key.
# Usadas por backup_adjuntos_b2 (media) y hacer_backup (DB dump).
# Si B2_KEY_ID está vacío, los comandos de backup saltean el upload (dry-run).
B2_KEY_ID          = os.getenv('B2_KEY_ID', '')            # keyID de Backblaze
B2_APPLICATION_KEY = os.getenv('B2_APPLICATION_KEY', '')   # applicationKey
B2_BUCKET_NAME     = os.getenv('B2_BUCKET_NAME', '')
B2_PREFIX          = os.getenv('B2_PREFIX', 'backup')


# ─── Cache backend ─────────────────────────────────────────────────────────
# Si REDIS_URL está seteado (ej. redis://redis:6379/0), usamos Redis. Es el
# único backend que es safe entre múltiples gunicorn workers — fundamental
# para que rate-limit y throttle compartan estado.
# Sin REDIS_URL caemos a LocMemCache (per-worker, suficiente para dev local
# y para deploys de un solo worker). El rate-limit funciona pero un atacante
# podría rotar entre workers para evadir; en prod multi-worker sin Redis,
# Cloudflare upstream cubre la diferencia.
REDIS_URL = os.getenv('REDIS_URL', '').strip()

if REDIS_URL:
    CACHES = {
        'default': {
            'BACKEND': 'django.core.cache.backends.redis.RedisCache',
            'LOCATION': REDIS_URL,
            'TIMEOUT': 300,
            'OPTIONS': {
                # Pool con tamaño razonable; Coolify Redis maneja conexiones.
                'pool_class': 'redis.connection.ConnectionPool',
            },
        },
    }
else:
    CACHES = {
        'default': {
            'BACKEND': 'django.core.cache.backends.locmem.LocMemCache',
            'LOCATION': 'pietramonte-default',
            'TIMEOUT': 300,
        },
    }


# ─── Proxies confiables (validación de X-Forwarded-For) ────────────────────
# CSV de IPs / CIDRs de los proxies que pueden setear XFF. Si la conexión
# llega de un IP que NO está en esta lista, ignoramos XFF y usamos REMOTE_ADDR
# (sino un atacante podría spoofear su IP burlando rate-limit).
#
# Default 2026-05-11: confiamos en redes RFC 1918 privadas. El container vive
# en la red Docker interna (10.x o 172.16.x según Coolify), recibe tráfico
# desde el reverse proxy de Coolify y a su vez de Cloudflare Tunnel. Ningún
# atacante externo puede hablar directo con Gunicorn (UFW solo abre SSH).
# Por eso confiar en estos rangos es seguro.
#
# Antes este default era vacío → _get_ip caía a REMOTE_ADDR siempre. Como
# REMOTE_ADDR en container es siempre la IP de Coolify, TODO el tráfico se
# veía como UNA sola IP → rate-limit por IP se aplicaba globalmente. Un
# atacante podía bloquear a todos los usuarios reales con una sola sesión
# fallida de brute-force.
TRUSTED_PROXIES = env_list(
    'TRUSTED_PROXIES',
    '10.0.0.0/8,172.16.0.0/12,192.168.0.0/16',
)


# ─── Límites de upload / form data (anti-DoS) ─────────────────────────────
# Defaults Django 5.x: 2.5 MB body en memoria, 100 archivos, 1000 fields.
# Endurecemos:
#  - DATA_UPLOAD_MAX_MEMORY_SIZE: el body del POST que se mantiene en RAM
#    antes de pasar a disco. Si llega más que esto, Django levanta
#    RequestDataTooBig (413). 5 MB cubre formularios con adjuntos chicos.
#  - DATA_UPLOAD_MAX_NUMBER_FILES: tope de adjuntos en un POST. Anti-DoS
#    por flood de uploads en un solo request.
#  - DATA_UPLOAD_MAX_NUMBER_FIELDS: cantidad de fields del form. Anti-DoS
#    por explotación de hash collisions en el parsing.
#  - FILE_UPLOAD_MAX_MEMORY_SIZE: tope per-file que se mantiene en memoria
#    antes de spillover a disco. 5 MB.
DATA_UPLOAD_MAX_MEMORY_SIZE   = 5 * 1024 * 1024
DATA_UPLOAD_MAX_NUMBER_FILES  = 25
DATA_UPLOAD_MAX_NUMBER_FIELDS = 500
FILE_UPLOAD_MAX_MEMORY_SIZE   = 5 * 1024 * 1024


# ─── Logging ────────────────────────────────────────────────────────────────
# Sin LOGGING configurado, los `except Exception: pass` (gmail_sync, import_mbox,
# admin_2fa) se comían excepciones sin dejar trazas. Ahora hay un handler
# stderr (Coolify lo captura como log del container) con timestamp + level +
# logger name. Loggers específicos por app a nivel INFO; resto a WARNING.
LOG_LEVEL = os.getenv('LOG_LEVEL', 'INFO').upper()

LOGGING = {
    'version': 1,
    'disable_existing_loggers': False,
    'formatters': {
        'standard': {
            'format': '[{asctime}] {levelname:7s} {name}: {message}',
            'style': '{',
            'datefmt': '%Y-%m-%d %H:%M:%S',
        },
    },
    'handlers': {
        'stderr': {
            'class': 'logging.StreamHandler',
            'formatter': 'standard',
            'level': 'DEBUG',
        },
    },
    'root': {
        'handlers': ['stderr'],
        'level': 'WARNING',
    },
    'loggers': {
        # Apps propias: detalle más granular para diagnóstico operacional.
        'correos':            {'level': LOG_LEVEL, 'handlers': ['stderr'], 'propagate': False},
        'taller':             {'level': LOG_LEVEL, 'handlers': ['stderr'], 'propagate': False},
        'archivo_pietramonte':{'level': LOG_LEVEL, 'handlers': ['stderr'], 'propagate': False},
        # Django: sólo warnings y errores; sino spamea con cada request 200.
        'django':             {'level': 'WARNING', 'handlers': ['stderr'], 'propagate': False},
        # SQL queries solo si DEBUG=True y LOG_SQL=1; útil para optimizar.
        'django.db.backends': {
            'level': 'DEBUG' if (DEBUG and env_bool('LOG_SQL', False)) else 'WARNING',
            'handlers': ['stderr'],
            'propagate': False,
        },
    },
}
