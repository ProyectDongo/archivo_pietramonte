"""
Captcha propio basado en íconos SVG (estilo Lucide) — sin dependencias externas.

Diseño:
  - El servidor genera un challenge (categoría + 9 íconos donde N son correctos).
  - Empaqueta los índices correctos + nonce + timestamp en JSON.
  - Firma el JSON con HMAC-SHA256 usando SECRET_KEY.
  - Envía: lista de íconos {nombre, svg} + payload firmado (base64).
  - El cliente envía: índices seleccionados + payload firmado.
  - El servidor verifica firma, expiración (3 min) y match.

Beneficios:
  - El cliente NO puede falsificar la respuesta (firma criptográfica).
  - Replay limitado por timestamp (3 min).
  - Cero estado en servidor (no hace falta cache ni sesión).
  - Cero dependencias (solo hmac, hashlib, json, base64, secrets, time — stdlib).
"""

import base64
import hashlib
import hmac
import json
import secrets
import time
from typing import Iterable

from django.conf import settings


# ─── Íconos SVG (paths internos, se envuelven en <svg> en el template) ─────
# Inspirados en Lucide (MIT). Stroke 1.8, viewBox 24x24, sin fill.
ICONS: dict[str, str] = {
    # ── Vehículos ──────────────────────────────────────────────────────
    'car':
        '<path d="M19 17h2c.6 0 1-.4 1-1v-3c0-.9-.7-1.7-1.5-1.9L18.4 5.5'
        'c-.6-1.1-1.7-1.7-2.9-1.7H8.5c-1.2 0-2.3.6-2.9 1.7L3.5 11.1'
        'C2.7 11.3 2 12.1 2 13v3c0 .6.4 1 1 1h2"/>'
        '<path d="M2 11h20"/>'
        '<circle cx="7" cy="17" r="2"/><circle cx="17" cy="17" r="2"/>',
    'truck':
        '<path d="M14 18V6a2 2 0 0 0-2-2H4a2 2 0 0 0-2 2v11a1 1 0 0 0 1 1h2"/>'
        '<path d="M15 18H9"/>'
        '<path d="M19 18h2a1 1 0 0 0 1-1v-3.65a1 1 0 0 0-.22-.624l-3.48-4.35'
        'A1 1 0 0 0 17.52 8H14"/>'
        '<circle cx="17" cy="18" r="2"/><circle cx="7" cy="18" r="2"/>',
    'bus':
        '<path d="M8 6v6"/><path d="M15 6v6"/><path d="M2 12h19.6"/>'
        '<path d="M18 18h3s.5-1.7.8-2.8c.1-.4.2-.8.2-1.2 0-.4-.1-.8-.2-1.2'
        'l-1.4-5C20.1 6.8 19.1 6 18 6H4a2 2 0 0 0-2 2v10h3"/>'
        '<circle cx="7" cy="18" r="2"/><circle cx="17" cy="18" r="2"/>',
    'bike':
        '<circle cx="18.5" cy="17.5" r="3.5"/>'
        '<circle cx="5.5" cy="17.5" r="3.5"/>'
        '<circle cx="15" cy="5" r="1"/>'
        '<path d="M12 17.5V14l-3-3 4-3 2 3h2"/>',
    'motorcycle':
        '<circle cx="6" cy="17" r="3"/><circle cx="19" cy="17" r="3"/>'
        '<path d="M8 14h6l-3-9h2l5 6"/>',
    'plane':
        '<path d="M17.8 19.2 16 11l3.5-3.5C21 6 21.5 4 21 3c-1-.5-3 0-4.5 1.5L13 8 4.8 6.2'
        'c-.5-.1-.9.1-1.1.5l-.3.5c-.2.5-.1 1 .3 1.3L9 12l-2 3H4l-1 1 3 2 2 3 1-1v-3'
        'l3-2 3.5 5.3c.3.4.8.5 1.3.3l.5-.2c.4-.3.6-.7.5-1.2z"/>',

    # ── Herramientas ──────────────────────────────────────────────────
    'wrench':
        '<path d="M14.7 6.3a1 1 0 0 0 0 1.4l1.6 1.6a1 1 0 0 0 1.4 0l3.77-3.77'
        'a6 6 0 0 1-7.94 7.94l-6.91 6.91a2.12 2.12 0 0 1-3-3l6.91-6.91'
        'a6 6 0 0 1 7.94-7.94l-3.76 3.76z"/>',
    'hammer':
        '<path d="m15 12-8.5 8.5c-.83.83-2.17.83-3 0a2.12 2.12 0 0 1 0-3L12 9"/>'
        '<path d="M17.64 15 22 10.64"/>'
        '<path d="m20.91 11.7-1.25-1.25c-.6-.6-.93-1.4-.93-2.25v-.86L16.01 4.6'
        'a5.56 5.56 0 0 0-3.94-1.64H9l.92.82A6.18 6.18 0 0 1 12 8.4v1.56l2 2h2.47l2.26 1.91"/>',
    'gear':
        '<path d="M12.22 2h-.44a2 2 0 0 0-2 2v.18a2 2 0 0 1-1 1.73l-.43.25'
        'a2 2 0 0 1-2 0l-.15-.08a2 2 0 0 0-2.73.73l-.22.38a2 2 0 0 0 .73 2.73'
        'l.15.1a2 2 0 0 1 1 1.72v.51a2 2 0 0 1-1 1.74l-.15.09a2 2 0 0 0-.73 2.73'
        'l.22.38a2 2 0 0 0 2.73.73l.15-.08a2 2 0 0 1 2 0l.43.25a2 2 0 0 1 1 1.73V20'
        'a2 2 0 0 0 2 2h.44a2 2 0 0 0 2-2v-.18a2 2 0 0 1 1-1.73l.43-.25'
        'a2 2 0 0 1 2 0l.15.08a2 2 0 0 0 2.73-.73l.22-.39a2 2 0 0 0-.73-2.73'
        'l-.15-.08a2 2 0 0 1-1-1.74v-.5a2 2 0 0 1 1-1.74l.15-.09a2 2 0 0 0 .73-2.73'
        'l-.22-.38a2 2 0 0 0-2.73-.73l-.15.08a2 2 0 0 1-2 0l-.43-.25'
        'a2 2 0 0 1-1-1.73V4a2 2 0 0 0-2-2z"/>'
        '<circle cx="12" cy="12" r="3"/>',
    'screwdriver':
        '<path d="m15 9-7 7"/><path d="m21 3-7 7"/>'
        '<path d="M19 8a4 4 0 1 1-8 0 4 4 0 0 1 8 0Z"/>'
        '<path d="m11 13 7-7"/>',

    # ── Naturaleza / objetos cotidianos (distractores neutros) ───────
    'home':
        '<path d="m3 9 9-7 9 7v11a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2z"/>'
        '<polyline points="9 22 9 12 15 12 15 22"/>',
    'tree':
        '<path d="M12 22V12"/>'
        '<path d="m17 14 3 3.3a1 1 0 0 1-.7 1.7H4.7a1 1 0 0 1-.7-1.7L7 14"/>'
        '<path d="M9 9h-.2A1 1 0 0 1 8 7.3L12 3l4 4.3a1 1 0 0 1-.8 1.7H15"/>'
        '<path d="m17 14h-.3a1 1 0 0 1-.7-1.7L19 9.5"/>'
        '<path d="M5 14h.3a1 1 0 0 0 .7-1.7L3 9.5"/>',
    'coffee':
        '<path d="M17 8h1a4 4 0 1 1 0 8h-1"/>'
        '<path d="M3 8h14v9a4 4 0 0 1-4 4H7a4 4 0 0 1-4-4Z"/>'
        '<line x1="6" x2="6" y1="2" y2="4"/>'
        '<line x1="10" x2="10" y1="2" y2="4"/>'
        '<line x1="14" x2="14" y1="2" y2="4"/>',
    'music':
        '<path d="M9 18V5l12-2v13"/>'
        '<circle cx="6" cy="18" r="3"/><circle cx="18" cy="16" r="3"/>',
    'sun':
        '<circle cx="12" cy="12" r="4"/>'
        '<path d="M12 2v2"/><path d="M12 20v2"/>'
        '<path d="m4.93 4.93 1.41 1.41"/><path d="m17.66 17.66 1.41 1.41"/>'
        '<path d="M2 12h2"/><path d="M20 12h2"/>'
        '<path d="m6.34 17.66-1.41 1.41"/><path d="m19.07 4.93-1.41 1.41"/>',
    'moon':
        '<path d="M12 3a6 6 0 0 0 9 9 9 9 0 1 1-9-9Z"/>',
    'heart':
        '<path d="M19 14c1.49-1.46 3-3.21 3-5.5A5.5 5.5 0 0 0 16.5 3c-1.76 0-3 .5-4.5 2'
        '-1.5-1.5-2.74-2-4.5-2A5.5 5.5 0 0 0 2 8.5c0 2.3 1.5 4.05 3 5.5l7 7Z"/>',
    'star':
        '<polygon points="12 2 15.09 8.26 22 9.27 17 14.14 18.18 21.02 12 17.77'
        ' 5.82 21.02 7 14.14 2 9.27 8.91 8.26 12 2"/>',
    'umbrella':
        '<path d="M22 12a10.06 10.06 1 0 0-20 0Z"/>'
        '<path d="M12 12v8a2 2 0 0 0 4 0"/>'
        '<path d="M12 2v1"/>',
    'gift':
        '<rect x="3" y="8" width="18" height="4" rx="1"/>'
        '<path d="M12 8v13"/><path d="M19 12v7a2 2 0 0 1-2 2H7a2 2 0 0 1-2-2v-7"/>'
        '<path d="M7.5 8a2.5 2.5 0 0 1 0-5A4.8 8 0 0 1 12 8a4.8 8 0 0 1 4.5-5 2.5 2.5 0 0 1 0 5"/>',
    'camera':
        '<path d="M14.5 4h-5L7 7H4a2 2 0 0 0-2 2v9a2 2 0 0 0 2 2h16a2 2 0 0 0 2-2V9'
        'a2 2 0 0 0-2-2h-3l-2.5-3z"/>'
        '<circle cx="12" cy="13" r="3"/>',
}


# ─── Categorías disponibles ────────────────────────────────────────────────
CHALLENGES: dict[str, dict] = {
    'vehiculos': {
        'pregunta': 'Selecciona todos los vehículos',
        'correctos':   ['car', 'truck', 'bus', 'bike', 'motorcycle', 'plane'],
        'distractores': ['home', 'tree', 'coffee', 'music', 'sun', 'moon',
                         'heart', 'star', 'umbrella', 'gift', 'camera'],
    },
    'herramientas': {
        'pregunta': 'Selecciona las herramientas mecánicas',
        'correctos':   ['wrench', 'hammer', 'gear', 'screwdriver'],
        'distractores': ['home', 'tree', 'coffee', 'music', 'sun', 'moon',
                         'heart', 'star', 'umbrella', 'gift', 'camera', 'car'],
    },
    'naturaleza': {
        'pregunta': 'Selecciona elementos de la naturaleza',
        'correctos':   ['tree', 'sun', 'moon', 'star'],
        'distractores': ['car', 'truck', 'wrench', 'hammer', 'home',
                         'coffee', 'music', 'umbrella', 'gift', 'camera', 'gear'],
    },
}

GRID_SIZE = 9
CORRECT_RANGE = (3, 5)
TTL_SEGUNDOS = 180


# ─── Generación ────────────────────────────────────────────────────────────
def _key() -> bytes:
    return hashlib.sha256(
        ('captcha-v2::' + settings.SECRET_KEY).encode('utf-8')
    ).digest()


def _b64encode(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b'=').decode('ascii')


def _b64decode(s: str) -> bytes:
    pad = 4 - (len(s) % 4)
    return base64.urlsafe_b64decode(s + ('=' * pad))


def _sign(payload: bytes) -> bytes:
    return hmac.new(_key(), payload, hashlib.sha256).digest()


def generar_challenge(categoria: str | None = None) -> dict:
    """
    Devuelve un challenge listo para renderizar:
      {
        'pregunta':  'Selecciona todos los vehículos',
        'categoria': 'vehiculos',
        'celdas':    [{'nombre': 'car', 'svg': '<path .../>...'}, ...],
        'token':     'b64payload.b64firma',
      }
    """
    if categoria is None or categoria not in CHALLENGES:
        categoria = secrets.choice(list(CHALLENGES.keys()))

    bloque = CHALLENGES[categoria]
    n_correctos = secrets.choice(range(CORRECT_RANGE[0], CORRECT_RANGE[1] + 1))
    n_correctos = min(n_correctos, len(bloque['correctos']), GRID_SIZE - 2)

    rng = secrets.SystemRandom()
    correctos_nombres = rng.sample(bloque['correctos'], n_correctos)
    n_distractores = GRID_SIZE - n_correctos
    distractores_nombres = rng.sample(bloque['distractores'], n_distractores)

    nombres = correctos_nombres + distractores_nombres
    rng.shuffle(nombres)

    indices_correctos = sorted([i for i, n in enumerate(nombres) if n in correctos_nombres])

    payload = json.dumps({
        'c': categoria,
        'i': indices_correctos,
        'n': secrets.token_hex(8),
        't': int(time.time()),
    }, separators=(',', ':')).encode('utf-8')

    firma = _sign(payload)
    token = f'{_b64encode(payload)}.{_b64encode(firma)}'

    return {
        'pregunta':  bloque['pregunta'],
        'categoria': categoria,
        'celdas': [{'nombre': n, 'svg': ICONS[n]} for n in nombres],
        'token':     token,
    }


# ─── Verificación ──────────────────────────────────────────────────────────
class CaptchaError(Exception):
    def __init__(self, motivo: str):
        super().__init__(motivo)
        self.motivo = motivo


def verificar(token: str, indices_seleccionados: Iterable[int]) -> str:
    if not token or '.' not in token:
        raise CaptchaError('token_ausente')

    try:
        payload_b64, firma_b64 = token.split('.', 1)
        payload = _b64decode(payload_b64)
        firma = _b64decode(firma_b64)
    except Exception:
        raise CaptchaError('token_malformado')

    firma_esperada = _sign(payload)
    if not hmac.compare_digest(firma, firma_esperada):
        raise CaptchaError('firma_invalida')

    try:
        data = json.loads(payload.decode('utf-8'))
    except Exception:
        raise CaptchaError('payload_no_json')

    creado = int(data.get('t', 0))
    if creado <= 0 or (time.time() - creado) > TTL_SEGUNDOS:
        raise CaptchaError('expirado')

    correctos = set(int(x) for x in data.get('i', []))
    try:
        seleccion = set(int(x) for x in indices_seleccionados)
    except (TypeError, ValueError):
        raise CaptchaError('seleccion_invalida')

    if any(i < 0 or i >= GRID_SIZE for i in seleccion):
        raise CaptchaError('seleccion_fuera_rango')

    if seleccion != correctos:
        raise CaptchaError('respuesta_incorrecta')

    return data.get('c', 'desconocida')
