"""Rehearse the two-pack demo beats through the HTTP APIs and check every outcome.

Kirana on :8001 (beat 7b, simulated Paytm channel), librarian on :8005 (beat 7a). Resets each instance's own database first.
Spends model calls (about 5 turns per pack) and waits PACE seconds between customer messages (Gemma allows about 1.5 turns a minute).

Usage: python scripts/rehearse.py [cs|kirana|librarian|both]   ("cs" = the damaged-item beat plus the same customer by email, on :8000)   (REHEARSE_PACE=30 seconds between messages)
"""
import os
import sys
import time

import httpx

PACE = float(os.environ.get("REHEARSE_PACE", "30"))
http = httpx.Client(timeout=150)
failures: list[str] = []
started = time.perf_counter()


def check(name: str, ok: bool, detail: str = "") -> None:
    print(f"   {'PASS' if ok else 'FAIL'}  {name} {detail}", flush=True)
    if not ok:
        failures.append(name)


def say(url: str, body: dict, label: str) -> dict:
    t = time.perf_counter()
    d = http.post(url, json=body).json()
    lane = str((d.get("meta") or {}).get("lane")).split(":")[-1]
    print(f"   > {label}\n   < [{lane}, {time.perf_counter() - t:.1f}s, {d.get('status')}] {(d.get('reply') or '')[:170]}", flush=True)
    time.sleep(PACE)
    return d


def post(url: str, body: dict | None = None, ok: int = 200) -> dict:
    r = http.post(url, json=body or {})
    if r.status_code != ok:
        raise SystemExit(f"POST {url} -> {r.status_code}: {r.text[:200]}")
    return r.json()


def kirana() -> None:
    b, rohit, imran = "http://localhost:8001", "+919800000501", "+919800000503"
    print("\n== KIRANA (beat 7b), simulated Paytm channel, fake data", flush=True)
    post(f"{b}/debug/reset")
    chat = lambda phone, text: say(f"{b}/api/paytm/chat", {"phone": phone, "text": text}, text[:90])  # noqa: E731
    tools = lambda: {t["name"]: t for t in http.get(f"{b}/api/tools").json()["tools"]}  # noqa: E731
    check("issue_coupon is not granted at start", not (tools()["issue_coupon"]["grant"] or {}).get("enabled"))
    r = chat(rohit, "Hi, I paid Rs 240 yesterday at Sharma Kirana for rice and butter but the rice packet was torn. What can you do?")
    check("refund offered, no coupon while ungranted", "refund" in (r.get("reply") or "").lower() and "288" not in (r.get("reply") or ""))
    g = post(f"{b}/api/tools/issue_coupon/grant", {"name": "Anurag", "max_per_day": 10})
    check("admin grants issue_coupon with cap 10 (audited)", g["enabled"] and g["max_per_day"] == 10)
    r = chat(rohit, "Okay, the desk says coupons are available now. Can you show me my options again for that Rs 240 payment?")
    check("refund Rs 240 and coupon Rs 288 shown side by side", "240" in r["reply"] and "288" in r["reply"])
    chat(rohit, "I'll take the coupon, please.")
    r = chat(rohit, "Yes, go ahead.")
    coupons = http.get(f"{b}/api/paytm/coupons", params={"phone": rohit}).json()
    check("coupon KIRANA-xxxx of Rs 288 issued once", [c["amount"] for c in coupons if c["status"] == "issued"] == [288] and "KIRANA-" in r["reply"])
    ins = post(f"{b}/api/insights/run", {"name": "Sharma"})
    suggested, skipped = [x["customer"] for x in ins["suggested"]], [x["customer"] for x in ins["skipped"]]
    check("insights propose the two eligible regulars at Rs 30", sorted(suggested) == ["Imran Khan", "Sunita Sharma"] and all(x["amount"] == 30 for x in ins["suggested"]))
    check("the lapsed regular who did not opt in is skipped", skipped == ["Meena Iyer"])
    card = next(a for a in http.get(f"{b}/api/queue").json()["approvals"] if a["customer"] == "Imran Khan")
    post(f"{b}/api/actions/{card['action_id']}/approve", {"name": "Sharma"})
    thread = http.get(f"{b}/api/paytm/messages", params={"phone": imran}).json()
    check("approved offer arrives in Imran's Paytm chat", any(m["author"] == "ai" and "KIRANA-" in m["text"] for m in thread))
    code = next(c["code"] for c in http.get(f"{b}/api/paytm/coupons", params={"phone": imran}).json() if c["status"] == "issued")
    paid = post(f"{b}/api/paytm/pay", {"phone": imran, "amount": 200, "coupon_code": code})
    check("redeeming at the store takes Rs 30 off (simulated)", paid["discount"] == 30 and paid["paid"] == 170)
    stats = http.get(f"{b}/api/insights/stats").json()
    check("strip: issued 2, redeemed 1, revenue brought back Rs 200 (simulated)", (stats["issued"], stats["redeemed"], stats["revenue_brought_back"]) == (2, 1, 200), str(stats))


def librarian() -> None:
    b = "http://localhost:8005"
    print("\n== LIBRARIAN (beat 7a), zero-Python pack", flush=True)
    post(f"{b}/debug/reset")
    chat = lambda text: say(f"{b}/api/chat", {"session_id": "rehearsal", "text": text}, text[:90])  # noqa: E731
    outbox = lambda: http.get(f"{b}/api/tools").json()["outbox"]  # noqa: E731
    r = chat("My member ID is M-1001 and my phone ends in 0401. Please renew The Monsoon Garden.")
    check("renewal held for the member's yes", r["status"] == "waiting_customer")
    r = chat("Yes please.")
    detail = http.get(f"{b}/api/tickets/{r['ticket_id']}").json()
    loans = {i["loan_id"]: i for rec in detail["records"] if rec["collection"] == "loans" for i in rec["items"]}
    check("renewal executed once (renewals 1)", loans.get("L-5001", {}).get("renewals") == 1, str(loans.get("L-5001")))
    r = chat("Please book study room B on Monday 2 November at 4 pm for one hour.")
    check("booking refused while book_slot is not granted", "BK-" not in (r.get("reply") or "") and not outbox())
    g = post(f"{b}/api/tools/book_slot/grant", {"name": "Anurag", "max_per_day": 5})
    check("admin grants book_slot with cap 5 (audited)", g["enabled"] and g["max_per_day"] == 5)
    chat("Bookings are enabled now. Please book study room B on Monday 2 November at 4 pm for one hour.")
    r = chat("Yes.")
    check("booking confirmed with an ID and an outbox email", "BK-" in (r.get("reply") or "") and len(outbox()) == 1)
    post(f"{b}/api/tools/book_slot/revoke", {"name": "Anurag"})
    chat("Can you also book study room C on Monday 2 November at 6 pm for one hour?")
    check("after a revoke, no new booking", len(outbox()) == 1)


def cs() -> None:
    b = "http://localhost:8000"
    print("\n== CUSTOMER SERVICE: damaged item, then the same customer by email (invented customer Ananya)", flush=True)
    post(f"{b}/debug/reset?fake=1")
    chat = lambda text: say(f"{b}/api/chat", {"session_id": "rehearsal-cs", "text": text}, text[:90])  # noqa: E731
    r = chat("Order RA-20012, email ananya.gupta@example.com. The phone case arrived cracked. Please refund the Rs 499 and also send me a replacement.")
    check("refund and replacement held for the customer's yes", r["status"] == "waiting_customer")
    r = chat("Yes, please go ahead with both.")
    detail = http.get(f"{b}/api/tickets/{r['ticket_id']}").json()
    done = {a["tool"] for a in detail["timeline"] if a["type"] == "action" and a["status"] == "executed"}
    check("refund and replacement both executed", {"issue_refund", "create_reship"} <= done, str(sorted(done)))
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    from scripts.simulate_channels import post_fixture
    code, body = post_fixture(b, "email", "ananya.gupta@example.com", "Hi, any update on my phone case order?")
    t0 = http.get(f"{b}/api/tickets/{r['ticket_id']}").json()["ticket"]
    print(f"   > (email from the same customer) Hi, any update on my phone case order?\n   < [{code}] {str(body.get('reply') or '')[:170]}", flush=True)
    check("email lands on the same ticket (web + email)", {"web", "email"} <= set(t0["channels"]), str(t0["channels"]))
    post(f"{b}/debug/reset")  # back to the demo data (demo contacts restored)


if __name__ == "__main__":
    which = sys.argv[1] if len(sys.argv) > 1 else "both"
    if which == "cs":
        cs()
    if which in ("kirana", "both"):
        kirana()
    if which in ("librarian", "both"):
        librarian()
    print(f"\n{'ALL PASS' if not failures else 'FAILURES: ' + ', '.join(failures)}  ({time.perf_counter() - started:.0f}s)")
    sys.exit(1 if failures else 0)
