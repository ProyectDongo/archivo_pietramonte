import hashlib

from django.conf import settings
from django.db import models


def hash_ip(ip: str) -> str:
    """Hash de IP para no guardar PII en claro. Sal incluida en el código."""
    return hashlib.sha256(('pm-archivo::' + (ip or '')).encode('utf-8')).hexdigest()[:32]


class Buzon(models.Model):
    """Representa un buzón/cuenta de correo importado desde .mbox"""
    email = models.EmailField(unique=True)
    nombre = models.CharField(max_length=100, blank=True)
    total_correos = models.IntegerField(default=0)
    importado_en = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return self.email

    class Meta:
        verbose_name = 'Buzón'
        verbose_name_plural = 'Buzones'
        ordering = ['email']


class Etiqueta(models.Model):
    """
    Tag para clasificar correos. Cada etiqueta vive dentro de un buzón:
    el buzón "aledezma" puede tener "Factura", "Urgente", etc.; el buzón
    "cobranza" tiene su propio set independiente.
    """
    PALETA = [
        ('#C80C0F', 'Rojo'),
        ('#1976D2', 'Azul'),
        ('#388E3C', 'Verde'),
        ('#F57C00', 'Naranja'),
        ('#7B1FA2', 'Morado'),
        ('#5D4037', 'Café'),
        ('#455A64', 'Grafito'),
        ('#FBC02D', 'Amarillo'),
    ]

    buzon  = models.ForeignKey(Buzon, on_delete=models.CASCADE, related_name='etiquetas')
    nombre = models.CharField(max_length=40)
    color  = models.CharField(max_length=7, default='#C80C0F', choices=PALETA)
    creado = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = 'Etiqueta'
        verbose_name_plural = 'Etiquetas'
        ordering = ['nombre']
        unique_together = [('buzon', 'nombre')]
        indexes = [models.Index(fields=['buzon', 'nombre'])]

    def __str__(self):
        return f'{self.buzon.email} · {self.nombre}'


class Correo(models.Model):
    """Un correo electrónico individual indexado desde .mbox"""

    class Carpeta(models.TextChoices):
        INBOX    = 'inbox',    'Bandeja de entrada'
        ENVIADOS = 'enviados', 'Enviados'
        OTROS    = 'otros',    'Otros / sin clasificar'

    buzon = models.ForeignKey(Buzon, on_delete=models.CASCADE, related_name='correos')

    # Tipo de carpeta dentro del buzón. Lo setea import_mbox a partir del
    # nombre del archivo .mbox (heurística + override --carpeta). Para los
    # correos viejos sin clasificar, ver clasificar_correos management cmd.
    tipo_carpeta  = models.CharField(max_length=10, choices=Carpeta.choices,
                                     default=Carpeta.OTROS, db_index=True)

    mensaje_id    = models.CharField(max_length=500, blank=True, db_index=True)
    remitente     = models.CharField(max_length=500, blank=True)
    destinatario  = models.TextField(blank=True)
    asunto        = models.CharField(max_length=1000, blank=True)
    fecha         = models.DateTimeField(null=True, blank=True, db_index=True)
    cuerpo_texto  = models.TextField(blank=True)   # texto plano para búsqueda
    cuerpo_html   = models.TextField(
        blank=True, default='',
        help_text='Cuerpo en HTML si el correo lo tenía. Vacío si era solo texto plano. '
                  'Se sanitiza con bleach al renderizar (no acá) — guardarse crudo está bien.',
    )
    tiene_adjunto = models.BooleanField(default=False)

    # Organización del archivo (compartido entre todos los usuarios del buzón)
    destacado     = models.BooleanField(default=False, db_index=True)
    notas         = models.TextField(blank=True, max_length=5000,
                                     help_text='Anotaciones internas del equipo (máx 5000 chars).')
    etiquetas     = models.ManyToManyField(Etiqueta, related_name='correos', blank=True)

    class Meta:
        verbose_name = 'Correo'
        verbose_name_plural = 'Correos'
        ordering = ['-fecha']
        indexes = [
            models.Index(fields=['buzon', '-fecha']),
            models.Index(fields=['buzon', 'destacado']),
            models.Index(fields=['buzon', 'tipo_carpeta', '-fecha']),
            # Para el conteo de "con adjunto" que se hace en _stats_de y para
            # el filtro ?adjuntos=1 — antes era seq scan parcial sobre el buzón.
            models.Index(fields=['buzon', 'tiene_adjunto'],
                         name='correos_cor_buzon_a_d2f8e1_idx'),
        ]

    def __str__(self):
        return f'{self.asunto[:60]} ({self.fecha})'

    @property
    def remitente_nombre(self):
        """Extrae solo el nombre del remitente si viene en formato 'Nombre <email>'"""
        if '<' in self.remitente:
            return self.remitente.split('<')[0].strip().strip('"')
        return self.remitente

    @property
    def remitente_email(self):
        if '<' in self.remitente:
            return self.remitente.split('<')[1].strip('>')
        return self.remitente


class UsuarioPortal(models.Model):
    """
    Cuenta de acceso al portal. Cada Gmail autorizado tiene una.
    El password se guarda hasheado con PBKDF2 (default de Django).

    Acceso a buzones:
      - Si es_admin == True: ve TODOS los buzones del sistema (la M2M se ignora).
      - Si no: ve solo los buzones listados en `buzones`.

    2FA (TOTP, RFC 6238):
      - `totp_secret` base32 — generado en setup, nunca se vuelve a mostrar.
      - `totp_activo` se marca True cuando el usuario confirma el primer código.
      - `recovery_codes_hash` lista de PBKDF2-hashes; cada code se quema al usarse.
      - `totp_ultimo_codigo` anti-replay del último código usado dentro de su ventana.
    """
    email          = models.EmailField(unique=True)
    password_hash  = models.CharField(max_length=256)
    es_admin       = models.BooleanField(
        default=False,
        help_text='Si está marcado, ve TODOS los buzones (la lista de buzones se ignora).',
    )
    activo         = models.BooleanField(default=True)
    creado         = models.DateTimeField(auto_now_add=True)
    ultimo_login   = models.DateTimeField(null=True, blank=True)

    buzones        = models.ManyToManyField(
        'Buzon',
        related_name='usuarios',
        blank=True,
        help_text='Buzones que este usuario puede consultar (ignorado para admins).',
    )

    # 2FA (TOTP)
    totp_secret           = models.CharField(max_length=64, blank=True, default='')
    totp_activo           = models.BooleanField(default=False)
    recovery_codes_hash   = models.JSONField(default=list, blank=True)
    totp_ultimo_codigo    = models.CharField(max_length=10, blank=True, default='')

    class Meta:
        verbose_name = 'Usuario del portal'
        verbose_name_plural = 'Usuarios del portal'
        ordering = ['email']

    def __str__(self):
        return f'{self.email}{" [admin]" if self.es_admin else ""}{"" if self.activo else " (inactivo)"}'

    def set_password(self, raw: str):
        """Hashea y guarda el password. Llamar save() después."""
        from django.contrib.auth.hashers import make_password
        self.password_hash = make_password(raw)

    def check_password(self, raw: str) -> bool:
        from django.contrib.auth.hashers import check_password
        return check_password(raw, self.password_hash)

    def buzones_visibles(self):
        """Queryset de los buzones que este usuario puede ver."""
        if self.es_admin:
            return Buzon.objects.all().order_by('email')
        return self.buzones.all().order_by('email')

    def puede_ver(self, buzon: 'Buzon') -> bool:
        """¿Tiene acceso a ese buzón concreto?"""
        if self.es_admin:
            return True
        return self.buzones.filter(id=buzon.id).exists()


class CorreoLeido(models.Model):
    """
    Marca per-usuario de "este correo lo leí". El estado de lectura es
    POR USUARIO (no compartido entre el equipo): si Anghelo abre un correo
    no debería marcarse leído para soporte.dongo.

    Existencia del registro = leído. Borrar el registro = volver a no-leído.
    """
    usuario   = models.ForeignKey('UsuarioPortal', on_delete=models.CASCADE,
                                  related_name='correos_leidos')
    correo    = models.ForeignKey('Correo', on_delete=models.CASCADE,
                                  related_name='leidos_por')
    leido_en  = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = 'Correo leído'
        verbose_name_plural = 'Correos leídos'
        unique_together = [('usuario', 'correo')]
        # Nombre explícito del índice — debe coincidir con el de la migration
        # 0010_correoleido (donde lo hardcodeé en vez de dejar que Django
        # autogenere). Sin este name=, el modelo y la migración divergen y
        # `makemigrations` propone una migración de rename eterna.
        indexes = [
            models.Index(fields=['usuario', 'correo'], name='correos_cor_usuario_e8d39e_idx'),
        ]

    def __str__(self):
        return f'{self.usuario.email} leyó #{self.correo_id}'


class CorreoSnooze(models.Model):
    """
    Snooze (posponer) per-usuario: oculta el correo de la bandeja hasta
    `until_at`. Cuando esa fecha pasa, el correo reaparece automáticamente
    (filtramos en la query con until_at > now() — no necesitamos cron).

    Existencia = activo. Borrar = unsnooze inmediato.
    """
    usuario   = models.ForeignKey('UsuarioPortal', on_delete=models.CASCADE,
                                  related_name='correos_snoozed')
    correo    = models.ForeignKey('Correo', on_delete=models.CASCADE,
                                  related_name='snoozes')
    until_at  = models.DateTimeField(db_index=True,
                                     help_text='El correo está oculto hasta este momento.')
    creado    = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = 'Correo pospuesto (snooze)'
        verbose_name_plural = 'Correos pospuestos'
        unique_together = [('usuario', 'correo')]
        indexes = [
            models.Index(fields=['usuario', 'until_at'], name='correos_snz_usr_until_idx'),
        ]

    def __str__(self):
        return f'{self.usuario.email} → #{self.correo_id} hasta {self.until_at:%Y-%m-%d %H:%M}'


class Adjunto(models.Model):
    """
    Archivo adjunto extraído de un correo .mbox y guardado en MEDIA_ROOT.
    Solo se sirve a usuarios logueados que tengan acceso al buzón asociado.
    """
    correo          = models.ForeignKey(Correo, on_delete=models.CASCADE, related_name='adjuntos')
    nombre_original = models.CharField(max_length=300)
    mime_type       = models.CharField(max_length=200, blank=True)
    tamano_bytes    = models.PositiveBigIntegerField(default=0)
    archivo         = models.FileField(upload_to='adjuntos/%Y/%m/')
    creado          = models.DateTimeField(auto_now_add=True)

    # Content-ID del part MIME (sin angle brackets). Solo lo traen los adjuntos
    # *inline* (imágenes embebidas en HTML). Se usa para resolver `cid:xxx`
    # en el cuerpo HTML al renderizar — sino se ve `[cid:xxx]` como texto.
    content_id      = models.CharField(
        max_length=300, blank=True, default='',
        help_text='Content-ID (sin <>) del adjunto inline. Vacío para attachments normales.',
    )

    class Meta:
        verbose_name = 'Adjunto'
        verbose_name_plural = 'Adjuntos'
        ordering = ['nombre_original']
        indexes = [models.Index(fields=['correo'])]

    def __str__(self):
        return f'{self.nombre_original} ({self.tamano_bytes} bytes)'

    @property
    def tamano_legible(self) -> str:
        n = self.tamano_bytes
        for unidad in ['B', 'KB', 'MB', 'GB']:
            if n < 1024:
                return f'{n:.1f} {unidad}' if unidad != 'B' else f'{n} {unidad}'
            n /= 1024
        return f'{n:.1f} TB'

    @property
    def es_seguro_inline(self) -> bool:
        """¿Se puede mostrar inline en el navegador sin riesgo XSS?"""
        seguros = {
            'application/pdf',
            'image/png', 'image/jpeg', 'image/gif', 'image/webp',
            'audio/mpeg', 'audio/ogg',
            'video/mp4', 'video/webm',
        }
        return self.mime_type.lower() in seguros


class IntentoLogin(models.Model):
    """
    Bitácora de cada intento de login. Datos para:
      - Bloqueo por rate-limit (consultas por ip_hash en últimos N minutos).
      - Análisis / ML futuro (detección de patrones de bot).
    No guardamos IP en claro: solo hash con sal interna.
    """
    MOTIVOS = [
        ('exito',             'Login exitoso'),
        ('honeypot',          'Honeypot lleno'),
        ('muy_rapido',        'Tiempo sospechosamente bajo'),
        ('captcha_fail',      'Captcha incorrecto/expirado'),
        ('email_no_lista',    'Email fuera de allowlist'),
        ('email_invalido',    'Formato de email inválido'),
        ('password_invalida', 'Contraseña incorrecta'),
        ('usuario_inactivo',  'Usuario marcado inactivo'),
        ('buzon_inexist',     'Buzón no importado'),
        ('throttled',         'Bloqueado por rate-limit'),
        ('csrf',              'CSRF inválido'),
        ('pwd_ok_2fa_pend',   'Password OK, 2FA pendiente'),
        ('totp_fail',         'Código 2FA incorrecto'),
        ('totp_ok',           '2FA verificado'),
        ('recovery_used',     'Recovery code usado'),
        ('recovery_inval',    'Recovery code inválido'),
        ('totp_setup',        '2FA configurado por primera vez'),
        ('totp_reset',        '2FA reseteado por admin'),
    ]

    ip_hash         = models.CharField(max_length=64, db_index=True)
    user_agent      = models.CharField(max_length=500, blank=True)
    email_intentado = models.CharField(max_length=254, blank=True)
    captcha_categoria = models.CharField(max_length=30, blank=True)
    tiempo_ms       = models.IntegerField(default=0)
    honeypot_lleno  = models.BooleanField(default=False)
    exito           = models.BooleanField(default=False, db_index=True)
    motivo          = models.CharField(max_length=20, choices=MOTIVOS, blank=True)
    creado          = models.DateTimeField(auto_now_add=True, db_index=True)

    class Meta:
        verbose_name = 'Intento de login'
        verbose_name_plural = 'Intentos de login'
        ordering = ['-creado']
        indexes = [
            models.Index(fields=['ip_hash', '-creado']),
            models.Index(fields=['exito', '-creado']),
        ]

    def __str__(self):
        return f'{self.creado:%Y-%m-%d %H:%M} {"OK" if self.exito else "FAIL"} {self.motivo}'


class ReenvioCorreo(models.Model):
    """
    Bitácora de cada vez que un UsuarioPortal reenvía un correo del archivo
    a un destinatario externo.

    Pensado para:
      - Auditoría: quién reenvió qué y a quién (los Correos pueden ser
        sensibles — facturas, contratos, etc.).
      - Rate-limit: contar reenvíos de las últimas 24h por usuario y bloquear
        si supera el cupo (30 normal, 100 admin).

    El cuerpo del email reenviado se arma en el momento — no se persiste.
    Los destinatarios SÍ se guardan (texto coma-separado).
    """
    correo          = models.ForeignKey(Correo, on_delete=models.CASCADE,
                                        related_name='reenvios')
    usuario         = models.ForeignKey(UsuarioPortal, on_delete=models.SET_NULL,
                                        null=True, blank=True,
                                        related_name='reenvios_realizados',
                                        help_text='Usuario que disparó el reenvío. NULL si el usuario fue eliminado.')
    destinatarios   = models.TextField(help_text='Emails coma-separados a los que se envió.')
    mensaje_extra   = models.TextField(blank=True, max_length=2000,
                                       help_text='Nota que el usuario agregó arriba del correo original.')
    enviado_en      = models.DateTimeField(auto_now_add=True, db_index=True)
    exito           = models.BooleanField(default=False, db_index=True,
                                          help_text='True si el envío SMTP completó sin error.')
    error_msg       = models.TextField(blank=True, max_length=500,
                                       help_text='Mensaje de error si el envío falló.')
    ip_hash         = models.CharField(max_length=64, blank=True, db_index=True)

    class Meta:
        verbose_name = 'Reenvío de correo'
        verbose_name_plural = 'Reenvíos de correos'
        ordering = ['-enviado_en']
        indexes = [
            models.Index(fields=['usuario', '-enviado_en']),
            models.Index(fields=['exito', '-enviado_en']),
        ]

    def __str__(self):
        return f'{self.enviado_en:%Y-%m-%d %H:%M} · {self.usuario_id} → {self.destinatarios[:60]}'


class CorreoEnviado(models.Model):
    """
    Bitácora de cada respuesta o composición nueva enviada desde el portal.

    Distinto de `ReenvioCorreo` (que es solo "forward de un correo del archivo
    a externos sin que el destinatario pueda responder al hilo"), este modelo
    cubre el caso "responder al remitente / responder a todos / componer
    nuevo" donde el From es la dirección del buzón y el destinatario puede
    responder y la respuesta vuelve via sync IMAP al mismo buzón.

    El `Correo` saved-to-sent se crea aparte (con `tipo_carpeta='enviados'`)
    para que el usuario lo vea en la pestaña "Enviados". Esta tabla es para
    auditoría: incluye errores de envío, IP, etc.
    """
    class Tipo(models.TextChoices):
        RESPONDER       = 'responder',        'Responder'
        RESPONDER_TODOS = 'responder_todos',  'Responder a todos'
        COMPOSE         = 'compose',          'Composición nueva'

    buzon            = models.ForeignKey('Buzon', on_delete=models.CASCADE,
                                         related_name='correos_enviados',
                                         help_text='Buzón desde el que se envió (define el From).')
    usuario          = models.ForeignKey('UsuarioPortal', on_delete=models.SET_NULL,
                                         null=True, blank=True,
                                         related_name='enviados_realizados')
    correo_original  = models.ForeignKey('Correo', on_delete=models.SET_NULL,
                                         null=True, blank=True,
                                         related_name='respuestas',
                                         help_text='Correo al que se respondió. NULL si fue compose nuevo.')
    correo_guardado  = models.ForeignKey('Correo', on_delete=models.SET_NULL,
                                         null=True, blank=True,
                                         related_name='entrada_envio',
                                         help_text='Copia guardada en BD con tipo_carpeta=enviados.')
    tipo             = models.CharField(max_length=20, choices=Tipo.choices,
                                        default=Tipo.RESPONDER, db_index=True)
    destinatarios    = models.TextField(help_text='Emails del To, coma-separados.')
    cc               = models.TextField(blank=True, help_text='Emails del Cc, coma-separados.')
    asunto           = models.CharField(max_length=1000)
    cuerpo           = models.TextField(blank=True,
                                        help_text='Body que escribió el usuario (sin el quote del original).')
    mensaje_id       = models.CharField(max_length=500, blank=True,
                                        help_text='Message-ID que generamos para este envío.')
    in_reply_to      = models.CharField(max_length=500, blank=True,
                                        help_text='Message-ID del correo al que respondemos.')
    enviado_en       = models.DateTimeField(auto_now_add=True, db_index=True)
    exito            = models.BooleanField(default=False, db_index=True)
    error_msg        = models.TextField(blank=True, max_length=500)
    ip_hash          = models.CharField(max_length=64, blank=True, db_index=True)

    class Meta:
        verbose_name = 'Correo enviado desde el portal'
        verbose_name_plural = 'Correos enviados desde el portal'
        ordering = ['-enviado_en']
        # Nombres explícitos para que matcheen con la migration 0011_correoenviado
        # (donde los hardcodeé). Mismo motivo que CorreoLeido.
        indexes = [
            models.Index(fields=['usuario', '-enviado_en'], name='correos_cor_usuario_4f8d2a_idx'),
            models.Index(fields=['buzon', '-enviado_en'],   name='correos_cor_buzon_i_e1c39b_idx'),
            models.Index(fields=['exito', '-enviado_en'],   name='correos_cor_exito_3a7e4f_idx'),
        ]

    def __str__(self):
        return f'{self.enviado_en:%Y-%m-%d %H:%M} · {self.tipo} · {self.buzon_id} → {self.destinatarios[:60]}'


class BuzonGmailLabel(models.Model):
    """
    Mapea un label de Gmail (en la cuenta soporte central) → un Buzon del
    archivo. El management command `sincronizar_gmail` corre por cron, abre
    una conexión IMAP a Gmail, y por cada label activo fetchea los mensajes
    con UID > last_uid → los inserta como Correo en el buzón asociado.

    El dedup por (buzon, mensaje_id) ya está garantizado por el flow de
    import_mbox (mismo código). Si el cron corre 2 veces no duplica.

    last_uid arranca en 0 → primera corrida importa TODA la historia del
    label. Después solo entra lo nuevo (UID monotónicamente creciente).
    """
    buzon         = models.ForeignKey(Buzon, on_delete=models.CASCADE, related_name='gmail_labels')
    label_name    = models.CharField(max_length=200,
                                     help_text='Nombre EXACTO del label en Gmail. Case-sensitive. '
                                               'Para ver los disponibles, usá la action '
                                               '"Listar labels disponibles" en este admin.')
    tipo_carpeta  = models.CharField(max_length=10, choices=Correo.Carpeta.choices,
                                     default=Correo.Carpeta.INBOX,
                                     help_text='Bajo qué pestaña aparecen estos correos en el portal.')
    activo        = models.BooleanField(default=True)
    last_uid      = models.PositiveBigIntegerField(default=0,
                                                   help_text='UID del último mensaje IMAP sincronizado. '
                                                             '0 = traer toda la historia del label en la próxima corrida.')
    last_sync_at  = models.DateTimeField(null=True, blank=True)
    correos_sincronizados = models.IntegerField(default=0)
    error_msg     = models.TextField(blank=True, max_length=1000,
                                     help_text='Último error de sync (si hay).')
    creado        = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = 'Sync Gmail label → buzón'
        verbose_name_plural = 'Sync Gmail labels → buzones'
        unique_together = [('buzon', 'label_name')]
        ordering = ['buzon__email', 'label_name']

    def __str__(self):
        estado = '' if self.activo else ' (inactivo)'
        return f'{self.label_name} → {self.buzon.email}{estado}'


class AdminTOTP(models.Model):
    """
    2FA del superuser de Django (auth.User). 1:1 con User.
    Se crea on-demand cuando el admin entra y todavía no tiene perfil.
    Mismo esquema TOTP+recovery que UsuarioPortal pero separado para no
    contaminar el modelo de Django auth.
    """
    user = models.OneToOneField(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name='totp',
    )
    totp_secret           = models.CharField(max_length=64, blank=True, default='')
    totp_activo           = models.BooleanField(default=False)
    recovery_codes_hash   = models.JSONField(default=list, blank=True)
    totp_ultimo_codigo    = models.CharField(max_length=10, blank=True, default='')
    creado                = models.DateTimeField(auto_now_add=True)
    ultima_2fa_ok         = models.DateTimeField(null=True, blank=True)

    class Meta:
        verbose_name = '2FA de admin'
        verbose_name_plural = '2FA de admins'

    def __str__(self):
        estado = 'activo' if self.totp_activo else 'sin configurar'
        return f'{self.user} · {estado}'
