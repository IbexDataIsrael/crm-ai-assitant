Don't push. Don't commit. Stop for my review.

Continue instructions12 with option 1, localhost only. The instruction to reuse existing service authentication was incorrect because none exists. Don't add a service key in this task. Production authentication remains unresolved and must be settled before deployment.

Keep the service bound to 127.0.0.1. Preserve all existing work in the working tree.

Accept the user's CRM bearer token per request. Forward it only to the configured CRM API origin. Never log it, store it, send it to an AI provider, or forward it through a redirect to another origin.

Build the structured contact bridge from instructions12: propose, return missing/invalid details or the exact proposal, accept explicit confirmation, and return the saved draft from the CRM response. Handle retries using the CRM's replay behavior. A timeout is an uncertain result, not proof that saving failed; preserve the proposal for safe retry.

Leave guide Q&A unchanged. Mock external HTTP in tests; make no live AI-provider calls. Document the localhost-only limit. Report changed files and actual test results, then stop.