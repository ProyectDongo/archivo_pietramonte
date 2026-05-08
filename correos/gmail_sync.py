"""
IMAP client para sincronizar correos desde Gmail al archivo.

Diseño:
  - Una sola cuenta IMAP (la centralizadora: soporte.dongo@gmail.com).
  - Filtros de Gmail aplican labels a cada email entrante (uno por buzón).
  - Por cada `BuzonGmailLabel` activo, fetcheamos los mensajes con
    UID > last_uid de la "carpeta" (label) correspondiente.
  - El mensaje se parsea con los mismos helpers de import_mbox.
  - Dedup por mensaje_id heredado del flow normal: si el cron corre
    2 veces no duplica.

Esto NO escribe a Gmail (readonly select). NO marca como leído. NO mueve
mensajes. Solo lee.
"""
from __future__ import annotations

import imaplib
import logging
import re
from contextlib import contextmanager

from django.conf import settings


logger = logging.getLogger('correos.gmail_sync')


class ImapError(RuntimeError):
    """Error de conexión / select / fetch IMAP."""


@contextmanager
def imap_connection():
    """
    Context manager que abre IMAP4_SSL contra Gmail con la App Password.
    Hace logout al salir, incluso si hay excepción.

    Credenciales (lookup en orden):
      1. GMAIL_IMAP_USER + GMAIL_IMAP_PASSWORD  ← preferidas, separadas del SMTP
      2. EMAIL_HOST_USER + EMAIL_HOST_PASSWORD  ← fallback histórico (cuando el
         outbound SMTP también era Gmail). Cuando el outbound se mueve a otro
         proveedor (ej. Resend), las EMAIL_HOST_* dejan de servir para IMAP y
         hay que setear las GMAIL_IMAP_* explícitas con la App Password.
    """
    user = (
        getattr(settings, 'GMAIL_IMAP_USER', '')
        or settings.EMAIL_HOST_USER
    )
    pwd  = (
        getattr(settings, 'GMAIL_IMAP_PASSWORD', '')
        or settings.EMAIL_HOST_PASSWORD
    )
    if not user or not pwd:
        raise ImapError(
            'Falta GMAIL_IMAP_USER / GMAIL_IMAP_PASSWORD (o el fallback '
            'EMAIL_HOST_USER / EMAIL_HOST_PASSWORD) en env. '
            'Configurá la App Password de Gmail antes de sincronizar.'
        )
    host = getattr(settings, 'GMAIL_IMAP_HOST', 'imap.gmail.com')
    port = getattr(settings, 'GMAIL_IMAP_PORT', 993)

    imap = imaplib.IMAP4_SSL(host, port)
    try:
        try:
            imap.login(user, pwd)
        except imaplib.IMAP4.error as e:
            raise ImapError(f'Login IMAP rechazado: {e}') from e
        yield imap
    finally:
        try:
            imap.logout()
        except Exception:
            # logout post-fetch — fallar acá no afecta la sincronización ya hecha,
            # pero queremos saberlo (puede indicar conexión muerta / Gmail caído).
            logger.warning('Fallo en imap.logout()', exc_info=True)


# Regex para parsear la respuesta de imap.list():
#   (\HasNoChildren) "/" "INBOX"
#   (\HasChildren \Noselect) "/" "[Gmail]"
#   (\HasNoChildren) "/" "[Gmail]/All Mail"
_LIST_RE = re.compile(r'\(([^)]*)\)\s+"([^"]*)"\s+(?:"([^"]+)"|(\S+))')


def listar_labels() -> list[str]:
    """
    Devuelve los labels (mailboxes) seleccionables de la cuenta IMAP.
    Filtra los flagged \\Noselect (categorías que no se pueden abrir).
    """
    out: list[str] = []
    with imap_connection() as imap:
        typ, data = imap.list()
        if typ != 'OK' or not data:
            return out
        for raw in data:
            if raw is None:
                continue
            line = raw.decode('utf-8', errors='replace') if isinstance(raw, bytes) else raw
            m = _LIST_RE.match(line)
            if not m:
                continue
            flags, _delim, quoted, unquoted = m.groups()
            name = quoted or unquoted or ''
            if not name:
                continue
            if '\\Noselect' in flags:
                continue
            out.append(name)
    return out


def fetch_nuevos(label_name: str, last_uid: int = 0) -> list[tuple[int, bytes]]:
    """
    Fetchea los mensajes con UID > last_uid del label dado, en READONLY.
    Devuelve lista [(uid, raw_rfc822_bytes), ...] ordenada por uid asc.

    Si last_uid == 0, trae todo el contenido del label.

    Lanza ImapError si el select falla.
    """
    out: list[tuple[int, bytes]] = []
    with imap_connection() as imap:
        # Quoteamos el nombre del label porque puede tener espacios o /.
        select_arg = f'"{label_name}"'
        typ, _ = imap.select(select_arg, readonly=True)
        if typ != 'OK':
            raise ImapError(f'No se pudo seleccionar label: {label_name}')

        if last_uid > 0:
            # UID search con rango "X:*" — IMAP devuelve TODOS los UIDs
            # del rango, INCLUYENDO el último de la carpeta aunque sea ≤ X.
            # Por eso filtramos a mano abajo.
            criterio = f'UID {last_uid + 1}:*'.encode('ascii')
        else:
            criterio = b'ALL'
        typ, data = imap.uid('search', None, criterio)
        if typ != 'OK':
            return out
        if not data or not data[0]:
            return out

        uids = [int(u) for u in data[0].split()]
        uids = sorted(set(u for u in uids if u > last_uid))

        for uid in uids:
            uid_b = str(uid).encode('ascii')
            typ, msg_data = imap.uid('fetch', uid_b, '(RFC822)')
            if typ != 'OK' or not msg_data:
                continue
            for chunk in msg_data:
                if isinstance(chunk, tuple) and len(chunk) >= 2 and isinstance(chunk[1], (bytes, bytearray)):
                    out.append((uid, bytes(chunk[1])))
                    break
    return out
