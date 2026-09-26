/* StockSense shared frontend helpers.
   Loaded by every page except login.html. */

const API_BASE = "http://localhost:8000";

const Session = {
  getToken() { return localStorage.getItem("ss_token"); },
  getUser() {
    try { return JSON.parse(localStorage.getItem("ss_user") || "null"); }
    catch { return null; }
  },
  save(token, user) {
    localStorage.setItem("ss_token", token);
    localStorage.setItem("ss_user", JSON.stringify(user));
  },
  clear() {
    localStorage.removeItem("ss_token");
    localStorage.removeItem("ss_user");
  },
  requireAuthOrRedirect() {
    if (!this.getToken()) {
      window.location.href = "login.html";
      return false;
    }
    return true;
  }
};

async function apiFetch(path, options = {}) {
  const headers = Object.assign({}, options.headers || {});
  const token = Session.getToken();
  if (token) headers["Authorization"] = `Bearer ${token}`;
  if (options.body && !headers["Content-Type"]) headers["Content-Type"] = "application/json";

  const res = await fetch(API_BASE + path, { ...options, headers });
  let data = null;
  try { data = await res.json(); } catch { /* no body */ }

  if (!res.ok) {
    const message = (data && data.error) || `Request failed (${res.status})`;
    throw new Error(message);
  }
  return data;
}

function escapeHtml(str) {
  const div = document.createElement("div");
  div.textContent = String(str ?? "");
  return div.innerHTML;
}

function formatDate(isoStr) {
  if (!isoStr) return "—";
  try {
    const d = new Date(isoStr);
    if (isNaN(d.getTime())) return isoStr;
    return d.toLocaleDateString() + " " + d.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });
  } catch { return isoStr; }
}

function typePill(type) {
  const t = String(type || "").toLowerCase();
  let cls = "pill-internal";
  if (t.includes("receipt")) cls = "pill-receipt";
  else if (t.includes("delivery")) cls = "pill-delivery";
  else if (t.includes("adjustment")) cls = "pill-adjustment";
  return `<span class="pill ${cls}">${escapeHtml(type)}</span>`;
}

function statusPill(status) {
  const s = String(status || "").toLowerCase();
  let cls = "pill-draft";
  if (s === "done") cls = "pill-done";
  else if (s === "ready" || s === "waiting") cls = "pill-ready";
  else if (s === "cancelled") cls = "pill-cancelled";
  return `<span class="pill ${cls}">${escapeHtml(status)}</span>`;
}

const NAV_ITEMS = [
  { href: "index.html", label: "Dashboard", key: "dashboard" },
  { href: "products.html", label: "Products", key: "products" },
  { section: "Operations" },
  { href: "operations.html?type=Receipt", label: "Receipts", key: "receipts" },
  { href: "operations.html?type=Delivery", label: "Deliveries", key: "deliveries" },
  { href: "operations.html?type=Internal", label: "Internal Transfers", key: "internal" },
  { href: "operations.html?type=Adjustment", label: "Adjustments", key: "adjustments" },
  { href: "move-history.html", label: "Move History", key: "move-history" },
  { section: "Configuration" },
  { href: "settings.html", label: "Warehouses & Locations", key: "settings" }
];

function renderSidebar(activeKey) {
  const mount = document.getElementById("sidebar-root");
  if (!mount) return;
  if (!Session.requireAuthOrRedirect()) return;

  const user = Session.getUser() || { name: "User", role: "" };
  const initials = user.name ? user.name.split(" ").map(p => p[0]).join("").slice(0, 2).toUpperCase() : "U";

  const navHtml = NAV_ITEMS.map(item => {
    if (item.section) {
      return `<div class="nav-section-label">${escapeHtml(item.section)}</div>`;
    }
    const isActive = item.key === activeKey;
    return `<a href="${item.href}" class="${isActive ? "active" : ""}"><span class="dot"></span> ${escapeHtml(item.label)}</a>`;
  }).join("");

  mount.innerHTML = `
    <aside class="sidebar">
      <div class="brand">
        <div class="brand-mark">SS</div>
        <div class="brand-name">StockSense</div>
      </div>
      <nav>${navHtml}</nav>
      <div class="profile-box">
        <div class="profile-menu" id="profileMenu">
          <a href="#" id="myProfileLink">My Profile</a>
          <button type="button" class="danger" id="logoutBtn">Logout</button>
        </div>
        <button class="profile-btn" id="profileBtn">
          <span class="profile-avatar">${escapeHtml(initials)}</span>
          <span class="profile-meta">
            <div class="profile-name">${escapeHtml(user.name || "User")}</div>
            <div class="profile-role">${escapeHtml(user.role || "Inventory Manager")}</div>
          </span>
        </button>
      </div>
    </aside>
  `;

  document.getElementById("profileBtn").addEventListener("click", () => {
    document.getElementById("profileMenu").classList.toggle("open");
  });
  document.getElementById("logoutBtn").addEventListener("click", () => {
    Session.clear();
    window.location.href = "login.html";
  });
  document.getElementById("myProfileLink").addEventListener("click", (e) => {
    e.preventDefault();
    alert(`Signed in as ${user.name || "—"} (${user.email || "—"})\nRole: ${user.role || "—"}`);
  });
  document.addEventListener("click", (e) => {
    const box = document.querySelector(".profile-box");
    if (box && !box.contains(e.target)) {
      document.getElementById("profileMenu").classList.remove("open");
    }
  });
}
