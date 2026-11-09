"""Laneh MCP server: lets Claude (or any MCP client) read a team's
workspaces and lay out projects and paths in Laneh Team.

It talks to the same server the app does, signed in as you (email + code),
so it can do exactly what you can: the server applies your role. Tasks it
makes are proposals by default (draft=True): they show under «پیشنهادها»
on the path until you approve them in the app.

Run:  python laneh_mcp.py            (stdio, for Claude Code / Claude Desktop)
Env:  LANEH_API   server (default: the Laneh server)
      LANEH_HOME  where the sign-in token is kept (default: ~/.laneh)
      LANEH_TZ    time zone for dates (default: Asia/Tehran)
"""
from __future__ import annotations

import json
import os
import re
import uuid
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

try:  # mcp 2.x
    from mcp.server.mcpserver import Context, MCPServer as FastMCP
    from mcp.server.mcpserver.exceptions import ToolError
except ImportError:  # mcp 1.x
    from mcp.server.fastmcp import Context, FastMCP
    from mcp.server.fastmcp.exceptions import ToolError

API = os.environ.get("LANEH_API", "https://37-32-38-170.sslip.io/api").rstrip("/")
HOME = Path(os.environ.get("LANEH_HOME", Path.home() / ".laneh"))
try:
    TZ = ZoneInfo(os.environ.get("LANEH_TZ", "Asia/Tehran"))
except Exception:  # no zone database (plain Windows Python): Iran has no DST
    TZ = timezone(timedelta(hours=3, minutes=30), "IRST")
STATE = HOME / "mcp.json"

mcp = FastMCP("laneh", instructions=(
    "Laneh Team planner. First sign_in(email) then confirm_code(code). "
    "Read the team with list_workspaces / list_members / list_projects / get_path, "
    "then lay work out with create_project and add_tasks. Tasks are proposals "
    "until approved in the app, unless draft=False. Day to day: my_tasks, "
    "team_status, update_task, complete_task, nudge. Assignees get the app's "
    "notices automatically."))


# ── server calls ────────────────────────────────────────────────────────

class ApiError(ToolError):
    """A message the client should see as the tool's answer."""


def _state() -> dict:
    try:
        return json.loads(STATE.read_text("utf-8"))
    except (OSError, ValueError):
        return {}


def _save(**kv) -> None:
    HOME.mkdir(parents=True, exist_ok=True)
    STATE.write_text(json.dumps({**_state(), **kv}), "utf-8")


def _call(method: str, path: str, body: dict | None = None,
          query: dict | None = None, auth: bool = True) -> dict:
    url = API + path + ("?" + urllib.parse.urlencode(query) if query else "")
    req = urllib.request.Request(
        url, method=method,
        data=json.dumps(body).encode() if body is not None else None)
    req.add_header("Content-Type", "application/json")
    if auth:
        tok = os.environ.get("LANEH_TOKEN") or _state().get("token")
        if not tok:
            raise ApiError("Not signed in: call sign_in(email) first.")
        req.add_header("Authorization", tok)
    try:
        with urllib.request.urlopen(req, timeout=25) as r:
            data = r.read()
            return json.loads(data) if data else {}
    except urllib.error.HTTPError as e:
        text = e.read().decode("utf-8", "replace")
        raise ApiError(f"{e.code}: {text[:300]}") from None
    except urllib.error.URLError as e:
        raise ApiError(f"Can't reach the Laneh server: {e.reason}") from None


def _all(collection: str, filt: str, expand: str = "", sort: str = "created") -> list[dict]:
    out, page = [], 1
    while True:
        r = _call("GET", f"/collections/{collection}/records",
                  query={"filter": filt, "perPage": 200, "page": page,
                         "skipTotal": 1, "expand": expand, "sort": sort})
        out += r["items"]
        if len(r["items"]) < 200:
            return out
        page += 1


def _me() -> str:
    uid = os.environ.get("LANEH_USER") or _state().get("user")
    if not uid:
        raise ApiError("Not signed in: call sign_in(email) first.")
    _still_connected()
    return uid


def _still_connected() -> None:
    """Every few minutes: mark this assistant as seen, and stop if the person
    disconnected it in the app («اتصال به هوش مصنوعی»)."""
    st = _state()
    conn = st.get("connection")
    if not conn or os.environ.get("LANEH_TOKEN"):
        return
    now = datetime.now(timezone.utc).timestamp()
    if now - st.get("seen_at", 0) < 300:
        return
    try:
        _call("PATCH", f"/collections/connections/records/{conn}", {"last_seen": _now()})
        _save(seen_at=now)
    except ApiError as e:
        if str(e).startswith("404"):
            _save(token=None, user=None, connection=None)
            raise ApiError("This assistant was disconnected in the Laneh app. "
                           "Sign in again with sign_in(email) to reconnect.") from None
        raise


# ── ids and dates, the way the app does them ───────────────────────────

def _remote_id(local_id: str) -> str:
    """The app's rule: first 15 lowercase letters/digits of the device uuid."""
    return re.sub(r"[^a-z0-9]", "", local_id.lower()).ljust(15, "0")[:15]


def _new_ids() -> tuple[str, str]:
    cid = str(uuid.uuid4())
    return _remote_id(cid), cid


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S.000Z")


def _date(d: str | None) -> str:
    """'2026-11-20' or '+3' (days from today) → local midnight, in UTC."""
    if not d:
        return ""
    today = datetime.now(TZ).date()
    if re.fullmatch(r"[+-]\d+", d):
        day = today + timedelta(days=int(d))
    else:
        day = datetime.strptime(d, "%Y-%m-%d").date()
    local = datetime(day.year, day.month, day.day, tzinfo=TZ)
    return local.astimezone(timezone.utc).strftime("%Y-%m-%d %H:%M:%S.000Z")


def _short(iso: str) -> str:
    if not iso:
        return ""
    return datetime.strptime(iso[:19], "%Y-%m-%d %H:%M:%S").replace(
        tzinfo=timezone.utc).astimezone(TZ).strftime("%Y-%m-%d")


def _meta() -> dict:
    return {"tz": int(datetime.now(TZ).utcoffset().total_seconds() // 60),
            "platform": "mcp", "version": "1.1"}


# ── sign in ────────────────────────────────────────────────────────────

@mcp.tool()
def sign_in(email: str) -> str:
    """Step 1: send a one-time code to this email (the same sign-in as the app)."""
    email = email.strip().lower()
    _call("POST", "/laneh/start", {"email": email}, auth=False)
    r = _call("POST", "/collections/users/request-otp", {"email": email}, auth=False)
    _save(otp_id=r["otpId"], email=email)
    return f"Code sent to {email}. Ask the person for it, then call confirm_code(code)."


def _client_name(ctx) -> str:
    try:
        info = ctx.session.client_params.client_info
        raw = (getattr(info, "title", None) or info.name or "").strip()
    except Exception:
        raw = ""
    known = {"claude-code": "Claude Code", "claude-ai": "Claude", "claude": "Claude",
             "codex": "Codex", "cursor": "Cursor"}
    return known.get(raw.lower(), raw or "AI assistant")[:80]


@mcp.tool()
def confirm_code(code: str, ctx: Context) -> str:
    """Step 2: finish signing in with the 6-digit code from the email."""
    fa = "۰۱۲۳۴۵۶۷۸۹"
    code = "".join(str(fa.index(c)) if c in fa else c for c in code.strip())
    st = _state()
    if not st.get("otp_id"):
        raise ApiError("Call sign_in(email) first.")
    r = _call("POST", "/collections/users/auth-with-otp",
              {"otpId": st["otp_id"], "password": code}, auth=False)
    uid = r["record"]["id"]
    _save(token=r["token"], user=uid, otp_id=None)
    # Show up in the app as connected; one row per assistant.
    client = _client_name(ctx)
    rows = _all("connections", f'user="{uid}" && client="{client}"')
    if rows:
        conn = rows[0]["id"]
        _call("PATCH", f"/collections/connections/records/{conn}", {"last_seen": _now()})
    else:
        conn = _call("POST", "/collections/connections/records",
                     {"user": uid, "client": client, "last_seen": _now()})["id"]
    _save(connection=conn, seen_at=datetime.now(timezone.utc).timestamp())
    return f"Signed in as {st['email']} (user {uid}), connected as {client}."


@mcp.tool()
def whoami() -> dict:
    """Who the server thinks you are, and your role in each workspace."""
    r = _call("POST", "/collections/users/auth-refresh", {})
    _save(token=r["token"], user=r["record"]["id"])
    u = r["record"]
    return {"id": u["id"], "email": u.get("email", ""), "name": u.get("name", ""),
            "handle": u.get("handle", ""), "workspaces": list_workspaces()}


# ── reading the team ───────────────────────────────────────────────────

@mcp.tool()
def list_workspaces() -> list[dict]:
    """Your workspaces, with your role in each (owner/admin lead; member; guest)."""
    me = _me()
    out = []
    for w in _all("workspaces", "id != ''", sort="created"):
        role = ""
        for m in _all("members", f'workspace="{w["id"]}" && user="{me}"'):
            role = m["role"]
        out.append({"id": w["id"], "name": w["name"], "my_role": role})
    return out


@mcp.tool()
def list_members(workspace: str) -> list[dict]:
    """Everyone in a workspace: id, name, @handle, job title, role, colour."""
    out = []
    for m in _all("members", f'workspace="{workspace}"', expand="user"):
        u = m.get("expand", {}).get("user", {}) or {}
        out.append({
            "user_id": m["user"],
            "name": (u.get("name") or "").strip(),
            "handle": u.get("handle") or "",
            "title": m.get("title") or "",
            "role": m["role"],
            "color": m.get("color") or 0,
        })
    return out


@mcp.tool()
def list_projects(workspace: str) -> list[dict]:
    """Projects («پروژه‌ها») of a workspace with goal, deadline and progress."""
    out = []
    for p in _all("projects", f'workspace="{workspace}" && deleted=false'):
        tasks = _all("tasks", f'project="{p["id"]}" && deleted=false')
        done = sum(1 for t in tasks if t["status"] == "done")
        out.append({
            "id": p["id"], "name": p["name"], "icon": p.get("icon") or "",
            "goal": p.get("goal") or "", "deadline": _short(p.get("deadline") or ""),
            "status": p.get("status"), "tasks": len(tasks), "done": done,
            "proposals": sum(1 for t in tasks if t.get("proposed")),
        })
    return out


def _stages(tasks: list[dict]) -> dict[str, int]:
    """Stage = longest chain of «after» links before a task, like the app."""
    by_id = {t["id"]: t for t in tasks}
    memo: dict[str, int] = {}

    def stage(tid: str, seen: frozenset = frozenset()) -> int:
        if tid in memo:
            return memo[tid]
        pre = [a for a in (by_id[tid].get("after") or []) if a in by_id and a not in seen]
        memo[tid] = 1 + max((stage(a, seen | {tid}) for a in pre), default=0)
        return memo[tid]

    return {t["id"]: stage(t["id"]) for t in tasks}


@mcp.tool()
def get_path(project: str) -> dict:
    """A project's «مسیر تا هدف»: tasks by stage, with who does each, what it
    waits for, due date, status and whether it's still a proposal."""
    p = _call("GET", f"/collections/projects/records/{project}")
    tasks = _all("tasks", f'project="{project}" && deleted=false')
    members = {m["user_id"]: m for m in list_members(p["workspace"])}
    stages = _stages(tasks)
    names = {t["id"]: t["title"] for t in tasks}

    def who(uid: str) -> str:
        m = members.get(uid)
        if not m:
            return ""
        # Name, then @handle, then their job title in the team, then role.
        return (m["name"] or ("@" + m["handle"] if m["handle"] else "")
                or m["title"] or m["role"])

    rows = sorted(tasks, key=lambda t: (stages[t["id"]], t.get("order") or 0))
    return {
        "project": {"id": p["id"], "name": p["name"], "goal": p.get("goal") or "",
                    "deadline": _short(p.get("deadline") or ""),
                    "strict_order": bool(p.get("strict_order"))},
        "stages": max(stages.values(), default=0),
        "tasks": [{
            "id": t["id"], "stage": stages[t["id"]], "title": t["title"],
            "status": t["status"], "proposed": bool(t.get("proposed")),
            "assignee": who(t.get("assignee") or ""),
            "assignee_id": t.get("assignee") or "",
            "after": [names.get(a, a) for a in (t.get("after") or [])],
            "after_ids": t.get("after") or [],
            "due": _short(t.get("due") or ""),
            "note": t.get("note") or "",
        } for t in rows],
    }


# ── writing ────────────────────────────────────────────────────────────

@mcp.tool()
def create_project(workspace: str, name: str, goal: str = "", deadline: str = "",
                   icon: str = "🚀", description: str = "",
                   strict_order: bool = False) -> dict:
    """Make a project. deadline: '2026-12-01' or '+30' (days from today).
    Only owners and admins can make projects."""
    rid, cid = _new_ids()
    r = _call("POST", "/collections/projects/records", {
        "id": rid, "client_id": cid, "workspace": workspace, "name": name[:120],
        "goal": goal[:300], "icon": icon[:8], "description": description[:2000],
        "deadline": _date(deadline), "start_date": _date("+0"),
        "status": "active", "strict_order": strict_order, "created_by": _me(),
        "client_created": _now(), "client_updated": _now(), "client_meta": _meta(),
    })
    return {"id": r["id"], "name": r["name"]}


def _resolve_member(members: list[dict], who: str | None) -> str:
    if not who:
        return ""
    w = who.strip().lstrip("@").lower()
    for m in members:
        if w in {m["user_id"], m["handle"].lower(), m["name"].lower()}:
            return m["user_id"]
    for m in members:
        if w and (w in m["name"].lower() or w == m["title"].lower()):
            return m["user_id"]
    raise ApiError(f"No one called «{who}» in this workspace. Use list_members.")


def _notify(ws: str, to: str, task: str, kind: str) -> bool:
    """Sends a notice the app shows on «امروز» and as a phone notification,
    the same ones the app sends: assigned («کار داد»), done («تموم شد»),
    waiting («منتظرتم»). The server allows one per hour per person, task and
    kind; a refused one is just skipped."""
    me = _me()
    if not to or to == me:
        return False
    try:
        _call("POST", "/collections/nudges/records",
              {"workspace": ws, "from": me, "to": to, "task": task, "kind": kind})
        return True
    except ApiError:
        return False


def _ids_in(project_tasks: list[dict], refs: list[str]) -> list[str]:
    """Task ids from ids or titles of tasks on the same path."""
    by = {t["id"]: t["id"] for t in project_tasks}
    by.update({t["title"].strip().lower(): t["id"] for t in project_tasks})
    out = []
    for a in refs:
        k = str(a).strip().lower()
        if k not in by:
            raise ApiError(f"No task «{a}» on this path. Use get_path.")
        out.append(by[k])
    return out


@mcp.tool()
def add_tasks(project: str, tasks: list[dict], draft: bool = True) -> dict:
    """Lay tasks out on a project's path. Each task: {key, title, after, assignee,
    due, note, flags}. `after` = keys of earlier tasks in this list, or titles/ids
    of tasks already on the path (a task waits for all of them; that makes the
    stages). `assignee` = a member's name, @handle or id. `due` = '2026-11-20'
    or '+5'. `flags` ⊂ [urgent, no_rush, long]. draft=True (default) makes them
    proposals the person approves in the app; draft=False puts them straight
    on the path."""
    p = _call("GET", f"/collections/projects/records/{project}")
    members = list_members(p["workspace"])
    existing = _all("tasks", f'project="{project}" && deleted=false')
    ids: dict[str, str] = {t["title"].strip().lower(): t["id"] for t in existing}
    ids.update({t["id"]: t["id"] for t in existing})
    made, me = [], _me()
    order = datetime.now().timestamp() * 1000
    for i, t in enumerate(tasks):
        key = str(t.get("key") or t["title"]).strip().lower()
        after = []
        for a in t.get("after") or []:
            k = str(a).strip().lower()
            if k not in ids:
                raise ApiError(f"Task «{t['title']}» waits for «{a}», which isn't "
                               "earlier in this list or already on the path.")
            after.append(ids[k])
        rid, cid = _new_ids()
        r = _call("POST", "/collections/tasks/records", {
            "id": rid, "client_id": cid, "workspace": p["workspace"], "project": project,
            "title": str(t["title"])[:300], "note": str(t.get("note") or "")[:5000],
            "bucket": "plan", "status": "todo", "after": after[:10],
            "assignee": _resolve_member(members, t.get("assignee")),
            "due": _date(t.get("due")), "flags": [f for f in (t.get("flags") or [])
                                                 if f in ("urgent", "no_rush", "long")],
            "proposed": draft, "order": order + i, "created_by": me,
            "client_created": _now(), "client_updated": _now(), "client_meta": _meta(),
        })
        ids[key] = r["id"]
        ids[str(t["title"]).strip().lower()] = r["id"]
        # A real task (not a proposal) given to someone: tell them, like the app.
        told = (not r.get("proposed") and
                _notify(p["workspace"], r.get("assignee") or "", r["id"], "assigned"))
        made.append({"key": t.get("key") or t["title"], "id": r["id"], "title": r["title"],
                     "proposed": bool(r.get("proposed")), "told_assignee": told})
    return {"created": made, "note": "Proposals: approve them in the app under "
            "«پیشنهادها» on the path (assignees hear about them once approved)."
            if draft else "On the path; assignees were told."}


@mcp.tool()
def assign_task(task: str, member: str | None) -> dict:
    """Give a task to someone (name, @handle or id), or to nobody with None."""
    t = _call("GET", f"/collections/tasks/records/{task}")
    uid = _resolve_member(list_members(t["workspace"]), member)
    r = _call("PATCH", f"/collections/tasks/records/{task}",
              {"assignee": uid, "client_updated": _now(), "client_meta": _meta()})
    told = (not r.get("proposed") and uid != (t.get("assignee") or "")
            and _notify(t["workspace"], uid, r["id"], "assigned"))
    return {"id": r["id"], "assignee": r.get("assignee") or "", "told_assignee": told}


@mcp.tool()
def approve(task_ids: list[str]) -> dict:
    """Approve proposals (owners and admins only): they join the path, and
    whoever they're given to hears about it."""
    done = []
    for tid in task_ids:
        r = _call("PATCH", f"/collections/tasks/records/{tid}",
                  {"proposed": False, "client_updated": _now(), "client_meta": _meta()})
        told = (not r.get("proposed") and
                _notify(r["workspace"], r.get("assignee") or "", r["id"], "assigned"))
        done.append({"id": r["id"], "proposed": bool(r.get("proposed")), "told_assignee": told})
    return {"approved": done}


@mcp.tool()
def remove_tasks(task_ids: list[str]) -> dict:
    """Take tasks off the path (soft delete, like the app's trash)."""
    for tid in task_ids:
        _call("PATCH", f"/collections/tasks/records/{tid}",
              {"deleted": True, "client_updated": _now(), "client_meta": _meta()})
    return {"removed": task_ids}


@mcp.tool()
def update_task(task: str, title: str | None = None, note: str | None = None,
                due: str | None = None, after: list[str] | None = None,
                flags: list[str] | None = None) -> dict:
    """Change a task. Only what you pass changes. due: '2026-11-20', '+3', or ''
    to clear. after: ids or titles of tasks on the same path it waits for
    (replaces the old list; [] = waits for nothing). flags ⊂ [urgent, no_rush, long]."""
    t = _call("GET", f"/collections/tasks/records/{task}")
    patch: dict = {"client_updated": _now(), "client_meta": _meta()}
    if title is not None:
        patch["title"] = title[:300]
    if note is not None:
        patch["note"] = note[:5000]
    if due is not None:
        patch["due"] = _date(due)
    if flags is not None:
        patch["flags"] = [f for f in flags if f in ("urgent", "no_rush", "long")]
    if after is not None:
        if not t.get("project"):
            raise ApiError("Only tasks on a project's path can wait for others.")
        path = _all("tasks", f'project="{t["project"]}" && deleted=false')
        ids = [i for i in _ids_in(path, after) if i != task]
        patch["after"] = ids[:10]
    r = _call("PATCH", f"/collections/tasks/records/{task}", patch)
    return {"id": r["id"], "title": r["title"], "due": _short(r.get("due") or ""),
            "after": r.get("after") or [], "flags": r.get("flags") or []}


@mcp.tool()
def complete_task(task_ids: list[str], done: bool = True) -> dict:
    """Tick tasks done (done=False opens them again). Like the app, whoever has
    a task that was waiting on one of these hears it's their turn («تموم شد»).
    Members can only tick their own tasks; the server checks."""
    out = []
    for tid in task_ids:
        r = _call("PATCH", f"/collections/tasks/records/{tid}", {
            "status": "done" if done else "todo", "done_at": _now() if done else "",
            "client_updated": _now(), "client_meta": _meta()})
        told = []
        if done and r.get("project"):
            for nxt in _all("tasks", f'project="{r["project"]}" && deleted=false'):
                if nxt["status"] == "done" or tid not in (nxt.get("after") or []):
                    continue
                who = nxt.get("assignee") or nxt.get("created_by") or ""
                if who not in told and _notify(r["workspace"], who, tid, "done"):
                    told.append(who)
        out.append({"id": r["id"], "status": r["status"], "told_next": len(told)})
    return {"tasks": out}


@mcp.tool()
def nudge(task: str, member: str | None = None) -> dict:
    """«منتظرتم»: tell someone you're waiting on a task. Defaults to whoever
    the task is given to. At most once an hour per person and task."""
    t = _call("GET", f"/collections/tasks/records/{task}")
    to = (_resolve_member(list_members(t["workspace"]), member) if member
          else t.get("assignee") or "")
    if not to:
        raise ApiError("This task isn't given to anyone; say who to nudge.")
    if to == _me():
        raise ApiError("That's you.")
    if not _notify(t["workspace"], to, task, "waiting"):
        raise ApiError("Not sent: they were already told in the last hour.")
    return {"sent_to": to, "task": t["title"]}


def _open_tasks(filt: str) -> list[dict]:
    return _all("tasks", f"deleted=false && proposed=false && status!=\"done\" && ({filt})")


@mcp.tool()
def my_tasks(workspace: str | None = None) -> dict:
    """What's on your plate: open tasks given to you (or made by you and given
    to no one), as late / today / ready (nothing left to wait for) / later.
    Pass a workspace id to look at one team only."""
    me = _me()
    scope = f' && workspace="{workspace}"' if workspace else ""
    mine = _open_tasks(f'(assignee="{me}" || (assignee="" && created_by="{me}")){scope}')
    projects = {p["id"]: p["name"] for p in _all(
        "projects", f'deleted=false{scope}')} if mine else {}
    waits = {}
    for pid in {t["project"] for t in mine if t.get("project")}:
        for t in _all("tasks", f'project="{pid}" && deleted=false'):
            waits[t["id"]] = t["status"]
    today = datetime.now(TZ).strftime("%Y-%m-%d")
    out: dict[str, list] = {"late": [], "today": [], "ready": [], "later": []}
    for t in sorted(mine, key=lambda t: (t.get("due") or "9999", t.get("order") or 0)):
        due, day = _short(t.get("due") or ""), _short(t.get("day") or "")
        blocked = any(waits.get(a, "done") != "done" for a in t.get("after") or [])
        row = {"id": t["id"], "title": t["title"], "due": due,
               "project": projects.get(t.get("project") or "", ""),
               "waiting_on_others": blocked}
        if due and due < today:
            out["late"].append(row)
        elif today in (due, day):
            out["today"].append(row)
        elif t.get("project") and not blocked:
            out["ready"].append(row)
        else:
            out["later"].append(row)
    return out


@mcp.tool()
def team_status(workspace: str) -> dict:
    """A quick report on a team: each project's progress and late tasks, what
    each person has open (and how much is late), and proposals waiting for
    approval. Good for «وضعیت تیم چطوره؟» or a weekly summary."""
    today = datetime.now(TZ).strftime("%Y-%m-%d")
    members = list_members(workspace)
    who = {m["user_id"]: m["name"] or ("@" + m["handle"] if m["handle"] else m["title"] or m["role"])
           for m in members}
    tasks = _all("tasks", f'workspace="{workspace}" && deleted=false')
    projects = []
    for p in _all("projects", f'workspace="{workspace}" && deleted=false && status!="archived"'):
        pt = [t for t in tasks if t.get("project") == p["id"] and not t.get("proposed")]
        late = [t for t in pt if t["status"] != "done" and t.get("due")
                and _short(t["due"]) < today]
        projects.append({
            "name": p["name"], "goal": p.get("goal") or "",
            "deadline": _short(p.get("deadline") or ""),
            "done": sum(t["status"] == "done" for t in pt), "tasks": len(pt),
            "late": [{"title": t["title"], "due": _short(t["due"]),
                      "who": who.get(t.get("assignee") or "", "")} for t in late],
        })
    people = []
    for m in members:
        open_ = [t for t in tasks if t.get("assignee") == m["user_id"]
                 and t["status"] != "done" and not t.get("proposed")]
        people.append({"name": who[m["user_id"]], "role": m["role"], "title": m["title"],
                       "open": len(open_),
                       "late": sum(1 for t in open_ if t.get("due") and _short(t["due"]) < today)})
    return {"today": today, "projects": projects, "people": people,
            "proposals_waiting": sum(1 for t in tasks if t.get("proposed")
                                     and t["status"] != "done")}


def install() -> None:
    """`python laneh_mcp.py --install`: register this file with Claude Code and
    the Claude app on this computer (each config is backed up once first)."""
    import glob
    import shutil
    import sys

    entry = {"command": sys.executable, "args": [str(Path(__file__).resolve())]}
    home = Path.home()
    appdata = Path(os.environ.get("APPDATA", home))
    local = Path(os.environ.get("LOCALAPPDATA", home))
    desktop = [Path(p) for p in glob.glob(str(local / "Claude-Data" / "*" / "claude_desktop_config.json"))]
    standard = {"win32": appdata / "Claude",
                "darwin": home / "Library" / "Application Support" / "Claude"
                }.get(sys.platform, home / ".config" / "Claude") / "claude_desktop_config.json"
    if standard.exists() or not desktop:
        desktop.append(standard)
    targets = [("Claude app", p) for p in desktop]
    if (home / ".claude.json").exists():
        targets.append(("Claude Code", home / ".claude.json"))
    for name, p in targets:
        p.parent.mkdir(parents=True, exist_ok=True)
        data = json.loads(p.read_text("utf-8") or "{}") if p.exists() else {}
        backup = p.with_name(p.name + ".bak-laneh")
        if p.exists() and not backup.exists():
            shutil.copy(p, backup)
        data.setdefault("mcpServers", {})["laneh"] = entry
        p.write_text(json.dumps(data, ensure_ascii=False, indent=2), "utf-8")
        print(f"✓ {name}: {p}")
    print("\nلانه وصل شد. اپ Claude رو کامل ببند و دوباره باز کن، بعد توی یه چت تازه بگو «توی لانه وارد شو».\n"
          "Laneh is connected. Quit and reopen Claude, then in a new chat say “sign in to Laneh”.")


if __name__ == "__main__":
    import sys
    if "--install" in sys.argv:
        install()
    else:
        mcp.run()
