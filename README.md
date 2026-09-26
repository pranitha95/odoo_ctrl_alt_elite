# StockSense — Inventory Management System

StockSense is a lightweight, self-contained Inventory Management System (IMS) built for the **Odoo × GCET hackathon**, based on the StockSense problem statement. It replaces manual registers and spreadsheets with a single web app for tracking products, warehouses, and every stock movement in and out of them — styled after Odoo's own Inventory app.

## Overview

The app is split into two layers:
- A **Python backend** (`server.py`) that serves the frontend files, exposes a small JSON REST API, and persists everything to flat JSON files (no database server required).
- A **vanilla HTML/CSS/JS frontend** — no build step, no npm install, no framework. Every page is a plain `.html` file that talks to the backend over `fetch()`.

Because the backend uses only Python's standard library, the whole thing runs with a single command and no external dependencies — useful for hackathon judging where installs and setup time are limited.

## Features

**Authentication**
- Sign up / log in with name, email, and password (SHA-256 + per-user salt, never stored in plaintext)
- Forgot-password flow with a one-time OTP and expiry
- Session-token based auth — every page redirects to the login screen if there's no valid session

**Multi-warehouse, multi-location stock**
- Warehouses (Name, Short Code, Address) and Locations (Name, Short Code, parent Warehouse) are real, manageable entities under **Settings**
- Two virtual locations — *Vendors* and *Customers* — act as the conceptual source/destination for Receipts and Deliveries
- Each product's stock is tracked **per location**, not as one flat number, so Internal Transfers actually move quantity from one place to another instead of being a no-op

**Product catalog**
- Name, SKU (unique), Category, Unit of Measure, Reorder Point, and optional initial stock
- Per-location stock breakdown, with total on-hand computed automatically
- Search by name/SKU, plus category and stock-level filters

**Operations (Receipts, Deliveries, Internal Transfers, Adjustments)**
- One shared operations screen with a tab per type, each with its own labels and location rules:
  - **Receipts** — Vendor → Warehouse, increases stock on validate
  - **Delivery Orders** — Warehouse → Customer, decreases stock on validate
  - **Internal Transfers** — Warehouse location → Warehouse location, total stock unchanged, per-location breakdown updated
  - **Adjustments** — sets an absolute *counted* quantity at one location (not a delta), for reconciling physical counts
- Repeatable product line items per operation (not limited to one product per operation)
- Status lifecycle: `Draft → Ready/Waiting → Done`, with **Validate** and **Cancel** actions
- Search by reference/partner, plus status filtering

**Dashboard**
- Five live KPIs: Total Products, Low/Out of Stock, Pending Receipts, Pending Deliveries, Internal Transfers Scheduled
- Dynamic filters on the operations stream: document type, status, warehouse, category

**Move History (ledger)**
- Every validated operation writes an immutable ledger entry per product per location: Reference, Timestamp, Type, Product, Location, Before → After, Change
- Gives a full audit trail independent of the operations list

## Tech Stack

| Layer | Technology |
|---|---|
| Backend | Python 3, `http.server` (standard library only — no Flask/Django/pip installs) |
| Frontend | Vanilla HTML, CSS, JavaScript — no framework, no build step |
| Storage | Flat JSON files, auto-seeded on first run |
| Auth | Server-issued session tokens + salted SHA-256 password hashing |

## Project Structure

```
server.py           Backend: routing, auth, warehouses/locations, products, operations, ledger
common.css           Shared styles used by every page (sidebar, cards, tables, modals, pills)
common.js            Shared JS: auth helpers, API fetch wrapper, sidebar/nav rendering
login.html           Sign up / log in / forgot password (OTP) / reset password
index.html           Dashboard — KPIs, dynamic filters, recent operations stream
products.html        Product catalog with per-location stock, search, and filters
operations.html      Receipts / Deliveries / Internal Transfers / Adjustments (tabbed)
move-history.html    Full stock movement ledger
settings.html        Manage Warehouses and Locations

users.json           }
warehouses.json      }
locations.json       }  Auto-created on first run — plain JSON acting as the database
products.json        }
operations.json      }
ledger.json          }
```

## Getting Started

**Prerequisites:** Python 3.8 or later. Nothing else to install.

```bash
python3 server.py
```

Then open **http://localhost:8000** in a browser.

There's no seeded login — sign up a new account from the login page the first time. Warehouses, locations, and a couple of sample products are seeded automatically on first launch so the app isn't empty on first open (edit the `DEFAULT_*` constants near the top of `server.py` to change the starting data).

## API Reference

All endpoints are under `/api/` and (aside from `/api/auth/*`) require a `Authorization: Bearer <token>` header from a logged-in session.

| Method | Endpoint | Description |
|---|---|---|
| POST | `/api/auth/signup` | Create an account |
| POST | `/api/auth/login` | Log in, returns a session token |
| POST | `/api/auth/forgot-password` | Request an OTP |
| POST | `/api/auth/reset-password` | Reset password with a valid OTP |
| GET | `/api/auth/me` | Current logged-in user |
| GET / POST | `/api/warehouses` | List / create warehouses |
| GET / POST | `/api/locations` | List / create locations |
| GET / POST | `/api/products` | List (with computed stock + low-stock flags) / create products |
| GET / POST | `/api/operations` | List (filterable by `type`, `status`) / create an operation |
| POST | `/api/operations/validate` | Validate an operation and update stock + ledger |
| POST | `/api/operations/cancel` | Cancel an operation |
| GET | `/api/ledger` | Full stock movement ledger |

## Known Limitations

These are conscious hackathon-scope trade-offs, not oversights:

- **Sessions are in-memory** — restarting the server logs everyone out. Fine for a single judging session, not for production.
- **OTP delivery is simulated** — with no email/SMS provider wired up, the OTP is returned directly in the API response (`devOtp`) so the forgot-password flow is demonstrable end-to-end.
- **Single-threaded dev server** — `http.server` handles one request at a time, which is fine at hackathon scale but not under real concurrent load.
- **No automated tests** — everything has been exercised manually end-to-end.

## Credits

Built for the Odoo × GCET hackathon.
