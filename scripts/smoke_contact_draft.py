"""TEMPORARY smoke test (docs/instructions14.md): the contact-draft bridge
against the real LOCAL CRM. Creates at most ONE draft contact "SmokeTest
DeleteMe" and leaves it for the owner to inspect and remove. Delete this file
after the test.

Run inside the assistant container (needs a terminal for the hidden input):
    docker compose exec app python scripts/smoke_contact_draft.py
Safe retry after a lost confirm answer (no new proposal is made):
    docker compose exec app python scripts/smoke_contact_draft.py --resume PROPOSAL_ID PAYLOAD_HASH

Login goes to the CRM's own POST /api/auth/token (form fields username,
password, optional organization_id). Username and password are read with
hidden input; the token stays in this process's memory only. Neither is
printed, logged, saved or sent anywhere except the CRM (login) and the
bridge (Authorization header). The login answer is never displayed.

Writes go only through the bridge (/crm/contact-drafts/...). The CRM is read
only through its existing GET /api/contacts/list and GET /api/contacts/{id}.
"""

import getpass
import os
import sys

import httpx

BRIDGE = "http://127.0.0.1:8000"
FIRST, LAST = "SmokeTest", "DeleteMe"
results = []  # (step, verdict, note)


def record(step, ok, note=""):
    verdict = "PASS" if ok else "FAIL"
    results.append((step, verdict, note))
    print(f"  -> {verdict}{': ' + note if note else ''}")
    return ok


def not_tested(step, why):
    results.append((step, "NOT TESTED", why))


def summary():
    print("\n=== Summary ===")
    for verdict in ("PASS", "FAIL", "NOT TESTED"):
        rows = [r for r in results if r[1] == verdict]
        print(f"{verdict} ({len(rows)})")
        for step, _, note in rows:
            print(f"  - {step}{': ' + note if note else ''}")


def detail_code(response):
    """Only the short error code/message, never the whole body."""
    try:
        detail = response.json().get("detail")
    except Exception:
        return None
    if isinstance(detail, dict):
        return detail.get("code") or detail.get("status")
    return str(detail)[:120] if detail else None


def login(crm):
    username = getpass.getpass("CRM username or email (hidden): ")
    password = getpass.getpass("CRM password (hidden): ")
    form = {"username": username, "password": password}
    try:
        r = crm.post("/api/auth/token", data=form)
        print(f"[login] POST /api/auth/token -> HTTP {r.status_code}")
        if r.status_code != 200:
            print(f"  error: {detail_code(r)}")
            return None
        data = r.json()
        if data.get("requires_org_selection"):
            print("  This login belongs to several organizations:")
            for org in data.get("organizations", []):
                print(f"    {org.get('id')}: {org.get('name')}")
            form["organization_id"] = input("  Organization id to use: ").strip()
            r = crm.post("/api/auth/token", data=form)
            print(f"[login] POST /api/auth/token (with organization_id) -> HTTP {r.status_code}")
            if r.status_code != 200:
                print(f"  error: {detail_code(r)}")
                return None
            data = r.json()
        token = data.get("access_token")
        print(f"  token received: {'yes' if token else 'no'} (not shown)")
        return token
    finally:
        del password, form


def count_smoke_drafts(crm, auth):
    r = crm.get("/api/contacts/list", params={"status": "draft", "q": FIRST, "page_size": 100}, headers=auth)
    if r.status_code != 200:
        print(f"  GET /api/contacts/list -> HTTP {r.status_code} ({detail_code(r)})")
        return None, []
    items = [c for c in r.json().get("items", []) if c.get("name") == FIRST and c.get("last_name") == LAST]
    return len(items), [c.get("id") for c in items]


def confirm(auth, proposal_id, payload_hash, label):
    """Confirm the same proposal; on a lost answer offer to retry the SAME one."""
    while True:
        try:
            r = httpx.post(f"{BRIDGE}/crm/contact-drafts/proposals/{proposal_id}/confirm",
                           json={"payload_hash": payload_hash}, headers=auth, timeout=60)
        except httpx.HTTPError as error:
            print(f"[{label}] bridge did not answer ({type(error).__name__})")
            r = None
        if r is not None:
            body = r.json()
            print(f"[{label}] POST /crm/contact-drafts/proposals/{{id}}/confirm -> HTTP {r.status_code}, "
                  f"status={body.get('status')}")
            # Only a lost answer (or no connection at all) is retried, and only the same proposal.
            if body.get("status") not in ("uncertain", "crm_unreachable"):
                return r, body
            print(f"  message: {body.get('message')}")
        print(f"  The save MAY have happened. Keep these for a safe retry:")
        print(f"    proposal_id  = {proposal_id}")
        print(f"    payload_hash = {payload_hash}")
        if input("  Confirm the SAME proposal again now? [y/N] ").strip().lower() != "y":
            print("  Later: docker compose exec app python scripts/smoke_contact_draft.py "
                  f"--resume {proposal_id} {payload_hash}")
            return r, None


def main():
    crm_url = os.environ.get("CRM_API_URL", "").strip().rstrip("/")
    if not crm_url:
        print("CRM_API_URL is not set in this container. Stop.")
        return
    print(f"CRM: {crm_url}   bridge: {BRIDGE}")
    resume = sys.argv[1:4] if len(sys.argv) >= 4 and sys.argv[1] == "--resume" else None

    with httpx.Client(base_url=crm_url, timeout=15, follow_redirects=False) as crm:
        token = login(crm)
        if not token:
            not_tested("bridge steps", "no CRM token (login failed)")
            return
        auth = {"Authorization": f"Bearer {token}"}

        print("\n[before] existing SmokeTest DeleteMe drafts (GET /api/contacts/list?status=draft)")
        before, before_ids = count_smoke_drafts(crm, auth)
        print(f"  count={before} ids={before_ids}")
        if before is None:
            not_tested("bridge steps", "could not list drafts before the test")
            return

        if resume:
            proposal_id, payload_hash = resume[1], resume[2]
            print(f"\nResuming proposal {proposal_id} (no new proposal is made)")
            for step in ("1 propose first_name only", "2 propose first+last name"):
                not_tested(step, "resume run")
        else:
            if before:
                print("  A SmokeTest DeleteMe draft already exists; creating another would make a second. Stop.")
                not_tested("bridge steps", f"draft(s) already exist: {before_ids}")
                return

            print("\n[1] propose with first_name only")
            r = httpx.post(f"{BRIDGE}/crm/contact-drafts/proposals",
                           json={"fields": {"first_name": FIRST}}, headers=auth, timeout=30)
            b = r.json()
            print(f"  HTTP {r.status_code}, status={b.get('status')}, missing_fields={b.get('missing_fields')}, "
                  f"invalid_fields={b.get('invalid_fields')}, fields={b.get('fields')}")
            record("1 propose first_name only",
                   r.status_code == 200 and b.get("status") == "needs_information"
                   and b.get("missing_fields") == ["last_name"])
            fields = dict(b.get("fields") or {"first_name": FIRST})

            print("\n[2] resubmit with first_name + last_name")
            fields["last_name"] = LAST
            r = httpx.post(f"{BRIDGE}/crm/contact-drafts/proposals",
                           json={"fields": fields}, headers=auth, timeout=30)
            b = r.json()
            draft = b.get("draft") or {}
            print(f"  HTTP {r.status_code}, status={b.get('status')}, proposal_id={b.get('proposal_id')}, "
                  f"payload_hash={b.get('payload_hash')}, expires_at={b.get('expires_at')}")
            print(f"  draft: first_name={draft.get('first_name')} last_name={draft.get('last_name')} "
                  f"status={draft.get('status')} source={draft.get('source')} "
                  f"contact_type={draft.get('contact_type')} phone={draft.get('phone')} "
                  f"email={draft.get('email')} address={draft.get('address')} "
                  f"assigned_user_id={'present' if draft.get('assigned_user_id') else 'missing'}")
            ok = (r.status_code == 201 and b.get("status") == "proposal" and b.get("proposal_id")
                  and b.get("payload_hash") and b.get("expires_at")
                  and draft.get("first_name") == FIRST and draft.get("last_name") == LAST
                  and draft.get("status") == "draft")
            if not record("2 propose first+last name", ok):
                for step in ("3 confirm", "4 confirm again (replay)", "5 read back saved contact"):
                    not_tested(step, "no proposal")
                return
            proposal_id, payload_hash = b["proposal_id"], b["payload_hash"]

        print("\n[3] confirm the proposal")
        r, b = confirm(auth, proposal_id, payload_hash, "3")
        if b is None:
            record("3 confirm", False, "outcome uncertain; ids printed above for a safe retry")
            for step in ("4 confirm again (replay)", "5 read back saved contact"):
                not_tested(step, "confirm outcome unknown")
            return
        if b.get("status") != "created":
            record("3 confirm", False, f"HTTP {r.status_code} {b.get('status')}: {b.get('message')}")
            for step in ("4 confirm again (replay)", "5 read back saved contact"):
                not_tested(step, "confirm refused")
            return
        contact = b.get("contact") or {}
        contact_id = b.get("contact_id")
        print(f"  replayed={b.get('replayed')}, contact_id={contact_id}")
        print(f"  contact: id={contact.get('id')} first_name={contact.get('first_name')} "
              f"last_name={contact.get('last_name')} status={contact.get('status')} "
              f"source={contact.get('source')} contact_type={contact.get('contact_type')} "
              f"created_at={contact.get('created_at')}")
        # On --resume the first answer may legitimately be a replay of an earlier save.
        expect_replay = bool(resume) and b.get("replayed")
        record("3 confirm",
               r.status_code in ((200,) if expect_replay else (201,)) and b.get("status") == "created"
               and (b.get("replayed") is False or expect_replay) and contact_id
               and contact.get("id") == contact_id and contact.get("first_name") == FIRST
               and contact.get("last_name") == LAST and contact.get("status") == "draft",
               "replay of an earlier save (resume)" if expect_replay else "")

        print("\n[4] confirm the SAME proposal again with the SAME hash")
        r, b2 = confirm(auth, proposal_id, payload_hash, "4")
        if b2 is None:
            record("4 confirm again (replay)", False, "outcome uncertain")
        else:
            print(f"  replayed={b2.get('replayed')}, contact_id={b2.get('contact_id')}")
            record("4 confirm again (replay)",
                   r.status_code == 200 and b2.get("status") == "created" and b2.get("replayed") is True
                   and b2.get("contact_id") == contact_id)

        print(f"\n[5] read back via GET /api/contacts/{contact_id} (CRM, read only)")
        r = crm.get(f"/api/contacts/{contact_id}", headers=auth)
        c = r.json() if r.status_code == 200 else {}
        print(f"  HTTP {r.status_code}: id={c.get('id')} name={c.get('name')} last_name={c.get('last_name')} "
              f"status={c.get('status')} source={c.get('source')} contact_type={c.get('contact_type')} "
              f"phone={c.get('phone')} email={c.get('email')} address={c.get('address')} "
              f"assigned_user_id matches bridge: {c.get('assigned_user_id') == contact.get('assigned_user_id')}")
        record("5 read back saved contact",
               r.status_code == 200 and c.get("id") == contact_id and c.get("name") == FIRST
               and c.get("last_name") == LAST and c.get("status") == "draft" and c.get("source") == "assistant")

        print("\n[6] no second contact (GET /api/contacts/list?status=draft)")
        after, after_ids = count_smoke_drafts(crm, auth)
        print(f"  before={before} {before_ids}  after={after} {after_ids}")
        record("6 exactly one new draft", after is not None and after - before == 1 and contact_id in after_ids)
        del token, auth


if __name__ == "__main__":
    try:
        main()
    except Exception as error:  # never print a traceback that could hold request details
        print(f"Stopped on an unexpected error: {type(error).__name__}")
        not_tested("remaining steps", f"unexpected {type(error).__name__}")
    summary()
