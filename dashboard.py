"""Read-only web dashboard API.

Discord-OAuth login issues a signed bearer token (HMAC, stdlib only -- no session store, no extra
dependency); the SPA keeps it in localStorage and sends it as `Authorization: Bearer <token>`. The
JSON endpoints reuse the service layer to show a player their own inventory / servants / war stats,
plus public leaderboards and war history. Everything is READ-ONLY -- the bot stays the only way to
change anything.

Mounted on the same in-process aiohttp app as /health (see bot._start_health_server), so it shares
the one serialized SQLite connection. Ships dark: bot.py only calls setup_dashboard() when
config.dashboard_enabled. Exposure is via a Cloudflare Tunnel to :8080 (see DEPLOY.md); the SPA on
Cloudflare Pages is a different origin, so a small CORS layer allows exactly the configured frontend.

Bearer-in-localStorage is chosen deliberately: this is a low-stakes read-only surface on *.pages.dev
(no shared parent domain for cookies), so a token avoids third-party-cookie breakage. If a custom
domain is added later, this can be swapped for a same-site cookie without touching the endpoints.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import logging
import time
import urllib.parse

from aiohttp import web

from data import contract_game
from data.autobattle import battle_stats

log = logging.getLogger("holmesbot.dashboard")

DISCORD_API = "https://discord.com/api"
SESSION_TTL = 7 * 24 * 3600        # bearer token lifetime (seconds)
STATE_TTL = 600                    # OAuth CSRF-state lifetime (seconds)
_LEADERBOARD_LIMIT = 25


# --- signed tokens (stateless sessions + OAuth state) -----------------------------------------
def _b64e(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode()


def _b64d(s: str) -> bytes:
    return base64.urlsafe_b64decode(s + "=" * (-len(s) % 4))


def _sign(payload: dict, secret: str) -> str:
    body = _b64e(json.dumps(payload, separators=(",", ":")).encode())
    sig = _b64e(hmac.new(secret.encode(), body.encode(), hashlib.sha256).digest())
    return f"{body}.{sig}"


def _verify(token: str, secret: str) -> "dict | None":
    """Return the payload if the signature is valid and not expired, else None."""
    try:
        body, sig = token.split(".", 1)
    except (ValueError, AttributeError):
        return None
    expected = _b64e(hmac.new(secret.encode(), body.encode(), hashlib.sha256).digest())
    if not hmac.compare_digest(sig, expected):
        return None
    try:
        payload = json.loads(_b64d(body))
    except (ValueError, json.JSONDecodeError):
        return None
    if float(payload.get("exp", 0)) < time.time():
        return None
    return payload


# --- helpers ---------------------------------------------------------------------------------
def _json(data, status: int = 200) -> web.Response:
    return web.json_response(data, status=status)


def _guild_id(bot) -> int:
    cfg = bot.config
    return cfg.dashboard_guild_id or (cfg.guild_ids[0] if cfg.guild_ids else 0)


def _require_uid(request: web.Request) -> int:
    """Extract + verify the bearer token, returning the Discord user id. Raises 401 otherwise."""
    auth = request.headers.get("Authorization", "")
    token = auth[7:].strip() if auth.lower().startswith("bearer ") else ""
    payload = _verify(token, request.app["dashboard_secret"]) if token else None
    if not payload or "uid" not in payload:
        raise web.HTTPUnauthorized(text='{"error":"not authenticated"}',
                                   content_type="application/json")
    return int(payload["uid"])


async def _resolve_name(bot, guild, uid: int) -> str:
    """Best-effort display name for a user id. Cache -> guild member -> global user -> fetch.
    Memoized on the bot so a leaderboard doesn't refetch the same users every request."""
    cache = bot.__dict__.setdefault("_dash_names", {})
    if uid in cache:
        return cache[uid]
    name = None
    m = guild.get_member(uid) if guild else None
    if m:
        name = m.display_name
    else:
        u = bot.get_user(uid)
        if u:
            name = u.global_name or u.name
        else:
            try:
                u = await bot.fetch_user(uid)
                name = u.global_name or u.name
            except Exception:  # user deleted / not fetchable
                name = f"User {uid}"
    cache[uid] = name
    return name


def _servant_dto(bot, servant_id: int, level: int, grails_used: int) -> dict:
    s = bot.servants.get(servant_id) if bot.servants else None
    cap = contract_game.level_cap(grails_used)
    atk = hp = None
    if s is not None:
        atk, hp = battle_stats(s, level)
    return {
        "servant_id": servant_id,
        "name": s.name if s else f"#{servant_id}",
        "class": (s.class_name if s else None),
        "rarity": (s.rarity if s else None),
        "face": (s.face if s else None),
        "art": (s.art.get("0") if s and s.art else None),
        "level": level,
        "cap": cap,
        "grails_used": grails_used,
        "atk": atk,
        "hp": hp,
    }


# --- OAuth -----------------------------------------------------------------------------------
def _redirect_uri(bot) -> str:
    return f"{bot.config.dashboard_api_base_url}/api/auth/callback"


async def auth_login(request: web.Request) -> web.Response:
    bot = request.app["bot"]
    state = _sign({"exp": time.time() + STATE_TTL}, request.app["dashboard_secret"])
    params = urllib.parse.urlencode({
        "client_id": str(bot.config.application_id),
        "redirect_uri": _redirect_uri(bot),
        "response_type": "code",
        "scope": "identify",
        "state": state,
    })
    raise web.HTTPFound(f"{DISCORD_API}/oauth2/authorize?{params}")


async def auth_callback(request: web.Request) -> web.Response:
    bot = request.app["bot"]
    secret = request.app["dashboard_secret"]
    code = request.query.get("code", "")
    state = request.query.get("state", "")
    if not code or not _verify(state, secret):
        raise web.HTTPBadRequest(text="invalid oauth state")
    data = {
        "client_id": str(bot.config.application_id),
        "client_secret": bot.config.client_secret,
        "grant_type": "authorization_code",
        "code": code,
        "redirect_uri": _redirect_uri(bot),
    }
    async with bot.http_session.post(f"{DISCORD_API}/oauth2/token", data=data) as resp:
        if resp.status != 200:
            log.warning("oauth token exchange failed: %s", resp.status)
            raise web.HTTPBadGateway(text="oauth token exchange failed")
        tok = await resp.json()
    async with bot.http_session.get(
        f"{DISCORD_API}/users/@me",
        headers={"Authorization": f"Bearer {tok['access_token']}"},
    ) as resp:
        if resp.status != 200:
            raise web.HTTPBadGateway(text="failed to fetch discord user")
        me = await resp.json()
    uid = int(me["id"])
    name = me.get("global_name") or me.get("username") or f"User {uid}"
    bot.__dict__.setdefault("_dash_names", {})[uid] = name
    session = _sign({"uid": uid, "exp": time.time() + SESSION_TTL}, secret)
    # Hand the token back to the SPA in the URL fragment (never sent to a server, not logged).
    raise web.HTTPFound(f"{bot.config.dashboard_frontend_url}/dashboard.html#token={session}")


# --- authed (own data) -----------------------------------------------------------------------
async def me(request: web.Request) -> web.Response:
    bot = request.app["bot"]
    uid = _require_uid(request)
    guild = bot.get_guild(_guild_id(bot))
    return _json({"id": str(uid), "name": await _resolve_name(bot, guild, uid)})


async def me_inventory(request: web.Request) -> web.Response:
    bot = request.app["bot"]
    uid = _require_uid(request)
    gid = _guild_id(bot)
    embers, hellfire = await bot.contracts.xp_items(gid, uid)
    return _json({
        "qp": await bot.scoring.get_balance(gid, uid),
        "grails": await bot.contracts.grail_balance(gid, uid),
        "summon_tickets": await bot.contracts.summon_tickets(gid, uid),
        "embers": embers,
        "hellfire": hellfire,
    })


async def me_servants(request: web.Request) -> web.Response:
    bot = request.app["bot"]
    uid = _require_uid(request)
    rows = await bot.contracts.owned(_guild_id(bot), uid)
    out = []
    for r in rows:
        dto = _servant_dto(bot, r["servant_id"], r["level"], r["grails_used"])
        dto["active"] = bool(r["active"])
        dto["acquired_at"] = r["created_at"]
        out.append(dto)
    return _json({"servants": out})


async def me_war(request: web.Request) -> web.Response:
    bot = request.app["bot"]
    uid = _require_uid(request)
    row = await bot.wars.member(_guild_id(bot), uid)
    if row is None:
        return _json({"in_war": False})
    return _json({"in_war": True, "faction": row["name"], "slot": row["slot"], "score": row["score"]})


# --- public (no auth) ------------------------------------------------------------------------
async def leaderboard_servants(request: web.Request) -> web.Response:
    bot = request.app["bot"]
    gid = _guild_id(bot)
    guild = bot.get_guild(gid)
    rows = (await bot.contracts.board(gid))[:_LEADERBOARD_LIMIT]
    out = []
    for rank, r in enumerate(rows, 1):
        dto = _servant_dto(bot, r["servant_id"], r["level"], r["grails_used"])
        dto["rank"] = rank
        dto["owner"] = await _resolve_name(bot, guild, r["user_id"])
        out.append(dto)
    return _json({"leaderboard": out})


async def leaderboard_qp(request: web.Request) -> web.Response:
    bot = request.app["bot"]
    gid = _guild_id(bot)
    guild = bot.get_guild(gid)
    rows = await bot.scoring.leaderboard(gid, _LEADERBOARD_LIMIT)
    out = [
        {
            "rank": rank,
            "name": await _resolve_name(bot, guild, r["user_id"]),
            "points": r["points"],
            "wins": r["wins"],
        }
        for rank, r in enumerate(rows, 1)
    ]
    return _json({"leaderboard": out})


async def wars_active(request: web.Request) -> web.Response:
    bot = request.app["bot"]
    gid = _guild_id(bot)
    if not await bot.wars.active(gid):
        return _json({"active": False})
    standings = await bot.wars.standings(gid)
    return _json({
        "active": True,
        "name": await bot.wars.name(gid),
        "description": await bot.wars.description(gid),
        "ends_at": await bot.wars.ends_at(gid),
        "factions": [
            {"slot": f["slot"], "name": f["name"], "score": f["score"], "members": f["members"]}
            for f in standings
        ],
    })


async def wars_history(request: web.Request) -> web.Response:
    bot = request.app["bot"]
    rows = await bot.wars.history(_guild_id(bot), _LEADERBOARD_LIMIT)
    return _json({"wars": [
        {
            "id": r["id"],
            "name": r["name"],
            "description": r["description"],
            "started_at": r["started_at"],
            "ended_at": r["ended_at"],
            "winner_slot": r["winner_slot"],
        }
        for r in rows
    ]})


async def wars_detail(request: web.Request) -> web.Response:
    bot = request.app["bot"]
    gid = _guild_id(bot)
    guild = bot.get_guild(gid)
    try:
        war_id = int(request.match_info["war_id"])
    except (KeyError, ValueError):
        raise web.HTTPBadRequest(text="bad war id")
    factions = await bot.wars.history_factions(war_id)
    members = await bot.wars.history_members(war_id)
    roster: dict[int, list] = {}
    for m in members:
        roster.setdefault(m["slot"], []).append(
            {"name": await _resolve_name(bot, guild, m["user_id"]), "score": m["score"]}
        )
    return _json({
        "war_id": war_id,
        "factions": [
            {"slot": f["slot"], "name": f["name"], "score": f["score"],
             "members": roster.get(f["slot"], [])}
            for f in factions
        ],
    })


# --- CORS + mounting -------------------------------------------------------------------------
def _cors_middleware(origin: str):
    @web.middleware
    async def cors(request: web.Request, handler):
        if request.method == "OPTIONS":
            resp = web.Response(status=204)
        else:
            # Catch HTTP errors (e.g. 401) and return them WITH CORS headers, else the browser
            # blocks the response cross-origin and the SPA can't see the status code.
            try:
                resp = await handler(request)
            except web.HTTPException as ex:
                resp = ex
        resp.headers["Access-Control-Allow-Origin"] = origin
        resp.headers["Access-Control-Allow-Methods"] = "GET, OPTIONS"
        resp.headers["Access-Control-Allow-Headers"] = "Authorization, Content-Type"
        resp.headers["Vary"] = "Origin"
        return resp

    return cors


def setup_dashboard(app: web.Application, bot) -> None:
    """Register the dashboard routes + CORS on an existing aiohttp app. Call before AppRunner setup."""
    app["bot"] = bot
    app["dashboard_secret"] = bot.config.dashboard_session_secret
    app.middlewares.append(_cors_middleware(bot.config.dashboard_frontend_url))
    r = app.router
    r.add_get("/api/auth/login", auth_login)
    r.add_get("/api/auth/callback", auth_callback)
    r.add_get("/api/me", me)
    r.add_get("/api/me/inventory", me_inventory)
    r.add_get("/api/me/servants", me_servants)
    r.add_get("/api/me/war", me_war)
    r.add_get("/api/leaderboard/servants", leaderboard_servants)
    r.add_get("/api/leaderboard/qp", leaderboard_qp)
    r.add_get("/api/wars/active", wars_active)
    r.add_get("/api/wars/history", wars_history)
    r.add_get("/api/wars/{war_id}", wars_detail)
    # OPTIONS preflight for every /api/* path (CORS middleware fills in the response).
    r.add_route("OPTIONS", "/api/{tail:.*}", lambda req: web.Response(status=204))
    log.info("dashboard API mounted (guild %d, frontend %s)", _guild_id(bot),
             bot.config.dashboard_frontend_url)
