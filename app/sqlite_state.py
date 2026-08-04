"""SQLite reference implementation of the authenticated durable state API."""

from __future__ import annotations

import json
import os
import secrets
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

DB_PATH = Path(os.getenv("STATE_DB_PATH", "/data/snorse.db"))
TOKEN = os.getenv("STATE_API_TOKEN", "")
MAX_GROUPS = int(os.getenv("MAX_ACTIVE_GROUPS", "1000000"))
MAX_PERSONAL = int(os.getenv("MAX_PERSONAL_USERS", "1000000"))
MODE = os.getenv("ADMISSION_MODE", "auto").casefold()
REPORT_STALE_SECONDS = 180
HEALTHY_REOPEN_SECONDS = 30 * 60


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


@contextmanager
def database():
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    db = sqlite3.connect(DB_PATH, timeout=30, isolation_level=None)
    db.row_factory = sqlite3.Row
    db.execute("PRAGMA journal_mode=WAL")
    db.executescript("""
      CREATE TABLE IF NOT EXISTS scopes (
        recipient TEXT PRIMARY KEY, payload TEXT NOT NULL, scope_type TEXT NOT NULL,
        admission_status TEXT NOT NULL, admitted_at TEXT, waitlisted_at TEXT,
        support_last_sent_at TEXT, waitlist_last_replied_at TEXT, active INTEGER NOT NULL
      );
      CREATE INDEX IF NOT EXISTS scopes_queue ON scopes(scope_type, admission_status, waitlisted_at);
      CREATE TABLE IF NOT EXISTS reminders (
        recipient TEXT NOT NULL, id TEXT NOT NULL, payload TEXT NOT NULL,
        version INTEGER NOT NULL, claimed_until TEXT, PRIMARY KEY(recipient,id)
      );
      CREATE TABLE IF NOT EXISTS capacity (id INTEGER PRIMARY KEY CHECK(id=1), payload TEXT NOT NULL);
    """)
    try:
        yield db
    finally:
        db.close()


def healthy(report: dict | None) -> tuple[bool, str]:
    if MODE == "closed": return False, "admissions are manually closed"
    if not report: return False, "capacity report is missing"
    try:
        age = datetime.now(timezone.utc) - datetime.fromisoformat(report["timestamp"].replace("Z", "+00:00"))
    except (KeyError, ValueError): return False, "capacity report is invalid"
    if age.total_seconds() > REPORT_STALE_SECONDS: return False, "capacity report is stale"
    if not report.get("signalApiHealthy") or not report.get("listenerHealthy"):
        return False, "a bot service is unhealthy"
    if float(report.get("ramPercent", 100)) >= 70: return False, "RAM is at least 70%"
    if float(report.get("diskPercent", 100)) >= 75: return False, "disk is at least 75%"
    since = report.get("healthySince")
    if not since: return False, "waiting for 30 continuous healthy minutes"
    duration = datetime.now(timezone.utc) - datetime.fromisoformat(since.replace("Z", "+00:00"))
    if duration.total_seconds() < HEALTHY_REOPEN_SECONDS: return False, "waiting for 30 continuous healthy minutes"
    return True, "healthy"


def scope_payload(row) -> dict:
    value = json.loads(row["payload"])
    value.update(admissionStatus=row["admission_status"], admittedAt=row["admitted_at"],
                 waitlistedAt=row["waitlisted_at"], supportLastSentAt=row["support_last_sent_at"],
                 waitlistLastRepliedAt=row["waitlist_last_replied_at"], active=bool(row["active"]))
    return value


def counts(db) -> dict:
    result = {"activeGroups": 0, "waitlistedGroups": 0, "activePersonalUsers": 0, "waitlistedPersonalUsers": 0}
    for row in db.execute("SELECT scope_type,admission_status,count(*) n FROM scopes GROUP BY 1,2"):
        key = (row["scope_type"], row["admission_status"])
        names = {("group","active"):"activeGroups", ("group","waitlisted"):"waitlistedGroups",
                 ("personal","active"):"activePersonalUsers", ("personal","waitlisted"):"waitlistedPersonalUsers"}
        if key in names: result[names[key]] = row["n"]
    return result


def admit_scope(db, item: dict, report: dict | None, grandfather=False) -> tuple[dict, bool]:
    recipient, kind = item["groupRecipient"], item["scopeType"]
    prior = db.execute("SELECT * FROM scopes WHERE recipient=?", (recipient,)).fetchone()
    if prior:
        value = scope_payload(prior)
        value["groupName"] = item.get("groupName", value.get("groupName"))
        db.execute("UPDATE scopes SET payload=?,active=1 WHERE recipient=?", (json.dumps(value),recipient))
        return value, False
    cap = MAX_GROUPS if kind == "group" else MAX_PERSONAL
    active = db.execute("SELECT count(*) FROM scopes WHERE scope_type=? AND admission_status='active'", (kind,)).fetchone()[0]
    ok, _ = healthy(report)
    status = "active" if grandfather or (ok and active < cap) else "waitlisted"
    stamp = now_iso()
    payload = {"groupRecipient": recipient, "groupName": item.get("groupName", "personal"), "scopeType": kind, "calendarIds": []}
    db.execute("INSERT INTO scopes VALUES(?,?,?,?,?,?,?,?,?)", (recipient,json.dumps(payload),kind,status,
               stamp if status == "active" else None, stamp if status == "waitlisted" else None,None,None,1))
    return {**payload, "admissionStatus":status, "admittedAt":stamp if status == "active" else None,
            "waitlistedAt":stamp if status == "waitlisted" else None, "active":True}, True


def activate_waitlist(db, report) -> list[dict]:
    ok, _ = healthy(report)
    if not ok: return []
    activated=[]
    for kind, cap in (("group",MAX_GROUPS),("personal",MAX_PERSONAL)):
        active=db.execute("SELECT count(*) FROM scopes WHERE scope_type=? AND admission_status='active'",(kind,)).fetchone()[0]
        rows=db.execute("SELECT * FROM scopes WHERE scope_type=? AND admission_status='waitlisted' AND active=1 ORDER BY waitlisted_at,recipient LIMIT ?",(kind,max(0,cap-active))).fetchall()
        for row in rows:
            stamp=now_iso(); payload=json.loads(row["payload"]); payload["activationPending"]=True
            db.execute("UPDATE scopes SET admission_status='active',admitted_at=?,payload=? WHERE recipient=?",(stamp,json.dumps(payload),row["recipient"]))
            value=scope_payload(row); value.update(admissionStatus="active",admittedAt=stamp,activationPending=True); activated.append(value)
    return activated


class Handler(BaseHTTPRequestHandler):
    def send_json(self, status, body, public=False):
        data=json.dumps(body).encode(); self.send_response(status)
        self.send_header("Content-Type","application/json; charset=utf-8"); self.send_header("Cache-Control","no-store")
        if public and self.headers.get("Origin") == "https://snorse.com": self.send_header("Access-Control-Allow-Origin","https://snorse.com")
        self.send_header("Content-Length",str(len(data))); self.end_headers(); self.wfile.write(data)
    def body(self):
        return json.loads(self.rfile.read(int(self.headers.get("Content-Length","0"))) or b"{}")
    def authorized(self):
        supplied=self.headers.get("X-Snorse-Token","") or self.headers.get("X-Bot-Token","")
        return bool(TOKEN) and secrets.compare_digest(supplied,TOKEN)
    def route(self):
        method,parsed=self.command,urlsplit(self.path); path=parsed.path; query=parse_qs(parsed.query)
        with database() as db:
            report_row=db.execute("SELECT payload FROM capacity WHERE id=1").fetchone(); report=json.loads(report_row[0]) if report_row else None
            if method=="GET" and path=="/api/public/capacity":
                ok,cause=healthy(report); c=counts(db)
                return self.send_json(200,{"status":"open" if ok else "closed","cause":cause,**c,"caps":{"groups":MAX_GROUPS,"personalUsers":MAX_PERSONAL},"currentPlan":"AWS Lightsail 512 MiB","pricesAsOf":"2026-08-04","updatedAt":report.get("timestamp") if report else None},True)
            if not self.authorized(): return self.send_json(401,{"error":"authentication required!"})
            if method=="POST" and path=="/api/capacity/report":
                value=self.body(); previous=report
                was_healthy,_=healthy(previous)
                raw_good=bool(value.get("signalApiHealthy") and value.get("listenerHealthy") and float(value.get("ramPercent",100))<70 and float(value.get("diskPercent",100))<75)
                if raw_good: value["healthySince"]=(previous or {}).get("healthySince") or value.get("timestamp") or now_iso()
                else: value["healthySince"]=None
                db.execute("INSERT INTO capacity VALUES(1,?) ON CONFLICT(id) DO UPDATE SET payload=excluded.payload",(json.dumps(value),))
                newly=activate_waitlist(db,value); is_healthy,cause=healthy(value)
                return self.send_json(200,{"accepted":True,"admissionsOpen":is_healthy,"cause":cause,"transition": "reopened" if is_healthy and not was_healthy else "closed" if was_healthy and not is_healthy else None,"newlyActivated":newly,**counts(db)})
            if method=="GET" and path=="/api/scopes": return self.send_json(200,{"scopes":[scope_payload(r) for r in db.execute("SELECT * FROM scopes")]})
            if method=="POST" and path=="/api/scopes/sync":
                value=self.body(); admitted=[]; waitlisted=[]; new=[]
                db.execute("BEGIN IMMEDIATE")
                # First deployment grandfathers all groups only when the table is empty.
                grandfather=db.execute("SELECT count(*) FROM scopes").fetchone()[0]==0
                seen=set()
                for group in value.get("groups",[]):
                    item={**group,"scopeType":"group"}; scope,created=admit_scope(db,item,report,grandfather); seen.add(item["groupRecipient"])
                    (admitted if scope["admissionStatus"]=="active" else waitlisted).append(scope)
                    if created: new.append(scope)
                db.execute("UPDATE scopes SET active=0,admission_status='inactive' WHERE scope_type='group' AND recipient NOT IN (%s)" % (",".join("?"*len(seen)) or "''"),tuple(seen))
                activated=activate_waitlist(db,report)
                pending=[scope_payload(r) for r in db.execute("SELECT * FROM scopes WHERE json_extract(payload,'$.activationPending')=1")]
                for item in pending:
                    item["activationPending"]=False
                    db.execute("UPDATE scopes SET payload=? WHERE recipient=?",(json.dumps(item),item["groupRecipient"]))
                return self.send_json(200,{"scopes":admitted+waitlisted,"newGroups":new,"admitted":admitted,"waitlisted":waitlisted,"newlyActivated":pending or activated})
            if method=="POST" and path=="/api/admission/personal":
                db.execute("BEGIN IMMEDIATE"); scope,_=admit_scope(db,{**self.body(),"scopeType":"personal","groupName":"personal"},report)
                return self.send_json(200,{"scope":scope})
            if method=="POST" and path=="/api/scopes/support-sent":
                value=self.body(); db.execute("UPDATE scopes SET support_last_sent_at=? WHERE recipient=? AND scope_type='group'",(value.get("sentAt") or now_iso(),value.get("groupRecipient")))
                return self.send_json(200,{"updated":db.total_changes==1})
            if method=="POST" and path=="/api/admission/waitlist-status":
                recipient=self.body().get("groupRecipient"); row=db.execute("SELECT waitlist_last_replied_at FROM scopes WHERE recipient=? AND admission_status='waitlisted'",(recipient,)).fetchone()
                notify=bool(row) and (not row[0] or datetime.now(timezone.utc)-datetime.fromisoformat(row[0].replace("Z","+00:00"))>=timedelta(hours=24))
                if notify: db.execute("UPDATE scopes SET waitlist_last_replied_at=? WHERE recipient=?",(now_iso(),recipient))
                return self.send_json(200,{"notify":notify})
            if method=="GET" and path=="/api/reminders":
                recipient=query.get("groupRecipient",[None])[0]
                rows=db.execute("SELECT payload,version FROM reminders WHERE (? IS NULL OR recipient=?) ORDER BY json_extract(payload,'$.nextRunUtc')",(recipient,recipient))
                return self.send_json(200,{"reminders":[{**json.loads(r["payload"]),"version":r["version"]} for r in rows]})
            if method=="POST" and path=="/api/reminders":
                value=self.body(); recipient=value.get("groupRecipient")
                if not recipient or not str(value.get("text","")).strip(): return self.send_json(400,{"error":"a valid scoped reminder is required!"})
                db.execute("BEGIN IMMEDIATE")
                if db.execute("SELECT count(*) FROM reminders WHERE recipient=?",(recipient,)).fetchone()[0]>=50: return self.send_json(409,{"error":"there are already 50 reminders! delete one first."})
                for _ in range(900):
                    reminder_id=str(secrets.randbelow(900)+100); payload={**value,"id":reminder_id,"createdAt":now_iso(),"updatedAt":now_iso()}
                    try:
                        db.execute("INSERT INTO reminders(recipient,id,payload,version) VALUES(?,?,?,1)",(recipient,reminder_id,json.dumps(payload)))
                        return self.send_json(201,{**payload,"version":1})
                    except sqlite3.IntegrityError: continue
                return self.send_json(503,{"error":"couldn't allocate a reminder id! try again."})
            reminder_match=path.removeprefix("/api/reminders/") if path.startswith("/api/reminders/") else None
            if reminder_match and method in ("PUT","DELETE"):
                value=self.body() if method=="PUT" else {}; recipient=value.get("groupRecipient") or query.get("groupRecipient",[None])[0]
                row=db.execute("SELECT * FROM reminders WHERE id=? AND (? IS NULL OR recipient=?)",(reminder_match,recipient,recipient)).fetchone()
                if not row: return self.send_json(404,{"error":"reminder not found!"})
                expected=int(value.get("version",query.get("version",[row["version"]])[0]))
                if expected!=row["version"]: return self.send_json(409,{"error":"that reminder changed! refresh and try again."})
                if method=="DELETE":
                    db.execute("DELETE FROM reminders WHERE recipient=? AND id=? AND version=?",(row["recipient"],row["id"],expected)); self.send_response(204); self.end_headers(); return
                prior=json.loads(row["payload"]); next_id=value.get("id",row["id"]); payload={**prior,**{k:v for k,v in value.items() if k not in ("version","groupRecipient")},"id":next_id,"updatedAt":now_iso()}
                try: db.execute("UPDATE reminders SET id=?,payload=?,version=version+1 WHERE recipient=? AND id=? AND version=?",(next_id,json.dumps(payload),row["recipient"],row["id"],expected))
                except sqlite3.IntegrityError: return self.send_json(409,{"error":"that id is already used!"})
                return self.send_json(200,{**payload,"version":expected+1})
            if path.startswith("/api/calendar/"):
                return self.send_json(503,{"error":"calendar integration is not configured"})
        return self.send_json(404,{"error":"not found!"})
    do_GET=do_POST=lambda self: self.route()
    def log_message(self, fmt, *args): pass


def main():
    ThreadingHTTPServer((os.getenv("STATE_BIND","127.0.0.1"),int(os.getenv("STATE_PORT","8090"))),Handler).serve_forever()


if __name__ == "__main__": main()
