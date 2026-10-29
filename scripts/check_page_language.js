// Page check for docs/instructions19.md (the user's language), without a browser:
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
  add(body, "button", { id: "language-toggle", class: "hidden" }, "עברית");
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
  // Every id the fake builds must exist in the real page.
  Object.keys(byId).forEach(id => { if (!html.includes(`id="${id}"`)) throw new Error("page has no #" + id); });
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
    script + "\n;return { get language() { return language; }, get state() { return languageState; }, get token() { return token; } };");
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

function turnAnswer(languageState, reply, extra = {}) {
  return [200, { intent: "ask_question", outcome: "ask", reply, language_state: languageState, language_notice: null, ...extra }];
}

(async () => {
  // 1. Before login the page is English only, whatever the browser; no toggle; the CRM's language after login.
  {
    const { document, calls, page, storage } = makePage("he-IL", (url, body) => {
      if (url === "/auth/login") return [200, { status: "logged_in", access_token: "tok", language: "he" }];
      return turnAnswer(body.language_state, "x");
    });
    const root = document.documentElement;
    check("before login: Hebrew browser -> still English, ltr", root.lang === "en" && root.dir === "ltr" && page.language === "en");
    check("before login: no toggle", $(document, "language-toggle").classList.contains("hidden"));
    $(document, "language-toggle").click();
    check("before login: a hidden toggle press changes nothing", page.language === "en" && calls.length === 0);
    const all = root.textContent;
    check("before login: no Hebrew anywhere on the page", !/[\u0590-\u05FF]/.test(all.replace("עברית", "")));
    check("before login: no remembered login -> no session check", !calls.some(c => c.url === "/auth/session"));
    loginWith(document);
    await settle();
    check("after login: the CRM's language (he), rtl", page.language === "he" && root.lang === "he" && root.dir === "rtl");
    check("after login: toggle shown, labelled English", !$(document, "language-toggle").classList.contains("hidden")
      && $(document, "language-toggle").textContent === "English");
    check("after login: nothing saved (the CRM already has it)", !calls.some(c => c.url === "/assistant/language"));
    check("after login: the login is remembered", JSON.parse(storage.getItem("crm-assistant.login")).token === "tok");
    check("after login: no English menu", !!button(document, "שאלה") && !button(document, "Ask a question"));
    $(document, "logout").click();
    check("log out: forgotten, back to English, toggle hidden", storage.getItem("crm-assistant.login") === null
      && page.language === "en" && root.dir === "ltr" && $(document, "language-toggle").classList.contains("hidden"));
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
    const { document, calls, page } = makePage("en-US", (url, body) => {
      if (url === "/auth/login") return [200, { status: "logged_in", access_token: "tok", language: "he" }];
      return turnAnswer(body.language_state, "שאלה?");
    });
    loginWith(document);
    await settle();
    const root = document.documentElement;
    check("CRM language he -> page he/rtl after login", page.language === "he" && root.lang === "he" && root.dir === "rtl");
    check("CRM language: no save call", !calls.some(c => c.url === "/assistant/language"));
    check("menu in Hebrew, bubble rtl", !!button(document, "שאלה") && log(document).children[0].dir === "rtl");
    check("logged-in line and placeholder from the dictionary",
      document.querySelectorAll("[data-t]").some(n => n.textContent === "מחוברים ל-CRM.") && $(document, "message").placeholder === "כתבו הודעה...");
    button(document, "שאלה").click();
    await settle();
    const turn = calls.find(c => c.url === "/assistant/turn");
    check("a turn sends language and language_state (no token inside)",
      turn.body.language === "he" && turn.body.language_state.language === "he" && turn.body.language_state.saved === "he"
      && !JSON.stringify(turn.body.language_state).includes("tok"));
    check("a menu choice is sent as an empty message", turn.body.message === "");
  }

  // 3. CRM has no value: the browser's language is used and never saved; a server switch re-labels the page.
  {
    const { document, calls, page } = makePage("en-GB", (url, body) => {
      if (url === "/auth/login") return [200, { status: "logged_in", access_token: "tok", language: null }];
      if (url === "/assistant/turn" && body.message) {
        return turnAnswer({ ...body.language_state, language: "he", saved: "he" }, "תשובה", { language_notice: "השפה נשמרה." });
      }
      return turnAnswer(body.language_state, "What would you like to know?");
    });
    loginWith(document);
    await settle();
    check("no CRM value -> English, nothing saved", page.language === "en" && !calls.some(c => c.url === "/assistant/language"));
    check("state saved=null", page.state.saved === null);
    button(document, "Ask a question").click();
    await settle();
    $(document, "message").value = "מה זה נקודות יהודיות?";
    $(document, "form").fire("submit");
    await settle();
    const root = document.documentElement;
    check("server switched the language -> page he/rtl", page.language === "he" && root.lang === "he" && root.dir === "rtl");
    check("toggle now shows English", $(document, "language-toggle").textContent === "English");
    const bubbles = log(document).children;
    const user = bubbles.find(b => b.classList.contains("user") && b.textContent.includes("נקודות"));
    check("user bubble dir=auto", user && user.dir === "auto");
    check("reply bubble rtl", bubbles[bubbles.length - 2].dir === "rtl");
    check("language notice shown", log(document).textContent.includes("השפה נשמרה."));
  }

  // 4. Toggle after login: saves once; back to the saved value: no call; a failed save sets the retry.
  {
    let fail = false;
    const { document, calls, page } = makePage("en-US", (url, body) => {
      if (url === "/auth/login") return [200, { status: "logged_in", access_token: "tok", language: "en" }];
      if (url === "/assistant/language") return fail ? [502, { status: "not_saved" }] : [200, { status: "saved", language: body.language }];
      return turnAnswer(body.language_state, "x");
    });
    loginWith(document);
    await settle();
    $(document, "language-toggle").click();
    await settle();
    const saves = () => calls.filter(c => c.url === "/assistant/language").map(c => c.body.language);
    check("toggle after login: one save", JSON.stringify(saves()) === '["he"]' && page.state.saved === "he");
    check("toggle: state toggled, streak 0", page.state.toggled === true && page.state.streak === 0);
    check("toggle: page relabelled at once", document.documentElement.lang === "he" && $(document, "language-toggle").textContent === "English");
    $(document, "language-toggle").click();
    await settle();
    check("toggle back: saved again (en differs from he)", JSON.stringify(saves()) === '["he","en"]');
    fail = true;
    $(document, "language-toggle").click();
    await settle();
    check("failed save: language kept, retry set", page.language === "he" && page.state.retry === "he" && page.state.saved === "en");
    check("failed save: notice shown", log(document).textContent.includes("לא הצלחתי לשמור את השפה"));
    // The menu was drawn in English before the toggles: earlier bubbles keep their own text.
    button(document, "Ask a question").click();
    await settle();
    const turn = calls.filter(c => c.url === "/assistant/turn").pop();
    check("next turn carries the retry to the server", turn.body.language_state.retry === "he");
    fail = false;
    $(document, "language-toggle").click();  // back to en, which the CRM already holds
    await settle();
    check("toggle to the saved value: no call", saves().length === 3);
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

  let failed = 0;
  results.forEach(([name, ok]) => { if (!ok) failed++; console.log((ok ? "PASS " : "FAIL ") + name); });
  console.log(`\n${results.length - failed} of ${results.length} page checks pass (fake DOM, not a real browser)`);
  process.exit(failed ? 1 : 0);
})();
