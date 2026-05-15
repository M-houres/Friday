(function () {
  window.API_BASE = "/api/v1";
  window.AUTH_STORAGE_KEY = "friday_ops_token";
  window.currentAccount = null;
  window.authBootstrap = null;

  window.toast = function toast(message, type = "info") {
    const root = document.getElementById("toast-root");
    const el = document.createElement("div");
    el.className = "toast";
    if (type === "err") el.style.background = "#7f1d1d";
    if (type === "ok") el.style.background = "#115e59";
    el.textContent = message;
    root.appendChild(el);
    setTimeout(() => el.remove(), 2800);
  };

  window.getStoredToken = function getStoredToken() {
    return window.localStorage.getItem(window.AUTH_STORAGE_KEY) || "";
  };

  window.setStoredToken = function setStoredToken(token) {
    if (token) {
      window.localStorage.setItem(window.AUTH_STORAGE_KEY, token);
    } else {
      window.localStorage.removeItem(window.AUTH_STORAGE_KEY);
    }
  };

  window.showAuthScreen = function showAuthScreen() {
    document.getElementById("auth-screen").style.display = "grid";
    document.getElementById("panel-app").style.display = "none";
  };

  window.showPanelApp = function showPanelApp() {
    document.getElementById("auth-screen").style.display = "none";
    document.getElementById("panel-app").style.display = "block";
  };

  window.switchAuthTab = function switchAuthTab(tab) {
    const loginActive = tab !== "register";
    document.getElementById("auth-login-form").style.display = loginActive ? "block" : "none";
    document.getElementById("auth-register-form").style.display = loginActive ? "none" : "block";
    document.getElementById("tab-login").className = loginActive ? "btn btn-primary auth-tab" : "btn auth-tab";
    document.getElementById("tab-register").className = loginActive ? "btn auth-tab" : "btn btn-primary auth-tab";
  };

  window.setAuthFormsDisabled = function setAuthFormsDisabled(disabled) {
    document.querySelectorAll("#auth-screen input, #auth-screen button").forEach(item => {
      if (item.id === "tab-login" || item.id === "tab-register") return;
      item.disabled = disabled;
    });
  };

  window.api = async function api(method, path, body) {
    const headers = {"Content-Type": "application/json"};
    const token = window.getStoredToken();
    if (token) {
      headers.Authorization = `Bearer ${token}`;
    }
    const response = await fetch(window.API_BASE + path, {
      method,
      headers,
      body: body ? JSON.stringify(body) : undefined,
    });
    if (!response.ok) {
      if (response.status === 401 && !path.startsWith("/auth/")) {
        window.setStoredToken("");
        window.currentAccount = null;
        window.showAuthScreen();
        throw new Error("登录已失效，请重新登录");
      }
      const text = await response.text();
      throw new Error(text || `${response.status}`);
    }
    return response.json();
  };

  window.loadAuthBootstrap = async function loadAuthBootstrap() {
    window.authBootstrap = await window.api("GET", "/auth/bootstrap");
    let note = window.authBootstrap.first_user_becomes_admin
      ? "当前还没有账号。首个注册用户会自动成为管理员。"
      : `当前已有 ${window.authBootstrap.user_count} 个账号。请使用已存在账号登录。`;
    if (!window.authBootstrap.database_available) {
      note = "数据库当前不可用，登录和注册暂时不可用。服务虽然处于降级启动状态，但后台账号系统依赖数据库。";
    }
    document.getElementById("auth-bootstrap-note").textContent = note;
    window.setAuthFormsDisabled(!window.authBootstrap.database_available);
    if (window.authBootstrap.first_user_becomes_admin) {
      window.switchAuthTab("register");
    }
  };

  window.submitLogin = async function submitLogin() {
    try {
      const result = await window.api("POST", "/auth/login", {
        email: document.getElementById("login-email").value.trim(),
        password: document.getElementById("login-password").value,
      });
      window.setStoredToken(result.access_token || "");
      window.currentAccount = result.account || null;
      window.toast("已登录后台", "ok");
      await window.bootPanel();
    } catch (error) {
      window.toast(error.message || "登录失败", "err");
    }
  };

  window.submitRegister = async function submitRegister() {
    const password = document.getElementById("register-password").value;
    const passwordConfirm = document.getElementById("register-password-confirm").value;
    if (password !== passwordConfirm) {
      window.toast("两次输入的密码不一致", "err");
      return;
    }
    try {
      const result = await window.api("POST", "/auth/register", {
        name: document.getElementById("register-name").value.trim(),
        email: document.getElementById("register-email").value.trim(),
        password,
      });
      window.setStoredToken(result.access_token || "");
      window.currentAccount = result.account || null;
      window.toast("账号已创建并登录", "ok");
      await window.bootPanel();
    } catch (error) {
      window.toast(error.message || "注册失败", "err");
    }
  };

  window.loadCurrentAccount = async function loadCurrentAccount() {
    window.currentAccount = await window.api("GET", "/auth/me");
    const title = document.querySelector(".brand p");
    if (title && window.currentAccount) {
      const roles = (window.currentAccount.roles || []).join(", ") || "anonymous";
      title.textContent = `${window.currentAccount.email || window.currentAccount.user_id} · ${roles}`;
    }
  };

  window.logoutPanel = function logoutPanel() {
    window.setStoredToken("");
    window.currentAccount = null;
    window.showAuthScreen();
    window.loadAuthBootstrap().catch(() => null);
    window.toast("已退出登录", "ok");
  };

  window.bootPanel = async function bootPanel() {
    if (!window.getStoredToken()) {
      window.showAuthScreen();
      await window.loadAuthBootstrap();
      return;
    }
    try {
      await window.loadCurrentAccount();
      window.showPanelApp();
      if (typeof window.loadPage === "function") {
        window.loadPage("overview");
      }
    } catch (error) {
      window.setStoredToken("");
      window.currentAccount = null;
      window.showAuthScreen();
      await window.loadAuthBootstrap();
    }
  };

  window.escapeHtml = function escapeHtml(value) {
    return String(value ?? "")
      .replaceAll("&", "&amp;")
      .replaceAll("<", "&lt;")
      .replaceAll(">", "&gt;")
      .replaceAll('"', "&quot;")
      .replaceAll("'", "&#39;");
  };

  window.shortText = function shortText(value, max = 60) {
    const text = String(value ?? "");
    return text.length > max ? `${text.slice(0, max)}...` : text;
  };

  window.fmtTime = function fmtTime(value) {
    if (!value) return "";
    const date = new Date(value);
    if (Number.isNaN(date.getTime())) return String(value);
    return date.toLocaleString();
  };

  window.parseJsonInput = function parseJsonInput(value) {
    const text = (value || "").trim();
    if (!text) return null;
    return JSON.parse(text);
  };

  window.parseCsv = function parseCsv(value) {
    return String(value || "").split(",").map(item => item.trim()).filter(Boolean);
  };

  window.encodeActionPayload = function encodeActionPayload(value) {
    return encodeURIComponent(JSON.stringify(value ?? null));
  };

  window.decodeActionPayload = function decodeActionPayload(value) {
    return JSON.parse(decodeURIComponent(String(value || "")));
  };

  window.statusBadge = function statusBadge(status) {
    const normalized = String(status || "").toLowerCase();
    if (["completed", "approved", "healthy", "ok", "active", "resumed"].includes(normalized)) return "badge ok";
    if (["failed", "rejected", "error", "disabled"].includes(normalized)) return "badge err";
    if (["running", "executing", "dispatching"].includes(normalized)) return "badge info";
    return "badge warn";
  };

  window.renderEmpty = function renderEmpty(message) {
    return `<div class="empty">${window.escapeHtml(message)}</div>`;
  };

  window.renderSimpleList = function renderSimpleList(items, renderer, emptyMessage) {
    if (!items || !items.length) return window.renderEmpty(emptyMessage);
    return `<div class="list">${items.map(renderer).join("")}</div>`;
  };
})();
