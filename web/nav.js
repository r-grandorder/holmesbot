/* Shared config + top-nav behavior for both pages (kit browser + dashboard).
   Include BEFORE any page-specific script; it exposes API_BASE + token helpers as globals. */

// The bot's API (Cloudflare Tunnel hostname), no trailing slash.
const API_BASE = "https://api.sherlockholmesbot.com";

const TK = "holmesbot_token";
const token = () => localStorage.getItem(TK);
const setToken = t => localStorage.setItem(TK, t);
const clearToken = () => localStorage.removeItem(TK);

// Capture an OAuth bearer token handed back in the URL fragment (#token=...), then clean the URL.
// Only strips the hash when a token is actually present, so hash-based tab routing (#me/#boards/#wars)
// on the dashboard is left intact.
(function () {
  const m = location.hash.match(/token=([^&]+)/);
  if (m) {
    setToken(decodeURIComponent(m[1]));
    history.replaceState(null, "", location.pathname + location.search);
  }
})();

// The shared top nav, injected wherever a <div id="topnav"></div> placeholder exists.
function renderTopNav() {
  const host = document.getElementById("topnav");
  if (!host) return;
  host.outerHTML = `
    <header class="topnav">
      <a class="brand" href="/"><img class="brandlogo" src="icons/logo.png" alt="" onerror="this.remove()"><span class="brandtext">Holmes&nbsp;Bot</span></a>
      <nav class="mainnav">
        <a href="/" data-nav="kits">Autobattle Kits</a>
        <a href="/dashboard.html#me" data-nav="me">My stuff</a>
        <a href="/dashboard.html#boards" data-nav="boards">Leaderboards</a>
        <a href="/dashboard.html#wars" data-nav="wars">Wars</a>
        <a href="/dashboard.html#raids" data-nav="raids">Raids</a>
        <a href="/dashboard.html#servants" data-nav="servants">Servants</a>
      </nav>
      <div class="navauth" id="navauth"></div>
    </header>`;
  renderNavAuth();
  // The kit page marks itself active here; the dashboard sets its own active tab as it routes.
  if (document.body.dataset.page === "kits") {
    document.querySelector('.mainnav a[data-nav="kits"]')?.classList.add("active");
  }
}

function renderNavAuth() {
  const el = document.getElementById("navauth");
  if (!el) return;
  if (token()) {
    el.innerHTML = `<button class="btn ghost" id="navLogout">Log out</button>`;
    document.getElementById("navLogout").onclick = () => { clearToken(); location.reload(); };
  } else {
    el.innerHTML = `<a class="btn-discord" href="${API_BASE}/api/auth/login">Log in with Discord</a>`;
  }
}

renderTopNav();
