"""
Template tags personalizados.
"""
import hashlib
import re

from django import template
from django.conf import settings
from django.utils import timezone
from django.utils.html import escape
from django.utils.safestring import mark_safe

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
    Devuelve la fecha legible. SIEMPRE incluye día/mes (y año cuando aplica).
    No oculta la fecha real para correos viejos: el usuario debe poder ver
    cuándo es un correo SIN tener que abrirlo.

    Formato:
      - Hoy 14:32
      - Ayer 09:15
      - Lun 12 may · 14:32      (esta semana, dentro del año)
      - 12 may · 14:32          (este año, fuera de la última semana)
      - 12 may 2024 · 14:32     (años anteriores, fecha completa)
    """
    if not dt:
        return '—'

    ahora = timezone.localtime(timezone.now())
    dt_local = timezone.localtime(dt)
    delta = ahora.date() - dt_local.date()

    hora = f'{dt_local:%H:%M}'

    if delta.days == 0:
        return f'Hoy {hora}'
    if delta.days == 1:
        return f'Ayer {hora}'
    if 0 < delta.days < 7 and dt_local.year == ahora.year:
        return f'{_DIAS[dt_local.weekday()]} {dt_local.day} {_MESES[dt_local.month - 1]} · {hora}'
    if dt_local.year == ahora.year:
        return f'{dt_local.day} {_MESES[dt_local.month - 1]} · {hora}'
    return f'{dt_local.day} {_MESES[dt_local.month - 1]} {dt_local.year} · {hora}'


@register.filter
def fecha_iso(dt):
    """Fecha completa para tooltips: '2024-05-12 14:32:18 (-04)'."""
    if not dt:
        return ''
    dt_local = timezone.localtime(dt)
    return dt_local.strftime('%Y-%m-%d %H:%M:%S (%z)')


_RE_EMAIL_BRACKET = re.compile(r'<[^>]+>')


def _ini_email(direccion: str) -> str:
    """Iniciales de un email "bare": 'a@b.cl' → 'AB', 'oficina@rtsp.cl' → 'OR'."""
    local, _, domain = direccion.partition('@')
    return ((local[:1] or '?') + (domain[:1] or '?')).upper()


@register.filter
def avatar_iniciales(texto):
    """
    Devuelve hasta 2 letras iniciales del remitente.

    Casos:
      'Ana Ledezma'                       → 'AL'
      'Rodrigo Del saz <a@b.cl>'          → 'RS'   (sin contar el email)
      '<solo@email.cl>'                   → 'SE'   (local + domain del email)
      'oficina@rtsp.cl'                   → 'OR'
      'soporte'                           → 'SO'
      ''                                  → '?'

    Antes el bug: 'Rodrigo Del saz <a@b.cl>' producía 'R<' porque '<a@b.cl>'
    se contaba como una palabra y su primer char era '<'.
    """
    if not texto:
        return '?'
    # Quitar "<email>" si el texto trae 'Nombre <email>'.
    limpio = _RE_EMAIL_BRACKET.sub('', str(texto)).strip().strip('"\' ')
    palabras = [p for p in limpio.split() if p]

    # Caso: el texto era solo '<email>' (sin nombre). Volver al original sin <>.
    if not palabras:
        bare = str(texto).strip().strip('<>"\' ')
        if '@' in bare:
            return _ini_email(bare)
        return bare[:2].upper() if bare else '?'

    # Una sola palabra: si es un email, partir por @; sino tomar 2 primeros chars.
    if len(palabras) == 1:
        p = palabras[0]
        if '@' in p:
            return _ini_email(p)
        return p[:2].upper()

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


@register.filter
def dict_get(d, key):
    """Subscript con clave variable: {{ mi_dict|dict_get:obj.id }}."""
    if not d:
        return None
    try:
        return d.get(key)
    except AttributeError:
        return None


# ─── Sanitización de HTML de email ─────────────────────────────────────────
# bleach + tinycss2 (extras [css]) — limpia tags peligrosos, eventos JS,
# javascript: URLs, y propiedades CSS arbitrarias. Tres cleaners cacheados:
#
#   - INBOUND_STRICT (legacy filter `sanitizar_email_html`): strip todas las
#     <img>. Se mantiene por compatibilidad pero ya no se usa en los templates.
#
#   - INBOUND_SAFE_IMGS (`render_correo_html` simple_tag): permite <img> SOLO
#     con src relativa (nuestras URLs internas para cid:) o data:image. Bloquea
#     URLs externas (anti tracking-pixel). Asume que `cid:` ya fue pre-resuelto
#     por _resolver_cid_en_html() ANTES de invocar el cleaner.
#
#   - OUTBOUND (emails que enviamos): permite <img>, cid:, data: porque van
#     adjuntos del lado nuestro y el destinatario los embebe.
_EMAIL_CLEANER_INBOUND = None
_EMAIL_CLEANER_INBOUND_SAFE_IMGS = None
_EMAIL_CLEANER_OUTBOUND = None


_CSS_PROPS = [
    'color', 'background-color',
    'font', 'font-family', 'font-size', 'font-weight', 'font-style',
    'font-variant', 'line-height', 'letter-spacing', 'text-align',
    'text-decoration', 'text-transform', 'text-indent', 'white-space',
    'vertical-align',
    'margin', 'margin-top', 'margin-bottom', 'margin-left', 'margin-right',
    'padding', 'padding-top', 'padding-bottom', 'padding-left', 'padding-right',
    'border', 'border-top', 'border-bottom', 'border-left', 'border-right',
    'border-color', 'border-style', 'border-width', 'border-radius',
    'border-collapse', 'border-spacing',
    'width', 'height', 'min-width', 'min-height', 'max-width', 'max-height',
    'display', 'list-style', 'list-style-type', 'list-style-position',
    'overflow', 'word-wrap', 'word-break',
]


def _img_attr_filter_safe(tag, name, value):
    """
    Filtro de atributos para <img> en modo INBOUND_SAFE_IMGS.

    src permitido SOLO si:
      - empieza con '/'  (URL interna nuestra, ej. /intranet/correo/X/cid/Y)
      - empieza con 'data:image/'  (imagen base64 inline, signatures)

    Cualquier otro src (http, https, cid: no resuelto, javascript:, etc.) se
    bloquea y bleach quita el tag completo (porque sin src válido no sirve).
    """
    if name in {'alt', 'width', 'height', 'border', 'title', 'style', 'class'}:
        return True
    if name == 'src':
        v = (value or '').strip()
        if v.startswith('/'):
            return True
        if v.lower().startswith('data:image/'):
            return True
        return False
    return False


def _make_email_cleaner(modo: str):
    """
    modo ∈ {'inbound_strict', 'inbound_safe_imgs', 'outbound'}.
    """
    import bleach
    from bleach.css_sanitizer import CSSSanitizer

    tags = {
        'p', 'br', 'hr', 'div', 'span', 'blockquote', 'pre', 'code',
        'strong', 'b', 'em', 'i', 'u', 's', 'sup', 'sub', 'font',
        'h1', 'h2', 'h3', 'h4', 'h5', 'h6',
        'ul', 'ol', 'li', 'dl', 'dt', 'dd',
        'table', 'thead', 'tbody', 'tfoot', 'tr', 'td', 'th',
        'caption', 'colgroup', 'col',
        'a',
    }
    attrs = {
        '*':       ['class', 'style', 'align', 'valign', 'dir', 'title', 'lang'],
        'a':       ['href', 'name', 'target', 'rel', 'title'],
        'table':   ['border', 'cellpadding', 'cellspacing', 'width', 'height', 'summary'],
        'td':      ['colspan', 'rowspan', 'width', 'height', 'align', 'valign', 'nowrap'],
        'th':      ['colspan', 'rowspan', 'width', 'height', 'align', 'valign', 'scope'],
        'tr':      ['align', 'valign'],
        'col':     ['span', 'width'],
        'colgroup':['span', 'width'],
        'font':    ['color', 'face', 'size'],
    }
    protocols = ['http', 'https', 'mailto', 'tel']

    if modo == 'outbound':
        tags.add('img')
        attrs['img'] = ['src', 'alt', 'width', 'height', 'border', 'title', 'style']
        # cid: para imágenes inline del propio email; data: para base64.
        protocols.extend(['data', 'cid'])
    elif modo == 'inbound_safe_imgs':
        tags.add('img')
        # Filtro callable: bleach llama img_attr_filter(tag, name, value) por
        # cada atributo. La validación de src vive ahí (ver _img_attr_filter_safe).
        attrs['img'] = _img_attr_filter_safe
        # data: necesario para que el sanitizer no bloquee data:image/... por protocol.
        protocols.append('data')
    elif modo != 'inbound_strict':
        raise ValueError(f'modo desconocido: {modo}')

    css_sanitizer = CSSSanitizer(allowed_css_properties=_CSS_PROPS)
    return bleach.Cleaner(
        tags=tags,
        attributes=attrs,
        protocols=protocols,
        css_sanitizer=css_sanitizer,
        strip=True,
        strip_comments=True,
    )


def _email_cleaner_inbound():
    global _EMAIL_CLEANER_INBOUND
    if _EMAIL_CLEANER_INBOUND is None:
        _EMAIL_CLEANER_INBOUND = _make_email_cleaner('inbound_strict')
    return _EMAIL_CLEANER_INBOUND


def _email_cleaner_inbound_safe_imgs():
    global _EMAIL_CLEANER_INBOUND_SAFE_IMGS
    if _EMAIL_CLEANER_INBOUND_SAFE_IMGS is None:
        _EMAIL_CLEANER_INBOUND_SAFE_IMGS = _make_email_cleaner('inbound_safe_imgs')
    return _EMAIL_CLEANER_INBOUND_SAFE_IMGS


def _email_cleaner_outbound():
    global _EMAIL_CLEANER_OUTBOUND
    if _EMAIL_CLEANER_OUTBOUND is None:
        _EMAIL_CLEANER_OUTBOUND = _make_email_cleaner('outbound')
    return _EMAIL_CLEANER_OUTBOUND


# ─── Resolución de cid: a URLs internas ────────────────────────────────────
# `cid:5db34974-...` es la sintaxis MIME para referenciar un adjunto inline
# desde el HTML del mismo correo (ej: `<img src="cid:xxx">` en una signature
# o screenshot embebido). Sin resolver, queda como texto literal "[cid:xxx]"
# en el render → feo y roto.
#
# Captura tanto el formato dentro de una URL `cid:xxx` (en src/href) como
# la forma "marcada como link plain text" `[cid:xxx]` que se cuela cuando
# el cliente convirtió HTML a texto plano y la app lo renderea pre-formato.
_RE_CID_URL = re.compile(r'cid:([^"\'\s>)\]]+)', re.IGNORECASE)
_RE_CID_BRACKETED = re.compile(r'\[\s*cid\s*:\s*([^\]\s]+)\s*\]', re.IGNORECASE)


def _resolver_cid_en_html(html: str, correo) -> str:
    """
    Reemplaza `cid:xxx` en HTML por URLs internas autenticadas para los
    adjuntos del correo dado. Las refs no resueltas quedan tal cual y bleach
    las strippa (cid: no está en el protocols whitelist del cleaner safe-imgs).

    Una sola query a Adjunto: devuelve un dict {content_id: url} y reemplaza
    en bloque.
    """
    if not html or 'cid:' not in html.lower():
        return html

    from django.urls import reverse
    cids = list(correo.adjuntos.exclude(content_id='')
                       .values_list('content_id', flat=True))
    if not cids:
        return html

    cid_to_url = {
        cid: reverse('adjunto_por_cid',
                     kwargs={'correo_id': correo.id, 'content_id': cid})
        for cid in cids
    }

    def repl(m):
        cid = m.group(1).strip().rstrip('>"\')')
        return cid_to_url.get(cid, m.group(0))

    return _RE_CID_URL.sub(repl, html)


def _strip_cid_brackets_en_texto(texto: str) -> str:
    """
    Elimina `[cid:xxx]` del cuerpo en TEXTO PLANO. Cuando el correo tiene
    cuerpo HTML (con su <img> que pre-resolvemos), esta forma de bracket
    aparece como ruido en el fallback de texto plano.

    Solo strippa el bracket — el resto del texto queda intacto.
    """
    if not texto or '[cid:' not in texto.lower():
        return texto
    return _RE_CID_BRACKETED.sub('', texto).rstrip()


# ─── Pre-strip de bloques que bleach deja como texto ──────────────────────
# Bleach con strip=True remueve la tag pero NO el contenido. Para <style>,
# <script>, <head>, etc. esto causa que el CSS/JS aparezca como texto plano
# en el portal. Los limpiamos antes de pasar a bleach.
_RE_BLOQUES_INDESEADOS = re.compile(
    r'<\s*(style|script|head|title|template|noscript|xml|o:[A-Za-z0-9_-]+)\b[^>]*>.*?'
    r'<\s*/\s*\1\s*>',
    re.DOTALL | re.IGNORECASE,
)
_RE_BLOQUES_AUTOCLOSE = re.compile(r'<!\[CDATA\[.*?\]\]>', re.DOTALL)
_RE_HTML_COMMENT = re.compile(r'<!--.*?-->', re.DOTALL)


def _pre_strip_html_para_bleach(html: str) -> str:
    """Quita <style>, <script>, <head>, comentarios y CDATA antes del cleaner."""
    if not html:
        return ''
    html = _RE_BLOQUES_INDESEADOS.sub('', html)
    html = _RE_BLOQUES_AUTOCLOSE.sub('', html)
    html = _RE_HTML_COMMENT.sub('', html)
    return html


@register.filter(is_safe=True)
def sanitizar_email_html(html: str) -> str:
    """
    Sanitiza HTML para mostrar en el PORTAL. Strip <img> (anti tracking-pixels
    y cid: rotos). Bloquea <script>, <iframe>, eventos on*, javascript: URLs,
    background-image: url(...) externos, etc.

    Uso: {{ correo.cuerpo_html|sanitizar_email_html|safe }}
    """
    if not html:
        return ''
    try:
        return _email_cleaner_inbound().clean(_pre_strip_html_para_bleach(html))
    except Exception:
        from django.utils.html import strip_tags
        return strip_tags(html)


@register.simple_tag
def render_correo_html(correo):
    """
    Renderiza el HTML de un correo con cid: resueltos a URLs internas y
    sanitización con `<img>` permitido SOLO con src interna o data:image.

    Uso en template:
        {% render_correo_html correo %}

    Garantías de seguridad:
      1. Pre-pass: `cid:xxx` se mapea a /intranet/correo/<id>/cid/<xxx>
         (URL autenticada que valida acceso al buzón antes de servir).
      2. Bleach con cleaner inbound_safe_imgs: strippa <script>, eventos on*,
         javascript: URLs, src http/https externos (anti tracking-pixel),
         CSS arbitrario, etc.
      3. Tags <img> con src no permitido se eliminan por completo.

    Devuelve `mark_safe(html_limpio)` — listo para emitir directo en plantilla.
    """
    from django.utils.safestring import mark_safe

    html = correo.cuerpo_html or ''
    if not html:
        return ''
    try:
        html = _resolver_cid_en_html(html, correo)
        html = _pre_strip_html_para_bleach(html)
        return mark_safe(_email_cleaner_inbound_safe_imgs().clean(html))
    except Exception:
        from django.utils.html import strip_tags
        return mark_safe(strip_tags(html))


@register.filter(is_safe=True)
def limpiar_cid_brackets(texto):
    """
    Filter para usar en el render del cuerpo en TEXTO PLANO. Quita los
    `[cid:xxx]` literales que quedan cuando el correo trae imágenes inline
    pero solo estamos mostrando la versión texto.

    Uso:
        {{ correo.cuerpo_texto|limpiar_cid_brackets }}
    """
    return _strip_cid_brackets_en_texto(texto or '')


@register.filter(is_safe=True)
def sanitizar_email_html_outbound(html: str) -> str:
    """
    Sanitiza HTML para EMAILS QUE ENVIAMOS (forwards, replies). Permisivo
    con <img>, cid: y data: para que el destinatario vea el formato original
    como en Gmail. Sigue bloqueando scripts, iframes, javascript: URLs, etc.

    Strippa texto literal `[cid:xxx]` que algunos clientes meten cuando
    convierten HTML con imágenes inline a texto plano. Sin esto, el destinatario
    veía "[cid:5db3...]" en el medio del cuerpo (ver screenshots de la sesión
    de UX 2026-05-08).

    Uso: {{ correo.cuerpo_html|sanitizar_email_html_outbound|safe }}
    """
    if not html:
        return ''
    try:
        clean = _email_cleaner_outbound().clean(_pre_strip_html_para_bleach(html))
    except Exception:
        from django.utils.html import strip_tags
        return strip_tags(html)
    return _strip_cid_brackets_en_texto(clean)


@register.simple_tag(takes_context=True)
def url_sin_filtros(context, *quitar):
    """
    Devuelve la URL del inbox con la querystring actual menos las keys listadas.
    Siempre quita `page` también (cambiar un filtro debe llevar a página 1).

    Devuelve una URL absoluta a {% url 'inbox' %} — relativos rompen cuando el
    JS hizo pushState a /intranet/correo/N/ y un click en un chip resolvería
    contra esa URL.

    Uso en template:
        <a href="{% url_sin_filtros 'q' %}">Quitar búsqueda</a>
        <a href="{% url_sin_filtros 'desde' 'hasta' %}">Quitar rango fechas</a>
    """
    from django.urls import reverse
    base = reverse('inbox')
    request = context.get('request')
    if request is None:
        return base
    qs = request.GET.copy()
    for key in quitar:
        qs.pop(key, None)
    qs.pop('page', None)
    encoded = qs.urlencode()
    return f'{base}?{encoded}' if encoded else base


# ─── Render de firma de buzón (auto-append en correos salientes) ──────────
def render_firma_html(buzon) -> str:
    """
    Devuelve el HTML de la firma de un buzón, con escape de los datos del
    usuario. Si el buzón no tiene firma activa o no tiene datos, devuelve ''.

    Layout: tabla MIME-safe (style inline) — el logo a la izquierda y los
    datos a la derecha. Si solo está seteado el email visible (caso default),
    sale logo + email nada más.
    """
    if not buzon or not getattr(buzon, 'firma_activa', True):
        return ''

    nombre   = (buzon.firma_nombre or '').strip()
    cargo    = (buzon.firma_cargo or '').strip()
    telefono = (buzon.firma_telefono or '').strip()
    email_v  = (buzon.firma_email_visible or buzon.email or '').strip()
    logo_url = getattr(settings, 'FIRMA_LOGO_URL', '') or ''

    # Si no hay nada que mostrar, no firmamos.
    if not (nombre or cargo or telefono or email_v or logo_url):
        return ''

    # Construir las filas de datos (tabla interna).
    filas = []
    if nombre:
        filas.append(
            f'<tr><td style="font-size:14px;font-weight:700;color:#1a1f22;'
            f'padding:0 0 4px;line-height:1.3">{escape(nombre)}</td></tr>'
        )
    if cargo:
        filas.append(
            f'<tr><td style="font-size:11px;font-weight:600;letter-spacing:1px;'
            f'text-transform:uppercase;color:#6b7280;padding:0 0 8px">'
            f'{escape(cargo)}</td></tr>'
        )
    if telefono:
        filas.append(
            f'<tr><td style="font-size:13px;color:#394348;padding:2px 0">'
            f'<span style="color:#C80C0F;font-weight:700;margin-right:6px">&#9742;</span>'
            f'{escape(telefono)}</td></tr>'
        )
    if email_v:
        filas.append(
            f'<tr><td style="font-size:13px;color:#394348;padding:2px 0">'
            f'<span style="color:#C80C0F;font-weight:700;margin-right:6px">&#9993;</span>'
            f'<a href="mailto:{escape(email_v)}" style="color:#394348;text-decoration:none">'
            f'{escape(email_v)}</a></td></tr>'
        )

    datos_tabla = (
        '<table cellpadding="0" cellspacing="0" border="0" role="presentation" '
        'style="border-collapse:collapse;border-left:3px solid #C80C0F;'
        'padding-left:14px;margin-left:14px">'
        + ''.join(filas)
        + '</table>'
    )

    # Logo (opcional)
    logo_celda = ''
    if logo_url:
        logo_celda = (
            f'<td valign="middle" style="padding-right:14px">'
            f'<img src="{escape(logo_url)}" alt="Pietramonte" '
            f'style="display:block;max-width:140px;height:auto" width="140"></td>'
        )

    html = (
        '<div class="pm-firma" style="margin-top:24px;padding-top:14px;'
        'border-top:1px solid #e8e8e8;font-family:-apple-system,BlinkMacSystemFont,'
        '\'Segoe UI\',Helvetica,Arial,sans-serif">'
        '<table cellpadding="0" cellspacing="0" border="0" role="presentation" '
        'style="border-collapse:collapse">'
        f'<tr>{logo_celda}<td valign="middle">{datos_tabla}</td></tr>'
        '</table></div>'
    )
    return html


def render_firma_texto(buzon) -> str:
    """Versión texto plano de la firma (para multipart/alternative)."""
    if not buzon or not getattr(buzon, 'firma_activa', True):
        return ''
    nombre   = (buzon.firma_nombre or '').strip()
    cargo    = (buzon.firma_cargo or '').strip()
    telefono = (buzon.firma_telefono or '').strip()
    email_v  = (buzon.firma_email_visible or buzon.email or '').strip()

    lineas = ['--']
    if nombre:   lineas.append(nombre)
    if cargo:    lineas.append(cargo)
    if telefono: lineas.append(f'Tel: {telefono}')
    if email_v:  lineas.append(email_v)
    if len(lineas) == 1:   # solo el "--"
        return ''
    return '\n'.join(lineas)


@register.simple_tag
def firma_html(buzon):
    """Renderiza la firma HTML del buzón. Marcada como segura (escaping interno)."""
    return mark_safe(render_firma_html(buzon))


@register.simple_tag
def firma_texto(buzon):
    """Renderiza la firma en texto plano del buzón."""
    return render_firma_texto(buzon)
