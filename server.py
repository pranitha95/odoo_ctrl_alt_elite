"""
StockSense Backend Server
--------------------------
Rewritten to match the problem statement:
  - Authentication (signup / login / OTP password reset)
  - Multi-warehouse & multi-location stock (Vendors -> Stock -> Customers, like Odoo)
  - Receipts / Delivery / Internal Transfer / Adjustment all move stock through
    real (or virtual) locations instead of one flat "currentStock" number
  - Every stock change is written to an immutable ledger (Move History)
  - Low-stock is driven by a per-product reorder point, not a hardcoded number

Still a single-file, dependency-free HTTP server (stdlib only) so it can run
anywhere during judging, but the data model now actually matches the
architecture the app needs.
"""

from http.server import HTTPServer, BaseHTTPRequestHandler
import json
import os
import re
import secrets
import hashlib
import urllib.parse
import random
import mimetypes
from datetime import datetime, timedelta

PORT = 8000
BASE_DIR = os.path.dirname(os.path.abspath(__file__))

USERS_FILE = os.path.join(BASE_DIR, "users.json")
WAREHOUSES_FILE = os.path.join(BASE_DIR, "warehouses.json")
LOCATIONS_FILE = os.path.join(BASE_DIR, "locations.json")
PRODUCTS_FILE = os.path.join(BASE_DIR, "products.json")
OPERATIONS_FILE = os.path.join(BASE_DIR, "operations.json")
LEDGER_FILE = os.path.join(BASE_DIR, "ledger.json")

# In-memory session store (token -> userId). Fine for a hackathon build;
# tokens don't need to survive a server restart.
SESSIONS = {}

# ---------------------------------------------------------------------------
# Seed data
# ---------------------------------------------------------------------------

DEFAULT_WAREHOUSES = [
    {"id": "wh_main", "name": "Main Warehouse", "shortCode": "WH", "address": "123 Industrial Avenue"}
]

# type: "internal" (real, stock-holding) | "vendor" / "customer" (virtual endpoints)
DEFAULT_LOCATIONS = [
    {"id": "loc_wh_main_stock", "name": "WH/Stock", "shortCode": "WH-STOCK", "warehouseId": "wh_main", "type": "internal"},
    {"id": "loc_wh_main_shelfb", "name": "WH/Shelf B", "shortCode": "WH-SHELF-B", "warehouseId": "wh_main", "type": "internal"},
    {"id": "loc_vendors", "name": "Vendors", "shortCode": "VENDORS", "warehouseId": None, "type": "vendor"},
    {"id": "loc_customers", "name": "Customers", "shortCode": "CUSTOMERS", "warehouseId": None, "type": "customer"},
]

DEFAULT_PRODUCTS = [
    {
        "id": "prod_1",
        "name": "Steel Rod 12mm",
        "sku": "SR-12MM",
        "category": "Raw Materials",
        "uom": "Meters",
        "reorderPoint": 30,
        "stock": [{"locationId": "loc_wh_main_stock", "quantity": 150}]
    },
    {
        "id": "prod_2",
        "name": "Industrial Fastener",
        "sku": "IF-900",
        "category": "Hardware",
        "uom": "Units",
        "reorderPoint": 100,
        "stock": [{"locationId": "loc_wh_main_stock", "quantity": 500}]
    }
]

DEFAULT_OPERATIONS = [
    {
        "id": "op_101",
        "type": "Receipt",
        "status": "Ready",
        "partner": "Supplier A",
        "sourceLocationId": "loc_vendors",
        "destinationLocationId": "loc_wh_main_stock",
        "items": [{"productId": "prod_1", "quantity": 50}],
        "scheduledDate": "2026-09-25T10:00:00Z",
        "createdAt": "2026-09-25T10:00:00Z"
    },
    {
        "id": "op_102",
        "type": "Delivery",
        "status": "Ready",
        "partner": "Client X",
        "sourceLocationId": "loc_wh_main_stock",
        "destinationLocationId": "loc_customers",
        "items": [{"productId": "prod_2", "quantity": 20}],
        "scheduledDate": "2026-09-25T11:30:00Z",
        "createdAt": "2026-09-25T11:30:00Z"
    },
    {
        "id": "op_103",
        "type": "Internal",
        "status": "Ready",
        "partner": None,
        "sourceLocationId": "loc_wh_main_stock",
        "destinationLocationId": "loc_wh_main_shelfb",
        "items": [{"productId": "prod_1", "quantity": 10}],
        "scheduledDate": "2026-09-26T08:15:00Z",
        "createdAt": "2026-09-26T08:15:00Z"
    },
    {
        "id": "op_104",
        "type": "Adjustment",
        "status": "Ready",
        "partner": "System Audit",
        "sourceLocationId": "loc_wh_main_stock",
        "destinationLocationId": "loc_wh_main_stock",
        "items": [{"productId": "prod_2", "quantity": 480}],
        "scheduledDate": "2026-09-26T09:00:00Z",
        "createdAt": "2026-09-26T09:00:00Z"
    }
]


def init_database():
    if not os.path.exists(USERS_FILE):
        save_json(USERS_FILE, [])
    if not os.path.exists(WAREHOUSES_FILE):
        save_json(WAREHOUSES_FILE, DEFAULT_WAREHOUSES)
    if not os.path.exists(LOCATIONS_FILE):
        save_json(LOCATIONS_FILE, DEFAULT_LOCATIONS)
    if not os.path.exists(PRODUCTS_FILE):
        save_json(PRODUCTS_FILE, DEFAULT_PRODUCTS)
    if not os.path.exists(OPERATIONS_FILE):
        save_json(OPERATIONS_FILE, DEFAULT_OPERATIONS)
    if not os.path.exists(LEDGER_FILE):
        save_json(LEDGER_FILE, [])


def load_json(filepath):
    if not os.path.exists(filepath):
        return []
    with open(filepath, "r", encoding="utf-8") as f:
        try:
            return json.load(f)
        except json.JSONDecodeError:
            return []


def save_json(filepath, data):
    with open(filepath, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def hash_password(password, salt):
    return hashlib.sha256((salt + password).encode("utf-8")).hexdigest()


def new_id(prefix):
    return f"{prefix}_{int(datetime.now().timestamp() * 1000)}_{secrets.token_hex(2)}"


def find_product(products, product_ref):
    return next((p for p in products if p["id"] == product_ref or p["sku"] == product_ref), None)


def get_stock_entry(product, location_id, create=False):
    for entry in product.setdefault("stock", []):
        if entry["locationId"] == location_id:
            return entry
    if create:
        entry = {"locationId": location_id, "quantity": 0}
        product["stock"].append(entry)
        return entry
    return None


def total_stock(product):
    return sum(e.get("quantity", 0) for e in product.get("stock", []))


def is_internal_location(location):
    return bool(location) and location.get("type") == "internal"


def get_user_from_token(headers):
    auth = headers.get("Authorization", "")
    token = None
    if auth.startswith("Bearer "):
        token = auth.split(" ", 1)[1].strip()
    if not token:
        return None
    user_id = SESSIONS.get(token)
    if not user_id:
        return None
    users = load_json(USERS_FILE)
    return next((u for u in users if u["id"] == user_id), None)


EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


class StockSenseHandler(BaseHTTPRequestHandler):

    # -------------------- shared plumbing --------------------

    def _set_cors_headers(self):
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, PUT, DELETE, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type, Authorization")

    def _send_response_json(self, status_code, data):
        self.send_response(status_code)
        self._set_cors_headers()
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(json.dumps(data).encode("utf-8"))

    def _read_json_body(self):
        content_length = int(self.headers.get("Content-Length", 0))
        raw = self.rfile.read(content_length) if content_length else b""
        if not raw:
            return {}
        return json.loads(raw.decode("utf-8"))

    def do_OPTIONS(self):
        self.send_response(200)
        self._set_cors_headers()
        self.end_headers()

    # -------------------- routing --------------------

    def do_GET(self):
        parsed = urllib.parse.urlparse(self.path)
        path = parsed.path
        query = urllib.parse.parse_qs(parsed.query)

        if not path.startswith("/api/"):
            filename = "index.html" if path in ("/", "") else path.lstrip("/")
            # Basic traversal guard: only ever serve files that live directly
            # inside BASE_DIR (css/js/html assets shared across pages).
            safe_name = os.path.basename(filename)
            if "/" not in filename and os.path.exists(os.path.join(BASE_DIR, safe_name)):
                content_type = mimetypes.guess_type(safe_name)[0] or "application/octet-stream"
                self._serve_static_file(safe_name, content_type)
            else:
                self._send_response_json(404, {"error": f"'{filename}' not found"})
            return

        try:
            if path == "/api/auth/me":
                self._handle_auth_me()
            elif path == "/api/warehouses":
                self._send_response_json(200, load_json(WAREHOUSES_FILE))
            elif path == "/api/locations":
                self._send_response_json(200, load_json(LOCATIONS_FILE))
            elif path == "/api/products":
                self._handle_get_products()
            elif path == "/api/operations":
                self._handle_get_operations(query)
            elif path == "/api/ledger":
                self._handle_get_ledger(query)
            else:
                self._send_response_json(404, {"error": "Endpoint not found"})
        except Exception as exc:  # keep the demo server alive even on bad input
            self._send_response_json(500, {"error": str(exc)})

    def do_POST(self):
        parsed = urllib.parse.urlparse(self.path)
        path = parsed.path

        try:
            body = self._read_json_body()
        except json.JSONDecodeError:
            self._send_response_json(400, {"error": "Invalid JSON payload"})
            return

        try:
            if path == "/api/auth/signup":
                self._handle_signup(body)
            elif path == "/api/auth/login":
                self._handle_login(body)
            elif path == "/api/auth/forgot-password":
                self._handle_forgot_password(body)
            elif path == "/api/auth/reset-password":
                self._handle_reset_password(body)
            elif path == "/api/warehouses":
                self._handle_create_warehouse(body)
            elif path == "/api/locations":
                self._handle_create_location(body)
            elif path == "/api/products":
                self._handle_create_product(body)
            elif path == "/api/operations":
                self._handle_create_operation(body)
            elif path == "/api/operations/validate":
                self._handle_validate_operation(body)
            elif path == "/api/operations/cancel":
                self._handle_cancel_operation(body)
            else:
                self._send_response_json(404, {"error": "Endpoint not found"})
        except ValueError as exc:
            self._send_response_json(400, {"error": str(exc)})
        except Exception as exc:
            self._send_response_json(500, {"error": str(exc)})

    # -------------------- auth --------------------

    def _handle_signup(self, body):
        name = (body.get("name") or "").strip()
        email = (body.get("email") or "").strip().lower()
        password = body.get("password") or ""

        if not name or not email or not password:
            raise ValueError("name, email and password are required")
        if not EMAIL_RE.match(email):
            raise ValueError("Please provide a valid email address")
        if len(password) < 6:
            raise ValueError("Password must be at least 6 characters")

        users = load_json(USERS_FILE)
        if any(u["email"] == email for u in users):
            raise ValueError("An account with this email already exists")

        salt = secrets.token_hex(8)
        user = {
            "id": new_id("user"),
            "name": name,
            "email": email,
            "role": body.get("role", "Inventory Manager"),
            "salt": salt,
            "passwordHash": hash_password(password, salt),
            "otp": None,
            "otpExpiry": None,
            "createdAt": datetime.now().isoformat()
        }
        users.append(user)
        save_json(USERS_FILE, users)

        token = secrets.token_hex(24)
        SESSIONS[token] = user["id"]
        self._send_response_json(201, {"token": token, "user": self._public_user(user)})

    def _handle_login(self, body):
        email = (body.get("email") or "").strip().lower()
        password = body.get("password") or ""
        users = load_json(USERS_FILE)
        user = next((u for u in users if u["email"] == email), None)
        if not user or hash_password(password, user["salt"]) != user["passwordHash"]:
            raise ValueError("Invalid email or password")

        token = secrets.token_hex(24)
        SESSIONS[token] = user["id"]
        self._send_response_json(200, {"token": token, "user": self._public_user(user)})

    def _handle_forgot_password(self, body):
        email = (body.get("email") or "").strip().lower()
        users = load_json(USERS_FILE)
        user = next((u for u in users if u["email"] == email), None)
        if not user:
            # Don't leak account existence, but still respond 200 like a real API would.
            self._send_response_json(200, {"message": "If that account exists, an OTP has been sent."})
            return

        otp = f"{random.randint(0, 999999):06d}"
        user["otp"] = otp
        user["otpExpiry"] = (datetime.now() + timedelta(minutes=10)).isoformat()
        save_json(USERS_FILE, users)

        # No real email service in this hackathon build, so the OTP is
        # returned in the response (devOtp) purely so the reset flow is
        # demonstrable end-to-end. A production build would email this.
        self._send_response_json(200, {
            "message": "If that account exists, an OTP has been sent.",
            "devOtp": otp
        })

    def _handle_reset_password(self, body):
        email = (body.get("email") or "").strip().lower()
        otp = (body.get("otp") or "").strip()
        new_password = body.get("newPassword") or ""

        if len(new_password) < 6:
            raise ValueError("Password must be at least 6 characters")

        users = load_json(USERS_FILE)
        user = next((u for u in users if u["email"] == email), None)
        if not user or not user.get("otp"):
            raise ValueError("Invalid or expired OTP")
        if user["otp"] != otp:
            raise ValueError("Invalid or expired OTP")
        if user.get("otpExpiry") and datetime.fromisoformat(user["otpExpiry"]) < datetime.now():
            raise ValueError("This OTP has expired. Please request a new one.")

        salt = secrets.token_hex(8)
        user["salt"] = salt
        user["passwordHash"] = hash_password(new_password, salt)
        user["otp"] = None
        user["otpExpiry"] = None
        save_json(USERS_FILE, users)
        self._send_response_json(200, {"message": "Password updated. Please log in."})

    def _handle_auth_me(self):
        user = get_user_from_token(self.headers)
        if not user:
            self._send_response_json(401, {"error": "Not authenticated"})
            return
        self._send_response_json(200, {"user": self._public_user(user)})

    @staticmethod
    def _public_user(user):
        return {"id": user["id"], "name": user["name"], "email": user["email"], "role": user.get("role")}

    # -------------------- warehouses & locations --------------------

    def _handle_create_warehouse(self, body):
        name = (body.get("name") or "").strip()
        if not name:
            raise ValueError("Warehouse name is required")

        warehouses = load_json(WAREHOUSES_FILE)
        warehouse = {
            "id": new_id("wh"),
            "name": name,
            "shortCode": (body.get("shortCode") or name[:4]).upper(),
            "address": body.get("address", "")
        }
        warehouses.append(warehouse)
        save_json(WAREHOUSES_FILE, warehouses)

        # Every warehouse needs at least one internal stock location.
        locations = load_json(LOCATIONS_FILE)
        default_location = {
            "id": new_id("loc"),
            "name": f"{warehouse['shortCode']}/Stock",
            "shortCode": f"{warehouse['shortCode']}-STOCK",
            "warehouseId": warehouse["id"],
            "type": "internal"
        }
        locations.append(default_location)
        save_json(LOCATIONS_FILE, locations)

        self._send_response_json(201, {"warehouse": warehouse, "location": default_location})

    def _handle_create_location(self, body):
        name = (body.get("name") or "").strip()
        warehouse_id = body.get("warehouseId")
        if not name or not warehouse_id:
            raise ValueError("Location name and warehouseId are required")

        warehouses = load_json(WAREHOUSES_FILE)
        if not any(w["id"] == warehouse_id for w in warehouses):
            raise ValueError("Unknown warehouseId")

        locations = load_json(LOCATIONS_FILE)
        location = {
            "id": new_id("loc"),
            "name": name,
            "shortCode": body.get("shortCode", name[:8].upper()),
            "warehouseId": warehouse_id,
            "type": "internal"
        }
        locations.append(location)
        save_json(LOCATIONS_FILE, locations)
        self._send_response_json(201, {"location": location})

    # -------------------- products --------------------

    def _handle_get_products(self):
        products = load_json(PRODUCTS_FILE)
        enriched = []
        for p in products:
            total = total_stock(p)
            enriched.append({
                **p,
                "currentStock": total,
                "isLowStock": total <= p.get("reorderPoint", 0) and total > 0,
                "isOutOfStock": total <= 0
            })
        self._send_response_json(200, enriched)

    def _handle_create_product(self, body):
        name = (body.get("name") or "").strip()
        sku = (body.get("sku") or "").strip().upper()
        if not name or not sku:
            raise ValueError("Product name and SKU are required")

        products = load_json(PRODUCTS_FILE)
        if any(p["sku"] == sku for p in products):
            raise ValueError(f"A product with SKU '{sku}' already exists")

        # Initial stock is optional per the spec.
        initial_stock = int(body.get("initialStock", body.get("currentStock", body.get("stock", 0))) or 0)
        location_id = body.get("locationId")
        if not location_id:
            locations = load_json(LOCATIONS_FILE)
            default_loc = next((l for l in locations if l["type"] == "internal"), None)
            location_id = default_loc["id"] if default_loc else None

        stock = []
        if initial_stock and location_id:
            stock.append({"locationId": location_id, "quantity": initial_stock})

        new_product = {
            "id": new_id("prod"),
            "name": name,
            "sku": sku,
            "category": body.get("category", "General"),
            "uom": body.get("uom", "Units"),
            "reorderPoint": int(body.get("reorderPoint", 0) or 0),
            "stock": stock
        }
        products.append(new_product)
        save_json(PRODUCTS_FILE, products)
        self._send_response_json(201, {"message": "Product created", "product": new_product})

    # -------------------- operations --------------------

    def _handle_get_operations(self, query):
        operations = load_json(OPERATIONS_FILE)
        op_type = query.get("type", [None])[0]
        status = query.get("status", [None])[0]
        if op_type and op_type != "ALL":
            operations = [o for o in operations if o.get("type") == op_type]
        if status and status != "ALL":
            operations = [o for o in operations if o.get("status") == status]
        self._send_response_json(200, operations)

    def _handle_create_operation(self, body):
        op_type = body.get("type", "Receipt")
        items = body.get("items", [])
        if not items:
            raise ValueError("At least one product line is required")

        locations = load_json(LOCATIONS_FILE)
        location_ids = {l["id"] for l in locations}
        source_id = body.get("sourceLocationId")
        dest_id = body.get("destinationLocationId")
        if source_id and source_id not in location_ids:
            raise ValueError("Unknown sourceLocationId")
        if dest_id and dest_id not in location_ids:
            raise ValueError("Unknown destinationLocationId")

        operations = load_json(OPERATIONS_FILE)
        new_op = {
            "id": body.get("id") or new_id("op"),
            "type": op_type,
            "status": body.get("status", "Draft"),
            "partner": body.get("partner"),
            "sourceLocationId": source_id,
            "destinationLocationId": dest_id,
            "items": items,
            "scheduledDate": body.get("scheduledDate", datetime.now().isoformat()),
            "createdAt": datetime.now().isoformat()
        }
        operations.insert(0, new_op)
        save_json(OPERATIONS_FILE, operations)
        self._send_response_json(201, {"message": "Operation created", "operation": new_op})

    def _handle_validate_operation(self, body):
        operation_id = body.get("id") or body.get("operationId")
        operations = load_json(OPERATIONS_FILE)
        operation = next((op for op in operations if op["id"] == operation_id), None)
        if not operation:
            raise ValueError(f"Operation '{operation_id}' not found")
        if operation["status"] == "Done":
            raise ValueError("Operation is already completed")
        if operation["status"] == "Cancelled":
            raise ValueError("Cancelled operations cannot be validated")

        products = load_json(PRODUCTS_FILE)
        locations = {l["id"]: l for l in load_json(LOCATIONS_FILE)}
        ledger = load_json(LEDGER_FILE)

        op_type = operation.get("type")
        source_loc = locations.get(operation.get("sourceLocationId"))
        dest_loc = locations.get(operation.get("destinationLocationId"))
        logs = []

        for item in operation.get("items", []):
            prod_ref = item.get("productId")
            qty = int(item.get("quantity", 0))
            product = find_product(products, prod_ref)
            if not product or qty <= 0:
                continue

            if op_type == "Adjustment":
                # Adjustment quantity is the freshly *counted* absolute stock,
                # not a delta, applied at a single location.
                target_loc_id = item.get("locationId") or operation.get("destinationLocationId")
                entry = get_stock_entry(product, target_loc_id, create=True)
                prev = entry["quantity"]
                entry["quantity"] = qty
                logs.append(self._ledger_entry(operation_id, op_type, product, target_loc_id,
                                                prev, qty, qty - prev, ledger))
                continue

            # Receipt / Delivery / Internal all move stock between two locations.
            if is_internal_location(source_loc):
                entry = get_stock_entry(product, source_loc["id"], create=True)
                prev = entry["quantity"]
                entry["quantity"] = max(0, prev - qty)
                logs.append(self._ledger_entry(operation_id, op_type, product, source_loc["id"],
                                                prev, entry["quantity"], entry["quantity"] - prev, ledger))

            if is_internal_location(dest_loc):
                entry = get_stock_entry(product, dest_loc["id"], create=True)
                prev = entry["quantity"]
                entry["quantity"] = prev + qty
                logs.append(self._ledger_entry(operation_id, op_type, product, dest_loc["id"],
                                                prev, entry["quantity"], qty, ledger))

        operation["status"] = "Done"
        save_json(PRODUCTS_FILE, products)
        save_json(OPERATIONS_FILE, operations)
        save_json(LEDGER_FILE, ledger)

        self._send_response_json(200, {
            "message": "Operation validated and inventory updated",
            "operation": operation,
            "ledgerEntries": logs
        })

    def _ledger_entry(self, operation_id, op_type, product, location_id, prev, new, change, ledger):
        entry = {
            "ledgerId": new_id("leg"),
            "operationId": operation_id,
            "type": op_type,
            "productId": product["id"],
            "productName": product["name"],
            "locationId": location_id,
            "previousStock": prev,
            "newStock": new,
            "quantityChange": change,
            "timestamp": datetime.now().isoformat()
        }
        ledger.append(entry)
        return entry

    def _handle_cancel_operation(self, body):
        operation_id = body.get("id") or body.get("operationId")
        operations = load_json(OPERATIONS_FILE)
        operation = next((op for op in operations if op["id"] == operation_id), None)
        if not operation:
            raise ValueError(f"Operation '{operation_id}' not found")
        if operation["status"] == "Done":
            raise ValueError("A completed operation cannot be cancelled")
        operation["status"] = "Cancelled"
        save_json(OPERATIONS_FILE, operations)
        self._send_response_json(200, {"message": "Operation cancelled", "operation": operation})

    # -------------------- ledger --------------------

    def _handle_get_ledger(self, query):
        ledger = load_json(LEDGER_FILE)
        product_id = query.get("productId", [None])[0]
        location_id = query.get("locationId", [None])[0]
        if product_id:
            ledger = [l for l in ledger if l.get("productId") == product_id]
        if location_id:
            ledger = [l for l in ledger if l.get("locationId") == location_id]
        self._send_response_json(200, ledger)

    # -------------------- static files --------------------

    def _serve_static_file(self, filename, content_type):
        filepath = os.path.join(BASE_DIR, filename)
        if not os.path.exists(filepath):
            self._send_response_json(404, {"error": f"File '{filename}' not found locally."})
            return
        with open(filepath, "rb") as f:
            content = f.read()
        self.send_response(200)
        self._set_cors_headers()
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(content)))
        self.end_headers()
        self.wfile.write(content)

    def log_message(self, format, *args):
        # Quieter console output during a demo.
        pass


def run():
    init_database()
    server_address = ("", PORT)
    httpd = HTTPServer(server_address, StockSenseHandler)
    print(f"\n==========================================")
    print(f" StockSense Backend Server Live!")
    print(f" URL: http://localhost:{PORT}")
    print(f"==========================================\n")
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        httpd.server_close()


if __name__ == "__main__":
    run()
