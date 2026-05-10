# OMNIME — Coste y optimización

## Estimación realista por uso

Asumiendo Sonnet 4.6 ($3/M input + $15/M output) y Haiku 4.5 ($1/M input + $5/M output)
con prompt caching activo:

| Caso | LLM calls por mensaje | Coste/mensaje aprox |
|---|---|---|
| Saludo trivial ("hola", "gracias") | 1× Sonnet | ~$0.005 |
| Pregunta normal con QUERY | 1× Haiku (intent) + 1× Sonnet (response) | ~$0.012 |
| STORE (info nueva) | 1× Haiku (intent) + 1× Haiku (extract) + 1× Sonnet (confirm) | ~$0.014 |
| `/cv` o tarea compleja | 1× Haiku + 1× Opus | ~$0.05–0.10 |
| `/onboard` (13 preguntas) | ~26× LLM calls | ~$0.20–0.40 |

**Uso típico de un usuario solo (asistente personal, ~20 mensajes/día):**
- Mes "normal": **€2-5**
- Mes con muchos `/cv` y `/prep`: **€5-10**

Con prompt caching, los mensajes en una conversación seguida cuestan 50-90%
menos que el primero.

## Las 3 capas de modelo

OMNIME enruta cada operación al tier más barato que mantenga calidad:

| Tier | Modelo | Para qué | Pricing input |
|---|---|---|---|
| `tiny` | Haiku 4.5 | Intent classification, extractor, sentiment, doc classifier | $1/M |
| `fast` | Sonnet 4.6 | Chat, query, streaming replies | $3/M |
| `powerful` | Opus 4.7 | CV generation, weekly review, code review, planning | $15/M |

Configurable via env:

```bash
LLM_MODEL_FAST=claude-sonnet-4-6
LLM_MODEL_POWERFUL=claude-opus-4-7
LLM_MODEL_TINY=claude-haiku-4-5-20251001
```

## Optimizaciones activadas por defecto

### 1. Trivial-chat fast-path
Mensajes como `hola`, `gracias`, `vale`, `ok`, emojis solos → directos a CHAT
**sin llamada al clasificador**.

### 2. Slash-command fast-path
`/cv`, `/email`, `/briefing`, `/review`, etc. → routing por regex,
**sin llamada al clasificador**.

### 3. Prompt caching
El system prompt + living profile + tool definitions se marcan con
`cache_control: ephemeral`. Cache hits cuestan **0.1× input** (Anthropic docs).

Verifica con `/usage` que `cache_read_tokens > 0`.

### 4. Haiku para tareas baratas
Intent classification, extracción de entidades, sentiment scoring y document
classification van por Haiku. ~10× más barato que Sonnet.

## Cómo medir tu coste real

Desde Telegram:
```
/usage
```

Te devuelve:
```
Token usage
calls: 47
input: 102348
output: 4521
cache write: 6240
cache read: 38912
est. cost: $0.0782
```

Recuerda que el contador se reinicia con cada restart del contenedor (no
persiste a Postgres todavía — es un TODO).

## Si el coste se dispara

Diagnóstico rápido:

1. **Verifica caching**: `/usage` debería mostrar `cache_read_tokens` aumentando
   con conversaciones seguidas. Si es 0, algo va mal.

2. **Revisa el living profile**: si es enorme (>5K tokens), incluso con cache
   estás pagando ~50 tokens cada turno por cache write/read. Podrías:
   - Truncar más en `summarizer.update_living_profile`
   - O bajarlo manualmente: `/forget memory <id>` para entradas irrelevantes

3. **Demasiadas STOREs**: cada una dispara 3 llamadas LLM (intent + extractor +
   confirm). Si cuentas todo el día en una sola sentada de 20 minutos, se
   acumulan ~60 llamadas.

4. **`/plan`, `/cv_for`, `/prep`** usan Opus. Si los corres muy seguido,
   considera limitarte.

## Apagar features caros

```bash
# .env
ENABLE_PROMPT_CACHING=false   # solo si Anthropic SDK falla con cache_control
ENABLE_STREAMING=false         # ahorra muy poco; principal valor es UX
```

## Alternativas radicales

### Ollama local como provider principal
```bash
LLM_PROVIDER=ollama
OLLAMA_HOST=http://host.docker.internal:11434
OLLAMA_MODEL=llama3.1:8b
```
**Coste: €0/mes**, calidad menor, latencia 5-15s.

### Groq como fallback (Llama 3.3 70B a $0.59/M)
```bash
LLM_FALLBACK_PROVIDER=openai
LLM_FALLBACK_MODEL=llama-3.3-70b-versatile
OPENAI_API_KEY=gsk_...   # Groq key
# Necesitarías añadir OPENAI_BASE_URL=https://api.groq.com/openai/v1 en el código
```

(Esto último requiere un patch pequeño al `_call_openai` para honrar `base_url`.
Pídelo si lo necesitas.)
