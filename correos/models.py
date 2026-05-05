import hashlib

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
    buzon = models.ForeignKey(Buzon, on_delete=models.CASCADE, related_name='correos')

    mensaje_id    = models.CharField(max_length=500, blank=True, db_index=True)
    remitente     = models.CharField(max_length=500, blank=True)
    destinatario  = models.TextField(blank=True)
    asunto        = models.CharField(max_length=1000, blank=True)
    fecha         = models.DateTimeField(null=True, blank=True, db_index=True)
    cuerpo_texto  = models.TextField(blank=True)   # texto plano para búsqueda
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
