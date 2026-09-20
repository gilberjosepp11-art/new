import os
import json
import urllib.request
from datetime import datetime
import psycopg2
from psycopg2.extras import RealDictCursor
from flask import Flask, render_template, request, jsonify

app = Flask(__name__)

# Lee la base de datos de Neon o usa una de prueba
DATABASE_URL = os.environ.get("DATABASE_URL")

def get_db_connection():
    if not DATABASE_URL:
        raise ValueError("DATABASE_URL no está configurada.")
    # Manejar URLs que empiecen por postgres://
    url = DATABASE_URL.replace("postgres://", "postgresql://", 1)
    return psycopg2.connect(url, sslmode="require", cursor_factory=RealDictCursor)

def init_db():
    if not DATABASE_URL:
        return
    with get_db_connection() as conn:
        with conn.cursor() as cursor:
            # 1. Tabla de Productos
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS productos (
                    id SERIAL PRIMARY KEY,
                    nombre VARCHAR(100) NOT NULL,
                    precio NUMERIC(10, 2) NOT NULL
                );
            """)
            
            # 2. Tabla de Ventas / Pagos / Créditos
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS ventas (
                    id SERIAL PRIMARY KEY,
                    fecha TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    cliente VARCHAR(100) NOT NULL,
                    detalle TEXT NOT NULL,
                    total_usd NUMERIC(10, 2) NOT NULL,
                    total_bs NUMERIC(15, 2) NOT NULL,
                    metodo VARCHAR(50) NOT NULL,
                    estado VARCHAR(20) NOT NULL
                );
            """)
            
            # Insertar menú inicial si está vacía
            cursor.execute("SELECT COUNT(*) as count FROM productos;")
            if cursor.fetchone()["count"] == 0:
                cursor.executemany(
                    "INSERT INTO productos (nombre, precio) VALUES (%s, %s);",
                    [
                        ("Hamburguesa Clásica", 3.5),
                        ("Hamburguesa Especial", 5.0),
                        ("Perro Caliente Sencillo", 1.5),
                        ("Perro Jumbo", 2.5),
                        ("Refresco", 1.0)
                    ]
                )
        conn.commit()

try:
    init_db()
except Exception as e:
    print(f"Error inicializando BD: {e}")

@app.route("/")
def home():
    return render_template("index.html")

@app.route("/api/tasa-bcv", methods=["GET"])
def get_tasa_bcv():
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
        return jsonify({"tasa": 845.00, "origen": "fallback", "error": str(e)})

# --- PRODUCTOS ---

@app.route("/api/productos", methods=["GET", "POST"])
def manage_productos():
    with get_db_connection() as conn:
        with conn.cursor() as cursor:
            if request.method == "POST":
                data = request.get_json() or {}
                prod_id = data.get("id")
                nombre = data.get("nombre", "").strip()
                precio = float(data.get("precio", 0))

                if not nombre or precio <= 0:
                    return jsonify({"error": "Datos inválidos"}), 400

                if prod_id:
                    cursor.execute(
                        "UPDATE productos SET nombre = %s, precio = %s WHERE id = %s;",
                        (nombre, precio, prod_id)
                    )
                else:
                    cursor.execute(
                        "INSERT INTO productos (nombre, precio) VALUES (%s, %s);",
                        (nombre, precio)
                    )
                conn.commit()
                return jsonify({"status": "ok"})
            else:
                cursor.execute("SELECT id, nombre, CAST(precio AS FLOAT) as precio FROM productos ORDER BY nombre ASC;")
                return jsonify(cursor.fetchall())

@app.route("/api/productos/<int:prod_id>", methods=["DELETE"])
def delete_producto(prod_id):
    with get_db_connection() as conn:
        with conn.cursor() as cursor:
            cursor.execute("DELETE FROM productos WHERE id = %s;", (prod_id,))
        conn.commit()
    return jsonify({"status": "ok"})

# --- VENTAS ---

@app.route("/api/ventas", methods=["GET", "POST"])
def manage_ventas():
    with get_db_connection() as conn:
        with conn.cursor() as cursor:
            if request.method == "POST":
                d = request.get_json() or {}
                cursor.execute("""
                    INSERT INTO ventas (cliente, detalle, total_usd, total_bs, metodo, estado)
                    VALUES (%s, %s, %s, %s, %s, %s);
                """, (
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
                    SELECT id, TO_CHAR(fecha, 'YYYY-MM-DD HH24:MI:SS') as fecha,
                           cliente, detalle, CAST(total_usd AS FLOAT) as total_usd,
                           CAST(total_bs AS FLOAT) as total_bs, metodo, estado 
                    FROM ventas ORDER BY id DESC;
                """)
                return jsonify(cursor.fetchall())

@app.route("/api/ventas/<int:venta_id>/pagar", methods=["POST"])
def pagar_credito(venta_id):
    data = request.get_json() or {}
    metodo = data.get("metodo", "Pago Móvil")
    with get_db_connection() as conn:
        with conn.cursor() as cursor:
            cursor.execute("""
                UPDATE ventas 
                SET estado = 'PAGADO', metodo = %s 
                WHERE id = %s;
            """, (f"{metodo} (Saldado)", venta_id))
        conn.commit()
    return jsonify({"status": "ok"})

# --- TOTALES Y MES ---

@app.route("/api/resumen-mensual", methods=["GET"])
def resumen_mensual():
    mes = request.args.get("mes") or datetime.now().strftime("%Y-%m")

    with get_db_connection() as conn:
        with conn.cursor() as cursor:
            # 1. Total Cobrado
            cursor.execute("""
                SELECT COALESCE(SUM(total_usd), 0) as usd, COALESCE(SUM(total_bs), 0) as bs, COUNT(*) as cant
                FROM ventas 
                WHERE TO_CHAR(fecha, 'YYYY-MM') = %s AND estado = 'PAGADO';
            """, (mes,))
            c_row = cursor.fetchone()

            # 2. Total Créditos
            cursor.execute("""
                SELECT COALESCE(SUM(total_usd), 0) as usd, COALESCE(SUM(total_bs), 0) as bs, COUNT(*) as cant
                FROM ventas 
                WHERE TO_CHAR(fecha, 'YYYY-MM') = %s AND estado = 'PENDIENTE';
            """, (mes,))
            cr_row = cursor.fetchone()

            # 3. Desglose
            cursor.execute("""
                SELECT metodo, CAST(SUM(total_usd) AS FLOAT) as total_usd, 
                       CAST(SUM(total_bs) AS FLOAT) as total_bs, COUNT(*) as cantidad
                FROM ventas 
                WHERE TO_CHAR(fecha, 'YYYY-MM') = %s AND estado = 'PAGADO'
                GROUP BY metodo;
            """, (mes,))
            desglose = cursor.fetchall()

            # 4. Historial del mes
            cursor.execute("""
                SELECT id, TO_CHAR(fecha, 'YYYY-MM-DD HH24:MI:SS') as fecha,
                       cliente, detalle, CAST(total_usd AS FLOAT) as total_usd, 
                       CAST(total_bs AS FLOAT) as total_bs, metodo, estado 
                FROM ventas 
                WHERE TO_CHAR(fecha, 'YYYY-MM') = %s 
                ORDER BY id DESC;
            """, (mes,))
            ventas_mes = cursor.fetchall()

            return jsonify({
                "mes": mes,
                "cobrado_usd": float(c_row["usd"]),
                "cobrado_bs": float(c_row["bs"]),
                "ordenes_cobradas": c_row["cant"],
                "credito_usd": float(cr_row["usd"]),
                "credito_bs": float(cr_row["bs"]),
                "ordenes_credito": cr_row["cant"],
                "desglose": desglose,
                "ventas": ventas_mes
            })

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port, debug=False)
