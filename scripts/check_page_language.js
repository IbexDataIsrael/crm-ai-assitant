// Page check for docs/instructions19.md + instructions20.md (the user's language, the free-text
// start, search results, Cancel), without a browser:
// runs static/index.html's script against a small fake DOM and a fake server.
// NOT a real browser - layout, fonts and real RTL rendering are not checked.
//
//   node scripts/check_page_language.js
"use strict";
const fs = require("fs");
const path = require("path");

const html = fs.readFileSync(path.join(__dirname, "..", "static", "index.html"), "utf8");
const script = html.match(/<script>([\s\S]*)<\/script>/)[1];

// --- a minimal DOM ---------------------------------------------------------------
class ClassList {
  constructor() { this.set = new Set(); }
  add(c) { this.set.add(c); }
  remove(c) { this.set.delete(c); }
  contains(c) { return this.set.has(c); }
  toggle(c, on) { if (on === undefined) on = !this.set.has(c); on ? this.set.add(c) : this.set.delete(c); }
}
class Node {
  constructor(tag, attrs = {}) {
    this.tag = tag; this.attrs = { ...attrs }; this.children = []; this.parentNode = null;
    this.classList = new ClassList(); this._text = ""; this.dir = ""; this.listeners = {};
    this.disabled = false; this.value = ""; this.placeholder = ""; this.type = "";
    (attrs.class || "").split(" ").filter(Boolean).forEach(c => this.classList.add(c));
  }
  set className(v) { this.classList = new ClassList(); v.split(" ").filter(Boolean).forEach(c => this.classList.add(c)); }
  get className() { return [...this.classList.set].join(" "); }
  set textContent(v) { this._text = String(v); this.children = []; }
  get textContent() { return this._text + this.children.map(c => c.textContent).join(""); }
  appendChild(n) { n.parentNode = this; this.children.push(n); return n; }
  append(...ns) { ns.forEach(n => this.appendChild(typeof n === "string" ? Object.assign(new Node("#text"), { _text: n }) : n)); }
  insertBefore(n, ref) { n.parentNode = this; const i = this.children.indexOf(ref); this.children.splice(i < 0 ? this.children.length : i, 0, n); }
  replaceChildren() { this.children = []; }
  setAttribute(k, v) { this.attrs[k] = v; }
  getAttribute(k) { return this.attrs[k]; }
  addEventListener(type, fn) { (this.listeners[type] = this.listeners[type] || []).push(fn); }
  fire(type) { (this.listeners[type] || []).forEach(fn => fn({ preventDefault() {} })); }
  click() { if (!this.disabled) this.fire("click"); }
  scrollIntoView() {}
  all() { return [this, ...this.children.flatMap(c => c.all())]; }
  matches(simple) {
    // "tag", ".cls", "tag.cls", "[attr]"
    const attr = simple.match(/^\[([\w-]+)\]$/);
    if (attr) return this.attrs[attr[1]] !== undefined;
    const [tag, ...classes] = simple.split(".");
    return (!tag || this.tag === tag) && classes.every(c => this.classList.contains(c));
  }
  querySelectorAll(selector) {
    const found = new Set();
    selector.split(",").map(s => s.trim()).forEach(part => {
      const steps = part.split(/\s+/);
      this.all().slice(1).forEach(n => {
        if (!n.matches(steps[steps.length - 1])) return;
        if (steps.length === 1) { found.add(n); return; }
        let up = n.parentNode;
        while (up && !up.matches(steps[0])) up = up.parentNode;
        if (up) found.add(n);
      });
    });
    return [...found];
  }
}

function buildDocument() {
  const root = new Node("html", { lang: "en", dir: "ltr" });
  const body = root.appendChild(new Node("body"));
  const byId = {};
  function add(parent, tag, attrs = {}, text) {
    const n = parent.appendChild(new Node(tag, attrs));
    if (attrs.id) byId[attrs.id] = n;
    if (text !== undefined) n._text = text;
    return n;
  }
  add(body, "h1", { "data-t": "title" }, "CRM Assistant");
  add(body, "p", { "data-t": "banner_demo" });
  add(body, "p", { "data-t": "banner_live" });
  const panel = add(body, "section", { id: "login-panel" });
  const form = add(panel, "form", { id: "login-form" });
  add(form, "label", {}, "CRM username or email");
  add(form, "input", { id: "username" });
  add(form, "label", {}, "Password");
  add(form, "input", { id: "password" });
  add(form, "button", { id: "login-button" }, "Log in");
  const org = add(panel, "div", { id: "org-choice", class: "hidden" });
  add(org, "div", { id: "org-buttons" });
  add(panel, "div", { id: "login-error", class: "hidden" });
  const session = add(body, "div", { id: "session", class: "hidden" });
  add(session, "span", { "data-t": "logged_in" });
  add(session, "button", { id: "logout", "data-t": "log_out" });
  add(body, "div", { id: "log" });
  const chat = add(body, "form", { id: "form", class: "hidden" });
  add(chat, "input", { id: "message", "data-t-placeholder": "placeholder" });
  add(chat, "button", { "data-t": "send" });
  add(chat, "button", { id: "cancel", class: "hidden", "data-t": "cancel" });
  // Every id the fake builds must exist in the real page.
  Object.keys(byId).forEach(id => { if (!html.includes(`id="${id}"`)) throw new Error("page has no #" + id); });
  // instructions20: no language setting in the chat.
  if (html.includes("language-toggle")) throw new Error("page still has a language toggle");
  return {
    documentElement: root,
    title: "",
    getElementById: id => byId[id],
    querySelectorAll: sel => root.querySelectorAll(sel),
    createElement: tag => new Node(tag),
  };
}

// --- a fake server -----------------------------------------------------------------
// localStorage stand-in, shared between "page loads" when a test passes the same one.
function makeStorage(initial = {}) {
  const items = { ...initial };
  return { items, getItem: k => (k in items ? items[k] : null), setItem: (k, v) => { items[k] = String(v); },
           removeItem: k => { delete items[k]; } };
}

function makePage(browserLanguage, server, storage = makeStorage()) {
  const document = buildDocument();
  const calls = [];
  async function fetch(url, options) {
    const body = options && options.body ? JSON.parse(options.body) : null;
    calls.push({ url, body, auth: options.headers && options.headers.Authorization });
    const [status, data] = server(url, body, calls);
    return { status, ok: status >= 200 && status < 300, json: async () => data };
  }
  const run = new Function("document", "navigator", "fetch", "localStorage",
    script + "\n;return { get language() { return language; }, get token() { return token; } };");
  const page = run(document, { language: browserLanguage }, fetch, storage);
  return { document, calls, page, storage };
}

const settle = async () => { for (let i = 0; i < 20; i++) await new Promise(r => setTimeout(r, 0)); };
const $ = (doc, id) => doc.getElementById(id);
const log = doc => $(doc, "log");
const button = (doc, text) => log(doc).all().find(n => n.tag === "button" && n.textContent === text);
const results = [];
function check(name, ok) { results.push([name, !!ok]); }

function loginWith(document, user = "dana") {
  $(document, "username").value = user;
  $(document, "password").value = "pw";
  $(document, "login-form").fire("submit");
}

function turnAnswer(reply, extra = {}) {
  return [200, { intent: "ask_question", outcome: "ask", reply, ...extra }];
}
const noSave = calls => !calls.some(c => c.url === "/assistant/language");

(async () => {
  // 1. Before login the page is English only, whatever the browser; the CRM's language after login.
  {
    const { document, calls, page, storage } = makePage("he-IL", (url) => {
      if (url === "/auth/login") return [200, { status: "logged_in", access_token: "tok", language: "he" }];
      return turnAnswer("x");
    });
    const root = document.documentElement;
    check("before login: Hebrew browser -> still English, ltr", root.lang === "en" && root.dir === "ltr" && page.language === "en");
    check("before login: no Hebrew anywhere on the page", !/[\u0590-\u05FF]/.test(root.textContent));
    check("before login: no remembered login -> no session check", !calls.some(c => c.url === "/auth/session"));
    loginWith(document);
    await settle();
    check("after login: the CRM's language (he), rtl", page.language === "he" && root.lang === "he" && root.dir === "rtl");
    check("after login: nothing saved", noSave(calls));
    check("after login: the login is remembered", JSON.parse(storage.getItem("crm-assistant.login")).token === "tok");
    check("after login: no English menu", !!button(document, "שאלה") && !button(document, "Ask a question"));
    $(document, "logout").click();
    check("log out: forgotten, back to English", storage.getItem("crm-assistant.login") === null
      && page.language === "en" && root.dir === "ltr");
  }

  // 1b. A reopened page with a remembered login: checked by the server, language read again.
  {
    const storage = makeStorage({ "crm-assistant.login": JSON.stringify({ token: "kept", since: Date.now() - 86400000 }) });
    const { document, calls, page } = makePage("en-US", (url) => {
      if (url === "/auth/session") return [200, { status: "logged_in", language: "he" }];
      return [500, {}];
    }, storage);
    await settle();
    const check1 = calls.find(c => c.url === "/auth/session");
    check("reopen: the remembered token is checked with the server", check1 && check1.auth === "Bearer kept");
    check("reopen: logged in, in the CRM's language", page.token === "kept" && page.language === "he"
      && document.documentElement.dir === "rtl");
  }
  {
    const storage = makeStorage({ "crm-assistant.login": JSON.stringify({ token: "old", since: Date.now() - 86400000 }) });
    const { page } = makePage("en-US", () => [401, { status: "unauthorized" }], storage);
    await settle();
    check("reopen: a token the CRM refuses is forgotten", page.token === null && storage.getItem("crm-assistant.login") === null);
  }
  {
    const storage = makeStorage({ "crm-assistant.login": JSON.stringify({ token: "ancient", since: Date.now() - 31 * 86400000 }) });
    const { calls, page } = makePage("en-US", () => [200, { status: "logged_in", language: "en" }], storage);
    await settle();
    check("reopen: after 30 days the login is forgotten without asking", calls.length === 0 && page.token === null
      && storage.getItem("crm-assistant.login") === null);
  }
  {
    const storage = makeStorage({ "crm-assistant.login": JSON.stringify({ token: "kept", since: Date.now() }) });
    const { document, page } = makePage("en-US", () => [503, { status: "unavailable", reply: "The service is temporarily unavailable." }], storage);
    await settle();
    check("reopen: an outage keeps the token, shows the login screen with the reason",
      page.token === null && storage.getItem("crm-assistant.login") !== null
      && $(document, "login-error").textContent === "The service is temporarily unavailable.");
  }

  // 2. English browser, the CRM says he: the CRM value wins, nothing is saved.
  {
    const { document, calls, page } = makePage("en-US", (url) => {
      if (url === "/auth/login") return [200, { status: "logged_in", access_token: "tok", language: "he" }];
      return turnAnswer("שאלה?");
    });
    loginWith(document);
    await settle();
    const root = document.documentElement;
    check("CRM language he -> page he/rtl after login", page.language === "he" && root.lang === "he" && root.dir === "rtl");
    check("CRM language: no save call", noSave(calls));
    check("menu in Hebrew, bubble rtl", !!button(document, "שאלה") && log(document).children[0].dir === "rtl");
    check("logged-in line and placeholder from the dictionary",
      document.querySelectorAll("[data-t]").some(n => n.textContent === "מחוברים ל-CRM.") && $(document, "message").placeholder === "כתבו הודעה...");
    button(document, "שאלה").click();
    await settle();
    const turn = calls.find(c => c.url === "/assistant/turn");
    check("a turn sends only the CRM language (no language state, no token in the body)",
      turn.body.language === "he" && !("language_state" in turn.body) && !JSON.stringify(turn.body).includes("tok"));
    check("a menu choice is sent as an empty message", turn.body.message === "");
  }

  // 3. CRM has no value: English. Hebrew typed by the user changes nothing; nothing is ever saved.
  {
    const { document, calls, page } = makePage("he-IL", (url, body) => {
      if (url === "/auth/login") return [200, { status: "logged_in", access_token: "tok", language: null }];
      return turnAnswer(body.message ? "Answer" : "What would you like to know?");
    });
    loginWith(document);
    await settle();
    check("no CRM value -> English (not the browser's Hebrew)", page.language === "en" && document.documentElement.dir === "ltr");
    button(document, "Ask a question").click();
    await settle();
    $(document, "message").value = "מה זה נקודות יהודיות?";
    $(document, "form").fire("submit");
    await settle();
    const root = document.documentElement;
    check("a Hebrew message keeps the page English", page.language === "en" && root.lang === "en" && root.dir === "ltr");
    check("still nothing saved", noSave(calls));
    const user = log(document).children.find(b => b.classList.contains("user") && b.textContent.includes("נקודות"));
    check("user bubble dir=auto", user && user.dir === "auto");
    const turns = calls.filter(c => c.url === "/assistant/turn");
    check("every turn sends the CRM language", turns.length === 2 && turns.every(c => c.body.language === "en"));
  }

  // 4. A server answer that still carried a language state (an old server) is ignored.
  {
    const { document, page } = makePage("en-US", (url) => {
      if (url === "/auth/login") return [200, { status: "logged_in", access_token: "tok", language: "en" }];
      return turnAnswer("x", { language_state: { language: "he" }, language_notice: "השפה נשמרה." });
    });
    loginWith(document);
    await settle();
    button(document, "Ask a question").click();
    await settle();
    check("an answer cannot switch the language", page.language === "en" && !log(document).textContent.includes("השפה נשמרה."));
  }

  // 5. Login errors in English only (the login screen is English), even with a Hebrew browser.
  {
    const { document } = makePage("he-IL", () => [401, { status: "invalid_credentials" }]);
    loginWith(document);
    await settle();
    check("login error in English only", $(document, "login-error").textContent === "Incorrect username or password.");
  }

  // 6. A Hebrew session whose login ends: the login screen says so in English.
  {
    const { document, page } = makePage("en-US", (url) => {
      if (url === "/auth/login") return [200, { status: "logged_in", access_token: "tok", language: "he" }];
      return [401, { status: "unauthorized", reply: "אנא התחברו.", logout: true }];
    });
    loginWith(document);
    await settle();
    button(document, "שאלה").click();
    await settle();
    check("ended login: English login screen, English reason", page.token === null && page.language === "en"
      && $(document, "login-error").textContent === "Your CRM login is no longer valid. Please log in again.");
  }

  // 7. Cancel: shown during an operation, sends nothing, switches old buttons off, honest when unsure.
  const APT = { label: "Herzl 10, apartment 3, floor 1 (#57797)", action: "open_apartment", id: 35852, group: "apartments" };
  function searchAnswer(choices, extra = {}) {
    return [200, { intent: "search", outcome: "results", reply: "I found 1 apartment.\nApartments:\n- x",
                   prompt: "I found 1 apartment.", choices, search_state: { offer_add: false, language: "en" },
                   logout: false, ...extra }];
  }
  function apartmentServer(url, body) {
    if (url === "/auth/login") return [200, { status: "logged_in", access_token: "tok", language: "en" }];
    if (body.intent === "search") return searchAnswer([APT]);
    return [200, { intent: "edit_apartment", outcome: "confirm", reply: "Change X?\nReply yes", prompt: "Change X?",
                   state: { step: body.message === "lost" ? "blocked" : "confirm", language: "en" },
                   choices: [{ label: "Yes", message: "yes" }] }];
  }
  async function searchAndOpen(document) {
    $(document, "message").value = "Herzl";
    $(document, "form").fire("submit");
    await settle();
    const opener = log(document).all().filter(n => n.tag === "button" && n.textContent === APT.label && !n.disabled).pop();
    opener.click();
    await settle();
  }
  {
    const { document, calls } = makePage("en-US", apartmentServer);
    loginWith(document);
    await settle();
    const cancel = $(document, "cancel");
    check("cancel: hidden at the start", cancel.classList.contains("hidden"));
    await searchAndOpen(document);
    const opened = calls.filter(c => c.url === "/assistant/turn").pop();
    check("an apartment picked from the results opens by its lineage id",
      opened.body.intent === "edit_apartment" && opened.body.open_id === 35852 && opened.body.message === "");
    check("cancel: shown during an operation, in the session language", !cancel.classList.contains("hidden")
      && cancel.textContent === "Cancel");
    const sent = calls.length;
    cancel.click();
    await settle();
    check("cancel: nothing is sent", calls.length === sent);
    check("cancel: says nothing more will be sent", log(document).textContent.includes("Cancelled. Nothing more will be sent to the CRM."));
    check("cancel: the old step's buttons are off", button(document, "Yes").disabled === true);
    check("cancel: the start shown again, cancel hidden", !!log(document).children.slice(-1)[0].all().find(n => n.textContent === "Ask a question" && !n.disabled)
      && cancel.classList.contains("hidden"));
    // A change whose answer was lost: cancelling says it can't tell whether it was saved.
    await searchAndOpen(document);
    $(document, "message").value = "lost";
    $(document, "form").fire("submit");
    await settle();
    cancel.click();
    check("cancel after a lost answer: tells to check the CRM", log(document).textContent.includes("can't tell whether it was saved"));
  }

  // 8. The free-text start: no menu of actions; typed text is a search; results open in the chat.
  {
    let next = null;
    const { document, calls, page } = makePage("en-US", (url, body) => {
      if (url === "/auth/login") return [200, { status: "logged_in", access_token: "tok", language: "en" }];
      const answer = next ? next(body) : searchAnswer([]);
      next = null;
      return answer;
    });
    loginWith(document);
    await settle();
    const all = log(document).all();
    check("start: only the Ask button, no action menu", !!button(document, "Ask a question")
      && !all.some(n => ["Create a contact", "Update a contact", "Edit an apartment"].includes(n.textContent)));
    const type = async text => { $(document, "message").value = text; $(document, "form").fire("submit"); await settle(); };
    next = () => searchAnswer([
      { label: "Yosef Cohen (#5)", action: "open_person", id: 5, group: "people" },
      { label: "Yosefa Cohen (#6)", action: "open_person", id: 6, group: "people" }, APT]);
    await type("Cohen");
    const turn = calls.filter(c => c.url === "/assistant/turn").pop();
    check("typed text is sent as a search", turn.body.intent === "search" && turn.body.message === "Cohen"
      && turn.body.language === "en" && !("open_id" in turn.body));
    const text = log(document).textContent;
    check("results: count first, then People and Apartments titles", text.includes("I found 1 apartment.")
      && text.includes("People") && text.includes("Apartments"));
    check("results: each record is a button", !!button(document, "Yosef Cohen (#5)") && !!button(document, APT.label));
    check("results: no cancel after a search (nothing is open)", $(document, "cancel").classList.contains("hidden"));
    next = () => [200, { intent: "search", outcome: "not_available", reply: "Not yet.\nOpen: https://app.example.org/dashboard/contacts/5",
                         prompt: "Not yet.", choices: [], search_state: { offer_add: false, language: "en" },
                         link: { label: "Open this person's page in the CRM:", url: "https://app.example.org/dashboard/contacts/5" } }];
    button(document, "Yosef Cohen (#5)").click();
    await settle();
    const opened = calls.filter(c => c.url === "/assistant/turn").pop();
    check("a person picked from the results is sent as open_person", opened.body.intent === "open_person" && opened.body.open_id === 5);
    const link = log(document).all().find(n => n.tag === "a");
    check("person: a link to the CRM page, in a new tab", link && link.getAttribute("href") === "https://app.example.org/dashboard/contacts/5"
      && link.getAttribute("target") === "_blank" && link.getAttribute("rel").includes("noopener"));
    check("person: back to the text box (no flow open)", $(document, "cancel").classList.contains("hidden"));
    check("old result buttons are off after a new answer", button(document, "Yosefa Cohen (#6)").disabled === true);
    next = () => [200, { intent: "search", outcome: "x", reply: "x", prompt: "x", choices: [],
                         link: { label: "bad", url: "javascript:alert(1)" } }];
    await type("zz");
    check("a link that isn't http(s) is never drawn", log(document).all().filter(n => n.tag === "a").length === 1);
    // No match: the add offer. "No" is answered by the server (the offer travels in search_state).
    next = () => searchAnswer([{ label: "Yes", action: "add_person" }, { label: "No", action: "dismiss" }],
                              { outcome: "no_match", search_state: { offer_add: true, language: "en" } });
    await type("Nobody");
    next = body => searchAnswer([], { outcome: "add_declined", reply: "OK, nothing was added.", seen: body });
    button(document, "No").click();
    await settle();
    const declined = calls.filter(c => c.url === "/assistant/turn").pop();
    check("No to the offer: sent as a search turn with the offer state", declined.body.intent === "search"
      && declined.body.message === "no" && declined.body.search_state.offer_add === true);
    // Yes to the offer: the add-contact path, with cancel.
    next = () => searchAnswer([{ label: "Yes", action: "add_person" }, { label: "No", action: "dismiss" }],
                              { outcome: "no_match", search_state: { offer_add: true, language: "en" } });
    await type("Nobody");
    next = () => [200, { intent: "create_contact", outcome: "started", reply: "Let's create a new contact.",
                         state: { language: "en" }, awaiting_confirmation: null }];
    log(document).all().filter(n => n.tag === "button" && n.textContent === "Yes" && !n.disabled).pop().click();
    await settle();
    const add = calls.filter(c => c.url === "/assistant/turn").pop();
    check("Yes to the offer starts adding (empty message)", add.body.intent === "create_contact" && add.body.message === "");
    check("adding: cancel shown", !$(document, "cancel").classList.contains("hidden"));
    // A search the server hands over to the apartment flow ("change 2C to English").
    $(document, "cancel").click();
    await settle();
    next = () => [200, { intent: "edit_apartment", outcome: "choose_field", reply: "You asked...", prompt: "You asked...",
                         state: { step: "choose_field", language: "en", lineage_id: 35852 }, choices: [] }];
    await type("change 2C to English");
    next = body => [200, { intent: "edit_apartment", outcome: "x", reply: "x", prompt: "x", state: body.apartment_state, choices: [] }];
    await type("1");
    const follow = calls.filter(c => c.url === "/assistant/turn").pop();
    check("after a hand-over the next message continues the apartment flow", follow.body.intent === "edit_apartment"
      && follow.body.apartment_state.lineage_id === 35852 && !("search_state" in follow.body));
  }
  {
    // While a request is in flight the cancel button is off (a sent request can't be recalled).
    const { document } = makePage("en-US", (url) => {
      if (url === "/auth/login") return [200, { status: "logged_in", access_token: "tok", language: "he" }];
      return [200, { intent: "ask_question", outcome: "ask", reply: "מה תרצו לדעת?" }];
    });
    loginWith(document);
    await settle();
    button(document, "שאלה").click();  // the turn starts; the fake answers after a tick
    check("cancel: off while a request is in flight", $(document, "cancel").disabled === true);
    await settle();
    check("cancel: back on after the answer, in Hebrew", $(document, "cancel").disabled === false
      && $(document, "cancel").textContent === "ביטול");
  }

  let failed = 0;
  results.forEach(([name, ok]) => { if (!ok) failed++; console.log((ok ? "PASS " : "FAIL ") + name); });
  console.log(`\n${results.length - failed} of ${results.length} page checks pass (fake DOM, not a real browser)`);
  process.exit(failed ? 1 : 0);
})();
