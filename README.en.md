<p align="center"><img src="agent/static/axiorhub-icon.png" alt="AxiorHub Pilote" width="96"></p>

<h1 align="center">AxiorHub Pilote</h1>

<p align="center"><strong>The AI execution agent for law firms</strong><br>
Emails, case files, exhibits, deadlines, calendar and draft documents, on your own server, with a local AI or a pseudonymised external AI.</p>

<p align="center">Created by <strong>Timo RAINIO</strong>, member of the Lyon Bar · Licence <a href="LICENSE">AGPL-3.0-or-later</a> · Version 5.6.45</p>

<p align="center"><a href="README.md">Français</a> · <strong>English</strong> · <a href="README.de.md">Deutsch</a> · <a href="README.es.md">Español</a></p>

<p align="center"><a href="docs/GUIDE-POSTE-10-MINUTES.md">Ubuntu 24.04 workstation: up and running in ten minutes (in French)</a> · <a href="docs/INSTALLATION-WINDOWS.md">Windows 11 workstation (in French)</a></p>

<p align="center"><img src="docs/captures/en/01-aujourdhui.png" alt="Today: the AxiorHub Pilote cockpit" width="900"></p>

---

> The software interface is available in French (default), English, German and Spanish. The legal content it produces
> (deadlines, procedure, legal sources) is designed for **French law** and French courts. The detailed guides in `docs/`
> are written in French.

AxiorHub Pilote reads the firm's mailbox and case files, understands what comes in and **prepares the work**:
draft replies, draft documents, exhibit lists, deadlines, tasks. It sends nothing, signs nothing and files nothing.
Every output is a draft that the lawyer reviews, corrects and approves.

| | |
|---|---|
| **For whom** | Lawyers practising alone or in small firms, with their associates and assistants. |
| **Where** | On the firm's server (a VPS is enough) or on a Windows or Ubuntu workstation: the mailbox and Nextcloud remain yours. |
| **Which AI** | Local (Ollama), external (Anthropic, OpenAI, Mistral…) **pseudonymised**, or both. |
| **What it does not do on its own** | Send an email (only your click on “Send” does, if you have allowed it), sign, file, set a procedural date. |
| **Sources** | Checked as far as possible, never guaranteed: see “[Sources and checks](#sources-and-checks-what-is-verified-what-remains-yours)”. |

## What the agent does

### The cockpit

The "Today" screen gathers what needs the lawyer's attention:

- an **input box**: "Prepare the reply to opposing counsel about the procedural timetable in the LEROY case".
  The agent guesses the case file, which you can correct. You can attach a document or dictate (microphone);
- **To review**: draft emails and documents ready to open in one click;
- **What the agent is doing**: work in progress, finished or blocked, with the name of the exhibit or request handled;
- **My day**: today's hearings and appointments, Nextcloud tasks (the agent's and yours), Deck board;
- **Routines**: morning briefing, morning email triage, weekly review. The briefing also comes as a written
  **spoken summary**, to read or to **listen to** (the firm's speech synthesis: Kokoro, Chatterbox or espeak);
  every number in the summary is checked against the briefing;
- **Legal watch**: dated and sourced updates on your topics;
- **Firm style**: suggested writing habits, to be approved.

A banner at the bottom of the page shows the agent's activity live. A dark mode is available.

<p align="center"><img src="docs/captures/en/07-pilote.png" alt="The Pilot: one instruction, one expected result" width="900"></p>

### Review and approve in one click


Each draft opens in a panel: full text, cited sources, "TO BE COMPLETED" items. You can:
- **approve** the draft;
- **ignore** it;
- **open it in the editor** (Word online with OnlyOffice) or in Nextcloud;
- ask for a **new version** in one sentence ("firmer tone", "add the hearing date").

The agent learns from these decisions.

### Know exactly what the agent does


Each task opens on its detail:
- the case file;
- the exhibit analysed, the document requested or the email concerned;
- the steps completed and the result;
- links to open the file produced;
- a "Retry" button if it is blocked.

The "Why is nothing produced?" screen explains any failure: services, work queue, errors and their cause,
connections, automatic acceptance tests.

### Emails

- **Triage** of incoming messages: email from a client, opposing counsel, a court, an RPVA notification, advertising,
  mailing list, automatic message.
- **Linking to the right case file** based on correspondents, references and history; when in doubt, a suggestion
  is made and the confirmed link is then used for later messages.
- **Draft replies** placed in the Drafts folder of your mailbox (IMAP), in the original thread, with your signature.
  Nothing is sent.
- **"Emails to review" workshop**:
  - edit the text, recipient and subject;
  - dictate corrections;
  - ask for a rewording ("shorter", "more courteous", "add a reminder of the date");
  - check legal citations;
  - save a rule "always do it this way".
- **Mailbox** ("Inbox" tab of "Emails"): mailbox folders (inbox, sent, drafts, trash, sub-folders), list and reading
  side by side, readable attachments; "Ask the agent for a reply" for a chosen email, with a case file, without one or by
  creating a new case file, an instruction and the text of the attachments.
- **Mail functions**: reply, reply all, forward, new message (To, Cc, Bcc, formatted text, attachments), archive, move,
  junk, delete (trash), read / unread, follow-up.
- **Mail settings**: formatted signature (new messages, replies, forwards), font and size, **automatic Bcc** on every
  message (including the agent's drafts) and **filing rules**: depending on the subject, the sender or a recipient, the
  incoming email is moved to the chosen sub-folder after the agent has read it.
- **Sending**, disabled by default. When enabled, it goes through a confirmation summarising recipients, subject and
  attachments.
- **Why no draft?** Each unprocessed email shows its reason (message from the firm, mailing list, uncertain case file,
  lawyer's decision needed…) and how to fix it.

### Documents and draft deeds

- **Natural-language requests**: pleadings, summons, petition, letter, formal notice, memo, opinion, contract. The draft
  is written from the case file (exhibits, facts, chronology) and the **firm's templates**.
- **Automatic filing** in the case folder tree (PROCEDURE, CORRESPONDANCES, PROJETS, LIVRABLES).
- **Working folders** added by the lawyer: for example `NOS PIECES` (our exhibits) and
  `PIECES ADVERSES - {{partie_adverse01}}` (opposing exhibits), the name being completed with each case's opposing
  parties. The AI knows which side each exhibit comes from.
- **Whole-case analysis**: each exhibit has a summary card (dated facts, claims, amounts, references, with the page);
  the AI examines every exhibit at the level of detail its working memory allows, and coverage is displayed.
- **New versions** on instruction, without overwriting the original, including for a document you wrote.
- **Online editing** of the Word document in the browser (OnlyOffice), without downloading.

### Exhibits and exhibit lists

- Reading the case PDFs, suggesting the **order**, **titles** and **dates**.
- **Numbering** and "Pièce n° X" **stamp** on the pages, Word and PDF **exhibit list** from your template.
- **Single PDF** with one bookmark per exhibit.
- **Renumbered references** in the pleadings, with tracked changes.
- **Splitting for e-Barreau filing** according to the maximum upload size.
- Everything runs locally, without AI, and only after your confirmation code. Original files are never modified.

### Deadlines, calendar and tasks

- **Procedural documents → calendar → drafts**: on the case page, choose the documents received; the agent extracts the
  dates they quote, you choose which to record and in which calendars (Nextcloud, CalDAV, Google), then it prepares the
  information drafts for the client and opposing counsel. Nothing is sent.
- **Procedural notices** (adjournment, summons, timetable): dates are extracted and checked in the text, recorded in the
  Nextcloud calendar, and an information email is prepared.
- **Procedural deadlines**: the triggering event (service, notification, notice of appeal…) is located in the exhibit.
  The time limit is calculated by deterministic rules, never by the AI. Each date is proposed "to be confirmed" with its
  calculation, then reminders are issued in cascade.
- **Limitation and foreclosure periods**: versioned rules with their articles, detailed step-by-step calculation.
- **Add an event** from "Calendar and tasks", in one or more configured calendars (Nextcloud, CalDAV, Google Calendar),
  with each deposit read back.
- **Tasks**: simple list grouped by due date (overdue, today, this week, later, no due date), filters by period and case
  file, adding a task right next to it.
- Nextcloud **calendar and tasks** (CalDAV) and **Deck**: work plan per case, agent tasks and personal tasks, cards that
  follow the progress of production.

<p align="center"><img src="docs/captures/en/03-agenda.png" alt="Calendar and tasks" width="900"></p>

### Case files

- **Case page**: one clear page. The work sheet prepared by the agent gives the summary, the written statement of facts,
  the parties (capacity, counsel, firm's client), the procedure and the chronology of decisive events, each item with its
  exhibits; the lawyer corrects it in place. Next to it: work with the agent in the case (letter, memo, pleadings,
  question), parties, procedure, latest emails.
- **Control of the agent per case**: "Pause the AI" (no more automatic work), standing instructions followed by the
  agent, automatic retries suspended after three consecutive failures.
- Unified **chronology** (emails, exhibits, deeds, hearings).
- **Case facts** extracted from the exhibits (docket number, amounts, dates, parties), each with its source, to approve.
- **Search** across the whole firm (emails, documents, case files).
- **Meeting preparation**: sourced sheet before the meeting, draft minutes afterwards.

<p align="center"><img src="docs/captures/en/02-dossiers.png" alt="The firm's case files" width="900"></p>

### Practice

- **Time and fees**: time entries proposed from actual activity, counted only once approved; budgets and alerts.
- **Conflicts of interest**: search for the parties in existing case files, sourced report; no production for a new
  case file until the lawyer has decided.
- **Traceable legal research**: a decision is read again on the enabled official source before being shown as verified;
  otherwise it stays a lead to check.
- **Legal sources**: connectors JusticeLibre (no key), OpenLegi, GoodLegal, LibreJustice and Legal Data Hunter, checked
  (connection, search, reading a text) before being enabled.
- **Reference check** in a document ("Verify"): articles, statutes, decisions and quotations are extracted and checked
  against the enabled source, at the relevant date; each reference is marked checked, identified, to verify or anomalous.
- **Scheduled legal watch**: topics, official feeds, legal source and Tavily Internet search (optional, capped); a frame
  in "Today" and a reading page.
- **AI skills**: written methods the AI follows for a type of work. Three are built in (including Christophe
  Quézel-Ambrunaz's [legal assistant](resources/skills/assistant-juridique/PROVENANCE.md), AGPL-3.0); you can create,
  import (SKILL.md or .zip, text only) or copy them to adapt them.
- **Hearing preparation** and analysis of the opposing party's submissions (supervised drafts).

### Initiatives, documents, AI tools and corporate secretarial work (5.6.36)

- **Initiatives**: when a writ of summons arrives, the agent proposes a plan (case file “CLAIMANT - DEFENDANT - reference”, attachments
  filed, parties involved, hearing in the calendar, email to the client, note on the opposing arguments, draft notice of appearance,
  request for documents to opposing counsel, letter to the court). You correct the extracted details, untick what is not needed and
  approve: only ticked steps run, as drafts.
- **Document data**: type, date, parties, key dates, amounts, cited texts and claims of each document, each marked “documented” or
  “to check”, searchable across the firm; email attachments filed in the case file.
- **AI tools**: analysis grids (the same criteria extracted from each document, with the excerpt behind each value), negotiation
  playbooks applied to a Word contract as tracked changes, a library of sourced legal memos approved by a lawyer.
- **Corporate**: “Approval of accounts” and “Other company law matter” (registered office, directors, share transfer, capital,
  dissolution…); one entry updates the record, cap table and registers, then prepares the deeds (firm or generic templates). Your own deeds and Word
  templates can be added, with variables such as `{{ denomination }}`. Nothing is signed or filed.
- **Contacts & CRM**: clients, parties, fellow lawyers and courts linked to each case file with their role.
- **Payment reminders**: every morning, reminder drafts for overdue Invoice Ninja invoices, reviewed before anything is sent.
- **Microsoft**: Word add-in (playbooks, memos, rewording in the open document) and Microsoft 365 calendar.

<p align="center"><img src="docs/captures/en/04-corporate.png" alt="Corporate: corporate secretarial work" width="900"></p>

### Firm style

The agent reads your final documents (procedure, correspondence, drafts) and your sent emails, then suggests your habits:
opening and closing formulas, usual structure of pleadings, numbering of parts, presentation of cited decisions, heading
of the operative part, recurring phrases. Each suggestion says what it will change; once approved, they apply to drafts
and documents. The usual length is only a guide passed on automatically. No sentence containing a name, number or
address is kept.

### An external AI, but pseudonymised

<p align="center"><img src="docs/captures/en/09-ia-externe.png" alt="Settings › Artificial intelligence: local models, routing, pseudonymised external AI" width="900"></p>

Before anything is sent to a non-local AI, names, companies, addresses, phone numbers, emails, IBANs and numbers are
replaced by markers (`[PERSONNE_1]`, `[SOCIETE_1]`…). The mapping table stays on the server and the answer is restored
locally. The **preview** shows exactly what would be sent. Three modes are available:
- **all local**;
- **mixed**: triage and reading locally, drafting via the API;
- **all via the API**, for a VPS without a graphics card.

**External fallback** (optional): if the local model takes more than 3 minutes for drafting or one of your requests, or if
the request has waited longer for its turn, it is handed to the first ready provider (Mistral, then Claude, ChatGPT,
OpenRouter), always pseudonymised. Email triage and checks remain local.

### Guided installation and firm accounts

<p align="center"><img src="docs/captures/en/06-guide-installation.png" alt="Setup guide" width="440">
&nbsp;<img src="docs/captures/en/05-parametres.png" alt="Settings › Connections" width="440"></p>

- **Installation guide** step by step: firm, mailbox, Nextcloud, AI and operating mode. Each connection is tested and
  receives a receipt.
- **Accounts and roles**: administrator, lawyer, assistant. Rights are enforced by the server and every operation is
  logged (see [docs/COMPTES-ET-ROLES.md](docs/COMPTES-ET-ROLES.md), in French).

### And also

- **Mobile app** (installable from the browser) with notifications: emails to approve, deadlines, search.
- **Local voice dictation** (optional gateway) for instructions and corrections.
- **Integrations** with Roundcube (button in the webmail) and Open WebUI (tool for conversations).
- **Verified updates** (SHA-256 manifest, signature, rollback) and **automatic acceptance tests** after each installation.
- **Interface in four languages** (French by default, English, German, Spanish), "All functions" menu and user guide
  linked to the actual screens.

<p align="center"><img src="docs/captures/en/08-mode-sombre.png" alt="Dark mode" width="700"></p>

## Sources and checks: what is verified, what remains yours

AxiorHub reduces the risk of error; it does not remove it. An AI model can be wrong, summarise inaccurately or attribute a
sentence to the wrong document. The software says so, checks what it can check and flags the rest.

**Available checks**
- **Facts, dates and amounts from documents**: each item quotes an excerpt; a date or amount is marked “documented” only if it
  appears in that excerpt (same currency, same sign), otherwise “to check”.
- **Decisions and texts**: a decision found by a search connector is read again on the enabled official source before being
  shown as verified; if it cannot be read, it stays a “lead”.
- **References in a document** (“Check”): articles, statutes, decisions and quotations are listed and checked against the
  enabled source, at the relevant date: checked, identified, to check or anomalous.
- **Spoken summary of the briefing**: every number is compared with the written briefing.

**Limits**
- Excerpt matching is textual: a paraphrase by the model stays “to check”.
- A badly scanned document (OCR) can distort an excerpt; an unavailable source leaves the reference “to check”.
- The legal meaning of a sentence, its scope and whether it is still good law are not guaranteed by these checks.
- Translations of the interface and reports are not reviewed by a legal translator.

**What remains yours**
Open the official source before any citation, review every draft before validating it, and resolve any “to check” item before
use. No AxiorHub output replaces your professional review.

## Privacy first

- **One installation = one firm.** Data stays on your server (mailbox, Nextcloud, local database).
- **Local AI by default.** An external AI is enabled only with your explicit consent, and any text that leaves is
  pseudonymised.
- **No data is sent to the author of the software.** No telemetry.
- The screenshots in this document use **fictitious data only**.

Details: [docs/CONFIDENTIALITE.md](docs/CONFIDENTIALITE.md) (in French).

## Installation

AxiorHub Pilote comes in three versions. **For your firm's work, choose the server version**; the workstation versions are for a single
lawyer on his or her own computer, or for a trial. The detailed guides are in French.

| Version | For | Access | Detailed guide |
|---|---|---|---|
| **Linux server** (Ubuntu 24.04 LTS, Debian 12) — **recommended** | the firm, all its computers, the Nextcloud app | HTTPS, from every computer | below, [turnkey VPS](docs/INSTALLATION-VPS.md), [existing server](docs/INSTALLATION-SERVEUR.md), [Docker](DOCKER-INSTALLATION.md) |
| **Windows 11 workstation** | one lawyer on his or her computer, trial | this computer only | [docs/INSTALLATION-WINDOWS.md](docs/INSTALLATION-WINDOWS.md) |
| **Ubuntu 24.04 workstation** | one lawyer on his or her computer, trial | this computer only | [docs/GUIDE-POSTE-10-MINUTES.md](docs/GUIDE-POSTE-10-MINUTES.md) |
| **Nextcloud ExApp** (development 0.1, tests only) | engine installed from the Nextcloud App Store, in a container managed by Nextcloud | Nextcloud accounts, from every computer | [AxiorHub-Pilote-ExApp](https://github.com/Arthur-Llevelys/AxiorHub-Pilote-ExApp) |

### 1. Server version (Linux) — recommended

**Requirements**: an Ubuntu 24.04 LTS or Debian 12 server (64-bit) with `sudo` access; at least 8 GB of memory without local AI (32 GB
and ideally a graphics card for local AI); 80 GB of disk; a domain name with HTTPS; the firm's mailbox settings (IMAP, SMTP); Nextcloud
(already in place, or installed by the VPS script).

**New server (VPS)**: AxiorHub Pilote, Nextcloud, Roundcube, OnlyOffice, Apache and Let's Encrypt HTTPS in one go — follow the
[full installation on a VPS, step by step](#full-installation-on-a-vps-step-by-step) below.

**Existing server** (Nextcloud and Apache already in place):

```bash
# 1. Prepare the server
sudo apt update && sudo apt full-upgrade -y
sudo apt install -y git python3 python3-venv

# 2. Download AxiorHub Pilote and build the verified archive (checksum manifest checked by the installer)
sudo git clone --branch v5.6.45 https://github.com/Arthur-Llevelys/AxiorHub-Pilote.git /opt/axiorhub-src
cd /opt/axiorhub-src
sudo python3 scripts/build-release.py /tmp/axiorhub-mail-agent-5.6.45.tar.gz

# 3. Install (or update): code in /opt/axiorhub-mail-agent, configuration in /etc/axiorhub-mail-agent
cd /tmp && tar -xzf axiorhub-mail-agent-5.6.45.tar.gz && cd axiorhub-mail-agent-5.6.45
sudo bash install.sh

# 4. Configure the mailbox, Nextcloud, calendars and the AI (passwords typed hidden), then check
sudo axiorhub-mail configure
sudo axiorhub-mail doctor

# 5. Publish the interface under the existing HTTPS name (https://cloud.your-firm.example/agent-courriel/)
sudo python3 /opt/axiorhub-mail-agent/current/install-interface.py
```

Alternative: the Debian package `axiorhub-pilote_5.6.45_all.deb` (`sudo apt install ./axiorhub-pilote_5.6.45_all.deb`, built by
`scripts/build-deb.py`, see [Development](#development)) replaces step 3; steps 4 and 5 remain. To update, run the new version's archive
(`sudo bash install.sh`); if it fails, the previous version stays active (rollback: `sudo python3 upgrade.py --rollback`).

### 2. Windows 11 workstation

**Requirements**: 64-bit Windows 11 and Microsoft Edge (built in); [Ollama for Windows](https://ollama.com/download/windows) or an
external AI provider; optional: Tesseract OCR, LibreOffice. Python is included.

1. Download `AxiorHub-Pilote-Setup-5.6.45.exe` and its `.sha256` file from the repository's *Releases* page, or build them
   (`python scripts\build-windows.py dist\windows`).
2. Check the checksum in PowerShell: `Get-FileHash .\AxiorHub-Pilote-Setup-5.6.45.exe -Algorithm SHA256`.
3. Run the installer (no administrator rights needed). If SmartScreen shows “Windows protected your PC”: **More info › Run anyway**.
4. Open **AxiorHub Pilote** from the Start menu: the window opens, already signed in, on the first settings.

Data: `%LOCALAPPDATA%\AxiorHub Pilote\donnees`; update: run the new version's installer (data are kept).

### 3. Ubuntu 24.04 workstation

**Requirements**: 64-bit Ubuntu 24.04 and a `sudo` account; Ollama with a model (on this computer, GPU recommended, or on another
machine of the firm).

```bash
# Ollama and a model (about 5 GB to download)
curl -fsSL https://ollama.com/install.sh | sh
ollama pull qwen3:8b

# AxiorHub Pilote (package and its .sha256 from the repository's Releases page)
sha256sum -c axiorhub-pilote-poste_5.6.45_amd64.deb.sha256
sudo apt install ./axiorhub-pilote-poste_5.6.45_amd64.deb
```

Start **AxiorHub Pilote** from the applications menu (or `axiorhub-pilote` in a terminal): the window opens, already signed in, on the
setup guide. Local password: `axiorhub-pilote --print-password`.

The workstation versions only listen on their own computer (`http://127.0.0.1:8769`), without HTTPS or automatic backups: for a firm,
prefer the server version.

### 4. First settings (all versions)

On first opening, the **Setup guide** (Settings › Setup guide) takes you one screen at a time: functions used, profile, contact details,
language and time zone, voice, summary. Only the steps of the chosen functions are shown; nothing is sent or written to the mailbox
during the guide.

<p align="center"><img src="docs/captures/en/06-guide-installation.png" alt="Setup guide, step 1: your functions" width="440">
&nbsp;<img src="docs/captures/en/05-parametres.png" alt="Settings › Connections" width="440"></p>

Then **Settings › Connections**:

1. **Case files**: local working folder (“📁 Choose…”, for example a folder synchronised by the Nextcloud client) or Nextcloud (HTTPS
   address, login, app password);
2. **Artificial intelligence**: Ollama (local or on a machine of the firm) or an external provider, always **pseudonymised**;
3. **Mail** (optional): IMAP (port 993, or 143 with STARTTLS), Drafts folder;
4. **Calendars** (optional): Nextcloud, CalDAV or Google Calendar.

Each section has its own test button. Start in **observation** mode (nothing is written), switch to **drafts** mode after a few days,
then enable the automations one by one.

### 5. Nextcloud app “AxiorHub Pilote”

The [Nextcloud app](https://github.com/Arthur-Llevelys/AxiorHub-Pilote-Nextcloud/blob/main/README.en.md) shows the AxiorHub interface
inside Nextcloud (Nextcloud App Store: **Apps › Office & text › AxiorHub Pilote**). It requires the **server version**; enable it in
**Settings › Connections › Nextcloud app**, then each lawyer links his or her account in Nextcloud (Personal settings › AxiorHub Pilote).

With a **workstation version**, it only works in **discovery mode, for tests**: Nextcloud installed on the same computer (address
`http://127.0.0.1:8769/agent-courriel`, permanent warning banner), or a simple “My local AxiorHub” link from a hosted Nextcloud. AxiorHub
can then only be reached from that computer and local exchanges are not encrypted: never for production.

### Full installation on a VPS, step by step

Allow about an hour, including domain name propagation. The examples use the domain `your-firm.example`: replace it
everywhere with your own.

1. **Rent the server**: Debian 12 or Ubuntu 24.04; at least 8 GB of memory without local AI (32 GB and ideally a
   graphics card for local AI); at least 80 GB of disk; a data centre in the European Union. Add your SSH key and note
   the server's IPv4 (and IPv6) address.
2. **Domain name**: keep the firm's existing domain if it has one; only four sub-domains are added.
3. **DNS zone**: create an **A** record (and **AAAA** if you have IPv6) pointing to the VPS for `agent` (AxiorHub
   Pilote), `cloud` (Nextcloud), `webmail` (Roundcube) and `office` (OnlyOffice). Do not touch the **MX** records.
   Check with `nslookup agent.your-firm.example`.
4. **First connection**:

   ```bash
   ssh debian@VPS_IP
   sudo apt update && sudo apt full-upgrade -y
   sudo apt install -y git unattended-upgrades
   sudo dpkg-reconfigure -plow unattended-upgrades
   ```

   Open ports **22**, **80** and **443** if your host uses a network firewall.
5. **Download**:

   ```bash
   sudo git clone https://github.com/Arthur-Llevelys/AxiorHub-Pilote.git /opt/axiorhub-src
   cd /opt/axiorhub-src
   ```

6. **Install**: `sudo bash deploy/vps/install-vps.sh`. The script asks for the firm's domain, the four addresses, the
   Let's Encrypt email, the mailbox IMAP/SMTP servers and whether to enable local AI. It installs Docker, Apache and
   Certbot, creates `deploy/vps/.env` **generating all passwords**, starts the containers, obtains the HTTPS certificates
   and configures Nextcloud (Calendar, Tasks, Deck, Contacts, OnlyOffice). It can be run again safely.
7. **The `.env` file** holds the settings and passwords; it is readable only by the administrator and must **never** be
   published or emailed. After any change: `cd /opt/axiorhub-src/deploy/vps && sudo docker compose --env-file .env up -d`.
8. **Nextcloud**: sign in as `admin`, create an account for each member of the firm and a technical account `axiorhub`,
   share the case-files folder (for example `/Dossiers`) and the firm's calendars with it, and create an **app
   password** for `axiorhub`.
9. **Mailbox**: have the IMAP server and port (usually 993), the login and password (or an app password) and the exact
   names of the Drafts and Sent folders ready.
10. **First administrator**: read the private sign-up code with
    `sudo cat deploy/vps/data/axiorhub/auth/bootstrap-token`, open `https://agent.your-firm.example/signup`, then follow
    the **installation guide** (firm, mailbox, Nextcloud, AI, mode). Start in **observation** mode.
11. **Getting started**: leave AxiorHub Pilote in observation for a few days, check the links between emails and case
    files, then switch to **drafts** mode and enable automations one by one.
12. **Check, back up, update**:

    ```bash
    cd /opt/axiorhub-src/deploy/vps
    sudo docker compose --env-file .env ps
    echo '30 2 * * * root bash /opt/axiorhub-src/deploy/vps/backup.sh /var/backups/axiorhub' | sudo tee /etc/cron.d/axiorhub-backup
    cd /opt/axiorhub-src && sudo git pull && cd deploy/vps && sudo docker compose --env-file .env up -d --build
    ```

    Backups contain client data: **encrypt them, copy them off the server** and test a restore.

## Architecture

```
            Internet (HTTPS, Let's Encrypt)
                         │
                  Apache (virtual hosts)
     ┌──────────────┬───────────┴───┬────────────────┐
 agent.…         cloud.…        webmail.…        office.…
 AxiorHub Pilote Nextcloud      Roundcube        OnlyOffice
 (8626)          (8080)         (8081)           (8082)
   │  workers, IMAP monitoring          │
   ├── firm IMAP/SMTP ──────────────────┘
   ├── Nextcloud WebDAV / CalDAV
   └── AI: local Ollama (option) │ pseudonymised external API (option)
```

See [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) (in French).

## Development

```bash
python3 -m unittest discover -s tests -p 'test_*.py' -q
python3 scripts/privacy-scan.py
python3 scripts/build-release.py dist/axiorhub-mail-agent-5.6.45.tar.gz
python3 scripts/build-deb.py dist/axiorhub-pilote_5.6.45_all.deb --archive dist/axiorhub-mail-agent-5.6.45.tar.gz
python scripts/build-windows.py dist/windows   # on Windows, after pip install -r scripts/windows-requirements.txt
```

Python 3.11 or later, standard library for the core, Waitress for the web server; Poppler, Tesseract and LibreOffice
for PDFs, OCR and documents. Contributing: [CONTRIBUTING.md](CONTRIBUTING.md) · Security: [SECURITY.md](SECURITY.md) ·
History: [CHANGELOG.md](CHANGELOG.md).

## Licence and attribution

The code is distributed under the **GNU Affero General Public License v3.0 or later**. You may use, study, modify and
redistribute it; if you offer a modified version to users over a network, you must give them access to its source code.

Additional condition (section 7 b of the AGPL): any modified or redistributed version keeps the notice
**"AxiorHub Pilote — créé par Timo RAINIO"** in the `NOTICE` file, the "About" page and the menu footer.

The AxiorHub Pilote name and logo are not covered by the AGPL: see [TRADEMARKS.md](TRADEMARKS.md) and
[LOGO-LICENSE.md](LOGO-LICENSE.md). A modified version must carry its own name.

> AxiorHub Pilote is a preparation tool. It replaces neither the lawyer's judgement nor their responsibility: every output
> must be reviewed before sending, signing, filing or communicating it to a third party.
