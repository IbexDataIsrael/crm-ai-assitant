# WhatsApp connection - plan for review

Status: plan only, nothing built. Written 3 November 2026.

## In one paragraph

People will write to a WhatsApp number in their own words, exactly as they do on the chat page.
Meta (WhatsApp's owner) passes each message to our server; our server runs the same assistant
(Gemini understanding, search, contacts, visits, apartments, confirm before every save) and sends
the answer back through Meta. Nothing in the assistant's logic changes - only a new "door".

## How it works

```
WhatsApp user --> Meta --> our server: /whatsapp/webhook
                                |   (answers Meta "OK" at once)
                                v
                    remembers this person's conversation (database)
                                |
                    the same assistant as the chat page
                    (CRM login check -> understand -> flow)
                                |
                    turns the answer into WhatsApp messages --> Meta --> user
```

What is new compared to the chat page:

- **Memory on the server.** The chat page keeps the conversation in the browser. WhatsApp can't,
  so the server keeps each person's conversation in the database (it is forgotten after 30
  minutes of silence).
- **Login by link.** A password is never typed into WhatsApp (it would stay in the chat history
  and pass through Meta). A new user gets a one-time link (valid 10 minutes, usable once), logs in
  on a small web page with their CRM username and password, and from then on WhatsApp knows them.
  When the CRM login expires (8 hours today) they get a new link. "logout" / "התנתק" ends it.
- **Buttons.** WhatsApp allows at most 3 buttons, or a list of up to 10 items. So:
  - yes / no and other choices of up to 3 -> buttons
  - 4 to 10 choices -> a WhatsApp list
  - more than 10, or "tick everything that was done" -> a numbered text: "reply with numbers,
    e.g. 1,3" (the assistant already understands that)
- **Commands.** "cancel" / "ביטול" and "help" / "עזרה".

Safety (same rules as today):

- Every message is checked to really come from Meta (a signature with the app secret).
- Meta sometimes delivers a message twice: duplicates are skipped, so nothing is saved twice.
- Every turn checks the CRM login first; roles and confirm screens are unchanged.
- During the pilot only numbers on an allowlist can use it.
- Logs never contain message text or login tokens; phone numbers in logs are hashed.

## Steps (each one stops for your review)

| Step | What | Needs from you |
|------|------|----------------|
| 1 | Share the assistant's turn logic between the chat page and WhatsApp; add the conversation memory table | nothing |
| 2 | Turn answers into WhatsApp buttons / lists / numbered text | nothing |
| 3 | The webhook and sending, tested with a pretend Meta (no real account) | nothing |
| 4 | Login by one-time link | nothing |
| 5 | Live test with Meta's free test number, through a tunnel, with test data only | Meta developer account, a tunnel, keys through the vault |
| 6 | Real launch: your own number, business verification, server online with HTTPS | Meta business verification, a phone number, hosting |

Steps 1-4 need nothing external and are tested like everything so far (mocked CRM, mocked Meta).

## What you will need to set up (for steps 5-6)

- A Meta developer account with an app that has WhatsApp enabled. For testing, Meta gives a free
  test number that can message up to 5 phones you register - no business verification needed.
- For the real launch: a phone number that is not used in the WhatsApp app, Meta business
  verification, a permanent access token and the app secret (given to the server through the vault).
- A public HTTPS address so Meta can reach the server. Today it runs only on your computer.
  For testing: a free tunnel (for example Cloudflare Tunnel). For real: the server profile
  deployed with HTTPS. Note: the server has about 2.8 GB of free memory and the app uses about
  1 GB; whether it fits there is not proven yet.

## Cost (check before launch)

We only answer messages people send us, so we stay inside WhatsApp's 24-hour reply window and
never need paid "template" messages. Under the rules I know, those replies are free. Sources
disagree on whether Meta started charging for them from October 2026 (with 1,000 free per number
per month): [Respond.io says yes](https://respond.io/blog/whatsapp-pricing-change-2026),
[Unipile says no](https://www.unipile.com/is-the-whatsapp-api-free/). Check Meta's own pricing
page before launch. At your volume it is likely free or very cheap either way.

## Privacy

What people type - names, phone numbers, visit notes - passes through Meta's servers (the Cloud
API is not end-to-end encrypted to us), in addition to Gemini's free tier, which already sees the
typed requests. This is your decision.

## Limits to re-check when building

Button and list limits (3 buttons with titles up to 20 characters; lists up to 10 rows with titles
up to 24 characters) come from third-party guides
([Meta blog](https://developers.secure.facebook.com/blog/post/2022/11/21/sending-interactive-messages/),
[360dialog](https://docs.360dialog.com/partner/messaging/interactive-messages)); they will be
checked against Meta's own reference.

---

## Your decisions - answer with the number and your choice

Each has a recommendation; "OK" means you accept it.

1. **Which service?**
   Recommended: Meta's WhatsApp Cloud API directly. (Other option: a provider such as Twilio -
   costs more and adds another company holding the messages.)

2. **How to test first?**
   Recommended: Meta's free test number + Cloudflare Tunnel (free), test data only.

3. **Login**
   Recommended: one-time login link.
   Extra question: should the WhatsApp number also have to match the user's phone in the CRM
   (if the CRM keeps one)? Recommended: no for the pilot, decide before launch.

4. **Pilot users**
   Which phone numbers may use it during the pilot? (Up to 5 with Meta's test number.)

5. **Privacy**
   Are you OK with contact details passing through Meta? (yes / no)

6. **Where it runs for real**
   The existing server, or somewhere else? (Can wait until step 6.)

7. **Start now?**
   Recommended: yes, start steps 1-4 (nothing external, nothing paid).
