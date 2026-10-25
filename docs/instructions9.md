Don't push. Stop for my review.
Work only in C:\projects\crm-assitant. Preserve current work. No commits, live AI calls, database changes or model changes.

An English question, "What is Jewish Points and what does it track?", produced a Hebrew answer from mixed Hebrew/English passages. The English explanation was present in passage [3].

Confirm the current language instruction in app/generation.py. Strengthen it explicitly: answer in the question's language even when the sources use another language; translate the supported information as needed, preserve proper nouns and citations, and don't let source language choose answer language.

Keep retrieval unchanged. Sources should remain in their original language. Don't add a translation API or another model call.

Run offline tests for prompt construction and existing response handling. Report actual results. Mocked tests do not prove the model will obey; leave that live check for me.

Stop with the changed files and test results. No commit, no push.