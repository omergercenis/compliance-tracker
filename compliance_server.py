import http.server
import urllib.request
import urllib.error
import urllib.parse
import json
import os
import base64
import sqlite3
import threading

PORT = int(os.environ.get("PORT", 8765))
CH_API_KEY = os.environ.get("CH_API_KEY", "")
DATABASE_URL = os.environ.get("DATABASE_URL", "")

# DB setup
db_lock = threading.Lock()

def get_db():
    if DATABASE_URL:
        try:
            import psycopg2
            conn = psycopg2.connect(DATABASE_URL, sslmode='require')
            return conn, 'pg'
        except Exception as e:
            print(f"PG error: {e}", flush=True)
    conn = sqlite3.connect('local_data.db', check_same_thread=False)
    return conn, 'sqlite'

def init_db():
    conn, dbtype = get_db()
    cur = conn.cursor()
    if dbtype == 'pg':
        cur.execute("""
            CREATE TABLE IF NOT EXISTS tracker (
                company_num TEXT PRIMARY KEY,
                data JSONB NOT NULL,
                updated_at TIMESTAMP DEFAULT NOW()
            )
        """)
        cur.execute("""
            CREATE TABLE IF NOT EXISTS done_items (
                key TEXT PRIMARY KEY,
                updated_at TIMESTAMP DEFAULT NOW()
            )
        """)
    else:
        cur.execute("CREATE TABLE IF NOT EXISTS tracker (company_num TEXT PRIMARY KEY, data TEXT NOT NULL)")
        cur.execute("CREATE TABLE IF NOT EXISTS done_items (key TEXT PRIMARY KEY)")
    conn.commit()
    conn.close()
    print("DB initialized", flush=True)

def db_get_tracker():
    with db_lock:
        conn, dbtype = get_db()
        cur = conn.cursor()
        cur.execute("SELECT company_num, data FROM tracker")
        rows = cur.fetchall()
        conn.close()
        result = {}
        for num, data in rows:
            result[num] = data if isinstance(data, dict) else json.loads(data)
        return result

def db_set_tracker(company_num, data):
    with db_lock:
        conn, dbtype = get_db()
        cur = conn.cursor()
        data_str = json.dumps(data, ensure_ascii=False)
        if dbtype == 'pg':
            cur.execute("INSERT INTO tracker (company_num, data, updated_at) VALUES (%s, %s::jsonb, NOW()) ON CONFLICT (company_num) DO UPDATE SET data=%s::jsonb, updated_at=NOW()", (company_num, data_str, data_str))
        else:
            cur.execute("INSERT OR REPLACE INTO tracker (company_num, data) VALUES (?,?)", (company_num, data_str))
        conn.commit()
        conn.close()

def db_delete_tracker(company_num):
    with db_lock:
        conn, dbtype = get_db()
        cur = conn.cursor()
        if dbtype == 'pg':
            cur.execute("DELETE FROM tracker WHERE company_num=%s", (company_num,))
        else:
            cur.execute("DELETE FROM tracker WHERE company_num=?", (company_num,))
        conn.commit()
        conn.close()

def db_get_done():
    with db_lock:
        conn, dbtype = get_db()
        cur = conn.cursor()
        cur.execute("SELECT key FROM done_items")
        rows = cur.fetchall()
        conn.close()
        return {row[0]: True for row in rows}

def db_set_done(key, checked):
    with db_lock:
        conn, dbtype = get_db()
        cur = conn.cursor()
        if checked:
            if dbtype == 'pg':
                cur.execute("INSERT INTO done_items (key) VALUES (%s) ON CONFLICT DO NOTHING", (key,))
            else:
                cur.execute("INSERT OR IGNORE INTO done_items (key) VALUES (?)", (key,))
        else:
            if dbtype == 'pg':
                cur.execute("DELETE FROM done_items WHERE key=%s", (key,))
            else:
                cur.execute("DELETE FROM done_items WHERE key=?", (key,))
        conn.commit()
        conn.close()

class Handler(http.server.BaseHTTPRequestHandler):
    def log_message(self, format, *args): pass

    def send_json(self, data, code=200):
        body = json.dumps(data, ensure_ascii=False).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_OPTIONS(self):
        self.send_response(200)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Headers", "*")
        self.send_header("Access-Control-Allow-Methods", "GET,POST,DELETE,OPTIONS")
        self.end_headers()

    def do_GET(self):
        parsed = urllib.parse.urlparse(self.path)
        params = urllib.parse.parse_qs(parsed.query)
        path = parsed.path

        # HTML
        if path in ("/", "/index.html"):
            html_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "uk_compliance_tracker.html")
            try:
                with open(html_path, "rb") as f:
                    content = f.read()
                self.send_response(200)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.send_header("Content-Length", str(len(content)))
                self.end_headers()
                self.wfile.write(content)
            except:
                self.send_error(404)
            return

        # DB: get tracker data
        if path == "/db/tracker":
            self.send_json(db_get_tracker())
            return

        # DB: get done items
        if path == "/db/done":
            self.send_json(db_get_done())
            return

        # Companies House proxy
        if path.startswith("/api/"):
            ch_path = path[4:]
            api_key = params.get("apikey", [""])[0] or CH_API_KEY
            ch_params = {k: v[0] for k, v in params.items() if k != "apikey"}
            url = "https://api.company-information.service.gov.uk" + ch_path
            if ch_params:
                url += "?" + urllib.parse.urlencode(ch_params)
            auth = base64.b64encode((api_key + ":").encode()).decode()
            req = urllib.request.Request(url, headers={"Authorization": "Basic " + auth, "Accept": "application/json"})
            try:
                with urllib.request.urlopen(req, timeout=10) as resp:
                    data = resp.read()
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Access-Control-Allow-Origin", "*")
                self.end_headers()
                self.wfile.write(data)
            except urllib.error.HTTPError as e:
                self.send_response(e.code)
                self.send_header("Access-Control-Allow-Origin", "*")
                self.end_headers()
            except Exception:
                self.send_response(500)
                self.send_header("Access-Control-Allow-Origin", "*")
                self.end_headers()
            return

        self.send_error(404)

    def do_POST(self):
        parsed = urllib.parse.urlparse(self.path)
        length = int(self.headers.get('Content-Length', 0))
        body = self.rfile.read(length)
        try:
            payload = json.loads(body)
        except:
            payload = {}

        # Save tracker entry
        if parsed.path == "/db/tracker":
            num = payload.get("company_num")
            data = payload.get("data")
            if num and data is not None:
                db_set_tracker(num, data)
                self.send_json({"ok": True})
            else:
                self.send_json({"error": "missing fields"}, 400)
            return

        # Save done item
        if parsed.path == "/db/done":
            key = payload.get("key")
            checked = payload.get("checked", True)
            if key:
                db_set_done(key, checked)
                self.send_json({"ok": True})
            else:
                self.send_json({"error": "missing key"}, 400)
            return

        self.send_error(404)

    def do_DELETE(self):
        parsed = urllib.parse.urlparse(self.path)
        if parsed.path.startswith("/db/tracker/"):
            num = parsed.path.split("/")[-1]
            db_delete_tracker(num)
            self.send_json({"ok": True})
            return
        self.send_error(404)

if __name__ == "__main__":
    # Install psycopg2 if needed
    if DATABASE_URL:
        try:
            import psycopg2
        except ImportError:
            import subprocess, sys
            subprocess.check_call([sys.executable, "-m", "pip", "install", "psycopg2-binary", "-q"])
    
    init_db()
    server = http.server.HTTPServer(("0.0.0.0", PORT), Handler)
    print(f"Server running on port {PORT}", flush=True)
    server.serve_forever()
