"""
Importa archivos .mbox a la base de datos, extrayendo también los adjuntos.

Uso:
    python manage.py import_mbox aledezma@pietramonte.cl --archivo=/ruta/Inbox
    python manage.py import_mbox aledezma@pietramonte.cl --carpeta=/ruta/carpeta/

Por seguridad, los adjuntos se guardan en MEDIA_ROOT/adjuntos/<año>/<mes>/...
con nombre único, fuera del directorio del repositorio.
"""
import email
import email.header
import email.utils
import mailbox
import re
from pathlib import Path

import chardet
from django.core.files.base import ContentFile
from django.core.management.base import BaseCommand, CommandError
from django.utils import timezone

from correos.models import Adjunto, Buzon, Correo


# Tamaño máximo por adjunto (más grande lo saltamos para no inflar disco)
MAX_ADJUNTO_BYTES = 25 * 1024 * 1024   # 25 MB

# Caracteres no permitidos en filenames (cross-platform safe)
_FILENAME_BAD = re.compile(r'[<>:"/\\|?*\x00-\x1f]')


def detectar_carpeta(filename: str) -> str:
    """
    Heurística por nombre de archivo .mbox para inferir si es Inbox / Sent / Otros.
    Acepta variantes en español e inglés que produce Gmail Takeout y exports.
    """
    n = filename.lower()
    if 'sent' in n or 'enviado' in n:
        return 'enviados'
    if 'inbox' in n or 'bandeja' in n or 'recibido' in n:
        return 'inbox'
    return 'otros'


def decodificar_header(valor):
    if not valor:
        return ''
    try:
        partes = email.header.decode_header(valor)
        resultado = []
        for parte, charset in partes:
            if isinstance(parte, bytes):
                if charset:
                    try:
                        resultado.append(parte.decode(charset, errors='replace'))
                    except (LookupError, UnicodeDecodeError):
                        resultado.append(parte.decode('utf-8', errors='replace'))
                else:
                    resultado.append(parte.decode('utf-8', errors='replace'))
            else:
                resultado.append(str(parte))
        return ' '.join(resultado)
    except Exception:
        return str(valor)


def extraer_texto(msg):
    """Cuerpo en texto plano del mensaje."""
    texto = []
    if msg.is_multipart():
        for parte in msg.walk():
            content_type = parte.get_content_type()
            disposition = str(parte.get('Content-Disposition', ''))
            if content_type == 'text/plain' and 'attachment' not in disposition:
                payload = parte.get_payload(decode=True)
                if payload:
                    charset = parte.get_content_charset() or 'utf-8'
                    try:
                        texto.append(payload.decode(charset, errors='replace'))
                    except (LookupError, UnicodeDecodeError):
                        detected = chardet.detect(payload)
                        enc = detected.get('encoding') or 'utf-8'
                        texto.append(payload.decode(enc, errors='replace'))
    else:
        payload = msg.get_payload(decode=True)
        if payload:
            charset = msg.get_content_charset() or 'utf-8'
            try:
                texto.append(payload.decode(charset, errors='replace'))
            except Exception:
                texto.append(payload.decode('latin-1', errors='replace'))
    return '\n'.join(texto)[:50000]


def _nombre_seguro(nombre: str, fallback: str = 'archivo.bin') -> str:
    """Sanea un filename: sin paths, sin chars peligrosos, max 200 chars."""
    if not nombre:
        return fallback
    # Solo el basename
    nombre = Path(nombre).name
    # Reemplaza chars no permitidos
    nombre = _FILENAME_BAD.sub('_', nombre).strip(' .')
    if not nombre:
        return fallback
    return nombre[:200]


def extraer_adjuntos(msg):
    """
    Devuelve lista de tuplas (nombre_original, mime_type, contenido_bytes)
    para todos los adjuntos del mensaje.
    """
    adjuntos = []
    if not msg.is_multipart():
        return adjuntos

    for parte in msg.walk():
        disposition = str(parte.get('Content-Disposition', ''))
        if 'attachment' not in disposition.lower():
            # También considera inline con filename (imágenes embebidas)
            if 'filename' not in disposition.lower():
                continue
        try:
            payload = parte.get_payload(decode=True)
            if not payload:
                continue
            if len(payload) > MAX_ADJUNTO_BYTES:
                continue
            nombre = decodificar_header(parte.get_filename() or '')
            nombre = _nombre_seguro(nombre)
            mime = parte.get_content_type() or 'application/octet-stream'
            adjuntos.append((nombre, mime, payload))
        except Exception:
            continue
    return adjuntos


class Command(BaseCommand):
    help = 'Importa archivos .mbox a la base de datos (incluyendo adjuntos)'

    def add_arguments(self, parser):
        parser.add_argument('email', type=str, help='Email del buzón a importar')
        parser.add_argument('--archivo', type=str, help='Ruta al archivo .mbox')
        parser.add_argument('--dir', dest='dir', type=str,
                            help='Directorio con múltiples .mbox (renombrado desde --carpeta para no chocar con --tipo-carpeta).')
        parser.add_argument('--tipo-carpeta', dest='tipo_carpeta',
                            choices=['inbox', 'enviados', 'otros'],
                            help='Forzar el tipo de carpeta (sobreescribe la heurística por nombre de archivo).')
        parser.add_argument('--limpiar', action='store_true',
                            help='Eliminar correos previos del buzón')
        parser.add_argument('--sin-adjuntos', action='store_true',
                            help='Saltarse extracción de adjuntos (más rápido, menos disco)')

    def handle(self, *args, **options):
        email_buzon = options['email'].lower().strip()
        skip_adj = options['sin_adjuntos']
        tipo_carpeta_forzado = options.get('tipo_carpeta')

        buzon, creado = Buzon.objects.get_or_create(email=email_buzon)
        self.stdout.write(f'{"Creado" if creado else "Existente"}: {email_buzon}')

        if options['limpiar']:
            n, _ = buzon.correos.all().delete()
            self.stdout.write(self.style.WARNING(f'  Eliminados {n} correos previos'))

        # Resuelve archivos
        archivos = []
        if options['archivo']:
            archivos.append(Path(options['archivo']))
        elif options['dir']:
            carpeta = Path(options['dir'])
            archivos = list(carpeta.glob('*.mbox')) + list(carpeta.glob('*.mbx'))
        else:
            raise CommandError('Especifica --archivo o --dir')

        total_correos = 0
        total_adjuntos = 0
        total_errores = 0

        for ruta in archivos:
            tipo_carpeta = tipo_carpeta_forzado or detectar_carpeta(ruta.name)
            self.stdout.write(f'\nProcesando: {ruta.name}  →  carpeta: {tipo_carpeta}')
            try:
                mbox = mailbox.mbox(str(ruta))
                # Iteracion lazy: NO list(mbox) porque para archivos grandes (19+ GB)
                # carga todos los mensajes parseados en RAM y mata el proceso.

                for i, msg in enumerate(mbox, 1):
                    try:
                        # Postgres rechaza NUL bytes en columnas de texto. Algunos
                        # correos viejos los traen en headers (encoding raro). Los
                        # quitamos antes de cualquier insert para no perder el msg.
                        asunto    = decodificar_header(msg.get('Subject', '')).replace('\x00', '')
                        remitente = decodificar_header(msg.get('From', '')).replace('\x00', '')
                        dest      = decodificar_header(msg.get('To', '')).replace('\x00', '')
                        msg_id    = (msg.get('Message-ID', '') or '').replace('\x00', '')
                        fecha_str = msg.get('Date', '')

                        fecha = None
                        if fecha_str:
                            try:
                                parsed = email.utils.parsedate_to_datetime(fecha_str)
                                if parsed.tzinfo is None:
                                    parsed = timezone.make_aware(parsed)
                                fecha = parsed
                            except Exception:
                                pass

                        texto = extraer_texto(msg).replace('\x00', '')
                        adjuntos_data = [] if skip_adj else extraer_adjuntos(msg)

                        # Crear correo
                        correo = Correo.objects.create(
                            buzon=buzon,
                            tipo_carpeta=tipo_carpeta,
                            mensaje_id=msg_id[:500],
                            remitente=remitente[:500],
                            destinatario=dest[:1000],
                            asunto=asunto[:1000],
                            fecha=fecha,
                            cuerpo_texto=texto,
                            tiene_adjunto=bool(adjuntos_data),
                        )
                        total_correos += 1

                        # Guardar adjuntos en el filesystem + crear registros
                        for nombre, mime, payload in adjuntos_data:
                            adj = Adjunto(
                                correo=correo,
                                nombre_original=nombre,
                                mime_type=mime[:200],
                                tamano_bytes=len(payload),
                            )
                            # archivo.save() respeta upload_to='adjuntos/%Y/%m/'
                            # y agrega sufijo único si hay colisión
                            adj.archivo.save(nombre, ContentFile(payload), save=False)
                            adj.save()
                            total_adjuntos += 1

                        if i % 100 == 0:
                            self.stdout.write(f'  ... {i} procesados', ending='\r')

                    except Exception as e:
                        total_errores += 1
                        if total_errores <= 5:
                            self.stderr.write(f'  Error msg {i}: {e}')

            except Exception as e:
                self.stderr.write(f'Error abriendo {ruta}: {e}')

        # Actualiza contador
        buzon.total_correos = buzon.correos.count()
        buzon.save(update_fields=['total_correos'])

        self.stdout.write(self.style.SUCCESS(
            f'\nImportación completada:\n'
            f'  Correos:  {total_correos}\n'
            f'  Adjuntos: {total_adjuntos}\n'
            f'  Errores:  {total_errores}\n'
            f'  Total en BD: {buzon.total_correos}'
        ))
