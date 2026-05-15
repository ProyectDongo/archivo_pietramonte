"""
Backfill de `Correo.thread` para correos viejos que no tienen el FK seteado.

Algoritmo (en orden de preferencia):

1. **Por headers**: si el Correo tiene `in_reply_to` o `references` poblados,
   se busca un Correo padre con `mensaje_id` matcheando y se hereda su thread.

2. **Por asunto con prefijo Re:/Fwd:**: si no hay match por headers, y el
   asunto trae prefijo Re:/Fwd:/RV:/Fw:, se busca un Thread existente con
   el mismo asunto normalizado en el mismo buzón. Sin prefijo se considera
   correo nuevo y abre thread propio.

Recorre los Correos por fecha ascendente — así el más antiguo de cada hilo
queda como raíz natural.

Idempotente: re-ejecutar no rompe nada porque los Correos ya con thread se
omiten (a menos que pases --force o --reset).

Uso:

    python manage.py recompute_threads --dry-run     # preview
    python manage.py recompute_threads               # aplicar
    python manage.py recompute_threads --force       # reprocesa todos los
                                                     # correos (incluso los
                                                     # que ya tenian thread)
                                                     # PERO mantiene los
                                                     # threads viejos: si la
                                                     # logica nueva difiere
                                                     # de la vieja, --force
                                                     # NO la limpia.
    python manage.py recompute_threads --reset       # null thread_id en
                                                     # todos los correos +
                                                     # delete de threads,
                                                     # luego backfill desde
                                                     # cero. Usar tras un
                                                     # cambio de logica de
                                                     # threading.
    python manage.py recompute_threads --buzon=1     # solo un buzón
"""
from __future__ import annotations

from django.core.management.base import BaseCommand
from django.db import transaction

from correos.models import Buzon, Correo, Thread
from correos.threading import (
    find_parent_thread,
    create_thread_for,
    recompute_thread_cache,
)


class Command(BaseCommand):
    help = 'Reconstruye Correo.thread y cache de Thread para correos viejos.'

    def add_arguments(self, parser):
        parser.add_argument('--dry-run', action='store_true',
                            help='No toca la DB, solo cuenta.')
        parser.add_argument('--force', action='store_true',
                            help='Recalcula todos los correos, incluso los que ya tenian thread.')
        parser.add_argument('--reset', action='store_true',
                            help='ANTES del backfill, borra todos los Thread y nullea Correo.thread. '
                                 'Usar tras cambios en la logica de threading para empezar de cero.')
        parser.add_argument('--buzon', type=int, default=None,
                            help='Limita el backfill a un buzon_id.')

    def handle(self, *args, **options):
        dry_run = options['dry_run']
        force   = options['force']
        reset   = options['reset']
        buzon_id = options['buzon']

        buzones_qs = Buzon.objects.all()
        if buzon_id:
            buzones_qs = buzones_qs.filter(id=buzon_id)

        if reset:
            self._aplicar_reset(buzones_qs, dry_run)
            # Tras el reset, todos los Correo.thread quedan en NULL → el
            # backfill normal (sin --force) los procesa a todos.
            force = False

        total_correos = 0
        total_asignados = 0
        total_threads_creados = 0

        for buzon in buzones_qs:
            self.stdout.write(f'\n→ Buzón: {buzon.email}')

            qs = Correo.objects.filter(buzon=buzon)
            if not force:
                qs = qs.filter(thread__isnull=True)
            qs = qs.order_by('fecha', 'id').only(
                'id', 'mensaje_id', 'in_reply_to', 'references',
                'asunto', 'fecha', 'buzon_id', 'thread_id',
            )

            asignados = 0
            threads_creados = 0
            count = qs.count()
            total_correos += count
            self.stdout.write(f'  correos a procesar: {count}')

            if count == 0:
                continue

            for c in qs.iterator(chunk_size=200):
                parent_thread = find_parent_thread(
                    buzon, c.in_reply_to, c.references, c.asunto,
                )
                if parent_thread is None:
                    if dry_run:
                        threads_creados += 1
                        continue
                    parent_thread = create_thread_for(c)
                    threads_creados += 1
                else:
                    if dry_run:
                        asignados += 1
                        continue

                c.thread = parent_thread
                Correo.objects.filter(id=c.id).update(thread=parent_thread)
                asignados += 1

            if not dry_run:
                buzon_threads = Thread.objects.filter(buzon=buzon)
                for t in buzon_threads:
                    recompute_thread_cache(t)

            self.stdout.write(self.style.SUCCESS(
                f'  asignados={asignados}, threads_nuevos={threads_creados}'
            ))
            total_asignados += asignados
            total_threads_creados += threads_creados

        self.stdout.write('\n──────────────────────────────────')
        self.stdout.write(self.style.SUCCESS(
            f'Total: {total_correos} correos analizados · '
            f'{total_asignados} asignados · {total_threads_creados} threads creados'
        ))
        if dry_run:
            self.stdout.write(self.style.NOTICE(
                'Dry-run: no se modificó nada. Re-corré sin --dry-run.'
            ))

    def _aplicar_reset(self, buzones_qs, dry_run: bool) -> None:
        """
        Nullea Correo.thread y borra Thread del scope (todo o un buzón).
        Indispensable antes de un backfill cuando la lógica de threading
        cambió, sino find_parent_thread sigue encontrando padres con thread
        viejo y reasigna a esos threads en vez de aplicar la nueva lógica.
        """
        correos_qs = Correo.objects.filter(buzon__in=buzones_qs).exclude(thread__isnull=True)
        threads_qs = Thread.objects.filter(buzon__in=buzones_qs)
        n_correos = correos_qs.count()
        n_threads = threads_qs.count()

        self.stdout.write(self.style.WARNING(
            f'\n[--reset] Limpiando: {n_correos} correos con thread, {n_threads} threads.'
        ))
        if dry_run:
            self.stdout.write(self.style.NOTICE(
                '[--reset] Dry-run: no se limpia nada. (El backfill simulado abajo asume reset hecho.)'
            ))
            return

        with transaction.atomic():
            correos_qs.update(thread=None)
            threads_qs.delete()
        self.stdout.write(self.style.SUCCESS('[--reset] Limpieza OK.'))
