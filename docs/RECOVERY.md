# OMNIME — Recovery & disaster procedures

## TL;DR del riesgo

OMNIME guarda toda tu data en **dos volúmenes Docker** del VPS:

- `postgres_data` — datos estructurados (perfil, proyectos, jobs, decisiones, etc.)
- `chroma_data` — vectores semánticos
- Más bind-mount `./data/uploads/` con los archivos que has subido

**Si el VPS muere y NO tienes backups off-site, pierdes todo.**

Por eso el sistema corre un backup automático diario a las 03:00 y, si configuras
`BACKUP_REMOTE`, lo sube a un destino externo. Sin eso, los backups viven en el
mismo VPS — útiles solo para recovery local.

## Cómo verificar que tienes backups

### Backups locales

```bash
docker compose exec omnime ls -la /app/data/backups/
# Deberías ver omnime_backup_YYYYMMDD-HHMMSS.tar.gz, hasta 14 ficheros
```

### Backup off-site (S3 / R2 / B2)

```bash
aws s3 ls s3://tu-bucket/omnime/ --endpoint-url=https://...r2.cloudflarestorage.com
```

### Disparar uno manual ahora mismo

Desde Telegram:
```
/backup
```

O desde la línea de comandos del VPS:
```bash
docker compose exec omnime python -m scripts.backup
```

## Anatomía de un backup

`omnime_backup_<timestamp>.tar.gz` contiene:

```
postgres.dump      ← pg_dump completo, formato authoritative
structured.json    ← todas las tablas serializadas en JSON (lectura humana)
uploads/           ← PDFs, fotos, audios subidos por el usuario
```

## Recuperación total — VPS nuevo desde cero

Asumiendo que perdiste el VPS y solo tienes el archivo `tar.gz`:

```bash
# 1. Servidor nuevo, instala Docker
sudo apt update && sudo apt install -y docker.io docker-compose-v2

# 2. Clona el repo
git clone https://github.com/<tu_user>/omnime.git
cd omnime

# 3. Restaura el .env (recuerda guardar una copia segura del original)
cp .env.example .env
nano .env   # rellenar con tus tokens y keys

# 4. Levanta solo postgres + chromadb (todavía sin el bot)
docker compose up -d postgres chromadb
sleep 10  # espera a que postgres acepte conexiones

# 5. Descarga el último backup
#    Si lo tenías en R2/S3:
aws s3 cp s3://omnime-backups/omnime/omnime_backup_<latest>.tar.gz . \
    --endpoint-url=https://<tu-cuenta>.r2.cloudflarestorage.com

#    Si lo tenías en otro Mac por SCP:
scp tu-mac:/Users/tu/omnime-backups/omnime_backup_<latest>.tar.gz .

# 6. Extrae
tar xzf omnime_backup_*.tar.gz

# 7. Restaura postgres con el dump nativo (lo más fiable)
docker compose exec -T postgres psql -U omnime -d omnime < postgres.dump

# 8. Restaura los uploads
mkdir -p data/uploads
cp -r uploads/* data/uploads/

# 9. Levanta el bot
docker compose up -d omnime
docker compose logs -f omnime
```

Verifica desde Telegram:
```
/me        ← debería mostrar tu perfil completo
/projects  ← lista de proyectos
/usage     ← cost tracker (se reinicia con cada arranque, no se persiste)
```

Tiempo total típico: **5-10 minutos**.

## Recuperación parcial — solo una entidad

Si borraste accidentalmente un proyecto o contacto y quieres recuperarlo SIN
restaurar todo:

```bash
# Saca el JSON del último backup
docker compose exec omnime tar xzf /app/data/backups/omnime_backup_*.tar.gz -C /tmp structured.json
docker compose exec omnime cat /tmp/structured.json | jq '.projects[] | select(.name | contains("ATLAS"))'
```

Y entonces puedes:
- Volver a contar al bot la información en lenguaje natural (lo guardará otra vez)
- O hacer un `INSERT` SQL manual con los datos del JSON

## Rebuild de la memoria semántica

Si solo se corrompió ChromaDB (no Postgres), no hace falta restaurar:

```bash
# Borra la colección
docker compose exec omnime python -c "
from src.memory.semantic import SemanticStore
s = SemanticStore()
for c in ('conversations', 'knowledge', 'documents'):
    try: s._client.delete_collection(c)
    except: pass
"

# Re-indexa todo desde Postgres (rebuild script — necesitarías escribirlo)
# La versión actual no tiene un script de re-indexado automático.
# Workaround: las conversaciones futuras irán llenando la memoria semántica de nuevo.
```

## Test de fuego — verifica tu plan

Una vez al mes, **prueba el restore** en otro entorno (local, otra VM):

```bash
# En un dir separado, monta una stack vacía
mkdir omnime-restore-test && cd $_
git clone <tu-repo> .
cp /path/to/your/.env .  # o uno de prueba

docker compose up -d postgres chromadb
sleep 5

# Descarga el backup más reciente
aws s3 cp s3://omnime-backups/omnime/omnime_backup_<latest>.tar.gz .
tar xzf omnime_backup_*.tar.gz

# Restaura
docker compose exec -T postgres psql -U omnime -d omnime < postgres.dump

# Comprueba
docker compose exec postgres psql -U omnime -d omnime -c "SELECT count(*) FROM projects;"
docker compose exec postgres psql -U omnime -d omnime -c "SELECT name FROM user_profile;"

# Limpia
docker compose down -v
```

Si esto falla, **tu backup no sirve** — corrige antes de necesitarlo en serio.

## Notas

- **`ANTHROPIC_API_KEY` y demás secretos del `.env` NO están en el backup.**
  Guárdalos aparte (1Password, Bitwarden, archivo cifrado en otro disco).
  Sin la key no puedes operar el bot, pero la data sí se restaura.
- **El `ENCRYPTION_KEY` ES crítico**: encripta `email` y `phone` en `contacts`.
  Si lo pierdes, esos campos quedan ilegibles incluso con el backup.
- **El `TELEGRAM_BOT_TOKEN`** lo puedes regenerar desde @BotFather si lo pierdes.
