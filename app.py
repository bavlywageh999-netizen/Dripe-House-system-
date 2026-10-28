"""Vape shop + repair workshop system. Python 3 stdlib only: python app.py -> http://localhost:8000"""
import base64, hashlib, json, os, re, secrets, sqlite3, sys, threading, time, traceback, webbrowser, datetime as dt
from urllib.parse import urlsplit, parse_qs, quote, unquote
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

DB = os.environ.get("DB_PATH", "workshop.db")
MIN_AGE = int(os.environ.get("MIN_AGE", 21))  # legal age in your country
LEGACY_PASSWORD = "Bisho@Vap2026"  # only used on the very first start if ADMIN_PASSWORD is not set; change it!
SESSION_HOURS, REMEMBER_DAYS = 12, 30
PUBLIC = {"/api/login", "/api/forgot"}  # everything else needs a valid session
ATTEMPTS = {}  # ip -> (failed count, window start)
CURRENCY = "L.E."
STATUSES = ["received", "diagnosing", "repairing", "ready", "collected"]

SCHEMA = """
CREATE TABLE IF NOT EXISTS products(
  id INTEGER PRIMARY KEY, sku TEXT UNIQUE NOT NULL, name TEXT NOT NULL,
  category TEXT NOT NULL, price REAL NOT NULL CHECK(price>=0),
  stock INTEGER NOT NULL DEFAULT 0 CHECK(stock>=0), low_stock INTEGER NOT NULL DEFAULT 5,
  cost REAL NOT NULL DEFAULT 0, image TEXT DEFAULT '', is_new INTEGER NOT NULL DEFAULT 0, color TEXT DEFAULT '', created_at TEXT DEFAULT '');
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
CREATE TABLE IF NOT EXISTS settings(key TEXT PRIMARY KEY, value TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS sessions(token TEXT PRIMARY KEY, expires_at TEXT NOT NULL);
"""
DEMO_SKUS = ["DS-001", "DS-002", "PD-101", "PD-102", "EL-001", "EL-002", "EG-001", "CL-201", "AC-301"]  # the old sample products

# ---------- authentication ----------
def hash_pw(pw, salt=None):
    salt = salt or secrets.token_bytes(16)
    return salt.hex() + "$" + hashlib.pbkdf2_hmac("sha256", pw.encode(), salt, 200_000).hex()

def verify_pw(pw, stored):
    try: return secrets.compare_digest(hash_pw(pw, bytes.fromhex(stored.split("$")[0])), stored)
    except ValueError: return False

def get_set(c, k):
    r = c.execute("SELECT value FROM settings WHERE key=?", (k,)).fetchone(); return r[0] if r else None

def put_set(c, k, v): c.execute("INSERT OR REPLACE INTO settings(key,value) VALUES(?,?)", (k, v))

def norm_key(k): return re.sub(r"[^0-9A-Z]", "", str(k or "").upper())

def new_recovery(c):
    key = "-".join(secrets.token_hex(2).upper() for _ in range(4))
    put_set(c, "recovery", hash_pw(norm_key(key))); return key

def now_utc(delta=None):
    t = dt.datetime.now(dt.timezone.utc) + (delta or dt.timedelta())
    return t.strftime("%Y-%m-%d %H:%M:%S")

def tok_hash(t): return hashlib.sha256(t.encode()).hexdigest()

def new_session(c, remember):
    tok = secrets.token_hex(32)
    exp = now_utc(dt.timedelta(days=REMEMBER_DAYS) if remember else dt.timedelta(hours=SESSION_HOURS))
    c.execute("INSERT INTO sessions(token,expires_at) VALUES(?,?)", (tok_hash(tok), exp)); return tok, exp

def valid_session(c, tok):
    c.execute("DELETE FROM sessions WHERE expires_at<?", (now_utc(),))
    return bool(tok) and c.execute("SELECT 1 FROM sessions WHERE token=?", (tok_hash(tok),)).fetchone() is not None

def throttle(ip):
    n, t0 = ATTEMPTS.get(ip, (0, time.time()))
    if time.time() - t0 > 600: ATTEMPTS.pop(ip, None)
    elif n >= 8: raise Err("Too many failed attempts. Wait 10 minutes and try again.", 429)

def failed(ip):
    n, t0 = ATTEMPTS.get(ip, (0, time.time()))
    if time.time() - t0 > 600: n, t0 = 0, time.time()
    ATTEMPTS[ip] = (n + 1, t0)

def ensure_password(c):
    """First start: store the password hashed and print a one-time recovery key."""
    if get_set(c, "password"): return
    env = os.environ.get("ADMIN_PASSWORD")
    put_set(c, "password", hash_pw(env or LEGACY_PASSWORD))
    print("\n*** First start: sign-in password saved (hashed) ***")
    if not env: print(f"*** WARNING: using the old default password '{LEGACY_PASSWORD}'. Change it: python app.py reset-password ***")
    print(f"*** RECOVERY KEY (write it down, shown only once): {new_recovery(c)} ***\n")

def db():
    c = sqlite3.connect(DB); c.row_factory = sqlite3.Row
    c.execute("PRAGMA foreign_keys=ON"); return c

def init():
    with db() as c:
        c.executescript(SCHEMA)
        for t, col, ddl in [("products", "cost", "REAL NOT NULL DEFAULT 0"), ("products", "image", "TEXT DEFAULT ''"),
                            ("products", "is_new", "INTEGER NOT NULL DEFAULT 0"), ("products", "color", "TEXT DEFAULT ''"), ("products", "created_at", "TEXT DEFAULT ''"), ("sale_items", "unit_cost", "REAL NOT NULL DEFAULT 0")]:
            if col not in [r[1] for r in c.execute(f"PRAGMA table_info({t})")]: c.execute(f"ALTER TABLE {t} ADD COLUMN {col} {ddl}")
        ensure_password(c)

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

def clean_image(v):
    """Photos are either an uploaded picture (data URL, resized by the browser) or an https link."""
    v = str(v or "").strip()
    if not v: return ""
    if len(v) > 600_000: raise Err("That photo is too large. Choose a smaller one.", 413)
    if not re.fullmatch(r"data:image/(png|jpe?g|webp|gif);base64,[A-Za-z0-9+/=]+|https?://\S+", v):
        raise Err("The photo must be an uploaded picture or a link starting with https://")
    return v

def list_products(c, b):
    out = rows(c, "SELECT * FROM products ORDER BY category,name")
    for p in out:  # keep the list small: uploaded photos are served separately from /img/<id>
        im = p["image"] or ""
        if im.startswith("data:"): p["image"] = f"/img/{p['id']}?v={hashlib.md5(im.encode()).hexdigest()[:8]}"
    return out

def clean_color(v):
    v = str(v or "").strip()
    if not re.fullmatch(r"[\w #\-]{0,30}", v): raise Err("Color can only be a name like Blue or a code like #1e90ff")
    return v

def add_product(c, b):
    need(b, "name", "category", "price")
    sku = str(b.get("sku") or "").strip() or "P-" + secrets.token_hex(3).upper()  # made automatically if left empty
    try: cur = c.execute("INSERT INTO products(sku,name,category,price,cost,stock,low_stock,image,is_new,color,created_at) VALUES(?,?,?,?,?,?,?,?,?,?,?)",
        (sku, b["name"], b["category"], float(b["price"]), float(b.get("cost") or 0), int(b.get("stock") or 0),
         int(b.get("low_stock") or 5), clean_image(b.get("image")), 1 if b.get("is_new") else 0, clean_color(b.get("color")), now_utc()))
    except sqlite3.IntegrityError as e: raise Err("That SKU is already used by another product" if "sku" in str(e) else f"Could not save product: {e}")
    return {"id": cur.lastrowid}

def update_product(c, b, pid):
    """Change cost/price/name/image/new-flag and receive stock (add can be negative for corrections)."""
    n = int(b.get("add") or 0)
    sets, vals = ["stock=stock+?"], [n]
    for k, cast in (("name", str), ("category", str), ("price", float), ("cost", float), ("is_new", int)):
        if b.get(k) not in (None, ""): sets.append(f"{k}=?"); vals.append(cast(b[k]))
    if "image" in b: sets.append("image=?"); vals.append(clean_image(b["image"]))  # "" removes the photo
    if "color" in b: sets.append("color=?"); vals.append(clean_color(b["color"]))
    try: ok = c.execute(f"UPDATE products SET {','.join(sets)} WHERE id=?", vals + [pid]).rowcount
    except sqlite3.IntegrityError: raise Err("Stock would go below zero")
    if not ok: raise Err("Product not found", 404)
    return {"ok": True}

def delete_product(c, b, pid):
    if c.execute("SELECT 1 FROM sale_items WHERE product_id=?", (pid,)).fetchone():
        raise Err("This product has been sold before, so it cannot be deleted (reports need its history). Set its stock to 0 instead.", 409)
    if not c.execute("DELETE FROM products WHERE id=?", (pid,)).rowcount: raise Err("Product not found", 404)
    return {"ok": True}

def clear_demo(c, b):
    q = ",".join("?" * len(DEMO_SKUS))
    n = c.execute(f"DELETE FROM products WHERE sku IN ({q}) AND id NOT IN (SELECT product_id FROM sale_items)", DEMO_SKUS).rowcount
    return {"deleted": n}

def cat_images(c, b):
    out = {}
    for k, v in c.execute("SELECT key,value FROM settings WHERE key LIKE 'catimg:%'"):
        cat = k[7:]; out[cat] = f"/catimg/{quote(cat)}?v={hashlib.md5(v.encode()).hexdigest()[:8]}" if v.startswith("data:") else v
    return out

def set_cat_image(c, b):
    need(b, "category")
    cat = str(b["category"]).strip().lower()
    if not re.fullmatch(r"[a-z0-9][a-z0-9 \-]{0,29}", cat): raise Err("Unknown category")
    img = clean_image(b.get("image"))
    if img: put_set(c, "catimg:" + cat, img)
    else: c.execute("DELETE FROM settings WHERE key=?", ("catimg:" + cat,))  # empty = back to the drawn banner
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
    ip = b["_ip"]; throttle(ip)
    if not verify_pw(str(b.get("password", "")), get_set(c, "password") or ""):
        failed(ip); raise Err("Wrong password", 401)
    ATTEMPTS.pop(ip, None)
    tok, exp = new_session(c, bool(b.get("remember")))
    return {"token": tok, "expires": exp}

def logout(c, b):
    c.execute("DELETE FROM sessions WHERE token=?", (tok_hash(b["_tok"]),)); return {"ok": True}

def forgot(c, b):
    """Reset the password with the recovery key. Signs everyone out and issues a new recovery key."""
    ip = b["_ip"]; throttle(ip)
    new = str(b.get("password", ""))
    if len(new) < 8: raise Err("New password must be at least 8 characters")
    if not verify_pw(norm_key(b.get("key")), get_set(c, "recovery") or ""):
        failed(ip); raise Err("That recovery key is not correct", 403)
    ATTEMPTS.pop(ip, None)
    put_set(c, "password", hash_pw(new)); c.execute("DELETE FROM sessions")
    return {"recovery_key": new_recovery(c)}

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
 ("GET", r"/api/products", list_products),
 ("POST", r"/api/products", add_product),
 ("PUT", r"/api/products/(\d+)", lambda c, b, i: update_product(c, b, int(i))),
 ("DELETE", r"/api/products/(\d+)", lambda c, b, i: delete_product(c, b, int(i))),
 ("POST", r"/api/products/clear-demo", clear_demo),
 ("GET", r"/api/catimages", cat_images),
 ("PUT", r"/api/catimage", set_cat_image),
 ("GET", r"/api/report", report),
 ("GET", r"/api/dashboard", dashboard),
 ("POST", r"/api/login", login),
 ("POST", r"/api/logout", logout),
 ("POST", r"/api/forgot", forgot),
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
    def send(self, code, body, ctype="application/json", cache=None):
        data = body if isinstance(body, bytes) else json.dumps(body).encode()
        self.send_response(code); self.send_header("Content-Type", ctype)
        if cache: self.send_header("Cache-Control", cache)
        o = self.headers.get("Origin") or ""
        if re.fullmatch(r"null|https?://(localhost|127\.0\.0\.1)(:\d+)?", o):  # only pages on this computer
            self.send_header("Access-Control-Allow-Origin", o); self.send_header("Vary", "Origin")
            self.send_header("Access-Control-Allow-Headers", "Content-Type, X-Token")
            self.send_header("Access-Control-Allow-Methods", "GET, POST, PUT, DELETE, OPTIONS")
        self.send_header("Content-Length", str(len(data))); self.end_headers(); self.wfile.write(data)

    def handle_api(self, method):
        path, tok = self.path.split("?")[0], self.headers.get("X-Token") or ""
        try:
            with db() as c:
                if path not in PUBLIC and not valid_session(c, tok): return self.send(401, {"error": "Please sign in"})
            for m, rx, fn in ROUTES:
                mt = re.fullmatch(rx, path)
                if m == method and mt:
                    n = int(self.headers.get("Content-Length") or 0)
                    body = json.loads(self.rfile.read(n) or b"{}") if n else {}
                    if method == "GET": body = {k: v[0] for k, v in parse_qs(urlsplit(self.path).query).items()}
                    if not isinstance(body, dict): raise Err("Invalid input")
                    body["_ip"], body["_tok"] = self.client_address[0], tok
                    with db() as c: return self.send(200, fn(c, body, *mt.groups()))
            self.send(404, {"error": "Not found"})
        except Err as e: self.send(e.code, {"error": e.msg})
        except (ValueError, KeyError, TypeError): self.send(400, {"error": "Invalid input"})
        except Exception: traceback.print_exc(); self.send(500, {"error": "Server error. See the server window for details."})

    def do_GET(self):
        if self.path.startswith("/api/"): return self.handle_api("GET")
        path = self.path.split("?")[0]; im = re.fullmatch(r"/img/(\d+)", path); ci = re.fullmatch(r"/catimg/(.+)", path)
        if im or ci:
            with db() as c:
                if im: r = c.execute("SELECT image FROM products WHERE id=?", (int(im.group(1)),)).fetchone(); v = r[0] if r else ""
                else: v = get_set(c, "catimg:" + unquote(ci.group(1)).lower())
            m = re.fullmatch(r"data:(image/[a-z+]+);base64,(.+)", v or "", re.S)
            if not m: return self.send(404, b"", "text/plain")
            return self.send(200, base64.b64decode(m.group(2)), m.group(1), "public, max-age=31536000, immutable")
        here = os.path.dirname(os.path.abspath(__file__))
        for p in (os.path.join(here, "static", "index.html"), os.path.join(here, "index.html")):
            if os.path.isfile(p):
                with open(p, "rb") as f: return self.send(200, f.read(), "text/html; charset=utf-8")
        self.send(500, b"index.html not found. Put it next to app.py (or in a 'static' folder).", "text/plain; charset=utf-8")
    def do_OPTIONS(self): self.send(204, b"", "text/plain")
    def do_POST(self): self.handle_api("POST")
    def do_PUT(self): self.handle_api("PUT")
    def do_DELETE(self): self.handle_api("DELETE")
    def log_message(self, *a): pass

def cli_reset():
    import getpass
    init()
    pw = getpass.getpass("New password (at least 8 characters): ")
    if len(pw) < 8 or pw != getpass.getpass("Repeat new password: "): sys.exit("Passwords did not match or are too short. Nothing changed.")
    with db() as c:
        put_set(c, "password", hash_pw(pw)); c.execute("DELETE FROM sessions")
        print(f"Password changed. New recovery key (write it down): {new_recovery(c)}")

if __name__ == "__main__":
    if sys.argv[1:] == ["reset-password"]: sys.exit(cli_reset())
    init(); port = int(os.environ.get("PORT", 8000))
    try: srv = ThreadingHTTPServer((os.environ.get("HOST", "127.0.0.1"), port), H)
    except OSError: sys.exit(f"Port {port} is already in use. The shop system may already be running (try http://localhost:{port}),\nor start it on another port:  set PORT=9000  then  python app.py")
    print(f"Shop system running: http://localhost:{port}  (minimum age {MIN_AGE}). Keep this window open; close it to stop.")
    if not os.environ.get("NO_BROWSER"): threading.Timer(1, webbrowser.open, (f"http://localhost:{port}",)).start()
    srv.serve_forever()
