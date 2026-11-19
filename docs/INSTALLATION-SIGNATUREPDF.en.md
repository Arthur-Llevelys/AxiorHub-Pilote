# SignaturePDF: signing by link, step-by-step installation (5.6.108)

[SignaturePDF](https://github.com/24eme/signaturepdf) is free software by 24ème (**AGPL-3.0** licence, PHP web application) for signing a
PDF in the browser, alone or **with several people through a shared link**. AxiorHub can use it as an **optional service of the server
edition**: on a PDF of a matter, the **“Faire signer par lien”** (have it signed by link) button uploads the document to the firm's
SignaturePDF and gives you the link to send to the signers; the signed PDF then comes back into the matter, **next to the original**.

SignaturePDF is not embedded in AxiorHub's pages: it is installed **alongside**, on its own HTTPS subdomain, like the document server
([INSTALLATION-EDITEUR-DOCUMENTS.en.md](INSTALLATION-EDITEUR-DOCUMENTS.en.md)). Workstation editions (Windows 11, Ubuntu 24.04) can use
it too: the typical case is a workstation uploading to the HTTPS instance on the firm's server. A local instance is possible (section H),
but its links only open on that computer.
The French guide ([INSTALLATION-SIGNATUREPDF.md](INSTALLATION-SIGNATUREPDF.md)) is the reference; commands are identical.

## Before you start

| Item | Value |
|---|---|
| Software | SignaturePDF, repository `https://github.com/24eme/signaturepdf`, branch `master` (stable branch according to the publisher) |
| Licence | AGPL-3.0 (see G) |
| Server | Ubuntu 24.04 with Apache, the server already hosting AxiorHub (and often Nextcloud) |
| Name | a dedicated subdomain, here `signature.example.test` (replace with yours) |
| PDF size | 24 MB at most (publisher's recommended setting) |
| Key or password for AxiorHub | none: SignaturePDF uses none; its exchange interface is protected by Apache (step A7) |

**How AxiorHub talks to SignaturePDF.** The publisher documents no programming interface. The public code does contain exchange addresses
(`/api/share/new` to upload a PDF and create the share, `/api/share/get/…` to read back the signed PDF, `/signature/…/nblayers` to count
signatures, `/api/share/delete/…` to delete a share). AxiorHub only uses them if “Tester la connexion” (test the connection) finds them;
otherwise the path stays **manual**: AxiorHub opens SignaturePDF in a new tab, you upload the PDF there, then file the signed PDF with
“Déposer le PDF signé” (upload the signed PDF). As these addresses are undocumented, run the test again after each SignaturePDF update.

**Why not the Apache setup of the official documentation?** It loads PHP inside Apache (`php_value`, `mod_php`). On a server already
running Nextcloud with PHP-FPM, installing `mod_php` can change how Apache runs for every site. This guide therefore uses PHP-FPM with a
**dedicated pool** (system account `signaturepdf`, with no rights on Nextcloud or AxiorHub); size limits are the publisher's.

## A. Install on Ubuntu 24.04 with Apache

**A1. DNS.** Create an `A` record (and `AAAA` with IPv6) for `signature.example.test` pointing to the server's public address.
*Expected result*: `getent hosts signature.example.test` prints the server's address.

**A2. Packages** (official list, plus PHP-FPM and Git):

```bash
sudo apt update && sudo apt install -y php-fpm php-cli librsvg2-bin pdftk imagemagick potrace ghostscript locales gpg git
```

*Expected result*: `php -v` prints PHP 8.3; `pdftk --version`, `rsvg-convert --version`, `potrace --version` and `gs --version` answer.

**A3. Dedicated system account and folders** (code owned by root; only shared PDFs are writable by `signaturepdf`):

```bash
sudo useradd --system --home-dir /var/lib/signaturepdf --shell /usr/sbin/nologin signaturepdf && sudo install -d -o signaturepdf -g signaturepdf -m 700 /var/lib/signaturepdf /var/lib/signaturepdf/partages
```

*Expected result*: `sudo ls -ld /var/lib/signaturepdf/partages` shows `drwx------ signaturepdf signaturepdf`.

**A4. Get the code** (branch `master`):

```bash
sudo git clone https://github.com/24eme/signaturepdf.git /opt/signaturepdf && sudo git -C /opt/signaturepdf log -1 --format='%h %cd'
```

*Expected result*: a version identifier and a date. Write it down: it lets you roll back (E2).

**A5. SignaturePDF configuration**:

```bash
sudo tee /opt/signaturepdf/config/config.ini >/dev/null <<'FIN'
[globals]
PDF_STORAGE_PATH=/var/lib/signaturepdf/partages
PDF_STORAGE_ENCRYPTION=true
PDF_DEMO_LINK=false
FIN
sudo chmod 644 /opt/signaturepdf/config/config.ini
```

`PDF_STORAGE_PATH` enables **multi-party signing** (sharing by link), which AxiorHub needs. `PDF_STORAGE_ENCRYPTION=true` encrypts stored
PDFs with GPG; the key is in the signing link, after the `#`.

**A6. Dedicated PHP-FPM pool** (24 MB, 201 files, as recommended by the publisher):

```bash
sudo tee /etc/php/8.3/fpm/pool.d/signaturepdf.conf >/dev/null <<'FIN'
[signaturepdf]
user = signaturepdf
group = signaturepdf
listen = /run/php/signaturepdf.sock
listen.owner = www-data
listen.group = www-data
listen.mode = 0660
pm = ondemand
pm.max_children = 5
pm.process_idle_timeout = 60s
php_admin_value[upload_max_filesize] = 24M
php_admin_value[post_max_size] = 24M
php_admin_value[max_file_uploads] = 201
FIN
sudo php-fpm8.3 -t && sudo systemctl reload php8.3-fpm
```

*Expected result*: `test is successful`, then `ls -l /run/php/signaturepdf.sock` shows the socket.

**A7. Apache virtual host** `/etc/apache2/sites-available/signature.example.test.conf`. The exchange interface (`/api/`) and the purge
(`/cron`) only answer the server itself (`Require local`): from the Internet they return 403.

```apache
<VirtualHost *:80>
    ServerName signature.example.test
    DocumentRoot /opt/signaturepdf/public
    <Directory /opt/signaturepdf/public>
        Require all granted
        FallbackResource /index.php
    </Directory>
    <FilesMatch "\.php$">
        SetHandler "proxy:unix:/run/php/signaturepdf.sock|fcgi://signaturepdf"
    </FilesMatch>
    <LocationMatch "^/(api/|cron$)">
        Require local
    </LocationMatch>
</VirtualHost>
```

```bash
sudo a2enmod proxy_fcgi setenvif && sudo a2ensite signature.example.test && sudo apache2ctl configtest && sudo systemctl reload apache2
```

*Expected result*: `Syntax OK`, then `curl -s -o /dev/null -w '%{http_code}\n' http://signature.example.test/signature` prints `200`.

**A8. HTTPS certificate**: `sudo certbot --apache -d signature.example.test --redirect`.
*Expected result*: `curl -s -o /dev/null -w '%{http_code}\n' https://signature.example.test/signature` prints `200`.

**A9. Check the protection**: `curl -s -o /dev/null -w '%{http_code}\n' https://signature.example.test/api/share/new`.
*Expected result*: `405` **from the server**, `403` **from another computer**.

## B. PDF storage and purge

- Each shared PDF is kept for the duration chosen in AxiorHub (*Paramètres › Connexions › SignaturePDF*: one hour to one year; one month
  by default). Choose the shortest that leaves signers enough time: the signed PDF itself is filed in the matter.
- SignaturePDF deletes expired shares when its `/cron` address is called. Schedule it hourly from the server:

```bash
echo '17 * * * * nobody curl -fsS -o /dev/null https://signature.example.test/cron' | sudo tee /etc/cron.d/signaturepdf >/dev/null && sudo chmod 644 /etc/cron.d/signaturepdf
```

- Multi-party signing: the link is the same for everyone; anyone holding it can read and sign the PDF. Send it to the signers only.

## C. Configure AxiorHub and test

*Paramètres › Connexions › “SignaturePDF (signature par lien)”* (administrator): HTTPS address `https://signature.example.test`, retention,
exchange mode (automatic if available, or manual), **Instance du cabinet** (firm's instance: tick only if the firm hosts and administers it;
never inferred), **Enregistrer** (save), then **Tester la connexion** (test the connection). The test uploads nothing; it checks the signing
page, multi-party signing, encryption (information) and the exchange interface (information). The button only appears after a successful
test. HTTPS is required; HTTP is accepted only for a local instance on the loopback address (`http://127.0.0.1:…`, section H).

Without the “firm's instance” box, the instance is treated as a **third party**: each upload warns that the PDF is uploaded as is and cannot
be pseudonymised, and asks for a second confirmation; reserved matters and confidential correspondence between lawyers are always refused;
the data policy applies as for an external provider (strict local mode: no upload). On a workstation, enter the HTTPS address of the firm's
server instance and tick the box: the link given to signers then points to that server.

## D. Have a PDF signed

Matter › **Fichiers** tab › a PDF › **Faire signer par lien**; confirm; copy the link and send it yourself (AxiorHub sends nothing); when
everyone has signed, **Récupérer le PDF signé** (retrieve the signed PDF): “… (signé le DD-MM-YYYY).pdf” is filed next to the original,
which is never modified, then read back. Manual path: download the PDF, open SignaturePDF, upload, share, then **Déposer le PDF signé**.

## E. Backup, update, stop

- Backup: `/opt/signaturepdf/config/config.ini`, the pool file, the Apache site files and `/etc/cron.d/signaturepdf`. Shared PDFs are
  temporary; backing them up is optional.
- Update (publisher's command `git pull -r`): `sudo git -C /opt/signaturepdf pull -r && sudo systemctl reload php8.3-fpm`, then test the
  connection again. Roll back with `sudo git -C /opt/signaturepdf checkout <version>`.
- Stop: `sudo a2dissite signature.example.test signature.example.test-le-ssl && sudo systemctl reload apache2`.

## F. If something goes wrong

> - `502`/`503`: the PHP-FPM pool is not running (`sudo systemctl status php8.3-fpm`, `ls -l /run/php/signaturepdf.sock`).
> - “Multi-party signing” test fails: `PDF_STORAGE_PATH` missing or folder not writable by `signaturepdf` (A3, A5).
> - Exchange interface “absent” while A9 answers `405` on the server: AxiorHub runs on another server; add `Require ip <its address>`.
> - A9 answers `403` on the server itself (NAT): add `127.0.0.1 signature.example.test` to `/etc/hosts`.
> - Logs: `sudo tail -n 50 /var/log/apache2/error.log`, `sudo journalctl -u php8.3-fpm -n 50`.

## G. Licence, confidentiality, legal value

- **AGPL-3.0**: using SignaturePDF unmodified only requires keeping its licence notices. If you **modify** it and people use it over the
  network (your signers), you must offer them the source code of your modified version. AxiorHub includes no SignaturePDF code.
- **Confidentiality**: uploaded PDFs stay **on the firm's server**, for the chosen retention, encrypted if A5 is applied, then purged. The
  link gives access to the document: AxiorHub shows it only to accounts that can see the matter and keeps its keys in the server's
  encrypted vault, never in logs or exports.
- **Legal value (information, not advice)**: SignaturePDF adds a **visible signature**. This is in principle a
  **simple electronic signature** under Regulation (EU) No 910/2014 (eIDAS): it cannot be rejected in court merely because it is electronic, but it does not
  enjoy the presumption of reliability that French law (Civil Code, art. 1367; decree 2017-1416) reserves for qualified signatures.
  Providers such as **Yousign (Youtrust)** or **DocuSign** offer advanced or qualified signatures with identity checks and audit trails.
  The right choice depends on the document and its formal requirements.

## H. Local installation (workstation)

> A workstation instance listens on `127.0.0.1` only: **its links only open on that computer**. Exposing it to the office network is not
> recommended. **To have a client sign remotely, use the instance on the firm's server.**

- **Ubuntu 24.04**: `sudo apt install -y php-cli librsvg2-bin pdftk imagemagick potrace ghostscript locales gpg git curl`; clone the
  repository to `~/signaturepdf`; write `config/config.ini` with `PDF_STORAGE_PATH` in `~/.local/share/signaturepdf/partages`; run the
  PHP built-in server as a systemd user service:
  `php -d upload_max_filesize=24M -d post_max_size=24M -d max_file_uploads=201 -S 127.0.0.1:8090 -t public` (exact files in the French
  guide, H1).
- **Windows 11**: SignaturePDF needs Linux tools; the project publishes an official `Dockerfile`, so use Docker Desktop:
  `docker build -t signaturepdf https://github.com/24eme/signaturepdf.git#master`, then
  `docker run -d --name signaturepdf --restart unless-stopped -p 127.0.0.1:8090:80 -e SERVERNAME=127.0.0.1 -e PDF_STORAGE_PATH=/data -e PDF_STORAGE_ENCRYPTION=true -e PDF_DEMO_LINK=false -v signaturepdf-donnees:/data signaturepdf`
  (French guide, H2).
- In AxiorHub: address `http://127.0.0.1:8090`, then test the connection; the page of each PDF reminds that the link only works on this
  workstation.

*Tests (5.6.108)*: connector and both paths tried against a local fake service reproducing SignaturePDF's public addresses; public pages
of a real instance read without uploading anything. Uploading to a real instance, a real browser signature, the Apache, PHP-FPM,
Certbot and cron commands and the local installation (section H) were **not tried**.
