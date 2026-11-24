# Tareas semanales

Lista de tareas muy parecida a [delafuu/todo-list](https://github.com/delafuu/todo-list), con una diferencia:
**todas las tareas se desmarcan automáticamente cada domingo**.

Misma tecnología que el original: Flask + PostgreSQL + PWA (se puede instalar en el móvil), con modo oscuro
automático y despliegue en Render mediante Docker.

**En producción:** <https://tareas-semanales.onrender.com>

## Cómo funciona el reinicio semanal

No hay ningún cron ni tarea programada. El reinicio es **perezoso**: se comprueba en cada petición a la API.

1. Se calcula la "clave de semana": la fecha del último domingo a las 00:00 (en la zona horaria configurada).
2. Esa clave se guarda en la tabla `estado` (`ultimo_reinicio`).
3. Si la clave guardada es distinta de la actual, significa que ha empezado una semana nueva:
   se ejecuta `UPDATE tareas SET done = 0` y se actualiza la clave, todo en la misma transacción.

Ventajas de hacerlo así:

- Funciona aunque el servidor esté dormido (plan gratuito de Render) o apagado: al abrir la app se aplica.
- No depende de la zona horaria del servidor: usa `ZONA_HORARIA` (por defecto `Europe/Madrid`).
- Si dos peticiones llegan a la vez, `SELECT ... FOR UPDATE` evita que se reinicie dos veces.

En la interfaz aparece un pequeño texto con el día del reinicio, la fecha del próximo y un aviso
cuando la lista acaba de reiniciarse.

## Estructura

```
comun/                     Recursos estáticos compartidos (PWA)
  css/estilo.css
  img/icono.png
  manifest.json
fetch/
  Dockerfile
  src/
    app.py                 API Flask + lógica de reinicio semanal
    requirements.txt
    templates/index.html   Interfaz (JS sin dependencias)
docker-compose.yml         Entorno local (Postgres + app)
render.yaml                Blueprint de Render (servicio web)
```

## Probarlo en local

Con Docker (lo más fácil):

```bash
docker compose up --build
```

Y abre <http://localhost:7070>.

Sin Docker, con una base de datos PostgreSQL propia:

```bash
python -m venv .venv
# Windows: .venv\Scripts\activate    |    Linux/macOS: source .venv/bin/activate
pip install -r fetch/src/requirements.txt

# Windows PowerShell
$env:DATABASE_URL="postgresql://usuario:password@localhost:5432/tareas"
# Linux/macOS
export DATABASE_URL="postgresql://usuario:password@localhost:5432/tareas"

cd fetch/src
python app.py
```

Las tablas (`tareas` y `estado`) se crean solas al arrancar.

Para probar el reinicio sin esperar al domingo:

```bash
curl -X POST http://localhost:7070/api/reset
```

## Variables de entorno

| Variable       | Por defecto     | Descripción                                                        |
| -------------- | --------------- | ------------------------------------------------------------------ |
| `DATABASE_URL` | —               | Cadena de conexión de PostgreSQL. Sin ella la app arranca vacía.    |
| `DIA_REINICIO` | `6`             | Día del reinicio: `0` lunes … `6` domingo.                          |
| `ZONA_HORARIA` | `Europe/Madrid` | Zona horaria usada para decidir qué día es hoy.                     |

## API

| Método | Ruta                 | Descripción                                            |
| ------ | -------------------- | ------------------------------------------------------ |
| GET    | `/api/tasks`         | Lista de tareas (aplica el reinicio si toca).          |
| POST   | `/api/tasks`         | Crea una tarea. Form: `text`.                          |
| POST   | `/api/toggle_task`   | Marca/desmarca una tarea. Form: `id`.                  |
| POST   | `/api/edit_task`     | Edita el texto. Form: `id`, `text`.                    |
| POST   | `/api/delete_task`   | Borra una tarea. Form: `id`.                           |
| POST   | `/api/move_task`     | Cambia el orden. Form: `id`, `position`.               |
| GET    | `/api/estado`        | Día y fecha del próximo reinicio + último reinicio.    |
| POST   | `/api/reset`         | Desmarca todas las tareas ahora mismo (para pruebas).  |

## Desplegar en Render

Ya está desplegado con el Blueprint de `render.yaml` (servicio web Docker) y una base de datos **Neon**
externa. Para recrearlo desde cero:

1. En Render: **New → Blueprint** y selecciona el repo `delafuu/tareas-semanales`.
2. Render lee `render.yaml` y crea solo el servicio web.
3. En **Environment** del servicio, define `DATABASE_URL` con la cadena de conexión de Neon
   (`postgresql://usuario:password@host/neondb?sslmode=require`). No se guarda en el repo porque es público.
4. Si prefieres hacerlo a mano: **New → Web Service**, runtime **Docker**, Dockerfile `fetch/Dockerfile`,
   y añade `DATABASE_URL` igual que en el paso 3.

## Repositorio

El proyecto está publicado en <https://github.com/delafuu/tareas-semanales>.

```bash
git clone https://github.com/delafuu/tareas-semanales.git
cd tareas-semanales
```

Cualquier push a `main` redespliega automáticamente el servicio. Para cambiar el plan o las variables del
despliegue, edita `render.yaml` y Render lo aplicará en la siguiente sincronización del Blueprint.

La **región no se puede cambiar** en un servicio existente: hay que borrar el servicio y sincronizar el
Blueprint para que lo recree en la región nueva.

> El primer push necesitó autenticarse con `gh auth login` y `gh auth setup-git` para que Git usara las
> credenciales de GitHub CLI.

## Mantenimiento

- La base de datos está en **Neon** (plan gratuito, proyecto `tareas-semanales`, región **Frankfurt**,
  `aws-eu-central-1`). No caduca: la rama `production` tiene `Expires: Never` y los datos se conservan
  indefinidamente. El cómputo se suspende tras unos minutos sin uso y se reactiva solo en la siguiente
  consulta (menos de un segundo).
- El **servicio web gratuito de Render se duerme** tras unos minutos sin uso: la primera visita tarda
  ~1 minuto. El reinicio de los domingos no se ve afectado (se aplica en la siguiente visita).
- `DATABASE_URL` se configura en el panel de Render (**Environment**), nunca en el repositorio.

