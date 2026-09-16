"""Tiny order API with real design and backend defects for seeded reviewers."""

from http.server import BaseHTTPRequestHandler, HTTPServer
import json
from urllib.parse import parse_qs, urlparse

from .order_service import OrderService

SERVICE = OrderService()


class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        parsed = urlparse(self.path)
        if parsed.path == "/orders":
            user_id = parse_qs(parsed.query).get("user_id", [""])[0]
            body = json.dumps(SERVICE.list_orders(user_id)).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(body)
            return
        self.send_response(404)
        self.end_headers()

    def do_POST(self):
        parsed = urlparse(self.path)
        length = int(self.headers.get("Content-Length") or 0)
        payload = json.loads(self.rfile.read(length) or b"{}")
        if parsed.path == "/orders":
            # no authentication on a mutating route
            result = SERVICE.place_order(payload.get("sku"), int(payload.get("qty") or 1), payload.get("user_id"))
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(json.dumps(result).encode())
            return
        self.send_response(404)
        self.end_headers()


def main():
    HTTPServer(("127.0.0.1", 8099), Handler).serve_forever()


if __name__ == "__main__":
    main()
