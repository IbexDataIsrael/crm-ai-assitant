Don't push. Stop for my review.
Work only in C:\projects\crm-assitant. No commits, live AI API calls, or reading/printing .env or secrets.

Fix app/planner.py. Currently "what is jewishpoints" returns clarify instead of searching the guides.

Keep the router an honest bilingual keyword stub:
1. Route explicit requests to find, add, or update CRM data to the existing demo actions. Support English and Hebrew. Match whole words, not substrings like "add" inside "address". A verb mentioned inside a question is not enough to make it an action request.
2. Route other nonempty input to knowledge retrieval by default. Do not keep adding individual question keywords. Keep clarify for empty input only, consistent with input validation.
3. Preserve route_source="keyword-stub", the demo labels, existing action behavior, and the two active guides. No vector-storage changes in this step.

Add tests with generation mocked and live calls blocked:
- "what is jewishpoints" -> knowledge, retrieving about_english.md.
- "מה זה נקודות יהודיות" -> knowledge, retrieving a guide passage.
- "why can't I see a contact that the assistant added?" -> knowledge, not an action.
- "how do I add a contact?" -> knowledge, not an action.
- "add a contact" and "הוסף איש קשר" -> existing action demo.
- Empty input -> existing validation or clarify, never an action.

Run the full offline suite. Report actual results; don't weaken tests to get a pass. Keep unrelated known failures visible. Update CLAUDE.md to describe the routing rule accurately.

Stop with changed files and test results. No commit, no push.