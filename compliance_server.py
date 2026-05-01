import http.server
import urllib.request
import urllib.parse
import urllib.error
import json
import os
import base64

PORT = int(os.environ.get("PORT", 8765))
CH_API_KEY = os.environ.get("CH_API_KEY", "")

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
                self.send_header("Content-Length", str(len(content)))
                self.end_headers()
                self.wfile.write(content)
            except Exception as e:
                self.send_error(404, str(e))
            return

        if parsed.path.startswith("/api/"):
            ch_path = parsed.path[4:]
            api_key = params.get("apikey", [""])[0] or CH_API_KEY

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
                self.send_header("Content-Length", str(len(data)))
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
    server = http.server.HTTPServer(("0.0.0.0", PORT), Handler)
    print(f"Server running on port {PORT}", flush=True)
    server.serve_forever()
