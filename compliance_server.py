import http.server
import urllib.request
import urllib.parse
import json
import os
import sys
import base64

PORT = 8765
KEY_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".ch_apikey")

def load_key():
    if os.path.exists(KEY_FILE):
        with open(KEY_FILE) as f:
            return f.read().strip()
    return ""

def save_key(k):
    with open(KEY_FILE, "w") as f:
        f.write(k)

class Handler(http.server.BaseHTTPRequestHandler):
    def log_message(self, format, *args):
        pass

    def do_OPTIONS(self):
        self.send_response(200)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Headers", "*")
        self.end_headers()

    def do_GET(self):
        parsed = urllib.parse.urlparse(self.path)
        params = urllib.parse.parse_qs(parsed.query)

        if parsed.path in ("/", "/index.html"):
            html_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "uk_compliance_tracker.html")
            try:
                with open(html_path, "rb") as f:
                    content = f.read()
                self.send_response(200)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.end_headers()
                self.wfile.write(content)
            except:
                self.send_error(404)
            return

        if parsed.path == "/save-key":
            key = params.get("key", [""])[0]
            if key:
                save_key(key)
            self.send_response(200)
            self.send_header("Access-Control-Allow-Origin", "*")
            self.end_headers()
            self.wfile.write(b"ok")
            return

        if parsed.path.startswith("/api/"):
            ch_path = parsed.path[4:]
            api_key = params.get("apikey", [""])[0] or load_key()
            
            # apikey parametresini CH'ye gönderme
            ch_params = {k: v[0] for k, v in params.items() if k != "apikey"}
            url = "https://api.company-information.service.gov.uk" + ch_path
            if ch_params:
                url += "?" + urllib.parse.urlencode(ch_params)

            auth = base64.b64encode((api_key + ":").encode()).decode()
            req = urllib.request.Request(url, headers={
                "Authorization": "Basic " + auth,
                "Accept": "application/json"
            })
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
            except Exception as e:
                self.send_response(500)
                self.send_header("Access-Control-Allow-Origin", "*")
                self.end_headers()
            return

        self.send_error(404)

if __name__ == "__main__":
    server = http.server.HTTPServer(("localhost", PORT), Handler)
    print("\n" + "="*45)
    print("  UK Compliance Tracker - Calistirildi!")
    print("="*45)
    print(f"\n  Tarayicinizda aciliyor...")
    print(f"  http://localhost:{PORT}")
    print(f"\n  Kapatmak icin bu pencereyi kapatin.\n")

    import threading, webbrowser, time
    def open_browser():
        time.sleep(1.2)
        webbrowser.open(f"http://localhost:{PORT}")
    threading.Thread(target=open_browser, daemon=True).start()

    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nKapatildi.")
