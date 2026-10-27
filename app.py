"""Vape shop + repair workshop system. Python 3 stdlib only: python app.py -> http://localhost:8000"""
import json, os, re, secrets, sqlite3, datetime as dt
from urllib.parse import urlsplit, parse_qs
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

DB = os.environ.get("DB_PATH", "workshop.db")
MIN_AGE = int(os.environ.get("MIN_AGE", 21))  # legal age in your country
ADMIN_PASSWORD = os.environ.get("ADMIN_PASSWORD", "Bisho@Vap2026")  # override with the ADMIN_PASSWORD env var
CURRENCY = "L.E."
TOKENS = set()
STATUSES = ["received", "diagnosing", "repairing", "ready", "collected"]

SCHEMA = """
CREATE TABLE IF NOT EXISTS products(
  id INTEGER PRIMARY KEY, sku TEXT UNIQUE NOT NULL, name TEXT NOT NULL,
  category TEXT NOT NULL, price REAL NOT NULL CHECK(price>=0),
  stock INTEGER NOT NULL DEFAULT 0 CHECK(stock>=0), low_stock INTEGER NOT NULL DEFAULT 5,
  cost REAL NOT NULL DEFAULT 0, image TEXT DEFAULT '', is_new INTEGER NOT NULL DEFAULT 0);
CREATE TABLE IF NOT EXISTS customers(
  id INTEGER PRIMARY KEY, name TEXT NOT NULL, phone TEXT, dob TEXT NOT NULL,
  id_checked INTEGER NOT NULL DEFAULT 0, created_at TEXT DEFAULT CURRENT_TIMESTAMP);
CREATE TABLE IF NOT EXISTS sales(
  id INTEGER PRIMARY KEY, customer_id INTEGER NOT NULL REFERENCES customers(id),
  total REAL NOT NULL, created_at TEXT DEFAULT CURRENT_TIMESTAMP);
CREATE TABLE IF NOT EXISTS sale_items(
  id INTEGER PRIMARY KEY, sale_id INTEGER NOT NULL REFERENCES sales(id),
  product_id INTEGER NOT NULL REFERENCES products(id), qty INTEGER NOT NULL CHECK(qty>0),
  unit_price REAL NOT NULL, unit_cost REAL NOT NULL DEFAULT 0);
CREATE TABLE IF NOT EXISTS repairs(
  id INTEGER PRIMARY KEY, customer_id INTEGER NOT NULL REFERENCES customers(id),
  device TEXT NOT NULL, issue TEXT NOT NULL, status TEXT NOT NULL DEFAULT 'received',
  quote REAL DEFAULT 0, notes TEXT DEFAULT '', created_at TEXT DEFAULT CURRENT_TIMESTAMP,
  updated_at TEXT DEFAULT CURRENT_TIMESTAMP);
"""
SEED = [("DS-001", "Disposable 9000 puffs - Mixed Berry", "disposable", 450, 300, 20, 1), ("DS-002", "Disposable 12000 puffs - Watermelon Ice", "disposable", 550, 380, 15, 1),
        ("PD-101", "Pod kit 800mAh", "pod", 1200, 800, 8, 1), ("PD-102", "Pod cartridge 2ml (2 pack)", "pod", 250, 150, 25, 0),
        ("EL-001", "Nic salt 30ml - Mango Ice", "e-liquid", 350, 220, 24, 0), ("EL-002", "Freebase 60ml - Tobacco", "e-liquid", 500, 320, 3, 0),
        ("EG-001", "Egyptian salt 30ml - Apple Double", "egyptian e-liquid", 200, 120, 18, 1), ("CL-201", "Mesh coil 0.6 ohm (5 pack)", "coil", 300, 180, 30, 0),
        ("AC-301", "18650 battery 3000mAh", "accessory", 250, 150, 12, 0)]

def db():
    c = sqlite3.connect(DB); c.row_factory = sqlite3.Row
    c.execute("PRAGMA foreign_keys=ON"); return c

def init():
    with db() as c:
        c.executescript(SCHEMA)
        for t, col, ddl in [("products", "cost", "REAL NOT NULL DEFAULT 0"), ("products", "image", "TEXT DEFAULT ''"),
                            ("products", "is_new", "INTEGER NOT NULL DEFAULT 0"), ("sale_items", "unit_cost", "REAL NOT NULL DEFAULT 0")]:
            if col not in [r[1] for r in c.execute(f"PRAGMA table_info({t})")]: c.execute(f"ALTER TABLE {t} ADD COLUMN {col} {ddl}")
        if not c.execute("SELECT 1 FROM products").fetchone():
            c.executemany("INSERT INTO products(sku,name,category,price,cost,stock,is_new) VALUES(?,?,?,?,?,?,?)", SEED)

class Err(Exception):
    def __init__(s, msg, code=400): s.msg, s.code = msg, code

def age(dob):
    try: d = dt.date.fromisoformat(dob)
    except ValueError: raise Err("Date of birth must be YYYY-MM-DD")
    t = dt.date.today(); return t.year - d.year - ((t.month, t.day) < (d.month, d.day))

def rows(c, q, a=()): return [dict(r) for r in c.execute(q, a)]
def need(b, *keys):
    for k in keys:
        if b.get(k) in (None, ""): raise Err(f"Missing field: {k}")

def summary(c, b=None):
    today = dt.date.today().isoformat()
    return {"min_age": MIN_AGE, "currency": CURRENCY,
        "sales_today": c.execute("SELECT COALESCE(SUM(total),0) FROM sales WHERE date(created_at,'localtime')=?", (today,)).fetchone()[0],
        "open_repairs": c.execute("SELECT COUNT(*) FROM repairs WHERE status NOT IN ('collected')").fetchone()[0],
        "ready_repairs": c.execute("SELECT COUNT(*) FROM repairs WHERE status='ready'").fetchone()[0],
        "low_stock": rows(c, "SELECT id,name,stock,low_stock FROM products WHERE stock<=low_stock ORDER BY stock")}

def add_product(c, b):
    need(b, "sku", "name", "category", "price")
    try: cur = c.execute("INSERT INTO products(sku,name,category,price,cost,stock,low_stock,image,is_new) VALUES(?,?,?,?,?,?,?,?,?)",
        (b["sku"], b["name"], b["category"], float(b["price"]), float(b.get("cost") or 0), int(b.get("stock") or 0),
         int(b.get("low_stock") or 5), b.get("image") or "", 1 if b.get("is_new") else 0))
    except sqlite3.IntegrityError as e: raise Err(f"Could not save product: {e}")
    return {"id": cur.lastrowid}

def update_product(c, b, pid):
    """Change cost/price/name/image/new-flag and receive stock (add can be negative for corrections)."""
    n = int(b.get("add") or 0)
    sets, vals = ["stock=stock+?"], [n]
    for k, cast in (("name", str), ("price", float), ("cost", float), ("image", str), ("is_new", int)):
        if b.get(k) not in (None, ""): sets.append(f"{k}=?"); vals.append(cast(b[k]))
    try: ok = c.execute(f"UPDATE products SET {','.join(sets)} WHERE id=?", vals + [pid]).rowcount
    except sqlite3.IntegrityError: raise Err("Stock would go below zero")
    if not ok: raise Err("Product not found", 404)
    return {"ok": True}

def report(c, b):
    d1, d2 = b.get("from") or "0000-01-01", b.get("to") or "9999-12-31"
    items = rows(c, """SELECT p.id,p.name,p.category,p.cost,p.price,p.stock,
        COALESCE(SUM(i.qty),0) sold, COALESCE(SUM(i.qty*i.unit_price),0) revenue, COALESCE(SUM(i.qty*i.unit_cost),0) cogs
        FROM products p LEFT JOIN (SELECT i.* FROM sale_items i JOIN sales s ON s.id=i.sale_id
        WHERE date(s.created_at,'localtime') BETWEEN ? AND ?) i ON i.product_id=p.id GROUP BY p.id ORDER BY revenue DESC, p.name""", (d1, d2))
    cats, t = {}, {"sold": 0, "revenue": 0, "profit": 0, "stock_units": 0, "stock_value": 0}
    for r in items:
        r["profit"] = r["revenue"] - r["cogs"]
        x = cats.setdefault(r["category"], {"category": r["category"], "sold": 0, "revenue": 0, "profit": 0})
        for k in ("sold", "revenue", "profit"): x[k] += r[k]; t[k] += r[k]
        t["stock_units"] += r["stock"]; t["stock_value"] += r["stock"] * r["cost"]
    return {"items": items, "categories": list(cats.values()), "totals": t}

def dashboard(c, b):
    today = dt.date.today()
    days = [(today - dt.timedelta(days=i)).isoformat() for i in range(29, -1, -1)]
    by = {r["d"]: r for r in c.execute("""SELECT date(s.created_at,'localtime') d, SUM(i.qty*i.unit_price) rev, SUM(i.qty*i.unit_cost) cost
        FROM sales s JOIN sale_items i ON i.sale_id=s.id WHERE date(s.created_at,'localtime')>=? GROUP BY d""", (days[0],))}
    sales = [round(by[d]["rev"], 2) if d in by else 0 for d in days]
    cost = [round(by[d]["cost"], 2) if d in by else 0 for d in days]
    rev, orders = c.execute("SELECT COALESCE(SUM(total),0), COUNT(*) FROM sales").fetchone()
    cats = rows(c, """SELECT p.category, COALESCE(SUM(i.qty),0) units, COALESCE(SUM(i.qty*i.unit_price),0) revenue
        FROM products p LEFT JOIN sale_items i ON i.product_id=p.id GROUP BY p.category ORDER BY units DESC""")
    ev = [{"kind": "sale", "at": r["created_at"], "text": f"Sale #{r['id']}: {r['customer']} paid {r['total']:,.0f} {CURRENCY}"} for r in c.execute(
        "SELECT s.id,s.total,s.created_at,cu.name customer FROM sales s JOIN customers cu ON cu.id=s.customer_id ORDER BY s.id DESC LIMIT 8")]
    ev += [{"kind": "repair", "at": r["updated_at"], "text": f"Repair #{r['id']}: {r['customer']}'s {r['device']} is {r['status']}"} for r in c.execute(
        "SELECT r.id,r.device,r.status,r.updated_at,cu.name customer FROM repairs r JOIN customers cu ON cu.id=r.customer_id ORDER BY r.updated_at DESC LIMIT 8")]
    ev.sort(key=lambda e: e["at"], reverse=True)
    return {"days": days, "sales": sales, "cost": cost, "profit": [round(x - y, 2) for x, y in zip(sales, cost)],
            "totals": {"revenue": rev, "orders": orders, "avg": rev / orders if orders else 0}, "categories": cats, "activity": ev[:8]}

def login(c, b):
    if not secrets.compare_digest(str(b.get("password", "")), ADMIN_PASSWORD): raise Err("Wrong password", 401)
    tok = secrets.token_hex(16); TOKENS.add(tok); return {"token": tok}

def add_customer(c, b):
    need(b, "name", "dob")
    a = age(b["dob"])
    if a < MIN_AGE: raise Err(f"Customer is {a}. Minimum age is {MIN_AGE}; record not saved.", 403)
    cur = c.execute("INSERT INTO customers(name,phone,dob,id_checked) VALUES(?,?,?,?)",
                    (b["name"], b.get("phone", ""), b["dob"], 1 if b.get("id_checked") else 0))
    return {"id": cur.lastrowid}

def make_sale(c, b):
    need(b, "customer_id", "items")
    cu = c.execute("SELECT * FROM customers WHERE id=?", (b["customer_id"],)).fetchone()
    if not cu: raise Err("Customer not found", 404)
    if not cu["id_checked"]: raise Err("Check the customer's ID and tick 'ID checked' before selling", 403)
    if age(cu["dob"]) < MIN_AGE: raise Err("Customer is under the minimum age", 403)
    total, lines = 0, []
    for it in b["items"]:
        qty = int(it["qty"]); p = c.execute("SELECT * FROM products WHERE id=?", (it["product_id"],)).fetchone()
        if not p or qty < 1: raise Err("Invalid item")
        if p["stock"] < qty: raise Err(f"Only {p['stock']} left of {p['name']}", 409)
        total += p["price"] * qty; lines.append((p["id"], qty, p["price"], p["cost"]))
    sid = c.execute("INSERT INTO sales(customer_id,total) VALUES(?,?)", (cu["id"], round(total, 2))).lastrowid
    for pid, qty, price, cost in lines:
        c.execute("INSERT INTO sale_items(sale_id,product_id,qty,unit_price,unit_cost) VALUES(?,?,?,?,?)", (sid, pid, qty, price, cost))
        c.execute("UPDATE products SET stock=stock-? WHERE id=?", (qty, pid))
    return {"id": sid, "total": round(total, 2)}

def add_repair(c, b):
    need(b, "customer_id", "device", "issue")
    if not c.execute("SELECT 1 FROM customers WHERE id=?", (b["customer_id"],)).fetchone(): raise Err("Customer not found", 404)
    cur = c.execute("INSERT INTO repairs(customer_id,device,issue,quote) VALUES(?,?,?,?)",
                    (b["customer_id"], b["device"], b["issue"], float(b.get("quote") or 0)))
    return {"id": cur.lastrowid}

def update_repair(c, b, rid):
    if "status" in b and b["status"] not in STATUSES: raise Err("Unknown status")
    c.execute("""UPDATE repairs SET status=COALESCE(?,status), quote=COALESCE(?,quote), notes=COALESCE(?,notes),
                 updated_at=CURRENT_TIMESTAMP WHERE id=?""", (b.get("status"), b.get("quote"), b.get("notes"), rid))
    return {"ok": True}

# (method, path regex) -> handler
ROUTES = [
 ("GET", r"/api/summary", summary),
 ("GET", r"/api/products", lambda c, b: rows(c, "SELECT * FROM products ORDER BY category,name")),
 ("POST", r"/api/products", add_product),
 ("PUT", r"/api/products/(\d+)", lambda c, b, i: update_product(c, b, int(i))),
 ("GET", r"/api/report", report),
 ("GET", r"/api/dashboard", dashboard),
 ("POST", r"/api/login", login),
 ("GET", r"/api/customers", lambda c, b: rows(c, "SELECT id,name,phone,dob,id_checked FROM customers ORDER BY name")),
 ("POST", r"/api/customers", add_customer),
 ("GET", r"/api/sales", lambda c, b: rows(c, """SELECT s.id,s.total,s.created_at,cu.name customer,
     (SELECT GROUP_CONCAT(p.name||' x'||i.qty, ', ') FROM sale_items i JOIN products p ON p.id=i.product_id WHERE i.sale_id=s.id) items
     FROM sales s JOIN customers cu ON cu.id=s.customer_id ORDER BY s.id DESC LIMIT 50""")),
 ("POST", r"/api/sales", make_sale),
 ("GET", r"/api/repairs", lambda c, b: rows(c, """SELECT r.*,cu.name customer,cu.phone FROM repairs r
     JOIN customers cu ON cu.id=r.customer_id ORDER BY r.status='collected', r.id DESC""")),
 ("POST", r"/api/repairs", add_repair),
 ("PUT", r"/api/repairs/(\d+)", lambda c, b, i: update_repair(c, b, int(i))),
]

class H(BaseHTTPRequestHandler):
    def send(self, code, body, ctype="application/json"):
        data = body if isinstance(body, bytes) else json.dumps(body).encode()
        self.send_response(code); self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data))); self.end_headers(); self.wfile.write(data)

    def handle_api(self, method):
        path = self.path.split("?")[0]
        if path != "/api/login" and self.headers.get("X-Token") not in TOKENS:
            return self.send(401, {"error": "Please sign in"})
        for m, rx, fn in ROUTES:
            mt = re.fullmatch(rx, path)
            if m == method and mt:
                try:
                    n = int(self.headers.get("Content-Length") or 0)
                    body = json.loads(self.rfile.read(n) or b"{}") if n else {}
                    if method == "GET": body = {k: v[0] for k, v in parse_qs(urlsplit(self.path).query).items()}
                    with db() as c: return self.send(200, fn(c, body, *mt.groups()))
                except Err as e: return self.send(e.code, {"error": e.msg})
                except (ValueError, KeyError, TypeError): return self.send(400, {"error": "Invalid input"})
        self.send(404, {"error": "Not found"})

    def do_GET(self):
        if self.path.startswith("/api/"): return self.handle_api("GET")
        with open(os.path.join(os.path.dirname(__file__), "static", "index.html"), "rb") as f:
            self.send(200, f.read(), "text/html; charset=utf-8")
    def do_POST(self): self.handle_api("POST")
    def do_PUT(self): self.handle_api("PUT")
    def log_message(self, *a): pass

if __name__ == "__main__":
    init(); port = int(os.environ.get("PORT", 8000))
    print(f"Shop system running: http://localhost:{port}  (minimum age {MIN_AGE})")
    ThreadingHTTPServer((os.environ.get("HOST", "127.0.0.1"), port), H).serve_forever()
