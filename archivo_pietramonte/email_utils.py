"""
Helpers de email — capa fina sobre `django.core.mail` con dos cosas extra:

  - `enviar_mail(...)`: renderiza template HTML + texto plano automático,
    arma un MultiAlternatives con Reply-To y manda. Usa los aliases
    configurados en settings (`EMAIL_*_FROM`).
  - `safe_send(...)`: igual pero atrapa la excepción y devuelve un dict
    `{'ok': bool, 'error': str|None}`. Útil cuando un fallo SMTP no debe
    tumbar la vista que llama (ej: confirmación de reserva — la reserva
    se creó, el mail puede reintentarse).

Templates:
  - Buscamos `<template>.html` y opcionalmente `<template>.txt`.
  - Si no hay .txt, se deriva del HTML con `strip_tags`.

Adjuntos:
  - Pasar como lista de tuplas (filename, content_bytes, mimetype).

Ejemplo:
    enviar_mail(
        asunto='Confirmá tu reserva',
        para='cliente@gmail.com',
        template='taller/email/nueva_reserva_cliente',
        contexto={'reserva': r, 'token': t},
        from_alias=settings.EMAIL_AGENDA_FROM,
        reply_to=[settings.EMAIL_REPLY_TO_AGENDA] if settings.EMAIL_REPLY_TO_AGENDA else None,
    )
"""
from __future__ import annotations

import logging
from typing import Iterable

from django.conf import settings
from django.core.mail import EmailMultiAlternatives
from django.template import TemplateDoesNotExist
from django.template.loader import render_to_string
from django.utils.html import strip_tags

logger = logging.getLogger('archivo_pietramonte.email')


def _to_list(x) -> list[str]:
    if not x:
        return []
    if isinstance(x, str):
        return [x]
    return [str(v) for v in x]


def enviar_mail(
    *,
    asunto: str,
    para,                    # str o list[str]
    template: str,           # ruta sin extensión, ej: 'taller/email/nueva_reserva_cliente'
    contexto: dict | None = None,
    from_alias: str | None = None,
    reply_to: Iterable[str] | str | None = None,
    cc: Iterable[str] | str | None = None,
    bcc: Iterable[str] | str | None = None,
    adjuntos: list[tuple[str, bytes, str]] | None = None,
    inline_images: list[tuple[str, bytes, str, str]] | None = None,
    headers: dict | None = None,    # ej {'In-Reply-To': '<xxx>', 'References': '<xxx>'}
    fail_silently: bool = False,
) -> int:
    """
    Renderiza y manda un email HTML+texto. Devuelve la cantidad de mensajes
    enviados (0 si fail_silently=True y hubo error).

    Parameters
    ----------
    asunto         Subject del email.
    para           Destinatario(s).
    template       Path del template SIN extensión. Renderiza .html (obligatorio)
                   y .txt (opcional — si falta, se deriva del HTML con strip_tags).
    contexto       Dict para los templates.
    from_alias     Override del From (ej: settings.EMAIL_AGENDA_FROM). Si es None,
                   usa DEFAULT_FROM_EMAIL.
    reply_to       Direcciones que reciben las respuestas (lista o string).
    adjuntos       Lista de (filename, content_bytes, mimetype) — attachments
                   normales (van como descargables, no se embeben).
    inline_images  Lista de (filename, content_bytes, mimetype, content_id) —
                   imágenes referenciadas desde el HTML como `<img src="cid:xxx">`.
                   Se adjuntan con `Content-ID: <xxx>` y `Content-Disposition:
                   inline` para que el cliente del destinatario las resuelva.
                   Sin esto, las refs cid: salen rotas (el recipient ve `[cid:xxx]`).
    """
    from email.mime.image import MIMEImage
    from email.mime.base import MIMEBase
    from email import encoders

    contexto = contexto or {}
    para_list      = _to_list(para)
    reply_to_list  = _to_list(reply_to) or None
    cc_list        = _to_list(cc) or None
    bcc_list       = _to_list(bcc) or None

    if not para_list:
        raise ValueError('enviar_mail: "para" no puede estar vacío.')

    # ─── Render templates ──────────────────────────────────────────────
    html_body = render_to_string(f'{template}.html', contexto)
    try:
        text_body = render_to_string(f'{template}.txt', contexto)
    except TemplateDoesNotExist:
        # Derivamos del HTML — útil para clientes de correo en texto plano.
        text_body = strip_tags(html_body)

    from_email = from_alias or settings.DEFAULT_FROM_EMAIL

    msg = EmailMultiAlternatives(
        subject=asunto,
        body=text_body,
        from_email=from_email,
        to=para_list,
        cc=cc_list,
        bcc=bcc_list,
        reply_to=reply_to_list,
        headers=headers or None,
    )
    # Cambiamos el subtype default ('mixed') a 'related' SOLO si hay inline
    # images. 'related' es el contenedor MIME correcto para HTML + recursos
    # referenciados con cid: — sin esto algunos clientes no resuelven.
    if inline_images:
        msg.mixed_subtype = 'related'

    msg.attach_alternative(html_body, 'text/html')

    if inline_images:
        for filename, content, mimetype, cid in inline_images:
            # Si el mime es image/<algo>, MIMEImage es el wrapper correcto.
            # Si por algún motivo viene otra cosa, fallback a MIMEBase genérico.
            main, _, sub = (mimetype or 'application/octet-stream').partition('/')
            if main == 'image' and sub:
                part = MIMEImage(content, _subtype=sub)
            else:
                part = MIMEBase(main or 'application', sub or 'octet-stream')
                part.set_payload(content)
                encoders.encode_base64(part)
            # Content-ID DEBE ir entre <>. El HTML referencia "cid:xxx" sin <>.
            part.add_header('Content-ID', f'<{cid}>')
            part.add_header('Content-Disposition', 'inline', filename=filename or 'inline')
            msg.attach(part)

    if adjuntos:
        for filename, content, mimetype in adjuntos:
            msg.attach(filename, content, mimetype)

    return msg.send(fail_silently=fail_silently)


def safe_send(**kwargs) -> dict:
    """
    Wrapper de `enviar_mail` que NO levanta excepciones — devuelve
    `{'ok': bool, 'error': str|None, 'enviados': int}`.

    Pensado para vistas donde el fallo del email no debe romper el flujo:
    la reserva ya se creó, el mail puede reintentarse vía cron de reminders.
    """
    try:
        n = enviar_mail(fail_silently=False, **kwargs)
        return {'ok': bool(n), 'error': None, 'enviados': n}
    except Exception as e:
        logger.exception('Error enviando mail asunto=%r para=%r', kwargs.get('asunto'), kwargs.get('para'))
        return {'ok': False, 'error': str(e), 'enviados': 0}
