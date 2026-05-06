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
# 2 h de inactividad → expira. SAVE_EVERY_REQUEST renueva la cookie en cada hit,
# así una sesión activa nunca caduca pero una abandonada muere sola.
SESSION_COOKIE_AGE = 60 * 60 * 2
SESSION_SAVE_EVERY_REQUEST = True
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


DEFAULT_AUTO_FIELD = 'django.db.models.BigAutoField'
