Don't push - stop for my review.

Work only in C:\projects\crm-assitant. Add real local semantic retrieval to the existing Docker app. Keep localhost port 8011. No hosted AI calls, keys, database, production changes or changes to other projects.

Use sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2 through sentence-transformers. Model reference:
https://huggingface.co/sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2

1. Check Docker memory/resources and compatibility with the existing Python image before building. Do not change Docker Desktop settings. If resources or dependencies block this, report the exact blocker rather than silently switching approach. Resource estimates are not guarantees.

2. Install a compatible pinned sentence-transformers version, pytest and CPU-only PyTorch. Use the official CPU wheel source https://download.pytorch.org/whl/cpu . Package/model downloads are allowed; no customer data or hosted inference. Pre-download the model into a cache outside /code so the existing bind mount won't hide it. Load from that cache at runtime. Avoid downloading GPU packages.

3. Add two short fictional instruction files for visits and donations beside the existing sample. Mark every document SAMPLE DATA - fictional.

4. Add chunking.py: split documents into small chunks retaining filename and heading. Read the model's actual tokenizer limit and keep chunks within it, including special tokens. Do not silently truncate long chunks. Use a little overlap.

5. Add embeddings.py: load the real pretrained model once, encode chunks and questions into vectors. No hashed or fake embeddings. Keep the interface small so another provider could replace it later.

6. Add vectorstore.py: keep chunk vectors in memory, rebuilt from knowledge/ at startup. Implement cosine similarity plainly so I can understand it. This is real vector search over a small corpus, not a persistent database. Neon/pgvector remains a later decision.

7. Change knowledge.py to return the top three relevant chunks with scores, filenames and headings. Knowledge replies show those passages and citations, labeled "Local semantic retrieval - source passages, no AI-written answer yet". Keep action/clarify behavior and demo-data labels intact. The keyword router is still a stub; don't call it AI routing.

8. Test retrieval directly with three paraphrased questions using different words from the source text. Check expected sources, report wrong rankings honestly. Also test /chat with questions that reach the current knowledge route. Keep existing action/clarify tests passing.

9. Add or update root CLAUDE.md in plain English: project goal, Docker start/stop commands and port8011, verified current state, next stage answer generation from retrieved passages, then CRM API actions. Clearly distinguish built from planned. Include no push/commit without permission, no touching minicrm, no secrets, no paid services or real CRM changes without approval.

Build and run with Docker Compose. Inspect the page if browser access is available; otherwise mark visual inspection pending. Report tests, one example question to try, first model-load behavior and any resource problems. Do not claim full RAG is finished: retrieval is real, generation is not connected yet. Do not commit or push. Stop for my review.