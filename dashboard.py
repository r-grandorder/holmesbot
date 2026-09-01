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

import datetime as dt

from data import contract_game
from data.autobattle import battle_stats, CLASS_NAMES
from data.kits import validate_kit, EFFECT_TYPES, TARGETS, TRIGGERS
from data.custom_servants import validate_custom_servant, servant_to_def
from data.contract_game import summon_rates
from data.raids import validate_raid_def, active_phase, boss_class

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


def _require_mod(request: web.Request) -> int:
    """Like _require_uid, but also require the user to be in the dashboard_mod_ids allowlist."""
    uid = _require_uid(request)
    if uid not in request.app["bot"].config.dashboard_mod_ids:
        raise web.HTTPForbidden(text='{"error":"not authorized"}', content_type="application/json")
    return uid


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
    is_mod = uid in bot.config.dashboard_mod_ids
    return _json({
        "id": str(uid),
        "name": await _resolve_name(bot, guild, uid),
        "is_mod": is_mod,
        "can_edit_kits": is_mod and bot.kits is not None,
    })


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
    guild = bot.get_guild(gid)
    standings = await bot.wars.standings(gid)
    # Group current members under their faction slot so the page can list each team's roster.
    by_slot: dict[int, list] = {}
    for m in await bot.wars.roster(gid):
        by_slot.setdefault(m["slot"], []).append(
            {"name": await _resolve_name(bot, guild, m["user_id"]), "score": m["score"]}
        )
    return _json({
        "active": True,
        "name": await bot.wars.name(gid),
        "description": await bot.wars.description(gid),
        "ends_at": await bot.wars.ends_at(gid),
        "factions": [
            {"slot": f["slot"], "name": f["name"], "score": f["score"], "members": f["members"],
             "roster": by_slot.get(f["slot"], [])}
            for f in standings
        ],
    })


def _img_content_type(data: bytes) -> str:
    if data[:8] == b"\x89PNG\r\n\x1a\n": return "image/png"
    if data[:3] == b"\xff\xd8\xff": return "image/jpeg"
    if data[:6] in (b"GIF87a", b"GIF89a"): return "image/gif"
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP": return "image/webp"
    return "application/octet-stream"


async def wars_banner(request: web.Request) -> web.Response:
    """Serve the active war's banner image (a BLOB on the war row); 404 if none is set."""
    bot = request.app["bot"]
    data = await bot.wars.banner(_guild_id(bot))
    if not data:
        raise web.HTTPNotFound()
    data = bytes(data)
    return web.Response(body=data, content_type=_img_content_type(data),
                        headers={"Cache-Control": "no-cache"})


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


# --- kit editor (mod-only, write) ------------------------------------------------------------
def _sid_arg(request: web.Request) -> int:
    try:
        return int(request.match_info["sid"])
    except (KeyError, ValueError):
        raise web.HTTPBadRequest(text="bad servant id")


async def kit_get(request: web.Request) -> web.Response:
    """The effective kit for a servant (override if any, else the baked one) + the vocab the
    editor needs for its dropdowns. Mod-only, since it's the editor's data source."""
    bot = request.app["bot"]
    _require_mod(request)
    if bot.kits is None:
        raise web.HTTPNotFound(text='{"error":"autobattle disabled"}', content_type="application/json")
    sid = _sid_arg(request)
    override = await bot.kit_service.get_override(sid)
    sk = bot.kits.get(sid)
    servant = bot.servants.get(sid) if bot.servants else None
    if override is not None:
        kit = override
    elif sk is not None:
        kit = {"id": sid, **sk.to_dict()}
    else:
        kit = None
    return _json({
        "servant_id": sid,
        "servant_name": servant.name if servant else f"#{sid}",
        "class": servant.class_name if servant else None,
        "face": servant.face if servant else None,
        "has_override": override is not None,
        "kit": kit,
        "vocab": {"triggers": sorted(TRIGGERS), "targets": sorted(TARGETS),
                  "effect_types": sorted(EFFECT_TYPES)},
    })


async def kit_put(request: web.Request) -> web.Response:
    """Validate + save a kit override, then re-apply it live (next battle uses it). Mod-only."""
    bot = request.app["bot"]
    uid = _require_mod(request)
    if bot.kits is None:
        raise web.HTTPNotFound(text='{"error":"autobattle disabled"}', content_type="application/json")
    sid = _sid_arg(request)
    try:
        kit = await request.json()
    except Exception:
        raise web.HTTPBadRequest(text="invalid JSON body")
    if not isinstance(kit, dict):
        raise web.HTTPBadRequest(text="body must be a kit object")
    kit["id"] = sid  # the path is authoritative for the id
    servant = bot.servants.get(sid) if bot.servants else None
    if servant:  # keep the battle-log labels even if the client omitted them
        kit.setdefault("servant_name", servant.name)
        kit.setdefault("class_name", servant.class_name)
    errors = validate_kit(kit)
    if errors:
        return _json({"ok": False, "errors": errors}, status=400)
    await bot.kit_service.set_override(sid, kit, uid)
    await bot.reload_kits()
    await bot.kit_service.audit(uid, "kit_edit",
                               {"servant_id": sid, "name": kit.get("name"),
                                "effects": len(kit.get("effects", []))})
    return _json({"ok": True, "has_override": True})


async def kit_delete(request: web.Request) -> web.Response:
    """Revert a servant to its baked kit by dropping the override, then re-apply live. Mod-only."""
    bot = request.app["bot"]
    uid = _require_mod(request)
    if bot.kits is None:
        raise web.HTTPNotFound(text='{"error":"autobattle disabled"}', content_type="application/json")
    sid = _sid_arg(request)
    await bot.kit_service.delete_override(sid)
    await bot.reload_kits()
    await bot.kit_service.audit(uid, "kit_revert", {"servant_id": sid})
    return _json({"ok": True, "has_override": False})


async def kit_overrides_all(request: web.Request) -> web.Response:
    """Public: every live kit override, so the kit browser can overlay them on the baked kits.json
    and show everyone the true current kit (not just the mod editing it). Read-only game data."""
    bot = request.app["bot"]
    svc = bot.kit_service
    overrides = {str(sid): kit for sid, kit in await svc.all_overrides()} if svc else {}
    return _json({"overrides": overrides})


# --- custom servants: mod-only editor (name / art / class / summon weight) -------------------


def _custom_id_arg(request: web.Request) -> int:
    try:
        sid = int(request.match_info["id"])
    except (KeyError, ValueError):
        raise web.HTTPBadRequest(text="bad servant id")
    if sid >= 0:
        raise web.HTTPBadRequest(text="custom servant ids are negative")
    return sid


async def _custom_odds(bot) -> "dict[int, float]":
    """Per-unit pull chance for every custom unit currently in the pool, keyed by servant id.

    Goes through the same summon_rates() + restriction gate that /summonodds uses
    (cogs/contracts.py), so the editor's preview and the real pull odds can't drift: without
    `allow`, content-restricted servants would still be counted in the denominator here but
    not in an actual roll."""
    if bot.servants is None:
        return {}
    try:
        allow = await bot.restrictions.build_allow() if bot.restrictions else None
        rows, _total = summon_rates(bot.servants, allow=allow)
    except Exception:
        log.exception("could not compute custom summon odds")
        return {}
    # Each custom unit is its own single-member bucket, so its bucket pct IS its pull chance.
    by_name = {label: pct for kind, label, _w, pct, _c, _e in rows if kind == "custom"}
    return {s.id: by_name[s.name] for s in bot.servants.all() if s.name in by_name}


def _file_customs(bot, exclude: "set[int]") -> "list[tuple[int, dict]]":
    """Custom units that come from the baked data/custom_servants.json and have no DB row yet.
    They're live in the index but invisible to the table, so the editor has to read them off the
    index or a mod would see an empty list next to 31 working servants."""
    if bot.servants is None:
        return []
    return [
        (s.id, servant_to_def(s))
        for s in bot.servants.all()
        if s.custom and s.id not in exclude
    ]


async def custom_servants_list(request: web.Request) -> web.Response:
    """Mod-only: every custom unit the editor can manage -- DB-backed and file-authored alike --
    with live odds. `source` tells the UI which it is; editing a file one adopts it into the DB."""
    bot = request.app["bot"]
    _require_mod(request)
    odds = await _custom_odds(bot)
    rows = await bot.custom_servants.all()
    out = [
        {"id": sid, "def": defn, "enabled": enabled, "source": "db", "pct": odds.get(sid)}
        for sid, defn, enabled in rows
    ]
    # A file custom is in the index unconditionally, so it is always live.
    out += [
        {"id": sid, "def": defn, "enabled": True, "source": "file", "pct": odds.get(sid)}
        for sid, defn in _file_customs(bot, {r["id"] for r in out})
    ]
    out.sort(key=lambda r: r["id"], reverse=True)
    return _json({"servants": out})


async def custom_servant_get(request: web.Request) -> web.Response:
    """Mod-only: one definition plus the vocab and live odds the editor needs."""
    bot = request.app["bot"]
    _require_mod(request)
    sid = _custom_id_arg(request)
    got = await bot.custom_servants.get(sid)
    source = "db" if got else "none"
    if got:
        defn, enabled = got
    else:
        # No row: this may still be a file-authored custom that is live in the index. Render it
        # from there so it can be opened and edited (saving adopts it into the DB).
        live = bot.servants.get(sid) if bot.servants else None
        if live is not None and live.custom:
            defn, enabled, source = servant_to_def(live), True, "file"
        else:
            defn, enabled = None, False
    return _json({
        "id": sid,
        "def": defn,
        "enabled": enabled,
        "source": source,
        "pct": (await _custom_odds(bot)).get(sid),
        "vocab": {"classes": list(CLASS_NAMES)},
    })


async def custom_servant_create(request: web.Request) -> web.Response:
    """Mod-only: allocate a fresh negative id. The floor comes off the LIVE index, not just this
    table, so a new unit can't be handed an id already used by a JSON-authored custom."""
    bot = request.app["bot"]
    _require_mod(request)
    floor = min((s.id for s in bot.servants.all()), default=0) if bot.servants else 0
    sid = await bot.custom_servants.next_id(floor)
    return _json({"ok": True, "id": sid, "vocab": {"classes": list(CLASS_NAMES)}})


async def custom_servant_put(request: web.Request) -> web.Response:
    """Mod-only: validate + save a custom servant, then re-apply the index live."""
    bot = request.app["bot"]
    uid = _require_mod(request)
    sid = _custom_id_arg(request)
    try:
        body = await request.json()
    except Exception:
        raise web.HTTPBadRequest(text="invalid JSON body")
    defn = body.get("def") if isinstance(body, dict) else None
    if not isinstance(defn, dict):
        raise web.HTTPBadRequest(text="body.def (object) required")
    defn["id"] = sid  # the path is authoritative for the id
    errors = validate_custom_servant(defn, index=bot.servants)
    if errors:
        return _json({"ok": False, "errors": errors}, status=400)
    # Adopting a file-authored custom: it is already live, so its first DB row must be enabled
    # or saving an edit would silently pull it out of the summon pool.
    live = bot.servants.get(sid) if bot.servants else None
    adopting = await bot.custom_servants.get(sid) is None and live is not None and live.custom
    await bot.custom_servants.upsert(sid, defn, uid, enable_on_create=adopting)
    await bot.reload_servants()
    await bot.custom_servants.audit(uid, "custom_servant_edit",
                                    {"id": sid, "name": defn.get("name")})
    return _json({"ok": True, "id": sid, "pct": (await _custom_odds(bot)).get(sid)})


async def custom_servant_enable(request: web.Request) -> web.Response:
    """Mod-only: switch a unit into or out of the live summon pool."""
    bot = request.app["bot"]
    uid = _require_mod(request)
    sid = _custom_id_arg(request)
    try:
        body = await request.json()
    except Exception:
        body = {}
    on = bool(body.get("enabled", True)) if isinstance(body, dict) else True
    if await bot.custom_servants.get(sid) is None:
        # No row yet. If it is a file-authored custom, adopt it so it can be toggled at all --
        # otherwise there is nothing to enable.
        live = bot.servants.get(sid) if bot.servants else None
        if live is None or not live.custom:
            raise web.HTTPNotFound(text="no such custom servant")
        await bot.custom_servants.upsert(sid, servant_to_def(live), uid, enable_on_create=True)
    await bot.custom_servants.set_enabled(sid, on, uid)
    await bot.reload_servants()
    await bot.custom_servants.audit(uid, "custom_servant_enable", {"id": sid, "enabled": on})
    return _json({"ok": True, "enabled": on, "pct": (await _custom_odds(bot)).get(sid)})


async def custom_servant_delete(request: web.Request) -> web.Response:
    """Mod-only: drop a custom unit entirely, then re-apply the index live."""
    bot = request.app["bot"]
    uid = _require_mod(request)
    sid = _custom_id_arg(request)
    await bot.custom_servants.delete(sid)
    await bot.reload_servants()
    # If this row was SHADOWING a file-authored custom, the unit comes straight back from
    # data/custom_servants.json on reload. That's a revert, not a delete -- say which happened
    # so the mod isn't left wondering why it reappeared.
    still = bot.servants.get(sid) if bot.servants else None
    reverted = still is not None and still.custom
    await bot.custom_servants.audit(uid, "custom_servant_delete",
                                    {"id": sid, "reverted_to_file": reverted})
    return _json({"ok": True, "reverted": reverted})


# --- uploaded images (shared blob store; raid sprites and custom servant art) ----------------


async def image_upload(request: web.Request) -> web.Response:
    """Mod-only: upload an image (multipart) -> stored as a BLOB -> returns its id and the URL
    to reference it by. Backed by the same table raid sprites use."""
    bot = request.app["bot"]
    uid = _require_mod(request)
    reader = await request.multipart()
    field = await reader.next()
    if field is None:
        raise web.HTTPBadRequest(text="no file uploaded")
    data = await field.read(decode=False)
    if not data:
        raise web.HTTPBadRequest(text="empty file")
    if len(data) > 2_000_000:
        raise web.HTTPBadRequest(text="file too large (max 2MB)")
    if _img_content_type(data) == "application/octet-stream":
        raise web.HTTPBadRequest(text="not a recognised image (png, jpeg, gif or webp)")
    image_id = await bot.raids.store_sprite(data, uid)
    await bot.raids.audit(uid, "image_upload", {"id": image_id, "bytes": len(data)})
    base = bot.config.dashboard_api_base_url
    return _json({"ok": True, "id": image_id, "url": f"{base}/api/images/{image_id}"})


async def image_get(request: web.Request) -> web.Response:
    """Public: serve an uploaded image BLOB. Public because Discord fetches these URLs
    unauthenticated to render embeds."""
    bot = request.app["bot"]
    try:
        image_id = int(request.match_info["id"])
    except ValueError:
        raise web.HTTPBadRequest(text="bad image id")
    data = await bot.raids.get_sprite(image_id)
    if not data:
        raise web.HTTPNotFound()
    data = bytes(data)
    return web.Response(body=data, content_type=_img_content_type(data),
                        headers={"Cache-Control": "public, max-age=86400"})


# --- raids: staff config (mod-only) + public status/history ----------------------------------
async def raids_list(request: web.Request) -> web.Response:
    """Public: all raid definitions for the config list + client menus. `enabled` is the persistent
    /raidstart gate; `running` is whether a raid instance is actually live right now. These differ: a
    raid can end on its own (boss defeated / expiry) while the def stays enabled, so the UI badge
    should track `running`, not `enabled`."""
    bot = request.app["bot"]
    if bot.raids is None:
        return _json({"raids": []})
    active = await bot.raids.active(_guild_id(bot))
    running_def = active["def_name"] if active else None
    return _json({"raids": [
        {"name": n, "display_name": d.get("display_name", n), "enabled": e,
         "running": n == running_def, "boss_servant_id": d.get("boss_servant_id")}
        for n, d, e in await bot.raids.all_defs()
    ]})


async def raid_get(request: web.Request) -> web.Response:
    """Mod-only: one raid definition + the vocab the editor needs for phase-kit dropdowns."""
    bot = request.app["bot"]
    _require_mod(request)
    name = request.match_info["name"]
    got = await bot.raids.get_def(name)
    return _json({
        "name": name,
        "def": got[0] if got else None,
        "enabled": got[1] if got else False,
        "vocab": {"triggers": sorted(TRIGGERS), "targets": sorted(TARGETS),
                  "effect_types": sorted(EFFECT_TYPES), "classes": list(CLASS_NAMES)},
    })


async def raid_put(request: web.Request) -> web.Response:
    """Mod-only: validate + save a raid definition (does not start it)."""
    bot = request.app["bot"]
    uid = _require_mod(request)
    name = request.match_info["name"].strip()
    if not name:
        raise web.HTTPBadRequest(text="bad raid name")
    try:
        body = await request.json()
    except Exception:
        raise web.HTTPBadRequest(text="invalid JSON body")
    defn = body.get("def") if isinstance(body, dict) else None
    if not isinstance(defn, dict):
        raise web.HTTPBadRequest(text="body.def (object) required")
    errors = validate_raid_def(defn)
    if errors:
        return _json({"ok": False, "errors": errors}, status=400)
    await bot.raids.set_def(name, defn, uid)
    await bot.raids.audit(uid, "raid_def_edit", {"name": name})
    return _json({"ok": True})


async def raid_delete(request: web.Request) -> web.Response:
    bot = request.app["bot"]
    uid = _require_mod(request)
    name = request.match_info["name"]
    await bot.raids.delete_def(name)
    await bot.raids.audit(uid, "raid_def_delete", {"name": name})
    return _json({"ok": True})


async def raid_enable(request: web.Request) -> web.Response:
    """Mod-only: enable a raid (starts a live instance in the configured guild) or disable it (ends
    the active instance if it's this def)."""
    bot = request.app["bot"]
    uid = _require_mod(request)
    name = request.match_info["name"]
    try:
        on = bool((await request.json()).get("enabled", False))
    except Exception:
        raise web.HTTPBadRequest(text="invalid JSON body")
    got = await bot.raids.get_def(name)
    if not got:
        raise web.HTTPNotFound(text='{"error":"no such raid"}', content_type="application/json")
    defn = got[0]
    gid = _guild_id(bot)
    started = ended = False
    if on:
        errors = validate_raid_def(defn)
        if errors:
            return _json({"ok": False, "errors": errors}, status=400)
        await bot.raids.set_enabled(name, True, uid)
        expires = (dt.datetime.now(dt.timezone.utc)
                   + dt.timedelta(hours=int(defn["duration_hours"]))).strftime("%Y-%m-%d %H:%M:%S")
        iid = await bot.raids.start(gid, name, defn.get("display_name", name),
                                    int(defn.get("boss_servant_id") or 0), int(defn["total_hp"]), expires)
        started = iid is not None
    else:
        await bot.raids.set_enabled(name, False, uid)
        active = await bot.raids.active(gid)
        if active and active["def_name"] == name:
            await bot.raids.end(active["id"], "expired")
            ended = True
    await bot.raids.audit(uid, "raid_enable",
                          {"name": name, "enabled": on, "started": started, "ended": ended})
    return _json({"ok": True, "enabled": on, "started": started, "ended": ended})


async def raids_active(request: web.Request) -> web.Response:
    """Public: the live raid's HP/phase/time + damage leaderboard (for the dashboard status card)."""
    bot = request.app["bot"]
    if bot.raids is None:
        return _json({"active": False})
    gid = _guild_id(bot)
    inst = await bot.raids.active(gid)
    if not inst:
        return _json({"active": False})
    got = await bot.raids.get_def(inst["def_name"])
    defn = got[0] if got else {}
    phase = active_phase(defn, inst["current_hp"], inst["total_hp"]) if got else None
    boss = bot.servants.get(inst["boss_id"]) if bot.servants else None
    guild = bot.get_guild(gid)
    lb = await bot.raids.leaderboard(inst["id"], 10)
    return _json({
        "active": True,
        "name": inst.get("display_name"),
        "current_hp": inst["current_hp"],
        "total_hp": inst["total_hp"],
        "phase": (phase or {}).get("name"),
        # The class can differ per phase, so report the one in force right now.
        "class": boss_class(defn, phase) or (getattr(boss, "class_name", "") or None),
        "flavor": (phase or {}).get("flavor"),
        "expires_at": inst["expires_at"],
        "leaderboard": [
            {"rank": i, "name": await _resolve_name(bot, guild, r["user_id"]), "damage": r["damage"]}
            for i, r in enumerate(lb, 1)
        ],
    })


async def raids_history(request: web.Request) -> web.Response:
    """Public: past raids + their final leaderboards (the 'Past raids' view)."""
    bot = request.app["bot"]
    if bot.raids is None:
        return _json({"raids": []})
    gid = _guild_id(bot)
    guild = bot.get_guild(gid)
    out = []
    for r in await bot.raids.history(gid, _LEADERBOARD_LIMIT):
        lb = await bot.raids.leaderboard(r["id"], 10)
        out.append({
            "id": r["id"], "name": r["display_name"] or r["def_name"], "status": r["status"],
            "started_at": r["started_at"], "ended_at": r["ended_at"],
            "leaderboard": [
                {"rank": i, "name": await _resolve_name(bot, guild, x["user_id"]), "damage": x["damage"]}
                for i, x in enumerate(lb, 1)
            ],
        })
    return _json({"raids": out})


async def raid_sprite_upload(request: web.Request) -> web.Response:
    """Mod-only: upload a boss/phase sprite (multipart) -> stored as a BLOB -> returns its id."""
    bot = request.app["bot"]
    uid = _require_mod(request)
    reader = await request.multipart()
    field = await reader.next()
    if field is None:
        raise web.HTTPBadRequest(text="no file uploaded")
    data = await field.read(decode=False)
    if not data:
        raise web.HTTPBadRequest(text="empty file")
    if len(data) > 2_000_000:
        raise web.HTTPBadRequest(text="file too large (max 2MB)")
    sprite_id = await bot.raids.store_sprite(data, uid)
    await bot.raids.audit(uid, "raid_sprite_upload", {"id": sprite_id, "bytes": len(data)})
    return _json({"ok": True, "id": sprite_id})


async def raid_sprite_get(request: web.Request) -> web.Response:
    """Public: serve an uploaded sprite BLOB (referenced by phase/boss sprite_id in embeds + the site)."""
    bot = request.app["bot"]
    try:
        sprite_id = int(request.match_info["id"])
    except ValueError:
        raise web.HTTPBadRequest(text="bad sprite id")
    data = await bot.raids.get_sprite(sprite_id)
    if not data:
        raise web.HTTPNotFound()
    data = bytes(data)
    return web.Response(body=data, content_type=_img_content_type(data),
                        headers={"Cache-Control": "public, max-age=86400"})


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
        resp.headers["Access-Control-Allow-Methods"] = "GET, POST, PUT, DELETE, OPTIONS"
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
    r.add_get("/api/wars/banner", wars_banner)
    r.add_get("/api/wars/history", wars_history)
    r.add_get("/api/wars/{war_id}", wars_detail)
    r.add_get("/api/kits/overrides", kit_overrides_all)  # before {sid} so it isn't caught as an id
    r.add_get("/api/kits/{sid}", kit_get)
    r.add_put("/api/kits/{sid}", kit_put)
    r.add_delete("/api/kits/{sid}", kit_delete)
    # Raids: static/collection paths BEFORE the {name} catch-all so they aren't read as a name.
    r.add_get("/api/images/{id}", image_get)
    r.add_post("/api/images", image_upload)
    # "custom" is a fixed segment so it can't be read as an {id}
    r.add_get("/api/servants/custom", custom_servants_list)
    r.add_post("/api/servants/custom", custom_servant_create)
    r.add_get("/api/servants/custom/{id}", custom_servant_get)
    r.add_put("/api/servants/custom/{id}", custom_servant_put)
    r.add_post("/api/servants/custom/{id}/enable", custom_servant_enable)
    r.add_delete("/api/servants/custom/{id}", custom_servant_delete)
    r.add_get("/api/raids", raids_list)
    r.add_get("/api/raids/active", raids_active)
    r.add_get("/api/raids/history", raids_history)
    r.add_get("/api/raids/sprite/{id}", raid_sprite_get)
    r.add_post("/api/raids/sprite", raid_sprite_upload)
    r.add_post("/api/raids/{name}/enable", raid_enable)
    r.add_get("/api/raids/{name}", raid_get)
    r.add_put("/api/raids/{name}", raid_put)
    r.add_delete("/api/raids/{name}", raid_delete)
    # OPTIONS preflight for every /api/* path (CORS middleware fills in the response).
    r.add_route("OPTIONS", "/api/{tail:.*}", lambda req: web.Response(status=204))
    log.info("dashboard API mounted (guild %d, frontend %s)", _guild_id(bot),
             bot.config.dashboard_frontend_url)
