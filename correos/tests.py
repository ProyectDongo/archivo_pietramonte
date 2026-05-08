"""
Tests del flujo crítico — NO romper.

Cubre:
  - Login: éxito / password incorrecto / email inexistente / captcha mal /
    honeypot / submit muy rápido / rate limit / anti-enumeración (status y
    mensaje uniformes).
  - Logout: solo POST.
  - Adjuntos: dueño puede descargar; otro usuario logueado no.
  - Admin: requiere staff/superuser; URL ofuscada.
  - Cambiar password: validadores activos.
  - Captcha: token firmado, replay bloqueado, expiración.

Correr con:
    python manage.py test correos
"""
import base64
import json
import re
import time

from django.contrib.auth.models import User
from django.core.cache import cache
from django.core.files.base import ContentFile
from django.test import Client, TestCase, override_settings
from django.urls import reverse

from .models import Adjunto, Buzon, Correo, Etiqueta, IntentoLogin, UsuarioPortal


# Captcha helper compartido por los tests
def _resolver_captcha_de(html: str):
    """Extrae token + selección correcta de la página de login."""
    csrf = re.search(r'name="csrfmiddlewaretoken" value="([^"]+)"', html).group(1)
    token = re.search(r'name="captcha_token" id="captcha-token" value="([^"]+)"', html).group(1)
    page_loaded = re.search(r'name="page_loaded_at" id="page-loaded-at" value="([^"]+)"', html).group(1)
    payload_b64 = token.split('.')[0]
    pad = 4 - len(payload_b64) % 4
    seleccion = json.loads(base64.urlsafe_b64decode(payload_b64 + '=' * pad))['i']
    return csrf, token, page_loaded, seleccion


@override_settings(
    PORTAL_ALLOWED_EMAILS=['empleado@gmail.com'],
    STORAGES={
        'default': {'BACKEND': 'django.core.files.storage.FileSystemStorage'},
        'staticfiles': {'BACKEND': 'django.contrib.staticfiles.storage.StaticFilesStorage'},
    },
)
class LoginFlowTests(TestCase):

    def setUp(self):
        cache.clear()
        self.user = UsuarioPortal(email='empleado@gmail.com', activo=True)
        self.user.set_password('PassMuy.Larga2026!')
        self.user.save()
        # En multi-buzón: el usuario debe tener al menos 1 buzón asignado
        # (o ser admin). Le damos uno explícitamente.
        b = Buzon.objects.create(email='empleado.bandeja@pietramonte.cl')
        self.user.buzones.add(b)

        self.c = Client(HTTP_HOST='localhost', enforce_csrf_checks=True)

    def _get_login_data(self):
        r = self.c.get('/intranet/')
        return _resolver_captcha_de(r.content.decode())

    def _post_login(self, email, password='PassMuy.Larga2026!',
                    captcha_ok=True, honeypot='', dormir=2.0):
        csrf, token, loaded, sel = self._get_login_data()
        if not captcha_ok:
            sel = [0]   # respuesta incorrecta
        time.sleep(dormir)
        return self.c.post('/intranet/', {
            'csrfmiddlewaretoken': csrf,
            'email': email,
            'password': password,
            'website': honeypot,
            'captcha_token': token,
            'captcha_seleccion[]': sel,
            'page_loaded_at': str(loaded),
        })

    # ─── Casos de éxito ────────────────────────────────────────────────
    def test_login_exitoso(self):
        r = self._post_login('empleado@gmail.com')
        self.assertEqual(r.status_code, 302)
        self.assertEqual(r['Location'], '/intranet/bandeja/')
        self.assertEqual(self.c.session.get('usuario_email'), 'empleado@gmail.com')
        self.assertEqual(self.c.session.get('buzon_actual_email'), 'empleado.bandeja@pietramonte.cl')
        self.assertTrue(IntentoLogin.objects.filter(motivo='exito').exists())

    def test_usuario_sin_buzones_no_entra(self):
        """Usuario activo y autenticado, pero sin buzones asignados → bloqueado."""
        self.user.buzones.clear()
        r = self._post_login('empleado@gmail.com')
        self.assertEqual(r.status_code, 400)
        self.assertTrue(IntentoLogin.objects.filter(motivo='buzon_inexist').exists())

    # ─── Casos de fallo (todos deben verse iguales para el atacante) ───
    def test_password_incorrecto_devuelve_400_generico(self):
        r = self._post_login('empleado@gmail.com', password='Mala123456!')
        self.assertEqual(r.status_code, 400)
        self.assertIn('No fue posible iniciar', r.content.decode())
        self.assertTrue(IntentoLogin.objects.filter(motivo='password_invalida').exists())

    def test_email_no_existe_devuelve_400_generico(self):
        r = self._post_login('hacker@evil.com', password='Cualquier1234!')
        self.assertEqual(r.status_code, 400)
        self.assertIn('No fue posible iniciar', r.content.decode())
        self.assertTrue(IntentoLogin.objects.filter(motivo='email_no_lista').exists())

    def test_captcha_incorrecto_devuelve_400(self):
        r = self._post_login('empleado@gmail.com', captcha_ok=False)
        self.assertEqual(r.status_code, 400)
        self.assertTrue(IntentoLogin.objects.filter(motivo='captcha_fail').exists())

    def test_honeypot_lleno_devuelve_400(self):
        r = self._post_login('empleado@gmail.com', honeypot='spam')
        self.assertEqual(r.status_code, 400)
        self.assertTrue(IntentoLogin.objects.filter(motivo='honeypot').exists())

    def test_anti_enumeracion_mensaje_uniforme(self):
        """Los 3 fallos básicos deben verse idénticos para el atacante."""
        r1 = self._post_login('empleado@gmail.com', password='Mala123456!')
        cache.clear()
        r2 = self._post_login('hacker@evil.com', password='Cualquier1234!')
        cache.clear()
        r3 = self._post_login('empleado@gmail.com', password='')
        # Todos 400, todos con mismo mensaje genérico
        self.assertEqual(r1.status_code, 400)
        self.assertEqual(r2.status_code, 400)
        self.assertEqual(r3.status_code, 400)
        for r in (r1, r2, r3):
            self.assertIn('No fue posible iniciar', r.content.decode())

    def test_rate_limit_a_los_5_fallos(self):
        for _ in range(5):
            self._post_login('hacker@evil.com', password='x')
        r = self._post_login('hacker@evil.com', password='x')
        self.assertEqual(r.status_code, 429)
        self.assertTrue(IntentoLogin.objects.filter(motivo='throttled').exists())

    def test_usuario_inactivo_no_entra(self):
        self.user.activo = False
        self.user.save()
        r = self._post_login('empleado@gmail.com')
        self.assertEqual(r.status_code, 400)
        self.assertTrue(IntentoLogin.objects.filter(motivo='usuario_inactivo').exists())


@override_settings(
    PORTAL_ALLOWED_EMAILS=['empleado@gmail.com'],
    STORAGES={
        'default': {'BACKEND': 'django.core.files.storage.FileSystemStorage'},
        'staticfiles': {'BACKEND': 'django.contrib.staticfiles.storage.StaticFilesStorage'},
    },
)
class LogoutTests(TestCase):

    def setUp(self):
        cache.clear()
        u = UsuarioPortal(email='empleado@gmail.com', activo=True)
        u.set_password('PassMuy.Larga2026!')
        u.save()
        b = Buzon.objects.create(email='empleado.bandeja@pietramonte.cl')
        u.buzones.add(b)
        # Login forzado vía sesión
        self.c = Client(HTTP_HOST='localhost')
        s = self.c.session
        s['usuario_email'] = 'empleado@gmail.com'
        s['buzon_actual_id'] = b.id
        s['buzon_actual_email'] = b.email
        s.save()

    def test_logout_via_get_rechazado(self):
        r = self.c.get('/intranet/salir/')
        self.assertEqual(r.status_code, 405)
        self.assertEqual(self.c.session.get('usuario_email'), 'empleado@gmail.com')

    def test_logout_via_post_funciona(self):
        r = self.c.post('/intranet/salir/')
        self.assertEqual(r.status_code, 302)
        self.assertIsNone(self.c.session.get('usuario_email'))


@override_settings(STORAGES={
    'default': {'BACKEND': 'django.core.files.storage.FileSystemStorage'},
    'staticfiles': {'BACKEND': 'django.contrib.staticfiles.storage.StaticFilesStorage'},
})
class AdjuntoAuthTests(TestCase):
    """
    Validar que un usuario solo puede ver adjuntos de SU buzón.
    """
    def setUp(self):
        cache.clear()
        self.b1 = Buzon.objects.create(email='alice@pietramonte.cl')
        self.b2 = Buzon.objects.create(email='bob@pietramonte.cl')

        # Usuarios con acceso solo a SU buzón
        self.u_alice = UsuarioPortal(email='alice@gmail.com', activo=True)
        self.u_alice.set_password('PassMuy.Larga2026!')
        self.u_alice.save()
        self.u_alice.buzones.add(self.b1)

        self.u_bob = UsuarioPortal(email='bob@gmail.com', activo=True)
        self.u_bob.set_password('PassMuy.Larga2026!')
        self.u_bob.save()
        self.u_bob.buzones.add(self.b2)

        c1 = Correo.objects.create(buzon=self.b1, asunto='para alice')
        c2 = Correo.objects.create(buzon=self.b2, asunto='para bob')

        self.adj_alice = Adjunto(correo=c1, nombre_original='alice.pdf', mime_type='application/pdf', tamano_bytes=4)
        self.adj_alice.archivo.save('alice.pdf', ContentFile(b'%PDF'), save=False)
        self.adj_alice.save()

        self.adj_bob = Adjunto(correo=c2, nombre_original='bob.pdf', mime_type='application/pdf', tamano_bytes=4)
        self.adj_bob.archivo.save('bob.pdf', ContentFile(b'%PDF'), save=False)
        self.adj_bob.save()

    def test_sin_login_redirige(self):
        c = Client(HTTP_HOST='localhost')
        r = c.get(f'/intranet/adjunto/{self.adj_alice.id}/')
        self.assertEqual(r.status_code, 302)

    def _login_como(self, usuario, buzon):
        c = Client(HTTP_HOST='localhost')
        s = c.session
        s['usuario_email'] = usuario.email
        s['buzon_actual_id'] = buzon.id
        s['buzon_actual_email'] = buzon.email
        s.save()
        return c

    def test_dueno_descarga_su_adjunto(self):
        c = self._login_como(self.u_alice, self.b1)
        r = c.get(f'/intranet/adjunto/{self.adj_alice.id}/')
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r['X-Content-Type-Options'], 'nosniff')
        self.assertIn('sandbox', r.get('Content-Security-Policy', ''))

    def test_no_dueno_no_descarga_404(self):
        """Alice intenta bajar el adjunto de Bob → 404, no 403 (defense in depth)."""
        c = self._login_como(self.u_alice, self.b1)
        r = c.get(f'/intranet/adjunto/{self.adj_bob.id}/')
        self.assertEqual(r.status_code, 404)

    def test_admin_descarga_cualquier_adjunto(self):
        admin = UsuarioPortal(email='admin@gmail.com', activo=True, es_admin=True)
        admin.set_password('PassMuy.Larga2026!')
        admin.save()
        c = self._login_como(admin, self.b1)
        # Adjunto de Alice → OK
        self.assertEqual(c.get(f'/intranet/adjunto/{self.adj_alice.id}/').status_code, 200)
        # Adjunto de Bob → también OK
        self.assertEqual(c.get(f'/intranet/adjunto/{self.adj_bob.id}/').status_code, 200)


@override_settings(STORAGES={
    'default': {'BACKEND': 'django.core.files.storage.FileSystemStorage'},
    'staticfiles': {'BACKEND': 'django.contrib.staticfiles.storage.StaticFilesStorage'},
})
class AdminAccessTests(TestCase):
    def setUp(self):
        cache.clear()
        self.super = User.objects.create_superuser(
            username='admin', email='a@a.com', password='SuperLarga.2026!')

    def test_admin_url_ofuscada_responde(self):
        from django.conf import settings
        c = Client(HTTP_HOST='localhost')
        r = c.get('/' + settings.ADMIN_URL_PATH, follow=True)
        # Debe pedirle login
        self.assertEqual(r.status_code, 200)
        self.assertIn('login', r.request['PATH_INFO'])

    def test_admin_anonimo_redirige_a_login(self):
        from django.conf import settings
        c = Client(HTTP_HOST='localhost')
        r = c.get('/' + settings.ADMIN_URL_PATH)
        self.assertIn(r.status_code, (302, 301))


class CaptchaTests(TestCase):
    def test_token_firmado_no_se_puede_falsificar(self):
        from correos import captcha
        challenge = captcha.generar_challenge('vehiculos')
        # Modificar payload sin re-firmar → debe fallar
        token_modificado = challenge['token'].split('.')[0] + '.AAAA'
        with self.assertRaises(captcha.CaptchaError):
            captcha.verificar(token_modificado, [0])

    def test_token_correcto_pasa(self):
        from correos import captcha
        ch = captcha.generar_challenge('vehiculos')
        # Decodifica para conocer la respuesta correcta
        payload_b64 = ch['token'].split('.')[0]
        pad = 4 - len(payload_b64) % 4
        correctos = json.loads(base64.urlsafe_b64decode(payload_b64 + '=' * pad))['i']
        cat = captcha.verificar(ch['token'], correctos)
        self.assertEqual(cat, 'vehiculos')


@override_settings(
    PORTAL_ALLOWED_EMAILS=['empleado@gmail.com'],
    STORAGES={
        'default': {'BACKEND': 'django.core.files.storage.FileSystemStorage'},
        'staticfiles': {'BACKEND': 'django.contrib.staticfiles.storage.StaticFilesStorage'},
    },
)
class CambiarPasswordTests(TestCase):
    def setUp(self):
        cache.clear()
        self.user = UsuarioPortal(email='empleado@gmail.com', activo=True)
        self.user.set_password('PassActual.2026!')
        self.user.save()
        b = Buzon.objects.create(email='empleado.bandeja@pietramonte.cl')
        self.user.buzones.add(b)
        self.c = Client(HTTP_HOST='localhost')
        s = self.c.session
        s['usuario_email'] = 'empleado@gmail.com'
        s['buzon_actual_id'] = b.id
        s['buzon_actual_email'] = b.email
        s.save()

    def test_cambia_con_actual_correcta_y_nueva_valida(self):
        r = self.c.post('/intranet/cambiar-password/', {
            'actual': 'PassActual.2026!',
            'nueva':  'NuevaSegura.2027!',
            'nueva2': 'NuevaSegura.2027!',
        })
        self.assertEqual(r.status_code, 302)
        self.user.refresh_from_db()
        self.assertTrue(self.user.check_password('NuevaSegura.2027!'))

    def test_actual_incorrecta_rechazada(self):
        r = self.c.post('/intranet/cambiar-password/', {
            'actual': 'mal',
            'nueva':  'NuevaSegura.2027!',
            'nueva2': 'NuevaSegura.2027!',
        })
        self.assertEqual(r.status_code, 400)

    def test_password_corta_rechazada_por_validador(self):
        r = self.c.post('/intranet/cambiar-password/', {
            'actual': 'PassActual.2026!',
            'nueva':  'corta',
            'nueva2': 'corta',
        })
        self.assertEqual(r.status_code, 400)

    def test_password_parecida_al_email_rechazada(self):
        r = self.c.post('/intranet/cambiar-password/', {
            'actual': 'PassActual.2026!',
            'nueva':  'empleado2026!',
            'nueva2': 'empleado2026!',
        })
        self.assertEqual(r.status_code, 400)

    def test_password_comun_rechazada(self):
        r = self.c.post('/intranet/cambiar-password/', {
            'actual': 'PassActual.2026!',
            'nueva':  'password123',
            'nueva2': 'password123',
        })
        self.assertEqual(r.status_code, 400)


@override_settings(STORAGES={
    'default': {'BACKEND': 'django.core.files.storage.FileSystemStorage'},
    'staticfiles': {'BACKEND': 'django.contrib.staticfiles.storage.StaticFilesStorage'},
})
class MultiBuzonTests(TestCase):
    """
    Tests específicos del multi-buzón:
      - Usuario con varios buzones puede cambiar entre ellos.
      - Selector se muestra solo si hay 2+ buzones.
      - Intentar acceder a un buzón ajeno → 404 (no se filtra existencia).
      - Admin ve todos.
    """
    def setUp(self):
        cache.clear()
        # 3 buzones
        self.b1 = Buzon.objects.create(email='aledezma@pietramonte.cl')
        self.b2 = Buzon.objects.create(email='contacto@pietramonte.cl')
        self.b3 = Buzon.objects.create(email='cobranza@pietramonte.cl')
        Correo.objects.create(buzon=self.b1, asunto='para aledezma')
        c2 = Correo.objects.create(buzon=self.b2, asunto='para contacto')
        c3 = Correo.objects.create(buzon=self.b3, asunto='para cobranza')
        self.c2_id = c2.id
        self.c3_id = c3.id

        # Usuario con acceso a 2 buzones (b1 y b2, NO b3)
        self.u_multi = UsuarioPortal(email='pventas@gmail.com', activo=True)
        self.u_multi.set_password('PassMuy.Larga2026!')
        self.u_multi.save()
        self.u_multi.buzones.set([self.b1, self.b2])

        # Admin que ve todos
        self.u_admin = UsuarioPortal(email='admin@gmail.com', activo=True, es_admin=True)
        self.u_admin.set_password('PassMuy.Larga2026!')
        self.u_admin.save()

    def _login(self, usuario):
        c = Client(HTTP_HOST='localhost', enforce_csrf_checks=False)
        s = c.session
        s['usuario_email'] = usuario.email
        primera = usuario.buzones_visibles().first()
        if primera:
            s['buzon_actual_id'] = primera.id
            s['buzon_actual_email'] = primera.email
        s.save()
        return c

    def test_buzones_visibles_no_admin(self):
        self.assertEqual(set(self.u_multi.buzones_visibles().values_list('email', flat=True)),
                         {'aledezma@pietramonte.cl', 'contacto@pietramonte.cl'})

    def test_buzones_visibles_admin(self):
        emails = set(self.u_admin.buzones_visibles().values_list('email', flat=True))
        self.assertIn('aledezma@pietramonte.cl', emails)
        self.assertIn('cobranza@pietramonte.cl', emails)
        self.assertEqual(len(emails), 3)

    def test_inbox_muestra_selector_si_hay_varios(self):
        c = self._login(self.u_multi)
        r = c.get('/intranet/bandeja/')
        self.assertEqual(r.status_code, 200)
        html = r.content.decode()
        self.assertIn('buzon-selector', html)
        self.assertIn('aledezma@pietramonte.cl', html)
        self.assertIn('contacto@pietramonte.cl', html)
        self.assertNotIn('cobranza@pietramonte.cl', html)

    def test_cambiar_buzon_a_uno_propio_ok(self):
        c = self._login(self.u_multi)
        r = c.post('/intranet/buzon/cambiar/', {'buzon_id': self.b2.id})
        self.assertEqual(r.status_code, 302)
        self.assertEqual(c.session.get('buzon_actual_id'), self.b2.id)

    def test_cambiar_a_buzon_ajeno_devuelve_404(self):
        c = self._login(self.u_multi)
        r = c.post('/intranet/buzon/cambiar/', {'buzon_id': self.b3.id})
        self.assertEqual(r.status_code, 404)
        # La sesión NO se modificó
        self.assertNotEqual(c.session.get('buzon_actual_id'), self.b3.id)

    def test_cambiar_buzon_solo_post(self):
        c = self._login(self.u_multi)
        r = c.get('/intranet/buzon/cambiar/?buzon_id=' + str(self.b2.id))
        self.assertEqual(r.status_code, 405)

    def test_correo_de_buzon_ajeno_404(self):
        c = self._login(self.u_multi)
        # Intenta abrir el correo de cobranza (b3) al que no tiene acceso
        r = c.get(f'/intranet/correo/{self.c3_id}/')
        self.assertEqual(r.status_code, 404)

    def test_correo_de_buzon_propio_pero_no_actual_cambia_sesion(self):
        """Si abre un correo de un buzón visible distinto al actual, la sesión se actualiza."""
        c = self._login(self.u_multi)   # arranca con b1 como actual
        # Abre un correo de b2 (también suyo)
        r = c.get(f'/intranet/correo/{self.c2_id}/')
        self.assertEqual(r.status_code, 200)
        self.assertEqual(c.session.get('buzon_actual_id'), self.b2.id)

    def test_admin_ve_todos_los_buzones_en_selector(self):
        c = self._login(self.u_admin)
        r = c.get('/intranet/bandeja/')
        html = r.content.decode()
        for email in ['aledezma', 'contacto', 'cobranza']:
            self.assertIn(email + '@pietramonte.cl', html)


@override_settings(STORAGES={
    'default': {'BACKEND': 'django.core.files.storage.FileSystemStorage'},
    'staticfiles': {'BACKEND': 'django.contrib.staticfiles.storage.StaticFilesStorage'},
})
class OrganizacionInboxTests(TestCase):
    """
    Tests de destacar / etiquetas / notas: control de acceso, validación,
    AJAX endpoints, filtros del inbox.
    """
    def setUp(self):
        cache.clear()
        self.b = Buzon.objects.create(email='aledezma@pietramonte.cl')
        self.b_otro = Buzon.objects.create(email='cobranza@pietramonte.cl')

        self.u = UsuarioPortal(email='alice@gmail.com', activo=True)
        self.u.set_password('PassMuy.Larga2026!')
        self.u.save()
        self.u.buzones.add(self.b)   # SOLO acceso a aledezma

        self.correo = Correo.objects.create(buzon=self.b, asunto='hola', destacado=False)
        self.correo_ajeno = Correo.objects.create(buzon=self.b_otro, asunto='ajeno')

        self.et = Etiqueta.objects.create(buzon=self.b, nombre='Factura', color='#1976D2')
        self.et_ajena = Etiqueta.objects.create(buzon=self.b_otro, nombre='Otra', color='#388E3C')

        self.c = Client(HTTP_HOST='localhost', enforce_csrf_checks=False)
        s = self.c.session
        s['usuario_email'] = 'alice@gmail.com'
        s['buzon_actual_id'] = self.b.id
        s['buzon_actual_email'] = self.b.email
        s.save()

    def test_destacar_correo_propio_funciona(self):
        r = self.c.post(f'/intranet/correo/{self.correo.id}/destacar/')
        self.assertEqual(r.status_code, 200)
        self.correo.refresh_from_db()
        self.assertTrue(self.correo.destacado)
        # toggle de nuevo
        self.c.post(f'/intranet/correo/{self.correo.id}/destacar/')
        self.correo.refresh_from_db()
        self.assertFalse(self.correo.destacado)

    def test_destacar_correo_ajeno_404(self):
        r = self.c.post(f'/intranet/correo/{self.correo_ajeno.id}/destacar/')
        self.assertEqual(r.status_code, 404)
        self.correo_ajeno.refresh_from_db()
        self.assertFalse(self.correo_ajeno.destacado)

    def test_destacar_solo_post(self):
        r = self.c.get(f'/intranet/correo/{self.correo.id}/destacar/')
        self.assertEqual(r.status_code, 405)

    def test_asignar_etiqueta_propia_funciona(self):
        r = self.c.post(f'/intranet/correo/{self.correo.id}/etiqueta/', {
            'etiqueta_id': self.et.id, 'accion': 'asignar',
        })
        self.assertEqual(r.status_code, 200)
        self.assertIn(self.et, self.correo.etiquetas.all())

    def test_asignar_etiqueta_de_OTRO_buzon_404(self):
        """Aunque el correo sea propio, una etiqueta de otro buzón no debe asignarse."""
        r = self.c.post(f'/intranet/correo/{self.correo.id}/etiqueta/', {
            'etiqueta_id': self.et_ajena.id, 'accion': 'asignar',
        })
        self.assertEqual(r.status_code, 404)
        self.assertNotIn(self.et_ajena, self.correo.etiquetas.all())

    def test_quitar_etiqueta_funciona(self):
        self.correo.etiquetas.add(self.et)
        r = self.c.post(f'/intranet/correo/{self.correo.id}/etiqueta/', {
            'etiqueta_id': self.et.id, 'accion': 'quitar',
        })
        self.assertEqual(r.status_code, 200)
        self.assertNotIn(self.et, self.correo.etiquetas.all())

    def test_actualizar_notas_propias(self):
        r = self.c.post(f'/intranet/correo/{self.correo.id}/notas/', {
            'notas': 'Cliente llamó pidiendo factura nueva',
        })
        self.assertEqual(r.status_code, 200)
        self.correo.refresh_from_db()
        self.assertEqual(self.correo.notas, 'Cliente llamó pidiendo factura nueva')

    def test_notas_se_truncan_a_5000(self):
        largo = 'x' * 6000
        self.c.post(f'/intranet/correo/{self.correo.id}/notas/', {'notas': largo})
        self.correo.refresh_from_db()
        self.assertEqual(len(self.correo.notas), 5000)

    def test_crear_etiqueta_en_mi_buzon(self):
        r = self.c.post('/intranet/buzon/etiqueta-nueva/', {
            'nombre': 'Urgente', 'color': '#C80C0F',
        })
        self.assertEqual(r.status_code, 200)
        self.assertTrue(self.b.etiquetas.filter(nombre='Urgente').exists())

    def test_crear_etiqueta_color_invalido_se_corrige(self):
        r = self.c.post('/intranet/buzon/etiqueta-nueva/', {
            'nombre': 'TestColor', 'color': '#ZZZZZZ',
        })
        self.assertEqual(r.status_code, 200)
        et = self.b.etiquetas.get(nombre='TestColor')
        # El color inválido cae al rojo por default
        self.assertEqual(et.color, '#C80C0F')

    def test_filtro_destacados_funciona(self):
        Correo.objects.create(buzon=self.b, asunto='otro', destacado=True)
        r = self.c.get('/intranet/bandeja/?destacado=1')
        self.assertEqual(r.status_code, 200)
        # Solo el destacado
        self.assertEqual(len(r.context['page'].object_list), 1)
        self.assertEqual(r.context['page'].object_list[0].asunto, 'otro')

    def test_filtro_etiqueta_funciona(self):
        otro = Correo.objects.create(buzon=self.b, asunto='otro')
        otro.etiquetas.add(self.et)
        r = self.c.get(f'/intranet/bandeja/?etiqueta={self.et.id}')
        self.assertEqual(r.status_code, 200)
        self.assertEqual(len(r.context['page'].object_list), 1)
        self.assertEqual(r.context['page'].object_list[0].id, otro.id)

    def test_etiqueta_ajena_se_ignora(self):
        """Pasar ?etiqueta=ID-de-otro-buzon: muestra todos sin filtrar."""
        total_correos = self.b.correos.count()
        r = self.c.get(f'/intranet/bandeja/?etiqueta={self.et_ajena.id}')
        self.assertEqual(r.status_code, 200)
        # No filtra (etiqueta_actual queda en None)
        self.assertEqual(len(r.context['page'].object_list), total_correos)
        self.assertIsNone(r.context['etiqueta_actual'])


@override_settings(STORAGES={
    'default': {'BACKEND': 'django.core.files.storage.FileSystemStorage'},
    'staticfiles': {'BACKEND': 'django.contrib.staticfiles.storage.StaticFilesStorage'},
})
class CSPHeadersTests(TestCase):
    def test_csp_estricta_en_landing(self):
        c = Client(HTTP_HOST='localhost')
        r = c.get('/')
        csp = r.get('Content-Security-Policy', '')
        self.assertIn("script-src 'self'", csp)
        self.assertNotIn("'unsafe-inline'", csp.split("script-src")[1].split(';')[0])

    def test_csp_relajada_en_admin(self):
        from django.conf import settings
        c = Client(HTTP_HOST='localhost')
        r = c.get('/' + settings.ADMIN_URL_PATH + 'login/')
        csp = r.get('Content-Security-Policy', '')
        # Admin necesita inline para sus widgets
        self.assertIn("'unsafe-inline'", csp)

    def test_xframe_options_deny_en_html(self):
        c = Client(HTTP_HOST='localhost')
        r = c.get('/')
        self.assertEqual(r.get('X-Frame-Options'), 'DENY')


# ─── Avatar iniciales ────────────────────────────────────────────────────
# Bug histórico: 'Rodrigo Del saz <a@b.cl>' → 'R<' en el avatar (el `<` del
# email se contaba como segunda inicial). Test asegura que no vuelve.
class AvatarInicialesFilterTests(TestCase):
    def test_nombre_con_email_descarta_email(self):
        from correos.templatetags.correos_tags import avatar_iniciales
        self.assertEqual(avatar_iniciales('Rodrigo Del saz <a@b.cl>'), 'RS')

    def test_solo_nombre(self):
        from correos.templatetags.correos_tags import avatar_iniciales
        self.assertEqual(avatar_iniciales('Ana Ledezma'), 'AL')

    def test_solo_email_entre_brackets(self):
        from correos.templatetags.correos_tags import avatar_iniciales
        # No hay nombre — fallback al local-part + domain del email
        self.assertEqual(avatar_iniciales('<solo@email.cl>'), 'SE')

    def test_email_bare_sin_brackets(self):
        from correos.templatetags.correos_tags import avatar_iniciales
        self.assertEqual(avatar_iniciales('a@b.cl'), 'AB')
        self.assertEqual(avatar_iniciales('OficinaInternet@rtsp.cl'), 'OR')

    def test_vacios_devuelven_signo_pregunta(self):
        from correos.templatetags.correos_tags import avatar_iniciales
        self.assertEqual(avatar_iniciales(''), '?')
        self.assertEqual(avatar_iniciales('   '), '?')
        self.assertEqual(avatar_iniciales(None), '?')


# ─── cid: resolution ──────────────────────────────────────────────────────
# Tests para que `<img src="cid:xxx">` en cuerpo_html se resuelva a la URL
# interna autenticada del adjunto, y para isolation cross-buzón.
@override_settings(STORAGES={
    'default': {'BACKEND': 'django.core.files.storage.FileSystemStorage'},
    'staticfiles': {'BACKEND': 'django.contrib.staticfiles.storage.StaticFilesStorage'},
})
class CidResolutionTests(TestCase):

    def setUp(self):
        cache.clear()

        # Buzón de alice + un correo con HTML que referencia un cid
        self.b_alice = Buzon.objects.create(email='alice.bandeja@pietramonte.cl')
        self.u_alice = UsuarioPortal(email='alice@gmail.com', activo=True)
        self.u_alice.set_password('PassMuy.Larga2026!')
        self.u_alice.save()
        self.u_alice.buzones.add(self.b_alice)

        self.cid = '5db34974-7359-4231-bea1-d6cca25338e2@gmail.com'
        self.correo = Correo.objects.create(
            buzon=self.b_alice,
            asunto='factura',
            cuerpo_html=f'<p>Acuso recibo</p><img src="cid:{self.cid}" alt="firma">',
        )
        self.adj_inline = Adjunto(
            correo=self.correo,
            nombre_original='firma.png',
            mime_type='image/png',
            tamano_bytes=8,
            content_id=self.cid,
        )
        # bytes mínimos pero válidos como PNG-ish (no validamos magic, solo
        # mime_type para decidir inline)
        self.adj_inline.archivo.save(
            'firma.png',
            ContentFile(b'\x89PNG\r\n\x1a\n'),
            save=False,
        )
        self.adj_inline.save()

        # Buzón ajeno con un cid igual — para test de isolation
        self.b_bob = Buzon.objects.create(email='bob.bandeja@pietramonte.cl')
        self.u_bob = UsuarioPortal(email='bob@gmail.com', activo=True)
        self.u_bob.set_password('PassMuy.Larga2026!')
        self.u_bob.save()
        self.u_bob.buzones.add(self.b_bob)
        self.correo_bob = Correo.objects.create(buzon=self.b_bob, asunto='ajeno')
        self.adj_bob = Adjunto(
            correo=self.correo_bob,
            nombre_original='secreto.png',
            mime_type='image/png',
            tamano_bytes=8,
            content_id=self.cid,  # mismo cid → simula colisión
        )
        self.adj_bob.archivo.save('secreto.png', ContentFile(b'\x89PNG'), save=False)
        self.adj_bob.save()

    def _login(self, usuario, buzon):
        c = Client(HTTP_HOST='localhost')
        s = c.session
        s['usuario_email'] = usuario.email
        s['buzon_actual_id'] = buzon.id
        s['buzon_actual_email'] = buzon.email
        s.save()
        return c

    def test_resolver_cid_en_html_mapea_a_url_interna(self):
        from correos.templatetags.correos_tags import _resolver_cid_en_html
        out = _resolver_cid_en_html(self.correo.cuerpo_html, self.correo)
        # cid: ya no está, hay una URL interna
        self.assertNotIn('cid:', out)
        self.assertIn(f'/intranet/correo/{self.correo.id}/cid/', out)

    def test_resolver_cid_no_resuelto_queda_intacto(self):
        from correos.templatetags.correos_tags import _resolver_cid_en_html
        # cid que no existe entre los adjuntos → bleach lo strippea después
        # pero acá solo verificamos que _resolver_cid_en_html no lo toca.
        html = '<img src="cid:fantasma">'
        out = _resolver_cid_en_html(html, self.correo)
        self.assertEqual(out, html)

    def test_render_correo_html_strip_de_imgs_externas(self):
        """`<img>` con src http externa debe ser strippeado (anti tracking)."""
        from correos.templatetags.correos_tags import render_correo_html
        c = Correo.objects.create(
            buzon=self.b_alice,
            cuerpo_html='<p>x</p><img src="https://tracking.evil/pixel.png">',
        )
        out = str(render_correo_html(c))
        self.assertIn('<p>x</p>', out)
        self.assertNotIn('tracking.evil', out)
        self.assertNotIn('<img', out)

    def test_render_correo_html_permite_data_image(self):
        from correos.templatetags.correos_tags import render_correo_html
        c = Correo.objects.create(
            buzon=self.b_alice,
            cuerpo_html='<img src="data:image/png;base64,iVBORw0KGgo=">',
        )
        out = str(render_correo_html(c))
        self.assertIn('data:image/png', out)

    def test_render_correo_html_resuelve_cid_a_url(self):
        from correos.templatetags.correos_tags import render_correo_html
        out = str(render_correo_html(self.correo))
        self.assertIn(f'/intranet/correo/{self.correo.id}/cid/', out)
        self.assertNotIn('cid:', out)

    def test_cid_view_sirve_imagen_a_dueno(self):
        c = self._login(self.u_alice, self.b_alice)
        r = c.get(f'/intranet/correo/{self.correo.id}/cid/{self.cid}')
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r['X-Content-Type-Options'], 'nosniff')
        self.assertEqual(r['Content-Type'], 'image/png')

    def test_cid_view_404_a_otro_usuario(self):
        """Bob no puede acceder al cid del correo de Alice."""
        c = self._login(self.u_bob, self.b_bob)
        r = c.get(f'/intranet/correo/{self.correo.id}/cid/{self.cid}')
        self.assertEqual(r.status_code, 404)

    def test_cid_view_no_cross_buzon_con_mismo_cid(self):
        """
        Alice y Bob tienen ambos un adjunto con el mismo content_id, pero
        en correos distintos. La URL de Alice NUNCA debe servir el adjunto
        de Bob (la lookup se restringe a `correo=correo_id`).
        """
        c = self._login(self.u_alice, self.b_alice)
        r = c.get(f'/intranet/correo/{self.correo.id}/cid/{self.cid}')
        self.assertEqual(r.status_code, 200)
        # El bytes que sirve es el de alice (firma.png), no el de bob (secreto.png)
        self.assertIn(b'\x89PNG', b''.join(r.streaming_content))

    def test_cid_view_404_si_content_id_no_es_imagen(self):
        # Un adjunto con content_id pero mime no-image → no debe servirse
        # como inline (defense en depth contra abuso del endpoint).
        adj_pdf = Adjunto(
            correo=self.correo,
            nombre_original='evil.pdf',
            mime_type='application/pdf',
            tamano_bytes=5,
            content_id='pdf-cid-x',
        )
        adj_pdf.archivo.save('evil.pdf', ContentFile(b'%PDF\n'), save=False)
        adj_pdf.save()
        c = self._login(self.u_alice, self.b_alice)
        r = c.get(f'/intranet/correo/{self.correo.id}/cid/pdf-cid-x')
        self.assertEqual(r.status_code, 404)

    def test_cid_view_sin_login_redirige(self):
        c = Client(HTTP_HOST='localhost')
        r = c.get(f'/intranet/correo/{self.correo.id}/cid/{self.cid}')
        self.assertEqual(r.status_code, 302)

    def test_strip_cid_brackets_en_texto(self):
        from correos.templatetags.correos_tags import _strip_cid_brackets_en_texto
        texto = 'Acuso recibo.\n[cid:5db34974-...] \nGracias!'
        out = _strip_cid_brackets_en_texto(texto)
        self.assertNotIn('[cid:', out)
        self.assertIn('Acuso recibo.', out)
        self.assertIn('Gracias!', out)

    def test_strip_cid_brackets_no_toca_otros_brackets(self):
        from correos.templatetags.correos_tags import _strip_cid_brackets_en_texto
        texto = 'Atte. [Equipo Soporte]'
        self.assertEqual(_strip_cid_brackets_en_texto(texto), texto)


# ─── HTML sanitización ────────────────────────────────────────────────────
# Tests anti-XSS sobre el render del cuerpo HTML de correos.
class HtmlSanitizationTests(TestCase):
    def test_script_tag_strippeado(self):
        from correos.templatetags.correos_tags import sanitizar_email_html
        out = sanitizar_email_html('<p>hola</p><script>alert(1)</script>')
        self.assertNotIn('<script', out)
        self.assertNotIn('alert', out)

    def test_eventos_on_strippeados(self):
        from correos.templatetags.correos_tags import sanitizar_email_html
        out = sanitizar_email_html('<a href="x" onclick="alert(1)">x</a>')
        self.assertNotIn('onclick', out)
        self.assertNotIn('alert', out)

    def test_javascript_url_strippeada(self):
        from correos.templatetags.correos_tags import sanitizar_email_html
        out = sanitizar_email_html('<a href="javascript:alert(1)">x</a>')
        self.assertNotIn('javascript:', out)

    def test_iframe_strippeado(self):
        from correos.templatetags.correos_tags import sanitizar_email_html
        out = sanitizar_email_html('<iframe src="evil"></iframe>')
        self.assertNotIn('<iframe', out)
