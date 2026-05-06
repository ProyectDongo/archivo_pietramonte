"""
Template tags personalizados.
"""
import hashlib

from django import template
from django.utils import timezone

register = template.Library()


# Paleta para avatares — colores derivados del logo Pietramonte (rojo + grafitos).
_AVATAR_COLORS = [
    ('#C80C0F', '#ffffff'),  # rojo
    ('#394348', '#ffffff'),  # grafito medio
    ('#1a1f22', '#ffffff'),  # grafito oscuro
    ('#9a0a0c', '#ffffff'),  # rojo oscuro
    ('#2c5364', '#ffffff'),  # azul acero
    ('#5d4037', '#ffffff'),  # marrón
    ('#37474f', '#ffffff'),  # gris azulado
    ('#6d4c41', '#ffffff'),  # marrón claro
]


_DIAS = ['Lun', 'Mar', 'Mié', 'Jue', 'Vie', 'Sáb', 'Dom']
_MESES = ['ene', 'feb', 'mar', 'abr', 'may', 'jun',
          'jul', 'ago', 'sep', 'oct', 'nov', 'dic']


@register.filter
def fecha_amigable(dt):
    """
    Convierte una fecha en algo legible:
      - Hoy 14:32
      - Ayer 09:15
      - Lun 12 may
      - 13/03/2024
    """
    if not dt:
        return '—'

    ahora = timezone.localtime(timezone.now())
    dt_local = timezone.localtime(dt)
    delta = ahora.date() - dt_local.date()

    if delta.days == 0:
        return f'Hoy {dt_local:%H:%M}'
    if delta.days == 1:
        return f'Ayer {dt_local:%H:%M}'
    if 0 < delta.days < 7:
        return f'{_DIAS[dt_local.weekday()]} {dt_local:%H:%M}'
    if dt_local.year == ahora.year:
        return f'{dt_local.day} {_MESES[dt_local.month - 1]}'
    return dt_local.strftime('%d/%m/%Y')


@register.filter
def avatar_iniciales(texto):
    """Devuelve hasta 2 letras iniciales: 'Ana Ledezma' → 'AL'."""
    if not texto:
        return '?'
    palabras = [p for p in str(texto).strip().split() if p]
    if not palabras:
        return '?'
    if len(palabras) == 1:
        return palabras[0][:2].upper()
    return (palabras[0][0] + palabras[-1][0]).upper()


@register.filter
def avatar_color(texto):
    """Color determinístico para un avatar dado un string (email/nombre)."""
    if not texto:
        return _AVATAR_COLORS[0][0]
    h = int(hashlib.md5(str(texto).encode()).hexdigest()[:8], 16)
    return _AVATAR_COLORS[h % len(_AVATAR_COLORS)][0]


# Mapeo MIME → categoría visual usada en la galería de adjuntos.
# Image, pdf, sheet, doc, slides, zip, audio, video, code, text, otro.
_TIPO_BY_PREFIX = {
    'image/': 'imagen',
    'audio/': 'audio',
    'video/': 'video',
    'text/':  'texto',
}
_TIPO_BY_EXACT = {
    'application/pdf':                                                         'pdf',
    'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet':       'sheet',
    'application/vnd.ms-excel':                                                'sheet',
    'application/vnd.oasis.opendocument.spreadsheet':                          'sheet',
    'application/vnd.openxmlformats-officedocument.wordprocessingml.document': 'doc',
    'application/msword':                                                      'doc',
    'application/vnd.oasis.opendocument.text':                                 'doc',
    'application/vnd.openxmlformats-officedocument.presentationml.presentation': 'slides',
    'application/vnd.ms-powerpoint':                                           'slides',
    'application/zip':                                                         'zip',
    'application/x-zip-compressed':                                            'zip',
    'application/x-rar-compressed':                                            'zip',
    'application/x-7z-compressed':                                             'zip',
    'application/json':                                                        'codigo',
    'application/javascript':                                                  'codigo',
    'application/xml':                                                         'codigo',
}


@register.filter
def tipo_archivo(adjunto):
    """Devuelve la categoría visual ('imagen' / 'pdf' / 'doc' / ...) de un Adjunto."""
    mime = (getattr(adjunto, 'mime_type', '') or '').lower().strip()
    if mime in _TIPO_BY_EXACT:
        return _TIPO_BY_EXACT[mime]
    for prefijo, t in _TIPO_BY_PREFIX.items():
        if mime.startswith(prefijo):
            return t
    return 'otro'


@register.filter
def es_imagen(adjunto):
    """True si el adjunto es una imagen renderizable inline."""
    mime = (getattr(adjunto, 'mime_type', '') or '').lower()
    return mime.startswith('image/')
