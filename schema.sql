-- Vape Workshop database (SQLite). app.py runs this automatically on first start.
-- To create the file by hand: sqlite3 workshop.db < schema.sql

PRAGMA foreign_keys=ON;

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
CREATE TABLE IF NOT EXISTS settings(key TEXT PRIMARY KEY, value TEXT NOT NULL);      -- hashed password + hashed recovery key
CREATE TABLE IF NOT EXISTS sessions(token TEXT PRIMARY KEY, expires_at TEXT NOT NULL); -- hashed login tokens, UTC expiry
