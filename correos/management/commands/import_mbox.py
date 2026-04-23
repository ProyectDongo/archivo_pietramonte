"""
Uso:
    python manage.py import_mbox aledezma@pietramonte.cl --archivo=/ruta/aledezma.mbox
    python manage.py import_mbox aledezma@pietramonte.cl --carpeta=/ruta/carpeta_mboxes/
"""
import mailbox
import email
import email.header
import email.utils
import chardet
from datetime import datetime
from django.core.management.base import BaseCommand, CommandError
from django.utils import timezone
from pathlib import Path
from correos.models import Buzon, Correo


def decodificar_header(valor):
    """Decodifica headers de email que pueden venir codificados."""
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
    """Extrae el cuerpo en texto plano de un mensaje."""
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
    return '\n'.join(texto)[:50000]  # límite de 50k chars por correo


def tiene_adjuntos(msg):
    if msg.is_multipart():
        for parte in msg.walk():
            disposition = str(parte.get('Content-Disposition', ''))
            if 'attachment' in disposition:
                return True
    return False


class Command(BaseCommand):
    help = 'Importa archivos .mbox a la base de datos'

    def add_arguments(self, parser):
        parser.add_argument('email', type=str, help='Email del buzón a importar')
        parser.add_argument('--archivo', type=str, help='Ruta al archivo .mbox')
        parser.add_argument('--carpeta', type=str, help='Carpeta con múltiples .mbox')
        parser.add_argument('--limpiar', action='store_true',
                            help='Eliminar correos existentes de este buzón antes de importar')

    def handle(self, *args, **options):
        email_buzon = options['email'].lower()

        buzon, creado = Buzon.objects.get_or_create(email=email_buzon)
        if creado:
            self.stdout.write(f'✓ Buzón creado: {email_buzon}')
        else:
            self.stdout.write(f'→ Buzón existente: {email_buzon}')

        if options['limpiar']:
            eliminados = buzon.correos.all().delete()
            self.stdout.write(f'  Correos anteriores eliminados: {eliminados[0]}')

        archivos = []
        if options['archivo']:
            archivos.append(Path(options['archivo']))
        elif options['carpeta']:
            carpeta = Path(options['carpeta'])
            archivos = list(carpeta.glob('*.mbox')) + list(carpeta.glob('*.mbx'))
        else:
            raise CommandError('Debes especificar --archivo o --carpeta')

        total_importados = 0
        total_errores = 0

        for ruta in archivos:
            self.stdout.write(f'\n📂 Procesando: {ruta.name}')
            try:
                mbox = mailbox.mbox(str(ruta))
                mensajes = list(mbox)
                self.stdout.write(f'   {len(mensajes)} mensajes encontrados')

                batch = []
                for i, msg in enumerate(mensajes, 1):
                    try:
                        asunto     = decodificar_header(msg.get('Subject', ''))
                        remitente  = decodificar_header(msg.get('From', ''))
                        dest       = decodificar_header(msg.get('To', ''))
                        msg_id     = msg.get('Message-ID', '')
                        fecha_str  = msg.get('Date', '')

                        fecha = None
                        if fecha_str:
                            try:
                                parsed = email.utils.parsedate_to_datetime(fecha_str)
                                if parsed.tzinfo is None:
                                    parsed = timezone.make_aware(parsed)
                                fecha = parsed
                            except Exception:
                                pass

                        texto = extraer_texto(msg)
                        adjunto = tiene_adjuntos(msg)

                        batch.append(Correo(
                            buzon=buzon,
                            mensaje_id=msg_id[:500],
                            remitente=remitente[:500],
                            destinatario=dest[:1000],
                            asunto=asunto[:1000],
                            fecha=fecha,
                            cuerpo_texto=texto,
                            tiene_adjunto=adjunto,
                        ))

                        if len(batch) >= 500:
                            Correo.objects.bulk_create(batch, ignore_conflicts=True)
                            total_importados += len(batch)
                            self.stdout.write(f'   ... {total_importados} importados', ending='\r')
                            batch = []

                    except Exception as e:
                        total_errores += 1
                        if total_errores <= 5:
                            self.stderr.write(f'   Error en mensaje {i}: {e}')

                if batch:
                    Correo.objects.bulk_create(batch, ignore_conflicts=True)
                    total_importados += len(batch)

            except Exception as e:
                self.stderr.write(f'Error abriendo {ruta}: {e}')

        buzon.total_correos = buzon.correos.count()
        buzon.save()

        self.stdout.write(f'\n✅ Importación completada:')
        self.stdout.write(f'   Importados: {total_importados}')
        self.stdout.write(f'   Errores:    {total_errores}')
        self.stdout.write(f'   Total en BD: {buzon.total_correos}')
