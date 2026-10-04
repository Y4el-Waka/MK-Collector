# MK-Collector v0.2

Collector pequeño y portable para visualizar telemetría de interfaces de un MikroTik RouterOS 7 en tiempo real y conservar un histórico local corto. Flask habla con RouterOS; el navegador sólo habla con Flask.

## Arquitectura

```text
MikroTik RouterOS 7
        │ REST API (monitor, read only)
        │ sobre red privada / WireGuard
        ▼
MK-Collector (Python + Flask)
        ├── worker tráfico: ~1 s
        ├── worker DDM: ~5 s
        ├── estado live: últimos 60 intentos
        └── SQLite: histórico raw y retención automática
                │
                ▼
        Dashboard web / Chart.js
```

El collector consulta únicamente:

- `interface/monitor-traffic` para `ether1` y `sfp-sfpplus1`.
- `interface/ethernet/monitor` para DDM de `sfp-sfpplus1`.

Aunque RouterOS implementa estos comandos de monitor mediante `POST` en REST, son operaciones de lectura. El cliente incluye una allowlist y no expone endpoints para escribir o modificar el router.

## Métricas

- RX/TX bits por segundo y packets por segundo.
- FastPath RX/TX.
- Errores, drops y queue drops.
- Link, rate, full duplex, temperatura, voltaje, TX bias, RX/TX power, vendor, modelo y wavelength cuando RouterOS los entrega.
- Estado online/offline, última muestra y latencia REST.
- Current, min, max/peak y average por interfaz y ventana.

Los valores ausentes se muestran como `N/A`. Una consulta fallida crea un gap visual y **no** inserta ceros artificiales en SQLite.

## Requisitos e instalación

- Python 3.10 o superior.
- Acceso a RouterOS REST API por una red privada. WireGuard es el transporte recomendado.

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
```

Edita `.env`:

```dotenv
MIKROTIK_URL=http://192.168.250.2
MIKROTIK_USER=collector
MIKROTIK_PASS=tu_password

TRAFFIC_INTERVAL=1
DDM_INTERVAL=5
HISTORY_RETENTION_HOURS=3
```

Opcionales:

```dotenv
ROUTEROS_TIMEOUT=4
DEVICE_NAME=DP-PRUEBAS
# DATABASE_PATH=/ruta/absoluta/mk_collector.sqlite3
```

`HISTORY_RETENTION_HOURS` debe ser al menos `2`. La base predeterminada es `mk_collector.sqlite3` junto a la aplicación. `.env` y los archivos SQLite están ignorados por Git.

## Ejecución

Con WireGuard activo y `.env` configurado:

```bash
source .venv/bin/activate
python app.py
```

Abre [http://127.0.0.1:5000](http://127.0.0.1:5000).

El proceso inicia dos workers. Ante timeouts, errores HTTP o respuestas inválidas, permanece vivo y vuelve a consultar en el siguiente intervalo. `Ctrl+C` activa el cierre de workers; cada worker usa su propia sesión HTTP y SQLite abre una conexión por operación.

## Ventanas del dashboard

- **LIVE 60s:** usa el buffer en memoria y se actualiza cada segundo.
- **20 MIN:** SQLite, buckets de 3 segundos.
- **1 HORA:** SQLite, buckets de 5 segundos.
- **2 HORAS:** SQLite, buckets de 10 segundos.

El downsampling devuelve el promedio de cada bucket junto con min/max. Las estadísticas y los marcadores de peak se calculan sobre muestras raw, por lo que el máximo real de la ventana no desaparece. Cambiar de ventana no recarga la página.

La leyenda de Chart.js permite activar o desactivar cada serie. El eje usa bps internamente y presenta Kbps/Mbps/Gbps según la escala. Chart.js 4.5.1 y su licencia MIT se incluyen en `static/vendor/` para operación sin Internet.

## API

### `GET /api/state`

Estado live, último dato válido, DDM, latencia, buffer de 60 segundos y estadísticas live. Conserva las claves `ether1`, `sfp` y `ddm` del MVP.

### `GET /api/history?window=20m`

Ventanas válidas: `20m`, `1h`, `2h`. Devuelve, por interfaz:

- puntos downsampled con `rx_bps`, `tx_bps` y min/max por bucket;
- `sample_count` raw;
- estadísticas exactas `current_bps`, `min_bps`, `max_bps`, `avg_bps` y `max_at` para RX/TX.

Una ventana inválida devuelve HTTP 400. Las respuestas API llevan `Cache-Control: no-store`.

## Persistencia y retención

SQLite contiene:

- `traffic_samples`: una fila por interfaz y muestra válida.
- `ddm_samples`: una fila por muestra DDM válida.

El cleanup se ejecuta al iniciar la recolección y después aproximadamente cada cinco minutos. La retención predeterminada es tres horas y nunca crece indefinidamente con la configuración recomendada. Reiniciar Flask o cerrar el navegador no elimina el histórico todavía dentro de la ventana de retención.

## Seguridad

- Nunca hardcodees credenciales; usa `.env`.
- `.env` no debe versionarse.
- Las credenciales no se envían al frontend ni se retornan en APIs.
- No se registran passwords en logs.
- No existen endpoints de escritura a RouterOS.
- La sesión HTTP se limita a los dos endpoints monitor de solo lectura.

> **Advertencia:** usa HTTP únicamente dentro de una red privada confiable, idealmente sobre WireGuard. No expongas RouterOS REST HTTP ni este dashboard directamente a Internet. Para redes no privadas, configura TLS de extremo a extremo antes de una prueba productiva.

## Bandwidth Test

Bandwidth Test puede utilizarse externamente para generar tráfico de laboratorio, pero **no forma parte de MK-Collector**. El collector no lo inicia, administra ni consulta. Todas las métricas provienen de las interfaces mediante RouterOS REST.

## Pruebas

No se requieren dependencias de test adicionales:

```bash
python -m unittest discover -s tests -v
```

Las pruebas cubren normalización RouterOS, conversiones, faltantes, timeouts REST, allowlist read-only, gaps, SQLite insert/query/cleanup, downsampling, peaks, estadísticas y ventanas API.

## Estructura

```text
app.py                  Flask, endpoints y lifecycle
collector.py            normalización, cliente RouterOS, estado y workers
config.py               configuración .env
database.py             SQLite, retención, estadísticas y downsampling
templates/dashboard.html
static/dashboard.css
static/dashboard.js
static/vendor/           Chart.js y licencia
tests/
```

El modelo conserva `device` e `interface` en cada fila para facilitar una evolución posterior hacia múltiples routers o collectors sin implementar todavía una plataforma multiusuario o de largo plazo.

