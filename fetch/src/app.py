from flask import Flask, render_template, jsonify, request
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError
import uuid
import os
import psycopg2
import psycopg2.extras

BASE = os.path.dirname(os.path.abspath(__file__))
# En Docker los estáticos se copian junto a app.py; en local están en la raíz del repo
CARPETA_COMUN = next(
    (ruta for ruta in (os.path.join(BASE, 'comun'), os.path.join(BASE, '..', '..', 'comun'))
     if os.path.isdir(ruta)),
    os.path.join(BASE, 'comun')
)

app = Flask(__name__, static_folder=CARPETA_COMUN, static_url_path='/comun')

# Render inyectará aquí la URL de la base de datos
DATABASE_URL = os.environ.get('DATABASE_URL')

# --- CONFIGURACIÓN DEL REINICIO SEMANAL ---
# Día en el que se desmarcan todas las tareas (0 = lunes ... 6 = domingo)
DIA_REINICIO = int(os.environ.get('DIA_REINICIO', '6'))
NOMBRES_DIAS = ['lunes', 'martes', 'miércoles', 'jueves', 'viernes', 'sábado', 'domingo']

# Zona horaria con la que se decide qué día es "hoy"
ZONA_HORARIA = os.environ.get('ZONA_HORARIA', 'Europe/Madrid')
try:
    ZONA = ZoneInfo(ZONA_HORARIA)
except ZoneInfoNotFoundError:
    ZONA = timezone.utc
    ZONA_HORARIA = 'UTC'


def ahora():
    return datetime.now(ZONA)


def inicio_semana(momento=None):
    """Fecha del día de reinicio de la semana actual (clave de semana)."""
    momento = momento or ahora()
    dias_desde_reinicio = (momento.weekday() - DIA_REINICIO) % 7
    return (momento - timedelta(days=dias_desde_reinicio)).date()


def proximo_reinicio(momento=None):
    """Fecha del siguiente reinicio (si hoy toca, el de la semana que viene)."""
    momento = momento or ahora()
    dias = (DIA_REINICIO - momento.weekday()) % 7
    if dias == 0:
        dias = 7
    return (momento + timedelta(days=dias)).date()


def get_db_cursor(conn):
    return conn.cursor(cursor_factory=psycopg2.extras.DictCursor)


def conectar():
    return psycopg2.connect(DATABASE_URL)


def init_db():
    if not DATABASE_URL:
        return
    conn = conectar()
    cur = conn.cursor()
    # Creamos las tablas si es la primera vez que arranca
    cur.execute('''
        CREATE TABLE IF NOT EXISTS tareas (
            id TEXT PRIMARY KEY,
            text TEXT NOT NULL,
            done INTEGER DEFAULT 0,
            orden INTEGER DEFAULT 0
        )
    ''')
    # Pequeña tabla de estado donde guardamos cuándo se reinició por última vez
    cur.execute('''
        CREATE TABLE IF NOT EXISTS estado (
            clave TEXT PRIMARY KEY,
            valor TEXT NOT NULL
        )
    ''')
    conn.commit()
    cur.close()
    conn.close()


def reiniciar_si_toca(cur):
    """Desmarca todas las tareas si ha empezado una semana nueva.

    Devuelve True si en esta llamada se han desmarcado tareas.
    Se hace de forma perezosa (al recibir una petición) para no depender de
    ningún cron ni de que el servidor esté siempre encendido.
    """
    clave = inicio_semana().isoformat()

    # Nos aseguramos de que exista la fila para poder bloquearla
    cur.execute(
        "INSERT INTO estado (clave, valor) VALUES ('ultimo_reinicio', %s) "
        "ON CONFLICT (clave) DO NOTHING",
        (clave,)
    )
    # FOR UPDATE evita que dos peticiones simultáneas reinicien a la vez
    cur.execute("SELECT valor FROM estado WHERE clave = 'ultimo_reinicio' FOR UPDATE")
    fila = cur.fetchone()

    if fila and fila['valor'] == clave:
        return False

    cur.execute('UPDATE tareas SET done = 0 WHERE done <> 0')
    cur.execute(
        "UPDATE estado SET valor = %s WHERE clave = 'ultimo_reinicio'",
        (clave,)
    )
    return True


def sin_bd():
    return jsonify({"error": "Falta configurar DATABASE_URL"}), 503


if DATABASE_URL:
    init_db()


@app.route('/', methods=['GET'])
def index():
    return render_template('index.html')


@app.route('/api/tasks', methods=['GET'])
def get_tasks():
    if not DATABASE_URL:
        return jsonify([])
    conn = conectar()
    cur = get_db_cursor(conn)
    reiniciar_si_toca(cur)
    conn.commit()
    cur.execute('SELECT * FROM tareas ORDER BY orden')
    filas = cur.fetchall()
    cur.close()
    conn.close()
    return jsonify([{"id": r["id"], "text": r["text"], "done": bool(r["done"])} for r in filas])


@app.route('/api/estado', methods=['GET'])
def get_estado():
    """Información del reinicio semanal para mostrarla en la interfaz."""
    hoy = ahora().date()
    info = {
        "hoy": hoy.isoformat(),
        "ultimo_reinicio": None,
        "proximo_reinicio": proximo_reinicio().isoformat(),
        "dia_reinicio": NOMBRES_DIAS[DIA_REINICIO],
        "zona_horaria": ZONA_HORARIA,
    }
    if not DATABASE_URL:
        return jsonify(info)

    conn = conectar()
    cur = get_db_cursor(conn)
    reiniciar_si_toca(cur)
    conn.commit()
    cur.execute("SELECT valor FROM estado WHERE clave = 'ultimo_reinicio'")
    fila = cur.fetchone()
    cur.close()
    conn.close()

    if fila:
        info["ultimo_reinicio"] = fila["valor"]
    return jsonify(info)


@app.route('/api/tasks', methods=['POST'])
def create_task():
    if not DATABASE_URL:
        return sin_bd()

    texto = request.form.get('text')
    if not texto or texto.strip() == '':
        return jsonify({"error": "El texto es obligatorio"}), 400

    nuevo_id = uuid.uuid4().hex
    conn = conectar()
    cur = get_db_cursor(conn)
    reiniciar_si_toca(cur)
    cur.execute('SELECT MAX(orden) FROM tareas')
    max_orden = cur.fetchone()[0]
    max_orden = 0 if max_orden is None else max_orden + 1

    cur.execute('INSERT INTO tareas (id, text, done, orden) VALUES (%s, %s, 0, %s)',
                (nuevo_id, texto.strip(), max_orden))
    conn.commit()
    cur.close()
    conn.close()
    return jsonify({'id': nuevo_id, 'text': texto.strip(), 'done': False}), 201


@app.route('/api/toggle_task', methods=['POST'])
def toggle_task():
    if not DATABASE_URL:
        return sin_bd()

    task_id = request.form.get('id')
    conn = conectar()
    cur = get_db_cursor(conn)
    # Si la semana ha cambiado, primero se desmarca todo para no arrastrar
    # el estado "hecho" de la semana anterior.
    reiniciar_si_toca(cur)
    cur.execute('SELECT * FROM tareas WHERE id = %s', (task_id,))
    tarea = cur.fetchone()
    if not tarea:
        conn.rollback()
        cur.close()
        conn.close()
        return jsonify({"error": "Tarea no encontrada"}), 404

    nuevo_estado = 0 if tarea["done"] else 1
    cur.execute('UPDATE tareas SET done = %s WHERE id = %s', (nuevo_estado, task_id))
    conn.commit()
    cur.close()
    conn.close()
    return jsonify({"id": task_id, "text": tarea["text"], "done": bool(nuevo_estado)}), 200


@app.route('/api/delete_task', methods=['POST'])
def delete_task():
    if not DATABASE_URL:
        return sin_bd()

    task_id = request.form.get('id')
    conn = conectar()
    cur = get_db_cursor(conn)
    reiniciar_si_toca(cur)
    cur.execute('DELETE FROM tareas WHERE id = %s', (task_id,))
    conn.commit()
    cur.close()
    conn.close()
    return '', 204


@app.route('/api/move_task', methods=['POST'])
def move_task():
    if not DATABASE_URL:
        return sin_bd()

    task_id = request.form.get('id')
    position = int(request.form.get('position', -1))

    conn = conectar()
    cur = get_db_cursor(conn)
    reiniciar_si_toca(cur)
    cur.execute('SELECT * FROM tareas ORDER BY orden')
    filas = cur.fetchall()
    tareas = [{"id": r["id"], "text": r["text"], "done": bool(r["done"])} for r in filas]

    current_idx = next((i for i, t in enumerate(tareas) if t['id'] == task_id), -1)
    if current_idx != -1:
        tarea_a_mover = tareas.pop(current_idx)
        position = max(0, min(position, len(tareas)))
        tareas.insert(position, tarea_a_mover)
        for index, tarea in enumerate(tareas):
            cur.execute('UPDATE tareas SET orden = %s WHERE id = %s', (index, tarea["id"]))
    conn.commit()

    cur.close()
    conn.close()
    return jsonify(tareas), 200


@app.route('/api/edit_task', methods=['POST'])
def edit_task():
    if not DATABASE_URL:
        return sin_bd()

    task_id = request.form.get('id')
    new_text = request.form.get('text')

    if not new_text or new_text.strip() == '':
        return jsonify({"error": "El texto no puede estar vacío"}), 400

    conn = conectar()
    cur = get_db_cursor(conn)
    reiniciar_si_toca(cur)
    cur.execute('UPDATE tareas SET text = %s WHERE id = %s', (new_text.strip(), task_id))
    conn.commit()
    cur.close()
    conn.close()

    return jsonify({"id": task_id, "text": new_text.strip()}), 200


@app.route('/api/reset', methods=['POST'])
def reset_manual():
    """Desmarca todas las tareas ahora mismo (útil para probar)."""
    if not DATABASE_URL:
        return sin_bd()

    conn = conectar()
    cur = conn.cursor()
    cur.execute('UPDATE tareas SET done = 0 WHERE done <> 0')
    conn.commit()
    cur.close()
    conn.close()
    return jsonify({"ok": True}), 200


if __name__ == '__main__':
    app.run(host='0.0.0.0', port=7070)
