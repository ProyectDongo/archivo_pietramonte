"""
Admin de Django personalizado.

Tres niveles de modelos:
  - UsuarioPortal: editable (CRUD) — controla quién puede entrar al portal.
  - Buzon: editable (puedes renombrar, marcar inactivo).
  - Correo, Adjunto: read-only mostly (vienen del importador, no se editan a mano).
  - IntentoLogin: read-only puro (bitácora de auditoría).

Notas de seguridad:
  - El form de UsuarioPortal NO muestra el hash. Se setea via campo password_nuevo,
    que pasa por AUTH_PASSWORD_VALIDATORS.
  - Eliminar usuarios en admin → revoca su acceso. No borra sus IntentoLogin.
"""
from django import forms
from django.contrib import admin, messages
from django.contrib.auth.password_validation import validate_password
from django.core.exceptions import ValidationError
from django.utils.html import format_html

from .models import Adjunto, Buzon, Correo, Etiqueta, IntentoLogin, UsuarioPortal


# ─── UsuarioPortal ─────────────────────────────────────────────────────────
class UsuarioPortalForm(forms.ModelForm):
    """
    Form custom: en vez de mostrar el hash, expone un campo "password_nuevo"
    que se valida con AUTH_PASSWORD_VALIDATORS y se hashea al guardar.
    """
    password_nuevo = forms.CharField(
        required=False,
        widget=forms.PasswordInput(attrs={'autocomplete': 'new-password'}),
        help_text='Mínimo 10 caracteres. Dejar vacío para no cambiar la contraseña actual.',
    )
    password_confirmar = forms.CharField(
        required=False,
        widget=forms.PasswordInput(attrs={'autocomplete': 'new-password'}),
        help_text='Repite la contraseña.',
    )

    class Meta:
        model = UsuarioPortal
        fields = ('email', 'es_admin', 'activo', 'buzones')

    def clean(self):
        cleaned = super().clean()
        pwd  = cleaned.get('password_nuevo') or ''
        pwd2 = cleaned.get('password_confirmar') or ''

        es_nuevo = self.instance.pk is None

        if pwd or pwd2:
            if pwd != pwd2:
                raise ValidationError({'password_confirmar': 'Las contraseñas no coinciden.'})
            try:
                validate_password(pwd, user=self.instance)
            except ValidationError as e:
                raise ValidationError({'password_nuevo': e.messages})
        elif es_nuevo:
            raise ValidationError({'password_nuevo': 'Define una contraseña para el usuario nuevo.'})

        return cleaned

    def save(self, commit=True):
        instance = super().save(commit=False)
        pwd = self.cleaned_data.get('password_nuevo')
        if pwd:
            instance.set_password(pwd)
        if commit:
            instance.save()
        return instance


@admin.register(UsuarioPortal)
class UsuarioPortalAdmin(admin.ModelAdmin):
    form = UsuarioPortalForm
    list_display  = ('email', 'es_admin', 'activo', 'cantidad_buzones', 'ultimo_login', 'creado')
    list_filter   = ('es_admin', 'activo', 'buzones')
    search_fields = ('email',)
    readonly_fields = ('creado', 'ultimo_login')
    filter_horizontal = ('buzones',)    # widget de doble lista
    fieldsets = (
        (None, {
            'fields': ('email', 'activo', 'es_admin'),
        }),
        ('Contraseña', {
            'fields': ('password_nuevo', 'password_confirmar'),
            'description': 'Debe tener al menos 10 caracteres y no parecerse al email.',
        }),
        ('Acceso a buzones', {
            'fields': ('buzones',),
            'description': 'Si "Es admin" está marcado, ve TODOS los buzones (esta lista se ignora).',
        }),
        ('Historial', {
            'fields': ('creado', 'ultimo_login'),
        }),
    )

    @admin.display(description='# buzones', ordering='buzones')
    def cantidad_buzones(self, obj):
        if obj.es_admin:
            return format_html('<strong>todos</strong>')
        return obj.buzones.count()

    actions = ['desactivar_usuarios', 'activar_usuarios']

    @admin.action(description='Desactivar usuarios seleccionados')
    def desactivar_usuarios(self, request, queryset):
        n = queryset.update(activo=False)
        self.message_user(request, f'{n} usuario(s) desactivado(s).', messages.WARNING)

    @admin.action(description='Activar usuarios seleccionados')
    def activar_usuarios(self, request, queryset):
        n = queryset.update(activo=True)
        self.message_user(request, f'{n} usuario(s) activado(s).', messages.SUCCESS)


# ─── Buzon ─────────────────────────────────────────────────────────────────
@admin.register(Buzon)
class BuzonAdmin(admin.ModelAdmin):
    list_display  = ('email', 'nombre', 'total_correos', 'importado_en')
    search_fields = ('email', 'nombre')
    readonly_fields = ('importado_en',)


# ─── Etiqueta ──────────────────────────────────────────────────────────────
@admin.register(Etiqueta)
class EtiquetaAdmin(admin.ModelAdmin):
    list_display  = ('nombre', 'buzon', 'color_swatch', 'cantidad_correos', 'creado')
    list_filter   = ('buzon', 'color')
    search_fields = ('nombre',)
    readonly_fields = ('creado',)

    @admin.display(description='Color')
    def color_swatch(self, obj):
        return format_html(
            '<span style="display:inline-block;width:18px;height:18px;'
            'border-radius:50%;background:{};border:1px solid rgba(0,0,0,.1)" '
            'title="{}"></span> {}',
            obj.color, obj.color, obj.color,
        )

    @admin.display(description='Correos etiquetados')
    def cantidad_correos(self, obj):
        return obj.correos.count()


# ─── Correo (read-only para datos importados, pero destacado/notas/etiquetas editables) ──
@admin.register(Correo)
class CorreoAdmin(admin.ModelAdmin):
    list_display    = ('asunto_corto', 'remitente_corto', 'buzon', 'fecha',
                       'tiene_adjunto', 'destacado', 'cantidad_etiquetas')
    list_filter     = ('buzon', 'tiene_adjunto', 'destacado', 'etiquetas')
    search_fields   = ('asunto', 'remitente', 'destinatario', 'cuerpo_texto', 'notas')
    date_hierarchy  = 'fecha'
    filter_horizontal = ('etiquetas',)
    readonly_fields = ('buzon', 'mensaje_id', 'remitente', 'destinatario',
                       'asunto', 'fecha', 'cuerpo_texto', 'tiene_adjunto')
    fieldsets = (
        ('Datos del correo (importados, no editables)', {
            'fields': ('buzon', 'mensaje_id', 'remitente', 'destinatario',
                       'asunto', 'fecha', 'cuerpo_texto', 'tiene_adjunto'),
        }),
        ('Organización', {
            'fields': ('destacado', 'etiquetas', 'notas'),
            'description': 'Estos campos sí se pueden editar — son del archivo, no del correo original.',
        }),
    )

    @admin.display(description='# etiquetas')
    def cantidad_etiquetas(self, obj):
        return obj.etiquetas.count()

    def asunto_corto(self, obj):
        return (obj.asunto or '(sin asunto)')[:60]
    asunto_corto.short_description = 'Asunto'

    def remitente_corto(self, obj):
        return obj.remitente_nombre[:40]
    remitente_corto.short_description = 'Remitente'

    def has_add_permission(self, request):
        return False    # se importan, no se crean a mano


# ─── Adjunto (read-only) ───────────────────────────────────────────────────
@admin.register(Adjunto)
class AdjuntoAdmin(admin.ModelAdmin):
    list_display    = ('nombre_original', 'mime_type', 'tamano_legible', 'correo_link', 'creado')
    list_filter     = ('mime_type',)
    search_fields   = ('nombre_original', 'correo__asunto')
    readonly_fields = ('correo', 'nombre_original', 'mime_type', 'tamano_bytes',
                       'archivo', 'creado')

    def correo_link(self, obj):
        return format_html('<a href="../correo/{}/change/">{}</a>',
                           obj.correo_id, (obj.correo.asunto or '(sin asunto)')[:50])
    correo_link.short_description = 'Correo'

    def has_add_permission(self, request):
        return False


# ─── IntentoLogin (auditoría, solo lectura) ────────────────────────────────
@admin.register(IntentoLogin)
class IntentoLoginAdmin(admin.ModelAdmin):
    list_display  = ('creado', 'exito_icon', 'motivo', 'email_intentado',
                     'ip_corta', 'tiempo_ms', 'honeypot_lleno', 'captcha_categoria')
    list_filter   = ('exito', 'motivo', 'creado')
    search_fields = ('email_intentado', 'ip_hash')
    readonly_fields = [f.name for f in IntentoLogin._meta.fields]
    date_hierarchy = 'creado'

    def exito_icon(self, obj):
        return format_html('<span style="color:{};font-weight:700">{}</span>',
                           '#1b5e20' if obj.exito else '#b71c1c',
                           'OK' if obj.exito else 'FAIL')
    exito_icon.short_description = 'Estado'

    def ip_corta(self, obj):
        return (obj.ip_hash or '')[:10] + '…'
    ip_corta.short_description = 'IP (hash)'

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        # Permitir borrado en bloque para limpiar bitácora vieja
        return request.user.is_superuser
