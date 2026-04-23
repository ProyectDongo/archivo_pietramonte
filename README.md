# Archivo de Correos — Pietramonte Automotriz

Portal de consulta de correos históricos para usuarios @pietramonte.cl

---

## Instalación en el servidor Linux (sin Docker, directo)

```bash
# 1. Clonar/copiar el proyecto
cd /opt
git clone ... archivo_pietramonte   # o copia manual
cd archivo_pietramonte

# 2. Crear entorno virtual
python3 -m venv venv
source venv/bin/activate

# 3. Instalar dependencias
pip install -r requirements.txt

# 4. Configurar variables de entorno
cp .env.example .env
nano .env   # editar SECRET_KEY y ALLOWED_HOSTS

# 5. Generar SECRET_KEY segura
python -c "from django.core.management.utils import get_random_secret_key; print(get_random_secret_key())"

# 6. Migrar base de datos
python manage.py migrate

# 7. Probar que funciona
python manage.py runserver 0.0.0.0:8001
```

---

## Instalación con Docker (recomendado)

```bash
# 1. Configurar .env
cp .env.example .env
nano .env

# 2. Construir imagen
docker compose build

# 3. Migrar base de datos (primera vez)
docker compose run --rm web python manage.py migrate

# 4. Levantar
docker compose up -d
```

---

## Importar archivos .mbox desde Thunderbird

Thunderbird guarda los correos en archivos .mbox en:
- Windows: C:\Users\TuUsuario\AppData\Roaming\Thunderbird\Profiles\xxx\Mail\
- Linux: ~/.thunderbird/xxx/Mail/

### Importar un buzón
```bash
# Sin Docker
python manage.py import_mbox aledezma@pietramonte.cl --archivo=/ruta/Inbox

# Con Docker
docker compose exec web python manage.py import_mbox aledezma@pietramonte.cl --archivo=/app/mbox/aledezma.mbox

# Reimportar limpio (borra e importa de nuevo)
python manage.py import_mbox aledezma@pietramonte.cl --archivo=/ruta/Inbox --limpiar
```

### Importar toda una carpeta de .mbox
```bash
python manage.py import_mbox cobranza@pietramonte.cl --carpeta=/opt/mboxes/cobranza/
```

### Copiar .mbox al servidor
```bash
# Desde tu PC con Thunderbird al servidor
scp "C:\Users\Anghello\AppData\Roaming\Thunderbird\Profiles\xxx\Mail\mail.pietramonte.cl\Inbox" \
    usuario@servidor:/opt/archivo_pietramonte/mbox/aledezma.mbox
```

---

## Configurar Cloudflare Tunnel

En Cloudflare → Zero Trust → Tunnels → tu tunnel existente:
```
Public hostname:  archivo.pietramonte.cl
Service:          http://localhost:8000   (o http://pietramonte_archivo:8000 si es Docker)
```

---

## Cuentas con acceso (editarlas en views.py)

```python
EMAILS_VALIDOS = [
    'aledezma@pietramonte.cl',
    'cobranza@pietramonte.cl',
    'contacto@pietramonte.cl',
    'cpietrasanta@pietramonte.cl',
    'vpietrasanta@pietramonte.cl',
    'ralbornoz@pietramonte.cl',
]
```

---

## Estructura del proyecto

```
archivo_pietramonte/
├── archivo_pietramonte/    # config Django
│   ├── settings.py
│   └── urls.py
├── correos/                # app principal
│   ├── models.py           # Buzon, Correo
│   ├── views.py            # login, inbox, detalle
│   ├── urls.py
│   └── management/commands/
│       └── import_mbox.py  # comando de importación
├── templates/
│   ├── base.html
│   └── correos/
│       ├── login.html
│       ├── inbox.html
│       └── detalle.html
├── mbox/                   # aquí van los archivos .mbox
├── .env
├── Dockerfile
├── docker-compose.yml
└── requirements.txt
```
