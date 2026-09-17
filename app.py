import os
import json
import sqlite3
import urllib.request
from datetime import datetime
from flask import Flask, render_template, request, jsonify

app = Flask(__name__)
DB_NAME = "gilber_burger.db"

def get_db_connection():
    """Crea y retorna una conexión a la base de datos SQLite."""
    conn = sqlite3.connect(DB_NAME)
    conn.row_factory = sqlite3.Row
    return conn

def init_db():
    """Inicializa las tablas necesarias si no existen."""
    with get_db_connection() as conn:
        cursor = conn.cursor()
        
        # 1. Tabla de Productos del Menú
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS productos (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                nombre TEXT NOT NULL,
                precio REAL NOT NULL
            )
        """)
        
        # 2. Tabla de Ventas / Pagos / Créditos
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS ventas (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                fecha TEXT NOT NULL,
                cliente TEXT NOT NULL,
                detalle TEXT NOT NULL,
                total_usd REAL NOT NULL,
                total_bs REAL NOT NULL,
                metodo TEXT NOT NULL,
                estado TEXT NOT NULL
            )
        """)
        
        # Insertar productos iniciales si la tabla está vacía
        cursor.execute("SELECT COUNT(*) FROM productos")
        if cursor.fetchone()[0] == 0:
            cursor.executemany("INSERT INTO productos (nombre, precio) VALUES (?, ?)", [
                ("Hamburguesa Clásica", 3.5),
                ("Hamburguesa Especial", 5.0),
                ("Perro Caliente Sencillo", 1.5),
                ("Perro Jumbo", 2.5),
                ("Refresco", 1.0)
            ])
        conn.commit()

# Ejecutar inicialización de la base de datos al arrancar
init_db()

# --- RUTAS DE LA APLICACIÓN ---

@app.route("/")
def home():
    """Renderiza la interfaz gráfica principal."""
    return render_template("index.html")

@app.route("/api/tasa-bcv", methods=["GET"])
def get_tasa_bcv():
    """Consulta la tasa oficial del BCV vía API pública con fallback de respaldo."""
    try:
        url = "https://rates.dolarvzla.com/bcv/current.json"
        req = urllib.request.Request(
            url, 
            headers={'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64)'}
        )
        with urllib.request.urlopen(req, timeout=4) as response:
            data = json.loads(response.read().decode('utf-8'))
            tasa = float(data['current']['usd'])
            return jsonify({"tasa": tasa, "origen": "api"})
    except Exception as e:
        # Tasa de respaldo por si falla la conexión externa
        return jsonify({"tasa": 845.00, "origen": "fallback", "error": str(e)})

# --- MÓDULO DE PRODUCTOS ---

@app.route("/api/productos", methods=["GET", "POST"])
def manage_productos():
    with get_db_connection() as conn:
        cursor = conn.cursor()
        if request.method == "POST":
            data = request.get_json() or {}
            prod_id = data.get("id")
            nombre = data.get("nombre", "").strip()
            precio = float(data.get("precio", 0))

            if not nombre or precio <= 0:
                return jsonify({"error": "Nombre y precio válidos son requeridos"}), 400

            if prod_id:
                cursor.execute(
                    "UPDATE productos SET nombre = ?, precio = ? WHERE id = ?",
                    (nombre, precio, prod_id)
                )
            else:
                cursor.execute(
                    "INSERT INTO productos (nombre, precio) VALUES (?, ?)",
                    (nombre, precio)
                )
            conn.commit()
            return jsonify({"status": "ok"})
        else:
            cursor.execute("SELECT id, nombre, precio FROM productos ORDER BY nombre ASC")
            rows = cursor.fetchall()
            productos = [{"id": r["id"], "nombre": r["nombre"], "precio": r["precio"]} for r in rows]
            return jsonify(productos)

@app.route("/api/productos/<int:prod_id>", methods=["DELETE"])
def delete_producto(prod_id):
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("DELETE FROM productos WHERE id = ?", (prod_id,))
        conn.commit()
    return jsonify({"status": "ok"})

# --- MÓDULO DE VENTAS Y COBROS ---

@app.route("/api/ventas", methods=["GET", "POST"])
def manage_ventas():
    with get_db_connection() as conn:
        cursor = conn.cursor()
        if request.method == "POST":
            d = request.get_json() or {}
            fecha_actual = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            
            cursor.execute("""
                INSERT INTO ventas (fecha, cliente, detalle, total_usd, total_bs, metodo, estado)
                VALUES (?, ?, ?, ?, ?, ?, ?)
            """, (
                fecha_actual,
                d.get("cliente", "Consumidor Final"),
                d.get("detalle", ""),
                float(d.get("total_usd", 0.0)),
                float(d.get("total_bs", 0.0)),
                d.get("metodo", "Efectivo USD"),
                d.get("estado", "PAGADO")
            ))
            conn.commit()
            return jsonify({"status": "ok"})
        else:
            cursor.execute("""
                SELECT id, fecha, cliente, detalle, total_usd, total_bs, metodo, estado 
                FROM ventas 
                ORDER BY id DESC
            """)
            rows = cursor.fetchall()
            ventas = [{
                "id": r["id"],
                "fecha": r["fecha"],
                "cliente": r["cliente"],
                "detalle": r["detalle"],
                "total_usd": r["total_usd"],
                "total_bs": r["total_bs"],
                "metodo": r["metodo"],
                "estado": r["estado"]
            } for r in rows]
            return jsonify(ventas)

@app.route("/api/ventas/<int:venta_id>/pagar", methods=["POST"])
def pagar_credito(venta_id):
    """Marca un crédito o fiado como pagado asignándole su método de pago final."""
    data = request.get_json() or {}
    metodo = data.get("metodo", "Pago Móvil")
    
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("""
            UPDATE ventas 
            SET estado = 'PAGADO', metodo = ? 
            WHERE id = ?
        """, (f"{metodo} (Saldado)", venta_id))
        conn.commit()
    return jsonify({"status": "ok"})

# --- MÓDULO DE TOTALES Y RESUMEN MENSUAL ---

@app.route("/api/resumen-mensual", methods=["GET"])
def resumen_mensual():
    # Parámetro mes en formato 'YYYY-MM' (por ejemplo: '2026-09')
    mes = request.args.get("mes")
    if not mes:
        mes = datetime.now().strftime("%Y-%m")

    with get_db_connection() as conn:
        cursor = conn.cursor()

        # 1. Total Cobrado en el mes
        cursor.execute("""
            SELECT COALESCE(SUM(total_usd), 0), COALESCE(SUM(total_bs), 0), COUNT(*)
            FROM ventas 
            WHERE strftime('%Y-%m', fecha) = ? AND estado = 'PAGADO'
        """, (mes,))
        row_cobrado = cursor.fetchone()
        cobrado_usd = row_cobrado[0]
        cobrado_bs = row_cobrado[1]
        ordenes_cobradas = row_cobrado[2]

        # 2. Total por Cobrar (Créditos pendientes del mes)
        cursor.execute("""
            SELECT COALESCE(SUM(total_usd), 0), COALESCE(SUM(total_bs), 0), COUNT(*)
            FROM ventas 
            WHERE strftime('%Y-%m', fecha) = ? AND estado = 'PENDIENTE'
        """, (mes,))
        row_credito = cursor.fetchone()
        credito_usd = row_credito[0]
        credito_bs = row_credito[1]
        ordenes_credito = row_credito[2]

        # 3. Desglose por método de pago de los cobros efectivos
        cursor.execute("""
            SELECT metodo, SUM(total_usd), SUM(total_bs), COUNT(*)
            FROM ventas 
            WHERE strftime('%Y-%m', fecha) = ? AND estado = 'PAGADO'
            GROUP BY metodo
        """, (mes,))
        desglose = [{
            "metodo": r[0],
            "total_usd": r[1],
            "total_bs": r[2],
            "cantidad": r[3]
        } for r in cursor.fetchall()]

        # 4. Lista detallada de todas las ventas del mes
        cursor.execute("""
            SELECT id, fecha, cliente, detalle, total_usd, total_bs, metodo, estado 
            FROM ventas 
            WHERE strftime('%Y-%m', fecha) = ? 
            ORDER BY id DESC
        """, (mes,))
        ventas_mes = [{
            "id": r["id"],
            "fecha": r["fecha"],
            "cliente": r["cliente"],
            "detalle": r["detalle"],
            "total_usd": r["total_usd"],
            "total_bs": r["total_bs"],
            "metodo": r["metodo"],
            "estado": r["estado"]
        } for r in cursor.fetchall()]

        return jsonify({
            "mes": mes,
            "cobrado_usd": cobrado_usd,
            "cobrado_bs": cobrado_bs,
            "ordenes_cobradas": ordenes_cobradas,
            "credito_usd": credito_usd,
            "credito_bs": credito_bs,
            "ordenes_credito": ordenes_credito,
            "desglose": desglose,
            "ventas": ventas_mes
        })

# --- ENTORNO Y PUERTO ---
if __name__ == "__main__":
    # Toma el puerto de la variable de entorno asignada por Render o usa 5000 en local
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port, debug=False)