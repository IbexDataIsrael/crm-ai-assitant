Don't push. Don't commit. Stop for my review.

TASK: smoke-test the contact-draft bridge against the real LOCAL CRM, in one coherent run.

I approve creating ONE local draft contact named SmokeTest DeleteMe. Leave it for me to inspect and remove. Don't approve the draft or change existing contacts. No production access, no AI-provider calls, no application-code changes. If the contract is wrong, report it rather than fixing it during this test.

1. Verify the setup from actual files and running containers.
- Read the CRM login code in C:\projects\sheepsidebay_crm to establish the real endpoint and request shape. Don't guess.
- Use docker ps to verify the CRM's published port. My earlier value was 8001; check it.
- Check only CRM_API_URL in the assistant container. Don't read or print the whole environment or .env.
- Inside Docker, localhost points to that container. The CRM address should use host.docker.internal and the verified host port. If the setting is wrong, give me the exact replacement value and the container-recreation command. Don't edit .env yourself.
- Verify reachability from the assistant container and confirm the bridge endpoints exist in the assistant's OpenAPI contract.

2. Handle login safely.
Use the actual local login contract. If credentials are needed, prepare a temporary test runner I can run through Docker, with hidden interactive password input. No local Python installation. Keep the token only in process memory for the same test run. Never print, log, save, or send credentials or tokens to an AI provider. Don't display the authentication response. Don't create a user or reset a password.

3. Exercise the bridge, not a shortcut directly to the CRM.
- Propose with first_name SmokeTest only. Expect needs_information with last_name missing.
- Resubmit with first_name SmokeTest and last_name DeleteMe. Expect a proposal with proposal_id, payload_hash, expires_at and draft details.
- Confirm that proposal. Expect created, replayed:false, contact_id and saved-contact readback.
- Confirm the SAME proposal again with the SAME hash. Expect replayed:true and the SAME contact_id, with no second contact.
- Verify the returned saved contact is the intended local draft. Use an existing read endpoint if available; don't invent one or read the database directly.

If confirmation times out, don't create a fresh proposal: the save may have happened. Preserve its ID and hash for a safe retry.

4. Report the real status code and relevant non-secret fields for every step. Separate passed, failed and not tested. Stop only on a genuine blocker and explain exactly what I must do next. Don't claim the chat works: free-text extraction, chat routing and popup wiring are outside this smoke test