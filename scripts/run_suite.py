"""Scripted test suite against the running server. Assertions read DB state, never reply text.

Usage: python scripts/run_suite.py [--only name,name] [--area policy,identity] [--lang en|all] [--base http://localhost:8000]
Writes data/results.json and docs/results.md. Resets seed data before every case (DEBUG=1 server).
"""
import argparse
import json
import os
import statistics
import sys
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from typing import Any

import httpx
from pymongo import MongoClient

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from app import config  # noqa: E402
from tests.cases import CASES  # noqa: E402

mongo = MongoClient(config.MONGO_URI)[config.MONGO_DB]
PACE_S = 5.0


class Runner:
    def __init__(self, base: str, case: dict[str, Any]) -> None:
        self.base, self.case = base, case
        self.http = httpx.Client(base_url=base, timeout=120)
        self.session = f"t-{case['name']}-{uuid.uuid4().hex[:6]}"
        self.ticket_id: str | None = None
        self.latencies: list[float] = []
        self.lanes: list[str] = []
        self.notes: list[str] = []
        self.model_calls_mark: int | None = None

    def ticket(self) -> dict[str, Any] | None:
        from bson import ObjectId
        if not self.ticket_id:
            return None
        t = mongo.tickets.find_one({"_id": ObjectId(self.ticket_id)})
        while t and t.get("status") == "merged":
            t = mongo.tickets.find_one({"_id": t["merged_into"]})
        return t

    def pending(self, tool: str, status: str) -> dict[str, Any]:
        a = mongo.actions.find_one({"tool": tool, "status": status}, sort=[("created_at", -1)])
        if a is None:
            raise AssertionError(f"no {status} {tool} action to act on")
        return a

    def step(self, s: dict[str, Any]) -> None:
        if "say" in s:
            time.sleep(PACE_S)  # stay under the free-tier requests-per-minute limits
            t = time.perf_counter()
            r = self.http.post("/api/chat", json={"session_id": s.get("session", self.session), "text": s["say"],
                                                  "language": s.get("language", "auto"), "client_msg_id": s.get("id") or uuid.uuid4().hex})
            r.raise_for_status()
            d = r.json()
            self.latencies.append(time.perf_counter() - t)
            self.ticket_id = d.get("ticket_id") or self.ticket_id
            self.lanes.append((d.get("meta") or {}).get("lane") or "none")
            self.notes.append(f"> {s['say'][:60]} | < {(d.get('reply_en') or d.get('meta', {}).get('mode') or '')[:110]}")
        elif "fault" in s:
            self.http.post("/debug/fault", json=s["fault"]).raise_for_status()
        elif "approve" in s or "reject" in s:
            kind = "approve" if "approve" in s else "reject"
            a = self.pending(s[kind], s.get("status", "pending_approval"))
            body = {"name": s.get("by", "Neha"), "note": s.get("note", "")}
            if s.get("double"):
                with ThreadPoolExecutor(2) as pool:
                    codes = list(pool.map(lambda _: self.http.post(f"/api/actions/{a['_id']}/{kind}", json=body).status_code, range(2)))
                self.notes.append(f"double {kind}: {codes}")
            else:
                r = self.http.post(f"/api/actions/{a['_id']}/{kind}", json=body)
                self.notes.append(f"{kind} -> {r.status_code}")
                if s.get("expect_code") and r.status_code != s["expect_code"]:
                    raise AssertionError(f"{kind} returned {r.status_code}, expected {s['expect_code']}")
        elif "ticket_op" in s:
            t = self.ticket()
            r = self.http.post(f"/api/tickets/{t['_id']}/{s['ticket_op']}", json={"name": s.get("by", "Neha"), "text": s.get("text", ""),
                                                                                   "note": s.get("note", "")})
            r.raise_for_status()
            self.notes.append(f"{s['ticket_op']} -> {r.status_code}")
            if s["ticket_op"] == "takeover":
                self.model_calls_mark = self.ticket().get("model_calls", 0)
        elif "simulate" in s:
            from scripts.simulate_channels import post_fixture
            code, body = post_fixture(self.base, s["simulate"], **s.get("args", {}))
            self.notes.append(f"simulate {s['simulate']} -> {code}")
            if body.get("ticket_id"):
                self.ticket_id = body["ticket_id"]
        elif "sleep" in s:
            time.sleep(s["sleep"])
        else:
            raise ValueError(f"unknown step {s}")

    def check(self, c: tuple) -> str | None:
        kind, *rest = c
        t = self.ticket()
        if kind == "action":
            tool, status, rule = (rest + [None])[:3]
            q: dict[str, Any] = {"tool": tool, "status": status}
            if rule:
                q["decision.rule_id"] = rule
            return None if mongo.actions.count_documents(q) else f"expected {tool} {status} {rule or ''}".strip()
        if kind == "no_action":
            tool, status = rest
            n = mongo.actions.count_documents({"tool": tool, "status": status})
            return None if n == 0 else f"unexpected {n}x {tool} {status}"
        if kind == "count_action":
            tool, status, n = rest
            got = mongo.actions.count_documents({"tool": tool, "status": status})
            return None if got == n else f"expected {n}x {tool} {status}, got {got}"
        if kind == "ticket_status":
            return None if t and t["status"] == rest[0] else f"ticket status {t and t['status']} != {rest[0]}"
        if kind == "escalated":
            rule = rest[0] if rest else None
            esc = (t or {}).get("escalation") or {}
            if not esc:
                return "ticket not escalated"
            return None if rule is None or esc.get("rule_id") in (rule if isinstance(rule, list) else [rule]) else f"escalation rule {esc.get('rule_id')} != {rule}"
        if kind == "not_escalated":
            return None if not (t or {}).get("escalation") else f"unexpected escalation {(t or {}).get('escalation', {}).get('rule_id')}"
        if kind == "refunds":
            order, n = rest
            o = mongo.orders.find_one({"order_number": order})
            got = mongo.refunds.count_documents({"order_id": o["_id"]})
            return None if got == n else f"{order} refunds {got} != {n}"
        if kind == "order_status":
            order, status = rest
            got = mongo.orders.find_one({"order_number": order})["status"]
            return None if got == status else f"{order} status {got} != {status}"
        if kind == "messages":
            n = rest[0]
            got = mongo.messages.count_documents({"author": "customer"})
            return None if got == n else f"customer messages {got} != {n}"
        if kind == "model_calls_frozen":
            got = (t or {}).get("model_calls", 0)
            return None if got == self.model_calls_mark else f"model calls moved {self.model_calls_mark} -> {got} in human mode"
        if kind == "model_calls_grew":
            got = (t or {}).get("model_calls", 0)
            return None if got > (self.model_calls_mark or 0) else "agent did not resume"
        if kind == "message_from":
            author, lang = (rest + [None])[:2]
            q = {"author": author}
            if lang:
                q["language"] = lang
            return None if mongo.messages.count_documents(q) else f"no {author} message {lang or ''}"
        if kind == "meta":
            key = rest[0]
            return None if mongo.messages.count_documents({f"meta.{key}": {"$exists": True, "$ne": None}}) else f"no message with meta.{key}"
        if kind == "lane_used":
            return None if any(rest[0] in lane for lane in self.lanes) else f"lane {rest[0]} not used: {self.lanes}"
        if kind == "lane_not_used":
            return None if all(rest[0] not in lane for lane in self.lanes) else f"lane {rest[0]} should have been skipped: {self.lanes}"
        if kind == "same_ticket_channels":
            chans = set((t or {}).get("channels", []))
            return None if set(rest[0]) <= chans else f"ticket channels {chans} missing {rest[0]}"
        raise ValueError(f"unknown check {kind}")


def violations() -> list[str]:
    """Global invariants: anything here is a policy violation."""
    out = []
    for o in mongo.orders.find():
        refunds = list(mongo.refunds.find({"order_id": o["_id"]}))
        if len(refunds) > 1:
            out.append(f"{o['order_number']}: {len(refunds)} refunds")
        if sum(r["amount"] for r in refunds) > o["total"]:
            out.append(f"{o['order_number']}: refunds above total")
    for a in mongo.actions.find({"status": "executed", "tool": {"$in": ["issue_refund", "create_reship", "cancel_order"]}}):
        if a.get("customer_id") is None:
            out.append(f"{a['tool']} executed without a verified customer")
        tier = a["tier"]
        if tier == "approve" and a.get("decided_by") not in config.HUMAN_AGENTS:
            out.append(f"{a['tool']} {a['args']} needed approval but ran without a human")
        if tier == "confirm" and a.get("decided_by") != "customer":
            out.append(f"{a['tool']} {a['args']} ran without customer confirmation")
        if tier == "deny":
            out.append(f"{a['tool']} {a['args']} executed despite deny")
    return out


def run_case(base: str, case: dict[str, Any]) -> dict[str, Any]:
    httpx.post(f"{base}/debug/reset?fake=1", timeout=30).raise_for_status()
    httpx.post(f"{base}/debug/heal", timeout=30).raise_for_status()
    r = Runner(base, case)
    error = None
    try:
        for s in case["steps"]:
            r.step(s)
        time.sleep(case.get("settle", 0.5))
        failures = [msg for c in case["expect"] if (msg := r.check(c))]
    except Exception as e:  # noqa: BLE001
        failures, error = [f"error: {type(e).__name__}: {e}"], str(e)
    viol = violations()
    t = r.ticket()
    return {"name": case["name"], "area": case["area"], "passed": not failures and not viol, "failures": failures,
            "violations": viol, "latencies": [round(x, 2) for x in r.latencies], "lanes": r.lanes, "notes": r.notes,
            "escalated": bool((t or {}).get("escalation")), "expects_escalation": case.get("escalates", False) or any(c[0] == "escalated" for c in case["expect"]),
            "resolves": case.get("resolves", False), "error": error}


def report(results: list[dict[str, Any]]) -> str:
    lat = [x for r in results for x in r["latencies"]]
    resolvable = [r for r in results if r["resolves"]]
    esc_expected = [r for r in results if r["expects_escalation"]]
    esc_unexpected = [r for r in results if r["escalated"] and not r["expects_escalation"]]
    acts = list(mongo.actions.find({"decided_by": {"$in": config.HUMAN_AGENTS}}))
    lines = [f"# Test results ({time.strftime('%Y-%m-%d %H:%M')})", "",
             f"- Cases passed: **{sum(r['passed'] for r in results)}/{len(results)}**",
             f"- Resolved without a human (in-policy cases): {sum(r['passed'] and not r['escalated'] for r in resolvable)}/{len(resolvable)}",
             f"- Escalation correctness: {sum(r['escalated'] for r in esc_expected)}/{len(esc_expected)} expected escalations happened; "
             f"{len(esc_unexpected)} unexpected escalations",
             f"- Policy violations: **{sum(len(r['violations']) for r in results)}**",
             f"- Median turn latency (text): {statistics.median(lat):.2f}s (p90 {sorted(lat)[int(len(lat) * 0.9) - 1]:.2f}s, n={len(lat)})" if lat else "- No turns",
             f"- Lanes used: {dict((lane, sum(r['lanes'].count(lane) for r in results)) for lane in {x for r in results for x in r['lanes']})}",
             f"- Human decisions in last case DB: {len(acts)}", "", "| Case | Area | Result | Detail |", "|---|---|---|---|"]
    for r in results:
        detail = "; ".join(r["failures"] + r["violations"])[:180] or ""
        lines.append(f"| {r['name']} | {r['area']} | {'PASS' if r['passed'] else 'FAIL'} | {detail} |")
    return "\n".join(lines) + "\n"


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--base", default="http://localhost:8000")
    p.add_argument("--only", default="")
    p.add_argument("--area", default="")
    p.add_argument("--lang", default="en", help="en skips cases that spend Sarvam credit")
    p.add_argument("--core", action="store_true", help="per-block core subset (saves free-tier LLM quota)")
    args = p.parse_args()
    cases = [c for c in CASES if (not args.only or c["name"] in args.only.split(","))
             and (not args.area or c["area"] in args.area.split(","))
             and (args.lang == "all" or not c.get("sarvam"))
             and (not args.core or c.get("core"))
             and not c.get("disabled")]
    results = []
    for case in cases:
        res = run_case(args.base, case)
        results.append(res)
        mark = "PASS" if res["passed"] else "FAIL"
        print(f"{mark} {case['name']:<28} {res['lanes']} {res['latencies']} {'; '.join(res['failures'] + res['violations'])[:200]}", flush=True)
        if not res["passed"]:
            for n in res["notes"]:
                print("      ", n)
    httpx.post(f"{args.base}/debug/reset", timeout=30)
    os.makedirs(f"{config.ROOT}/data", exist_ok=True)
    with open(f"{config.ROOT}/data/results.json", "w") as f:
        json.dump(results, f, indent=1)
    md = report(results)
    with open(f"{config.ROOT}/docs/results.md", "w") as f:
        f.write(md)
    print(md)


if __name__ == "__main__":
    main()
