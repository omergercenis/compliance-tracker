import http.server
import urllib.request
import urllib.error
import urllib.parse
import json
import os
import base64
import sqlite3
import threading
import time

PORT = int(os.environ.get("PORT", 8765))
CH_API_KEY = os.environ.get("CH_API_KEY", "")
DATABASE_URL = os.environ.get("DATABASE_URL", "")
CRON_SECRET = os.environ.get("CRON_SECRET", "tradist-cron-2024")

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
        cur.execute("""CREATE TABLE IF NOT EXISTS tracker (
            company_num TEXT PRIMARY KEY, data JSONB NOT NULL, updated_at TIMESTAMP DEFAULT NOW())""")
        cur.execute("""CREATE TABLE IF NOT EXISTS done_items (
            key TEXT PRIMARY KEY, updated_at TIMESTAMP DEFAULT NOW())""")
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

def fetch_company(num, api_key):
    url = f"https://api.company-information.service.gov.uk/company/{num}"
    auth = base64.b64encode((api_key + ":").encode()).decode()
    req = urllib.request.Request(url, headers={"Authorization": "Basic " + auth, "Accept": "application/json"})
    with urllib.request.urlopen(req, timeout=10) as resp:
        return json.loads(resp.read())

def refresh_all_companies():
    """Tüm şirketlerin Companies House bilgilerini güncelle"""
    api_key = CH_API_KEY
    if not api_key:
        print("No API key, skipping refresh", flush=True)
        return {"updated": 0, "errors": 0, "message": "No API key"}

    tracker = db_get_tracker()
    updated = 0
    errors = 0

    print(f"Starting refresh for {len(tracker)} companies...", flush=True)

    for num, data in tracker.items():
        try:
            profile = fetch_company(num, api_key)
            # Sadece Companies House verilerini güncelle, kullanıcı verilerini koru
            data['profile'] = {
                'company_name': profile.get('company_name'),
                'company_number': profile.get('company_number'),
                'company_status': profile.get('company_status'),
                'type': profile.get('type'),
                'date_of_creation': profile.get('date_of_creation'),
                'confirmation_statement': profile.get('confirmation_statement'),
                'accounts': profile.get('accounts'),
            }
            data['name'] = profile.get('company_name', data.get('name', ''))
            # nextDue güncelle
            cs = profile.get('confirmation_statement', {}) or {}
            acc = profile.get('accounts', {}) or {}
            dates = [d for d in [cs.get('next_due'), acc.get('next_due')] if d]
            data['nextDue'] = sorted(dates)[0] if dates else '9999'
            # dl güncelle
            data['dl'] = [
                {'id': 'cs', 'type': 'Confirmation Statement', 'label': 'Son beyan tarihi',
                 'date': cs.get('next_due'), 'sub': f"Son CS: {cs.get('last_made_up_to','')}" if cs.get('last_made_up_to') else None},
                {'id': 'acc', 'type': 'Accounts', 'label': 'Hesap beyanı son tarihi',
                 'date': acc.get('next_due'), 'sub': f"Dönem sonu: {(acc.get('next_accounts') or {}).get('period_end_on','')}" if (acc.get('next_accounts') or {}).get('period_end_on') else None},
            ]
            db_set_tracker(num, data)
            updated += 1
            print(f"  ✓ {num} {data['name']}", flush=True)
            time.sleep(0.3)  # API rate limit için bekle
        except Exception as e:
            errors += 1
            print(f"  ✗ {num}: {e}", flush=True)

    print(f"Refresh complete: {updated} updated, {errors} errors", flush=True)
    return {"updated": updated, "errors": errors, "total": len(tracker)}

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

        if path == "/db/tracker":
            self.send_json(db_get_tracker())
            return

        if path == "/db/done":
            self.send_json(db_get_done())
            return

        # Otomatik güncelleme endpoint — Cron Job buraya istek atar
        if path == "/cron/refresh":
            secret = params.get("secret", [""])[0]
            if secret != CRON_SECRET:
                self.send_json({"error": "unauthorized"}, 401)
                return
            # Arka planda çalıştır
            def run():
                refresh_all_companies()
            threading.Thread(target=run, daemon=True).start()
            self.send_json({"message": "Refresh started", "companies": len(db_get_tracker())})
            return

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

        if parsed.path == "/db/tracker":
            num = payload.get("company_num")
            data = payload.get("data")
            if num and data is not None:
                db_set_tracker(num, data)
                self.send_json({"ok": True})
            else:
                self.send_json({"error": "missing fields"}, 400)
            return

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
