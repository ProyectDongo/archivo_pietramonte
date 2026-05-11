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

Hardening 2026-05-11 (post-incidente OVERQUOTA + OOM):
  - `fetch_nuevos` ahora es generator → memoria constante (no carga
    miles de mensajes a RAM antes de procesar).
  - Timeout TCP de 120s → conexión IMAP no se cuelga eternamente si
    Gmail tarda en responder.
  - `imap.close()` antes de logout en finally → no quedan conexiones
    half-open del lado de Gmail (que es lo que acumula el OVERQUOTA).
  - `OverquotaError` excepción específica para que el caller pueda
    pausar el sync N horas en vez de fallar silencioso.
"""
from __future__ import annotations

import imaplib
import logging
import re
from contextlib import contextmanager

from django.conf import settings


logger = logging.getLogger('correos.gmail_sync')


# Timeout TCP en segundos para todas las operaciones IMAP. Si Gmail no responde
# en este tiempo, el socket levanta excepción y liberamos la conexión, en vez
# de quedar colgados hasta que el OOM killer lo mate (lo que dejaba la
# conexión half-open del lado de Gmail y disparaba OVERQUOTA).
IMAP_TIMEOUT_SEG = 120


class ImapError(RuntimeError):
    """Error de conexión / select / fetch IMAP."""


class OverquotaError(ImapError):
    """
    Gmail bloqueó la cuenta por exceder los límites de IMAP (conexiones/
    bandwidth/comandos por día). Se desbloquea solo en 12-24h.
    El caller debe pausar el sync durante ese período en vez de reintentar.
    """


def _es_overquota(error_obj) -> bool:
    """Detecta si una IMAP4.error es por [OVERQUOTA]."""
    return 'OVERQUOTA' in str(error_obj).upper()


def _es_conexion_muerta(error_obj) -> bool:
    """
    Detecta si el error indica que la conexión IMAP se cayó (SSL EOF,
    socket roto, broken pipe). Si la conexión murió, seguir intentando
    sobre la misma conexión es inútil — hay que abortar y reconectar en
    la próxima corrida.
    """
    s = str(error_obj).lower()
    return any(marker in s for marker in [
        'eof',
        'socket error',
        'broken pipe',
        'connection reset',
        'connection aborted',
        'closed by',
        'timed out',
    ])


@contextmanager
def imap_connection():
    """
    Context manager que abre IMAP4_SSL contra Gmail con la App Password.
    Hace close + logout al salir, incluso si hay excepción.

    `close()` antes de `logout()` es importante: select pone un mailbox en
    estado "selected" en el server; sin close el server lo mantiene abierto
    en el estado interno. Acumulado en múltiples crashes silenciosos (OOM
    killer mata el proceso sin chance al finally), esto contribuye al
    OVERQUOTA.

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

    imap = imaplib.IMAP4_SSL(host, port, timeout=IMAP_TIMEOUT_SEG)
    try:
        try:
            imap.login(user, pwd)
        except imaplib.IMAP4.error as e:
            if _es_overquota(e):
                raise OverquotaError(f'Gmail rate-limit: {e}') from e
            raise ImapError(f'Login IMAP rechazado: {e}') from e
        yield imap
    finally:
        # close() cierra el mailbox SELECTED (si había uno). Si nunca hicimos
        # select(), close() puede fallar — lo silenciamos. Después logout().
        try:
            imap.close()
        except Exception:
            pass
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


def fetch_nuevos(label_name: str, last_uid: int = 0):
    """
    Generator que yieldea (uid, raw_rfc822_bytes) de los mensajes con
    UID > last_uid del label dado, en READONLY.

    Si last_uid == 0, recorre todo el contenido del label.

    Generator → memoria CONSTANTE: procesa uno por uno en vez de cargar
    todos los mensajes a RAM antes de empezar. Anti-OOM en buzones grandes
    (label con 5000+ correos).

    Errores:
      - OverquotaError: Gmail bloqueó la cuenta por exceso de uso IMAP.
        El caller debe pausar el sync y reintentar después de 12-24h.
      - ImapError: cualquier otro fallo de IMAP (select inválido, etc).

    Errores en un mensaje INDIVIDUAL no abortan el resto — se skipea.
    """
    with imap_connection() as imap:
        # Quoteamos el nombre del label porque puede tener espacios o /.
        select_arg = f'"{label_name}"'
        try:
            typ, _ = imap.select(select_arg, readonly=True)
        except imaplib.IMAP4.error as e:
            if _es_overquota(e):
                raise OverquotaError(f'select {label_name}: {e}') from e
            raise ImapError(f'No se pudo seleccionar label {label_name}: {e}') from e
        if typ != 'OK':
            raise ImapError(f'No se pudo seleccionar label: {label_name}')

        if last_uid > 0:
            # UID search con rango "X:*" — IMAP devuelve TODOS los UIDs
            # del rango, INCLUYENDO el último de la carpeta aunque sea ≤ X.
            # Por eso filtramos a mano abajo.
            criterio = f'UID {last_uid + 1}:*'.encode('ascii')
        else:
            criterio = b'ALL'
        try:
            typ, data = imap.uid('search', None, criterio)
        except imaplib.IMAP4.error as e:
            if _es_overquota(e):
                raise OverquotaError(f'search {label_name}: {e}') from e
            raise ImapError(f'IMAP search error: {e}') from e
        if typ != 'OK':
            return
        if not data or not data[0]:
            return

        uids = sorted(set(int(u) for u in data[0].split() if int(u) > last_uid))

        for uid in uids:
            uid_b = str(uid).encode('ascii')
            try:
                typ, msg_data = imap.uid('fetch', uid_b, '(RFC822)')
            except imaplib.IMAP4.abort as e:
                # IMAP4.abort = la librería ya considera la conexión muerta.
                # Cualquier fetch posterior va a fallar igual.
                raise ImapError(f'IMAP abort en fetch uid={uid}: {e}') from e
            except imaplib.IMAP4.error as e:
                # OVERQUOTA en medio del fetch → abortar todo el sync para
                # no extender el bloqueo (cada fetch que falla con
                # OVERQUOTA cuenta como otro intento contra la cuota).
                if _es_overquota(e):
                    raise OverquotaError(f'fetch uid={uid}: {e}') from e
                # Conexión muerta (SSL EOF, socket roto, broken pipe): hay
                # que abortar el for loop entero. Seguir intentando sobre
                # una conexión cerrada genera miles de warnings inútiles y
                # cero inserts. La próxima corrida del cron reconecta limpio.
                if _es_conexion_muerta(e):
                    raise ImapError(
                        f'Conexión IMAP perdida en fetch uid={uid}: {e}'
                    ) from e
                # Error puntual del mensaje (borrado del label en el medio,
                # encoding raro, etc.): skip ese mensaje y seguir.
                logger.warning('Skip uid=%s en %s: %s', uid, label_name, e)
                continue
            except (OSError, ConnectionError) as e:
                # Socket-level error: idem, conexión rota.
                raise ImapError(
                    f'Socket error en fetch uid={uid}: {e}'
                ) from e
            if typ != 'OK' or not msg_data:
                continue
            for chunk in msg_data:
                if isinstance(chunk, tuple) and len(chunk) >= 2 and isinstance(chunk[1], (bytes, bytearray)):
                    yield (uid, bytes(chunk[1]))
                    break
