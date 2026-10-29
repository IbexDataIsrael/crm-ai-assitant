"""TEMPORARY live smoke test (docs/instructions18.md, owner's go 29 Oct 2026):
the apartment-edit chat against the CRM in CRM_API_URL. Delete this file
after the test.

Run inside the assistant container, in your own terminal (hidden input):
    docker compose exec app python scripts/smoke_apartment_edit.py

Part 1 (read only): a CRM search for an address you type, checked against the
shape the chat expects (fixes115), with a summary of kinds and option counts.
No personal data in that summary: field keys, kinds and counts only.

Part 2 (the real chat): you talk to POST /assistant/turn exactly as the page
does - same login check, same code. Before any "yes" on a confirm screen the
script asks you once more, because that yes SAVES in the live CRM. Suggested:
first save a field's current value again (expect "unchanged"), then one real
change on a test apartment and change it back. Type "quit" to end.

Login goes through the assistant's POST /auth/login. Username and password are
read with hidden input and kept in this process's memory only; the token is
never printed, logged or saved.
"""

import getpass
import json
import os
import sys
from collections import Counter

import httpx

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from app import crm_api  # noqa: E402

BRIDGE = "http://127.0.0.1:8000"
results = []  # (step, verdict, note)


def record(step, ok, note=""):
    verdict = "PASS" if ok else "FAIL"
    results.append((step, verdict, note))
    print(f"  -> {verdict}{': ' + note if note else ''}")
    return ok


def summary(outcomes):
    print("\n=== Summary (safe to paste: no names, addresses or values) ===")
    for verdict in ("PASS", "FAIL"):
        rows = [r for r in results if r[1] == verdict]
        print(f"{verdict} ({len(rows)})")
        for step, _, note in rows:
            print(f"  - {step}{': ' + note if note else ''}")
    print("Chat outcomes, in order: " + (", ".join(outcomes) or "none"))


def login():
    username = getpass.getpass("CRM username or email (hidden): ")
    password = getpass.getpass("CRM password (hidden): ")
    body = {"username": username, "password": password}
    try:
        r = httpx.post(f"{BRIDGE}/auth/login", json=body, timeout=30)
        data = r.json()
        print(f"[login] POST /auth/login -> HTTP {r.status_code}, status={data.get('status')}")
        if data.get("status") == "choose_organization":
            for org in data.get("organizations", []):
                print(f"    {org.get('id')}: {org.get('name')}")
            body["organization_id"] = int(input("  Organization id to use: ").strip())
            r = httpx.post(f"{BRIDGE}/auth/login", json=body, timeout=30)
            data = r.json()
            print(f"[login] again with organization_id -> HTTP {r.status_code}, status={data.get('status')}")
        token = data.get("access_token")
        record("login", bool(token), "token received (not shown)" if token else str(data.get("message")))
        return token
    finally:
        del password, body


def shape_report(token):
    """Part 1: read only. The CRM's raw search answer, field by field, against the chat's checks."""
    query = input("\n[part 1] An address to search (read only): ").strip()
    raw = crm_api._get(f"{crm_api.APARTMENTS_PATH}/search", token, {"q": query})
    record("search HTTP 200", raw.status_code == 200, f"HTTP {raw.status_code}")
    if raw.status_code != 200:
        return
    data = raw.json()
    found = data.get("results") or []
    print(f"  results: {len(found)}, truncated: {data.get('truncated')}")
    kinds, bad = Counter(), []
    for apt in found:
        if not crm_api._is_apartment(apt):
            top = [k for k in ("apartment_id", "lineage_id") if not crm_api._is_id(apt.get(k))]
            fields = apt.get("editable_fields")
            wrong = [f"{f.get('field_key')} ({f.get('kind')})" for f in fields or []
                     if not crm_api._is_editable_field(f)] if isinstance(fields, list) else ["editable_fields"]
            bad.append(", ".join(top + wrong))
        for f in apt.get("editable_fields") or []:
            options = f.get("options")
            kinds[f.get("kind")] += 1
            if isinstance(options, list):
                ints = sum(1 for o in options if isinstance(o.get("value"), int))
                no_he = sum(1 for o in options if not o.get("label"))
                print(f"    {f.get('field_key')}: {f.get('kind')}, {len(options)} options"
                      f"{f', {ints} numeric values' if ints else ''}{f', {no_he} without a Hebrew label' if no_he else ''}")
            else:
                print(f"    {f.get('field_key')}: {f.get('kind')}")
    record("every apartment has the expected shape", not bad, "; ".join(bad))
    unknown = set(kinds) - set(("single_option", "multi_option", "text", "number", "date", "yes_no"))
    record("only known kinds", not unknown, ", ".join(map(str, unknown)))
    print(f"  kinds seen: {dict(kinds)}")
    via_bridge = crm_api.search_apartments(token, query)
    record("bridge accepts the search answer", via_bridge.http_status == 200, via_bridge.body.get("status"))
    if found:
        first = crm_api.apartment_by_lineage(token, found[0].get("lineage_id"))
        record("by-lineage read of the first result", first.http_status == 200, first.body.get("status"))


def show(data):
    print("\n--- assistant ---")
    print(data.get("prompt") if data.get("choices") and data.get("prompt") else data.get("reply"))
    for c in data.get("choices") or []:
        print(f"   [{c['message']}] {c['label']}")
    if data.get("multi"):
        print("   (several: numbers separated by commas)")
    print("---")


def chat(token):
    """Part 2: the real conversation through /assistant/turn."""
    auth = {"Authorization": f"Bearer {token}"}
    outcomes, state = [], None
    print("\n[part 2] The chat, as on the page. Type your answers; a button's [value] is what it sends. "
          "\"quit\" ends.")
    message = ""
    while True:
        body = {"intent": "edit_apartment", "message": message, "apartment_state": state}
        try:
            r = httpx.post(f"{BRIDGE}/assistant/turn", json=body, headers=auth, timeout=90)
        except httpx.HTTPError as error:
            print(f"  the assistant did not answer ({type(error).__name__}); nothing is resent")
            outcomes.append("no_answer")
            return outcomes
        data = r.json()
        outcome = data.get("outcome") or data.get("status")
        outcomes.append(str(outcome))
        print(f"[turn] HTTP {r.status_code}, outcome={outcome}")
        if r.status_code != 200:
            print(f"  {data.get('reply') or data.get('message')}")
            return outcomes
        held = json.dumps(data.get("state"))
        if "editable_fields" in held or token in held:
            record("state holds no field list and no token", False)
        state = data.get("state")
        show(data)
        if data.get("show_menu"):
            if input("Start again? [y/N] ").strip().lower() != "y":
                return outcomes
            state, message = None, ""
            continue
        message = input("you> ").strip()
        if message.lower() == "quit":
            return outcomes
        if state and state.get("step") == "confirm" and (message.lower() in ("yes", "y") or message == "כן"):
            if input("  This SAVES in the live CRM. Send it? [y/N] ").strip().lower() != "y":
                print("  Sending \"no\" instead: the current value is kept.")
                message = "no" if state.get("language") == "en" else "לא"


def main():
    url = os.environ.get("CRM_API_URL", "").strip()
    print(f"CRM: {url or '(CRM_API_URL not set)'}")
    if not url:
        return
    token = login()
    if not token:
        summary([])
        return
    shape_report(token)
    outcomes = chat(token)
    record("no failed chat turn", not any(o in ("crm_failed", "crm_contract_error") for o in outcomes))
    summary(outcomes)


if __name__ == "__main__":
    main()
