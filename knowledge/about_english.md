Jewish Points CRM - User Guide 

1. What this system is
A bilingual (Hebrew/English) CRM tailored for the needs of Chabad and Shlichus activity. It tracks the people you reach out to (called "mekuravim"), the activists who visit them (Peilim), where people live (Areas → Buildings → Apartments), and the visits made to those locations. The system also documents the work handed out (Campaigns & Tasks). Every record belongs to one organization, and the organization's users (admins, staff, campaign managers, activists) see only their own organization's data. 
2. Signing in
Open the system address and sign in with your username and password, or with Google sign-in.
New organization accounts must verify their email before first sign-in; a "resend verification link" (שליחת קישור אימות מחדש) option is on the sign-in page.
If the same email belongs to more than one organization, you will be asked to choose one after signing in.
The interface language toggles between Hebrew and English.
Forgot your password: use the reset link on the sign-in page. A link is sent to the user to reset the password
3. Who can do what (roles)
Admin - full access: all pages, user management, and organization settings.
Staff - like an admin, but cannot manage users or organization settings.
Campaign manager - manages Apartments, Tasks, Campaigns, and the Users directory; no access to Contacts, Visits, or global settings.
Activist (פעיל / shliach) - a limited, personal view: their own home page, only the apartments they cover, and their own tasks - not the general dashboard.
4. Everyday work (admin and staff)
Finding anything
The search bar at the top of every page 
Searches contacts, areas, buildings, and apartments by name, address, phone, or email, and groups the results by type.
Contacts (אנשי קשר)
The Contacts page is a filterable, sortable data grid. 
You can choose the columns to view,  reorder columns, and your choice is remembered per account; you can edit data inline and also view a contact the traditional way.
Add a new contact. Key fields: first name, last name, phone, email, status, color tag, priority score, assigned to, apartment, plus tzedaka potential, background, recommendations, and potential next step.
From the display, you can also call, open WhatsApp, email one contact or email all results, and use bulk edit.
The contact card shows contact details, additional fields (שדות נוספים), significant dates (with Hebrew date support and email reminders), donation history, and visit history.
Pending approval: contacts created through the system's assistant/API channel arrive as drafts and wait in the Pending approval section until someone approves them. They do not appear in the regular list before that. 
Logging a visit
Add a visit from the contact card. Fields: contact (optional), date (required), visit type, location, notes.
Your Chabad house can add its own visit fields (for example, a checklist) through Custom fields - they appear on the visit form. 
Summary of Visits gives totals over a chosen date range, per contact or org-wide.
Places: Areas, Buildings, Apartments
An Area can have a drawn polygon boundary on the map (תחום מוגדר); the activists covering each area are listed under it.
A Building belongs to an area and has a type (סוג בניין): regular building (with floors) or townhouse (a single unit, no floors).
An Apartment is identified by its number (מספר דירה) inside a building and floor.
Edits keep history: editing an apartment (or an activist's record) does not overwrite it - the old version is retired, and a new one is created. Old versions stay in the history.
Report numbers
A report number is an organizational grouping, independent of geography: apartments and activists attach to one, and it drives the grid sort order. Its page shows the calculated number of Jews, engaged Jews, and the covering activist.
Campaigns & Tasks 
A task gives one activist access to specific report numbers' apartments for a limited time, or carries free-text instructions.
A task can be left unassigned: print its sheet (PDF with a QR code) and whoever scans the QR claims it. The office can also enter paper results instead, which locks the task.
The Tasks page filters the orphan pool, prints one sheet per task or (print all displayed tasks), and supports reassign/unassign. Assigning a task emails the activist automatically.
Donations
Donation history with amount (סכום), date (תאריך), standing order (הוראת קבע), number of payments, and notes; a donation can be linked to a contact or left unlinked.
Reports 
Create Reports: pick one or more report numbers and "הפק דוח PDF" produces an apartment summary PDF (area, building, floor, apartment number, Jewish status, mezuzah status, notes), in Hebrew or English.
AI query
Admin/staff can ask questions in plain language (for example, ai query about contacts or visits). The question is converted into a limited choice of table and filters from an approved list, scoped to your organization - not free-form SQL written by the model. Activists have no access to this page or its endpoint. The feature can be switched off per organization. [TO COMPLETE: example questions your team finds useful]
5. The activist experience
How activists join
An admin adds them (הוסף פעיל) in Users or Activists, with email for login - or without email (no login, but they can still be assigned tasks), or
they self-register on the organization's public page (when enabled in Settings), or
they scan a task sheet's QR code and register through it.
What an activist sees
Their own home page (ברוך הבא): my apartments (הדירות שלי) with the count of assigned apartments, my tasks (המשימות שלי), start route (התחל סיבוב), edit my details (עריכת הפרטים שלי), and updates from the office (עדכונים).
Two feedback buttons: report completed (דיווח על השלמת הדוח) and comments about the system (הערות על המערכת). The office reviews these in Messages from activists.
An activist sees only the apartments they cover - nothing else.
6. Setting the system up (admin)
Picklists - the options of every dropdown (status, Jewish status, visit type, and more). You can rename, reorder, and add options. Built-in lists cannot be deleted, and an option cannot be removed while records use it.
Custom fields  - your own free-typed fields (text/number/date/yes-no) on contacts, visits, apartments, tasks.
Manage fields - show/hide fields org-wide: a hidden field is removed from the contact card, the form, and the list.
Translations  - edit any interface text in Hebrew and English (takes effect after refresh).
Configuration for Activist Role - what activists see and may edit, per-field edit locks, the announcements feed, the task assignment email text, and the messages shown to activists.
Settings  - organization name and logo, default map center, the public page (address, subtitle, photo gallery) and whether self-registration is open, email templates, and database backups (the system keeps the last 10).
7. Good to know
Significant-date reminders are emailed automatically to the covering activist or assigned user.
The activity log (if enabled for your organization) records actions and page views for 90 days.
Server and browser errors are recorded in the system's error log, and notification emails to the team's address are configured. If something breaks, note the time and what you clicked so it can be found in the log. 

