# Deploy en Hetzner + Coolify + Cloudflare Tunnel

Guía paso a paso para llevar el archivo a producción.

**Servidor target:** Hetzner CPX21 (3 vCPU / 4 GB RAM / 80 GB disco) con Ubuntu 24.04.
**Acceso público:** Cloudflare Tunnel (cero puertos abiertos al internet).
**Stack:** Docker Compose orquestado por Coolify.

> **Antes de empezar:** Ten a mano la IP del servidor, tus claves SSH, y acceso al panel de Cloudflare donde está `pietramonte.cl`.

---

## 1. Preparar el servidor (15 min)

### 1.1. Conectarte
```bash
ssh root@<IP-DEL-SERVIDOR>
```

### 1.2. Crear usuario no-root
```bash
adduser pietra
usermod -aG sudo pietra
mkdir -p /home/pietra/.ssh
cp ~/.ssh/authorized_keys /home/pietra/.ssh/
chown -R pietra:pietra /home/pietra/.ssh
chmod 700 /home/pietra/.ssh
chmod 600 /home/pietra/.ssh/authorized_keys
```

Salí (`exit`) y reconectate como `pietra`.

### 1.3. Hardening básico de SSH
Edita `/etc/ssh/sshd_config`:
```
PermitRootLogin no
PasswordAuthentication no
```
Recarga: `sudo systemctl restart ssh`.

### 1.4. UFW (firewall)
```bash
sudo ufw default deny incoming
sudo ufw default allow outgoing
sudo ufw allow ssh
sudo ufw enable
```
Verifica: `sudo ufw status`. **Solo SSH (22) debe estar abierto**. El Tunnel sale, no entra.

### 1.5. Swap (importante con 4 GB RAM)
```bash
sudo fallocate -l 2G /swapfile
sudo chmod 600 /swapfile
sudo mkswap /swapfile
sudo swapon /swapfile
echo '/swapfile none swap sw 0 0' | sudo tee -a /etc/fstab
```

### 1.6. Actualizar
```bash
sudo apt update && sudo apt upgrade -y
sudo apt install -y curl git ufw
```

---

## 2. Instalar Docker (5 min)

```bash
curl -fsSL https://get.docker.com | sudo sh
sudo usermod -aG docker $USER
newgrp docker
docker run hello-world   # debe imprimir "Hello from Docker!"
```

---

## 3. Instalar Coolify (10 min)

```bash
curl -fsSL https://cdn.coollabs.io/coolify/install.sh | sudo bash
```

Al final imprime una URL tipo `http://<IP>:8000`. Para acceder:
- Si NO tienes Tunnel todavía → crea uno provisorio (siguiente paso) o expón temporalmente `8000` sólo a tu IP.
- Si SÍ tienes Tunnel → crea ahora una regla para el panel de Coolify.

> ⚠️ **No expongas el panel de Coolify (puerto 8000) al internet sin protección.** Es el control total del servidor.

Crea cuenta de admin de Coolify (te lo pide al primer login).

---

## 4. Instalar y configurar Cloudflare Tunnel (10 min)

```bash
curl -L --output cloudflared.deb https://github.com/cloudflare/cloudflared/releases/latest/download/cloudflared-linux-amd64.deb
sudo dpkg -i cloudflared.deb
cloudflared tunnel login         # abre URL, autorizas en navegador
cloudflared tunnel create archivo-pietramonte
```

Apunta el dominio en Cloudflare:
```bash
cloudflared tunnel route dns archivo-pietramonte archivo.pietramonte.cl
```

Crea `/etc/cloudflared/config.yml`:
```yaml
tunnel: archivo-pietramonte
credentials-file: /home/pietra/.cloudflared/<TUNNEL-UUID>.json

ingress:
  - hostname: archivo.pietramonte.cl
    service: http://localhost:8001       # ← este es el puerto que expone nuestro Compose
  - hostname: coolify.pietramonte.cl     # OPCIONAL: panel de Coolify
    service: http://localhost:8000
  - service: http_status:404
```

Ahora instalalo como servicio:
```bash
sudo cloudflared service install
sudo systemctl start cloudflared
sudo systemctl enable cloudflared
```

Verifica logs: `sudo journalctl -u cloudflared -n 30`.

---

## 5. Deploy del proyecto vía Coolify (15 min)

### 5.1. Subir tu repo a GitHub
Si todavía no está, en tu PC:
```bash
git remote add origin git@github.com:tu-usuario/archivo_pietramonte.git
git push -u origin main
```

> ⚠️ **Verifica antes** que `.env`, `data/`, y `db.sqlite3` NO estén en git. Si dudas:
> ```bash
> git ls-files | grep -E "\.env$|\.sqlite|^data/" 
> ```
> No debe imprimir nada.

### 5.2. En Coolify (vía web)
1. **+ New Resource** → **Application** → **Public Repository** (o **Private** con tu PAT).
2. URL del repo, rama `main`.
3. **Build Pack: Dockerfile** (Coolify detecta el `Dockerfile` automáticamente).
4. **Domains**: `archivo.pietramonte.cl`.
5. **Port**: `8000` (lo que expone el contenedor; Coolify lo proxea internamente; el Tunnel apunta a Coolify).
6. **Environment variables**: copia el contenido de `.env.production.example` y rellena los valores reales (en Coolify, "Bulk add").

   **Genera SECRET_KEY**:
   ```bash
   docker run --rm python:3.12-slim python -c "import secrets; print(secrets.token_urlsafe(60))"
   ```

7. **Persistent Storage**: añade un volume `/app/data`. Coolify lo monta automáticamente y lo respalda.
8. **Healthcheck**: ya viene en el Dockerfile (`/healthz`). Coolify lo respeta.
9. **Deploy**.

Coolify clona el repo, hace `docker build`, levanta el contenedor, hace migraciones (NO automático — paso siguiente), y levanta.

### 5.3. Migrar BD en producción (1 sola vez)
Desde el panel de Coolify → **Terminal** del contenedor:
```bash
python manage.py migrate
python manage.py createsuperuser            # admin de Django (tú)
python manage.py seed_estructura            # crea los 7 buzones + 5 usuarios reales
```

> El `seed_estructura` te imprime los passwords de los empleados. **Anótalos** y compártelos por canal seguro.

---

## 6. Subir los `.mbox` históricos (varía según tamaño)

### 6.1. Desde tu PC (donde está Thunderbird)
Comprime los `.mbox` de cada cuenta:
```powershell
# PowerShell — repite por cada cuenta
Compress-Archive -Path "C:\Users\<TU>\AppData\Roaming\Thunderbird\Profiles\<HASH>\Mail\<servidor>\Inbox" `
                 -DestinationPath "$env:USERPROFILE\Desktop\aledezma.zip"
```

### 6.2. Subirlos al servidor
```bash
scp aledezma.zip pietra@<IP>:/tmp/
# repite por cada cuenta
```

### 6.3. En el servidor: extraer + importar
```bash
# Ubica la carpeta data del contenedor (Coolify la monta en algún path real)
docker volume inspect $(docker inspect <container-id> --format '{{ range .Mounts }}{{ .Source }}{{end}}')
# Para simplificar, usa la ruta dentro del compose:
mkdir -p /opt/coolify/.../data/mbox/import   # ajustar según donde Coolify pone tu volumen

# Descomprime cada uno
unzip /tmp/aledezma.zip -d ~/imports/aledezma/

# Copia al volumen del contenedor (más simple via terminal del contenedor)
# Desde Coolify → Terminal del container:
mkdir -p /app/data/mbox/import
# luego copia con docker cp desde fuera, o usa volumes share
```

Más fácil: **abre la terminal del contenedor en Coolify**, y desde otra ventana:
```bash
docker cp ~/imports/aledezma/Inbox <container-id>:/app/data/mbox/aledezma_inbox
```

Y ya en la terminal del contenedor:
```bash
python manage.py import_mbox aledezma@pietramonte.cl --archivo=/app/data/mbox/aledezma_inbox
```

Repite por cada buzón. Los adjuntos se extraen automáticamente a `/app/data/adjuntos/`.

---

## 7. Verificar (2 min)

Desde tu PC, navega:
- `https://archivo.pietramonte.cl/` → landing público
- `https://archivo.pietramonte.cl/intranet/` → login del portal
- `https://archivo.pietramonte.cl/admin-pm-<TU-SUFIJO>/` → admin Django

Verifica el healthcheck (en Coolify debe estar verde):
```bash
curl https://archivo.pietramonte.cl/healthz
# → ok
```

Si todo verde: **¡estás en producción!** 🎉

---

## 7.5 Backup de adjuntos a Backblaze B2 (post-deploy, 10 min)

Coolify ya respalda la DB Postgres a B2 (sección 8). Esto cubre **el otro
volumen crítico**: `/app/data/adjuntos/` (archivos adjuntos de correos).

El comando vive en el contenedor (`python manage.py backup_adjuntos_b2`)
porque ahí está `rclone` instalado (Dockerfile) y `MEDIA_ROOT` se resuelve
con `settings.py`. Lo dispara el cron del host vía `docker exec` para que
sobreviva al rebuild de imagen.

### 7.5.1. Env vars en Coolify (una vez)

```env
B2_KEY_ID=REEMPLAZAR
B2_APPLICATION_KEY=REEMPLAZAR
B2_BUCKET_NAME=pietramonte-backups
B2_REGION=us-west-002        # opcional, informativo
B2_ENDPOINT=https://s3.us-west-002.backblazeb2.com   # opcional, no se usa con backend b2 nativo
```

Marcar `B2_APPLICATION_KEY` como **Is Secret** en Coolify. Redeploy para
que las env vars lleguen al contenedor.

### 7.5.2. Verificar credenciales antes del primer sync

```bash
ssh dongo
CONT=$(docker ps --format '{{.Names}}' | grep o1rd | head -1)
docker exec -it $CONT python manage.py backup_adjuntos_b2 --check
# → debe imprimir el contenido del bucket (vacío al principio) + "OK"
```

Si falla con "credenciales o bucket inválidos": revisar que las env vars
estén bien escritas (sin espacios, sin comillas) y que la Application Key
tenga acceso al bucket (Read+Write).

### 7.5.3. Primera corrida — dry run primero

```bash
# Simula sin subir nada (ver qué archivos transferiría)
docker exec -it $CONT python manage.py backup_adjuntos_b2 --dry-run

# Si el output se ve sano, corre el sync real
docker exec -it $CONT python manage.py backup_adjuntos_b2
```

La primera vez sube todo (~varios GB según volumen). Las siguientes solo
los cambios. Tiempo estimado: ~1 min por GB con `--bwlimit 10M`.

### 7.5.4. Activar cron nocturno

Ver §11.5 abajo — agregá la línea `30 3 * * * ...` al crontab del host.

### 7.5.5. Soft-delete + retención

El comando usa `rclone sync --backup-dir`. Archivos borrados localmente
**no** se borran del bucket — se mueven a `adjuntos-archive/YYYYMMDD/`.

Recomendado en Backblaze: activar **Lifecycle Rules** en el bucket para
borrar los archive viejos automáticamente:
- "Keep prior versions for N days" → 30 días, por ejemplo.
- Y activar **Object Lock** una vez estabilizado (anti-ransomware).

---

## 8. Backup automático (recomendado, 5 min)

El archivo crítico es `/app/data/db.sqlite3` + `/app/data/adjuntos/`.

### 8.1. Snapshot periódico simple
```bash
sudo nano /usr/local/bin/backup-pietra.sh
```
Contenido:
```bash
#!/bin/bash
set -e
BACKUP_DIR=/var/backups/pietra
mkdir -p "$BACKUP_DIR"
DATE=$(date +%Y%m%d-%H%M)
docker exec pietramonte_archivo sqlite3 /app/data/db.sqlite3 ".backup /app/data/backup-$DATE.sqlite3"
tar -czf "$BACKUP_DIR/pietra-$DATE.tar.gz" \
    -C /opt/coolify/<.../data> \
    db.sqlite3 adjuntos/
# Conserva últimos 14 días
find "$BACKUP_DIR" -name "pietra-*.tar.gz" -mtime +14 -delete
```

```bash
sudo chmod +x /usr/local/bin/backup-pietra.sh
sudo crontab -e
# añadir:
0 3 * * * /usr/local/bin/backup-pietra.sh > /var/log/backup-pietra.log 2>&1
```

### 8.2. Offsite (opcional pero recomendado)
Sincroniza `/var/backups/pietra` a Backblaze B2 o S3 con `rclone`. Costo aprox $0.005/GB/mes.

---

## 9. Operación diaria — comandos útiles

```bash
# Ver logs del contenedor
docker logs -f pietramonte_archivo

# Reiniciar
docker compose -f /opt/coolify/.../docker-compose.yml restart

# Crear nuevo usuario portal
docker exec -it pietramonte_archivo python manage.py crear_usuario nuevo@gmail.com

# Importar nuevo .mbox
docker exec -it pietramonte_archivo python manage.py import_mbox correo@pietramonte.cl --archivo=/app/data/mbox/archivo

# Cantidad de correos por buzón
docker exec pietramonte_archivo python manage.py shell -c "from correos.models import Buzon; [print(b.email, b.correos.count()) for b in Buzon.objects.all()]"
```

---

## 10. Cuando agregues otros proyectos (clearentry, portafolio)

Tu CPX21 tiene 4 GB. Hoy este proyecto consume ~700 MB. Quedan ~3 GB para el resto.

- Para **portafolio** y **clearentry**: cada uno ~200-300 MB. Caben holgados.
- Para **Mailcow**: NO cabe sin upgrade a CPX31. Ver `memory/decision_mailcow.md`.

Cada nuevo proyecto repite el flujo:
1. Subir repo a GitHub
2. New Resource en Coolify → Dockerfile → Domain
3. Tunnel rule en `/etc/cloudflared/config.yml`
4. Restart `cloudflared`

---

## 11. Troubleshooting rápido

| Síntoma | Causa probable | Fix |
|---|---|---|
| 502 desde Cloudflare | Coolify no está corriendo o Tunnel mal configurado | `docker ps`, `journalctl -u cloudflared` |
| 400 Bad Request "DisallowedHost" | `archivo.pietramonte.cl` no está en `ALLOWED_HOSTS` | Edita `.env` → redeploy |
| 500 al cargar `/intranet/` | `SECRET_KEY` mal formado o falta | Genera uno nuevo y redeploy |
| Static no cargan (404 en CSS) | `collectstatic` no corrió | Rebuild en Coolify |
| Login no acepta nadie | El usuario no existe o está marcado inactivo en `UsuarioPortal` | Crea/activa desde `/admin-…/correos/usuarioportal/` |
| Adjuntos 404 | El volumen `/app/data` no se montó | `docker inspect` y revisa `Mounts` |

---

## 11.5 Cron del taller + backups (Hetzner host)

Los jobs corren en el host (NO dentro del container, así sobreviven al
rebuild). Editá el crontab con `crontab -e`:

```cron
# Reminders 24h/1h + cleanup de pendientes vencidas. Cada 5 min.
*/5 * * * * docker exec $(docker ps --format '{{.Names}}' | grep o1rd | head -1) python manage.py enviar_recordatorios >> /var/log/pietramonte-recordatorios.log 2>&1

# Carga feriados oficiales del año actual + siguiente. 1ro de enero, 4 AM.
0 4 1 1 * docker exec $(docker ps --format '{{.Names}}' | grep o1rd | head -1) python manage.py cargar_feriados >> /var/log/pietramonte-feriados.log 2>&1

# Backup nocturno de adjuntos a Backblaze B2. 3:30 AM (después del pg_dump de Coolify).
30 3 * * * docker exec $(docker ps --format '{{.Names}}' | grep o1rd | head -1) python manage.py backup_adjuntos_b2 >> /var/log/pietramonte-backup-adjuntos.log 2>&1
```

Setup inicial (después del primer deploy con la app `taller` activa):

```bash
ssh dongo
docker exec $(docker ps --format '{{.Names}}' | grep o1rd | head -1) python manage.py setup_grupos_taller
docker exec $(docker ps --format '{{.Names}}' | grep o1rd | head -1) python manage.py cargar_catalogo_inicial
docker exec $(docker ps --format '{{.Names}}' | grep o1rd | head -1) python manage.py cargar_feriados
```

Verificación del cron a la hora siguiente de configurarlo:

```bash
tail -20 /var/log/pietramonte-recordatorios.log
```


---

## 12. Lo que sigue (cuando quieras seguir mejorando)

- **Cloudflare Access frente al admin**: Zero Trust → Access Application → ruta `/admin-pm-*`. Solo emails específicos pasan, segunda capa de auth.
- **Sincronización Gmail (Fase 3)**: cron systemd cada 5 min vía Gmail API + OAuth para `soporte.dongo@gmail.com`.
- **Monitoreo externo**: UptimeRobot o BetterStack pingando `/healthz` cada 5 min, gratis hasta 50 monitores.
