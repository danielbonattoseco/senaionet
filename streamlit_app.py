#!/usr/bin/env python3

import os
import sys
import json
import base64
import urllib.request
import urllib.parse
from http.server import HTTPServer, BaseHTTPRequestHandler

# -----------------------------------------------------------------------------
# Config
# -----------------------------------------------------------------------------
CONFIG = {
    "instance_url": os.environ.get("INSTANCE_URL"),
    "dashboard_id": os.environ.get("DASHBOARD_ID"),
    "service_principal_id": os.environ.get("SERVICE_PRINCIPAL_ID"),
    "service_principal_secret": os.environ.get("SERVICE_PRINCIPAL_SECRET"),
    "external_viewer_id": os.environ.get("EXTERNAL_VIEWER_ID"),
    "external_value": os.environ.get("EXTERNAL_VALUE"),
    "workspace_id": os.environ.get("WORKSPACE_ID"),
    "port": int(os.environ.get("PORT", 3000)),
}

basic_auth = base64.b64encode(
    f"{CONFIG['service_principal_id']}:{CONFIG['service_principal_secret']}".encode()
).decode()

# -----------------------------------------------------------------------------
# HTTP Request Helper
# -----------------------------------------------------------------------------
def http_request(url, method="GET", headers=None, body=None):
    headers = headers or {}
    if body is not None and not isinstance(body, (bytes, str)):
        raise ValueError("Body must be bytes or str")

    req = urllib.request.Request(url, method=method, headers=headers)
    if body is not None:
        if isinstance(body, str):
            body = body.encode()
        req.data = body

    try:
        with urllib.request.urlopen(req) as resp:
            data = resp.read().decode()
            try:
                return {"data": json.loads(data)}
            except json.JSONDecodeError:
                return {"data": data}
    except urllib.error.HTTPError as e:
        raise RuntimeError(f"HTTP {e.code}: {e.read().decode()}") from None

# -----------------------------------------------------------------------------
# Token logic
# -----------------------------------------------------------------------------
def get_scoped_token():
    # 1. Get all-api token
    oidc_res = http_request(
        f"{CONFIG['instance_url']}/oidc/v1/token",
        method="POST",
        headers={
            "Content-Type": "application/x-www-form-urlencoded",
            "Authorization": f"Basic {basic_auth}",
        },
        body=urllib.parse.urlencode({
            "grant_type": "client_credentials",
            "scope": "all-apis"
        })
    )
    oidc_token = oidc_res["data"]["access_token"]

    # 2. Get token info
    token_info_url = (
        f"{CONFIG['instance_url']}/api/2.0/lakeview/dashboards/"
        f"{CONFIG['dashboard_id']}/published/tokeninfo"
        f"?external_viewer_id={urllib.parse.quote(CONFIG['external_viewer_id'])}"
        f"&external_value={urllib.parse.quote(CONFIG['external_value'])}"
    )
    token_info = http_request(
        token_info_url,
        headers={"Authorization": f"Bearer {oidc_token}"}
    )["data"]

    # 3. Generate scoped token
    params = token_info.copy()
    authorization_details = params.pop("authorization_details", None)
    params.update({
        "grant_type": "client_credentials",
        "authorization_details": json.dumps(authorization_details)
    })

    scoped_res = http_request(
        f"{CONFIG['instance_url']}/oidc/v1/token",
        method="POST",
        headers={
            "Content-Type": "application/x-www-form-urlencoded",
            "Authorization": f"Basic {basic_auth}",
        },
        body=urllib.parse.urlencode(params)
    )
    return scoped_res["data"]["access_token"]

# -----------------------------------------------------------------------------
# HTML generator
# -----------------------------------------------------------------------------
def generate_html(token):
    return f"""<!DOCTYPE html>
<html>
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Dashboard Demo</title>
    <style>
        body {{ font-family: system-ui; margin: 0; padding: 20px; background: #f5f5f5; }}
        .container {{ max-width: 1200px; margin: 0 auto; height:calc(100vh - 40px) }}
    </style>
</head>
<body>
    <div id="dashboard-content" class="container"></div>
    <script type="module">
        import {{ DatabricksDashboard }} from "https://cdn.jsdelivr.net/npm/@databricks/aibi-client@0.0.0-alpha.7/+esm";
        const dashboard = new DatabricksDashboard({{
            instanceUrl: "{CONFIG['instance_url']}",
            workspaceId: "{CONFIG['workspace_id']}",
            dashboardId: "{CONFIG['dashboard_id']}",
            token: "{token}",
            container: document.getElementById("dashboard-content")
        }});
        dashboard.initialize();
    </script>
</body>
</html>"""

# -----------------------------------------------------------------------------
# HTTP server
# -----------------------------------------------------------------------------
class RequestHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path != "/":
            self.send_response(404)
            self.send_header("Content-Type", "text/plain")
            self.end_headers()
            self.wfile.write(b"Not Found")
            return

        try:
            token = get_scoped_token()
            html = generate_html(token)
            status = 200
        except Exception as e:
            html = f"<h1>Error</h1><p>{e}</p>"
            status = 500

        self.send_response(status)
        self.send_header("Content-Type", "text/html")
        self.end_headers()
        self.wfile.write(html.encode())

def start_server():
    missing = [k for k, v in CONFIG.items() if not v]
    if missing:
        print(f"Missing: {', '.join(missing)}", file=sys.stderr)
        sys.exit(1)

    server = HTTPServer(("localhost", CONFIG["port"]), RequestHandler)
    print(f":rocket: Server running on http://localhost:{CONFIG['port']}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        sys.exit(0)

if __name__ == "__main__":
    start_server()