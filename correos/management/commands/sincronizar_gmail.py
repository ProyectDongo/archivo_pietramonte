"""
Sincroniza correos NUEVOS desde Gmail al archivo, por label.

Diseñado para correr por cron cada 5-15 min:
    */10 * * * * docker exec $CONT python manage.py sincronizar_gmail

Setup:
  1. Configurar EMAIL_HOST_USER + EMAIL_HOST_PASSWORD en Coolify env vars
     (la cuenta Gmail centralizadora + su App Password).
  2. Crear los `BuzonGmailLabel` desde el admin (label_name → buzón).
     Para ver labels disponibles: `python manage.py sincronizar_gmail --listar-labels`.
  3. Primera corrida con last_uid=0 trae TODA la historia del label.
     Después solo entra lo nuevo.

Uso manual:
    sincronizar_gmail                       # Sincroniza todos los labels activos.
    sincronizar_gmail --label "aledezma"    # Solo este label.
    sincronizar_gmail --listar-labels       # Lista labels Gmail y sale.
    sincronizar_gmail --reset-uid --label X # last_uid=0 → re-fetch todo.

Dedup por mensaje_id está garantizado: si el cron corre 2 veces o si el
mismo correo aparece en varios labels, no se duplica.
"""
from __future__ import annotations

import email as email_lib
import email.utils
import logging

from django.core.files.base import ContentFile
from django.core.management.base import BaseCommand
from django.db import transaction
from django.utils import timezone

from correos.gmail_sync import ImapError, fetch_nuevos, listar_labels
from correos.models import Adjunto, BuzonGmailLabel, Correo
from correos.management.commands.import_mbox import (
    decodificar_header,
    extraer_adjuntos,
    extraer_cuerpos,
)


logger = logging.getLogger('correos.sincronizar_gmail')


class Command(BaseCommand):
    help = 'Sincroniza correos nuevos desde Gmail vía IMAP, por label → buzón.'

    def add_arguments(self, parser):
        parser.add_argument('--label', type=str,
                            help='Sincronizar solo este label_name (string exacto).')
        parser.add_argument('--listar-labels', dest='listar_labels', action='store_true',
                            help='Lista los labels disponibles en Gmail y sale (no sincroniza).')
        parser.add_argument('--reset-uid', dest='reset_uid', action='store_true',
                            help='Pone last_uid=0 antes de sincronizar (re-fetch toda la historia). '
                                 'Combinar con --label para no resetear todo.')
        parser.add_argument('--quiet', action='store_true',
                            help='Silencia el output rutinario (útil para cron sin spam).')

    def handle(self, *args, **options):
        if options['listar_labels']:
            try:
                for lab in sorted(listar_labels()):
                    self.stdout.write(f'  {lab}')
            except ImapError as e:
                self.stderr.write(self.style.ERROR(f'IMAP error: {e}'))
            return

        qs = BuzonGmailLabel.objects.filter(activo=True).select_related('buzon')
        if options.get('label'):
            qs = qs.filter(label_name=options['label'])

        if options.get('reset_uid'):
            n = qs.update(last_uid=0)
            self.stdout.write(self.style.WARNING(f'Reset last_uid=0 en {n} sync(s)'))

        if not qs.exists():
            if not options['quiet']:
                self.stdout.write('No hay BuzonGmailLabel activos para sincronizar.')
            return

        total_nuevos_global = 0
        total_dedup_global  = 0
        total_errores_global = 0

        for sync in qs:
            n_nuevos, n_dedup, n_err = self._sync_one(sync, quiet=options['quiet'])
            total_nuevos_global += n_nuevos
            total_dedup_global  += n_dedup
            total_errores_global += n_err

        if not options['quiet'] or total_nuevos_global > 0:
            self.stdout.write(self.style.SUCCESS(
                f'\nResumen sync · nuevos={total_nuevos_global} · '
                f'dedup={total_dedup_global} · errores={total_errores_global}'
            ))

    def _sync_one(self, sync: BuzonGmailLabel, quiet: bool = False) -> tuple[int, int, int]:
        if not quiet:
            self.stdout.write(
                f'\n→ {sync.label_name} → {sync.buzon.email} (last_uid={sync.last_uid})'
            )

        try:
            mensajes = fetch_nuevos(sync.label_name, sync.last_uid)
        except ImapError as e:
            sync.error_msg = str(e)[:1000]
            sync.last_sync_at = timezone.now()
            sync.save(update_fields=['error_msg', 'last_sync_at'])
            self.stderr.write(self.style.ERROR(f'  IMAP: {e}'))
            return 0, 0, 1
        except Exception as e:
            sync.error_msg = f'Inesperado: {e}'[:1000]
            sync.last_sync_at = timezone.now()
            sync.save(update_fields=['error_msg', 'last_sync_at'])
            self.stderr.write(self.style.ERROR(f'  ERROR: {e}'))
            return 0, 0, 1

        if not mensajes:
            sync.last_sync_at = timezone.now()
            sync.error_msg = ''
            sync.save(update_fields=['last_sync_at', 'error_msg'])
            if not quiet:
                self.stdout.write('  (sin novedades)')
            return 0, 0, 0

        # Cargar mensaje_ids existentes en este buzón para dedup
        existing_msgids = set(
            sync.buzon.correos.exclude(mensaje_id='').values_list('mensaje_id', flat=True)
        )

        nuevos = 0
        dedup  = 0
        errores = 0
        max_uid = sync.last_uid

        for uid, raw in mensajes:
            try:
                if uid > max_uid:
                    max_uid = uid

                msg = email_lib.message_from_bytes(raw)

                # NUL bytes que Postgres rechaza
                msg_id = (msg.get('Message-ID', '') or '').replace('\x00', '')[:500]

                # Dedup
                if msg_id and msg_id in existing_msgids:
                    dedup += 1
                    continue

                asunto    = decodificar_header(msg.get('Subject', '')).replace('\x00', '')
                remitente = decodificar_header(msg.get('From', '')).replace('\x00', '')
                dest      = decodificar_header(msg.get('To', '')).replace('\x00', '')
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

                texto, html = extraer_cuerpos(msg)
                texto = texto.replace('\x00', '')
                html  = html.replace('\x00', '')
                adjuntos_data = extraer_adjuntos(msg)

                with transaction.atomic():
                    correo = Correo.objects.create(
                        buzon=sync.buzon,
                        tipo_carpeta=sync.tipo_carpeta,
                        mensaje_id=msg_id,
                        remitente=remitente[:500],
                        destinatario=dest[:1000],
                        asunto=asunto[:1000],
                        fecha=fecha,
                        cuerpo_texto=texto,
                        cuerpo_html=html,
                        tiene_adjunto=bool(adjuntos_data),
                    )
                    for nombre, mime, payload in adjuntos_data:
                        adj = Adjunto(
                            correo=correo,
                            nombre_original=nombre,
                            mime_type=mime[:200],
                            tamano_bytes=len(payload),
                        )
                        adj.archivo.save(nombre, ContentFile(payload), save=False)
                        adj.save()

                if msg_id:
                    existing_msgids.add(msg_id)
                nuevos += 1

            except Exception as e:
                errores += 1
                if errores <= 3:
                    self.stderr.write(f'  Error msg uid={uid}: {e}')

        sync.last_uid = max_uid
        sync.last_sync_at = timezone.now()
        sync.correos_sincronizados += nuevos
        sync.error_msg = ''
        sync.save(update_fields=[
            'last_uid', 'last_sync_at', 'correos_sincronizados', 'error_msg',
        ])

        if not quiet:
            self.stdout.write(self.style.SUCCESS(
                f'  +{nuevos} nuevos · dedup {dedup} · errores {errores} · last_uid={max_uid}'
            ))
        return nuevos, dedup, errores
