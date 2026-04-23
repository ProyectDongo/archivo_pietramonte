from django.db import models


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

    class Meta:
        verbose_name = 'Correo'
        verbose_name_plural = 'Correos'
        ordering = ['-fecha']
        indexes = [
            models.Index(fields=['buzon', '-fecha']),
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
