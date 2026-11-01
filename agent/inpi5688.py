"""5.6.88 : statuts, actes et comptes d'une société, tirés du registre national des entreprises (INPI) et rangés dans le dossier.

Le compte est créé par le cabinet (gratuit) ; ses identifiants vivent dans le coffre et ne passent que par ``adaptateurs5652.InpiRne``.
- **Sans dossier** : rien n'est téléchargé, une ligne le propose.
- **Avec un dossier** : un travail ``registre5688`` copie les derniers statuts à jour, les 3 derniers actes et les 2 derniers comptes dans le sous-dossier ``Registre``. Il tourne
  hors de la réponse (rien ne la retient) ; le texte extrait rejoint les réponses suivantes, borné, si « Documents » est ouvert dans « Ce que l'IA peut voir ».
- **Jamais d'écrasement ni de doublon** : dépôt exclusif, identifiant INPI conservé, empreinte comparée. **Tailles bornées**, par fichier et par passage.
- **Confidentialité** : seul un document marqué « Public » par l'INPI est demandé ; un compte confidentiel est signalé, jamais contourné. Un document que l'INPI a retiré (``deleted``)
  n'est plus proposé au modèle ; le fichier du dossier reste, l'avocat en décide (aucune suppression automatique).
- **Aucun coût** : service gratuit, aucun appel à Infogreffe, aucun appel au modèle ici : l'extrait passe dans la réponse déjà en cours, dans ses plafonds.
Licence INPI : ces données ne servent pas à la prospection (rappelé sur la fiche de connexion). Quotas : 10 000 appels ou 10 000 Mo par jour (erreur 429, dite telle quelle).
"""
from datetime import datetime, timezone
import hashlib
from html import escape as e
import logging
from pathlib import PurePosixPath
import re
import time

from .common import Stop, clean_path, fold, load_matters, under

log = logging.getLogger(__name__)
SCHEMA = '''CREATE TABLE IF NOT EXISTS registre_docs5688(inpi_id TEXT NOT NULL, matter TEXT NOT NULL, siren TEXT NOT NULL, nature TEXT NOT NULL, libelle TEXT NOT NULL, date_depot TEXT NOT NULL,
  date_cloture TEXT NOT NULL DEFAULT '', chemin TEXT NOT NULL, sha256 TEXT NOT NULL, taille INTEGER NOT NULL, texte_etat TEXT NOT NULL, extrait TEXT NOT NULL DEFAULT '',
  retire INTEGER NOT NULL DEFAULT 0, recupere_le TEXT NOT NULL, PRIMARY KEY(inpi_id, matter));
CREATE INDEX IF NOT EXISTS registre_docs5688_s ON registre_docs5688(matter, siren);'''
CAP_LISTE = 'registre.documents'
CAP_FICHIER = 'registre.telecharger'
DOSSIER = 'Registre'
NB_ACTES, NB_COMPTES = 3, 2
MAX_FICHIER = 30_000_000
MAX_PASSAGE = 80_000_000
EXTRAIT = 2500
EXTRAITS_TOTAL = 6000
REPOS = 86400              # un même dossier et une même société : un passage par jour au plus
NOM_SUR = re.compile(r'[^\w .,()\'’-]+')


def ensure_schema(desk):
    if not desk.db.in_transaction:
        desk.db.executescript(SCHEMA)


def _now():
    return datetime.now(timezone.utc).isoformat(timespec='seconds')


def connexion(desk, owner='cabinet'):
    """Connexion INPI vérifiée, ou None : un compte non testé ne sert à rien ici."""
    from .connexions5652 import _connections_for
    rows = _connections_for(desk, CAP_LISTE, owner)
    return rows[0] if rows else None


def _types(acte):
    return ' ; '.join(str(t.get('typeActe') or '') for t in acte.get('typeRdd') or [] if isinstance(t, dict)) or str(acte.get('libelle') or '')


def _est_statuts(acte):
    return 'statut' in fold(_types(acte))


def selection(liste):
    """(à télécharger, confidentiels) : derniers statuts à jour, 3 derniers autres actes, 2 derniers comptes ; un seul dépôt par exercice ; documents retirés ignorés."""
    actes = sorted((a for a in liste.get('actes') or [] if isinstance(a, dict) and a.get('id') and not a.get('deleted')), key=lambda a: str(a.get('dateDepot') or ''), reverse=True)
    bilans = sorted((b for b in liste.get('bilans') or [] if isinstance(b, dict) and b.get('id') and not b.get('deleted')),
                    key=lambda b: (str(b.get('dateCloture') or ''), str(b.get('dateDepot') or '')), reverse=True)
    chosen, confidentiels = [], []
    statuts = next((a for a in actes if _est_statuts(a) and 'jour' in fold(_types(a))), None) or next((a for a in actes if _est_statuts(a)), None)

    def prendre(doc, nature, libelle):
        if str(doc.get('confidentiality') or '').strip().lower() == 'public':
            chosen.append({'id': str(doc['id']), 'nature': nature, 'libelle': libelle, 'date_depot': str(doc.get('dateDepot') or '')[:10], 'date_cloture': str(doc.get('dateCloture') or '')[:10]})
        else:
            confidentiels.append({'nature': nature, 'libelle': libelle, 'date': str(doc.get('dateCloture') or doc.get('dateDepot') or '')[:10]})
    if statuts:
        prendre(statuts, 'acte', 'Statuts à jour')
    for a in [x for x in actes if x is not statuts][:NB_ACTES]:
        prendre(a, 'acte', re.sub(r'\s+', ' ', _types(a)).strip()[:80] or 'Acte')
    vus = set()
    for b in bilans:
        cloture = str(b.get('dateCloture') or '')[:10]
        if cloture in vus:
            continue
        vus.add(cloture)
        if len(vus) > NB_COMPTES:
            break
        prendre(b, 'bilan', 'Comptes annuels clos le ' + cloture if cloture else 'Comptes annuels')
    return chosen, confidentiels


def _chemin(dossier, doc):
    base = NOM_SUR.sub(' ', '%s %s' % (doc['date_depot'] or doc['date_cloture'] or 'sans date', doc['libelle']))
    return clean_path('%s/%s (%s).pdf' % (dossier, re.sub(r'\s+', ' ', base).strip()[:120], re.sub(r'\W', '', doc['id'])[:12]))


def _deposer(client, chemin, data):
    """Dépôt exclusif : un fichier déjà là n'est jamais remplacé ; un nom libre est pris à côté."""
    stem, n = chemin[:-4], 1
    while True:
        cible = chemin if n == 1 else '%s (%d).pdf' % (stem, n)
        try:
            return client.put_archive(cible, data, MAX_FICHIER, 'application/pdf')
        except Stop as ex:
            if str(ex).split(':')[0] != 'http_412' or n >= 5:
                raise
            n += 1


def _texte(desk, data, nom):
    """(état, extrait) : le texte vient de la chaîne d'extraction existante (pypdf embarqué, puis OCR) ; un scan illisible est dit, jamais deviné."""
    from .documents import extract
    from .workspace import unreadable_reason
    try:
        text = extract(data, nom, {**desk.c.get('documents', {}), 'max_document_chars': 2_000_000})
        text = text if isinstance(text, str) else text.get('text', '')
    except Stop as ex:
        return 'illisible', unreadable_reason(str(ex)) or 'lecture impossible'
    except Exception:   # noqa: BLE001 — un PDF malformé ne doit pas arrêter le passage
        log.exception('registre : extraction')
        return 'illisible', 'lecture impossible'
    text = re.sub(r'[ \t]+', ' ', str(text or '')).strip()
    return ('lu', text[:EXTRAIT]) if text else ('illisible', 'aucun texte reconnu (scan ou image)')


def recuperer(desk, siren, matter_id, owner='cabinet'):
    """Copie les documents publics dans le dossier. Refuse un dossier réservé au lecteur ; ne télécharge rien sans dossier ni compte vérifié."""
    from . import document_projects, registres5688
    from .membres5650 import viewer_hidden
    ensure_schema(desk)
    siren = registres5688.norm_siren(siren)
    if not matter_id:
        raise Stop('dossier_requis')
    if matter_id in (viewer_hidden() or frozenset()):
        raise Stop('dossier_reserve')
    m = next((x for x in load_matters(desk.c) if x['id'] == matter_id), None)
    if m is None or not m.get('path'):
        raise Stop('dossier_absent')
    row = connexion(desk, owner)
    if row is None:
        raise Stop('aucune_connexion_verifiee_pour_cet_outil')
    from .connexions5652 import adapter
    ad = adapter(desk, row)
    bilan = {'telecharges': [], 'confidentiels': [], 'illisibles': [], 'erreurs': [], 'deja_la': 0, 'retires': 0, 'sans_controle': True}
    liste = ad.call(CAP_LISTE, {'siren': siren})
    retires = {str(x.get('id')) for x in (liste.get('actes') or []) + (liste.get('bilans') or []) if isinstance(x, dict) and x.get('deleted')}
    if retires:
        marks = ','.join('?' * len(retires))
        bilan['retires'] = desk.db.execute("UPDATE registre_docs5688 SET retire=1,extrait='' WHERE matter=? AND retire=0 AND inpi_id IN (%s)" % marks, [matter_id] + sorted(retires)).rowcount
        desk.db.commit()
    chosen, bilan['confidentiels'] = selection(liste)
    connus = {r['inpi_id']: r for r in desk.db.execute('SELECT * FROM registre_docs5688 WHERE matter=?', (matter_id,))}
    empreintes = {r['sha256'] for r in connus.values()}
    dossier = clean_path(m['path'] + '/' + DOSSIER)
    if not under(dossier, m['path']):
        raise Stop('destination_hors_dossier')
    client = document_projects._dav(desk)
    client.ensure_folder(dossier, m['path'])
    total = 0
    for doc in chosen:
        if doc['id'] in connus:
            bilan['deja_la'] += 1
            continue
        try:
            data = ad.call(CAP_FICHIER, {'nature': doc['nature'], 'id': doc['id'], 'limite': MAX_FICHIER})['fichier']
            if len(data) + total > MAX_PASSAGE:
                bilan['erreurs'].append({'libelle': doc['libelle'], 'code': 'passage_trop_volumineux'})
                break
            sha = hashlib.sha256(data).hexdigest()
            if sha in empreintes:
                bilan['deja_la'] += 1
                continue
            chemin = _chemin(dossier, doc)
            if not under(chemin, m['path']):
                raise Stop('destination_hors_dossier')
            chemin = _deposer(client, chemin, data)
        except Stop as ex:
            code = str(ex).split(':')[0]
            if code == 'http_403':      # l'INPI réserve ce document à des personnes habilitées : signalé, jamais contourné
                bilan['confidentiels'].append({'nature': doc['nature'], 'libelle': doc['libelle'], 'date': doc['date_cloture'] or doc['date_depot']})
            else:
                bilan['erreurs'].append({'libelle': doc['libelle'], 'code': code})
            if code == 'http_429':      # quota journalier : inutile d'insister
                break
            continue
        total += len(data)
        empreintes.add(sha)
        etat, extrait = _texte(desk, data, PurePosixPath(chemin).name)
        desk.db.execute('INSERT OR IGNORE INTO registre_docs5688(inpi_id,matter,siren,nature,libelle,date_depot,date_cloture,chemin,sha256,taille,texte_etat,extrait,recupere_le) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)',
                        (doc['id'], matter_id, siren, doc['nature'], doc['libelle'], doc['date_depot'], doc['date_cloture'], chemin, sha, len(data), etat, extrait if etat == 'lu' else '', _now()))
        desk.db.commit()
        bilan['telecharges'].append({'libelle': doc['libelle'], 'chemin': chemin})
        if etat == 'illisible':
            bilan['illisibles'].append({'libelle': doc['libelle'], 'motif': extrait})
    desk.setting('inpi5688:confidentiels:%s:%s' % (matter_id, siren), bilan['confidentiels'])
    desk.audit('inpi5688_recuperation', {'matter': matter_id, 'societe': siren, 'telecharges': len(bilan['telecharges']), 'confidentiels': len(bilan['confidentiels']),
                                         'illisibles': len(bilan['illisibles']), 'erreurs': [x['code'] for x in bilan['erreurs']], 'retires': bilan['retires']})
    if bilan['telecharges'] or bilan['confidentiels'] or bilan['retires']:
        from .messagerie5650 import notice
        notice(desk, matter_id, _resume(bilan), '/dossier?id=' + matter_id, 'Dossier')
    bilan['message'] = _resume(bilan)
    return bilan


def _resume(b):
    parts = []
    if b['telecharges']:
        parts.append('%d document(s) du registre ajouté(s) dans « %s »' % (len(b['telecharges']), DOSSIER))
    if b['deja_la']:
        parts.append('%d déjà présent(s)' % b['deja_la'])
    if b['confidentiels']:
        parts.append('%d document(s) confidentiel(s), non accessible(s)' % len(b['confidentiels']))
    if b['illisibles']:
        parts.append('%d scan(s) illisible(s)' % len(b['illisibles']))
    if b['retires']:
        parts.append('%d document(s) retiré(s) par l’INPI : plus transmis à l’IA, fichier laissé dans le dossier pour votre décision' % b['retires'])
    if b['erreurs']:
        parts.append('%d lecture(s) en échec' % len(b['erreurs']))
    return (' ; '.join(parts) + '.') if parts else 'Aucun nouveau document au registre.'


def demander(desk, siren, matter_id, repos=True):
    """Met le passage en file, hors de la réponse. Un dossier réservé est refusé ici aussi. (Vrai si un travail est parti.)"""
    from .membres5650 import viewer_hidden
    ensure_schema(desk)
    if not matter_id or matter_id in (viewer_hidden() or frozenset()):
        raise Stop('dossier_reserve' if matter_id else 'dossier_requis')
    cle = 'inpi5688:essai:%s:%s' % (matter_id, siren)
    if repos and time.time() - float(desk.settings(cle, 0) or 0) < REPOS:
        return False
    desk.enqueue('registre5688', {'siren': siren, 'matter': matter_id, 'masques': sorted(viewer_hidden() or ())})
    desk.setting(cle, time.time())
    return True


def perform(desk, args):
    """Travail ``registre5688`` : mêmes dossiers masqués que la personne qui a posé la question."""
    from . import membres5650
    membres5650.set_viewer(args.get('masques') or ())
    try:
        return recuperer(desk, args.get('siren'), str(args.get('matter') or ''))
    except Stop as ex:
        return {'erreur': str(ex).split(':')[0], 'sans_controle': True}
    finally:
        membres5650.set_viewer(())


def extraits(desk, siren, matter_id):
    """Documents déjà récupérés pour cette société dans ce dossier : ce qui est donné à la réponse, borné."""
    ensure_schema(desk)
    rows = desk.db.execute('SELECT * FROM registre_docs5688 WHERE matter=? AND siren=? AND retire=0 ORDER BY date_depot DESC', (matter_id, siren)).fetchall()
    rows = sorted(rows, key=lambda r: (r['libelle'] != 'Statuts à jour', r['nature'] != 'acte'))
    out, reste = [], EXTRAITS_TOTAL
    for r in rows:
        if r['texte_etat'] == 'lu' and reste > 200:
            out.append({'document': r['libelle'], 'depot': r['date_depot'], 'extrait': r['extrait'][:reste]})
            reste -= len(out[-1]['extrait'])
    return out, rows


def pour_question(desk, controle, matter_id):
    """Complète le contrôle du tour : propose sans dossier, met en file avec un dossier, joint ce qui est déjà récupéré. Aucune attente ici."""
    from . import vueia5688
    from .membres5650 import viewer_hidden
    societes = [s for s in controle['societes'] if s['statut'] == 'controlee' and s['siren']]
    if not societes or connexion(desk) is None:
        return
    if not matter_id:
        controle['propositions'].append('Statuts, actes et comptes publics disponibles auprès de l’INPI : rattachez la question à un dossier pour les y ranger. Rien n’a été téléchargé.')
        return
    if matter_id in (viewer_hidden() or frozenset()):
        return
    ouvert = not vueia5688.blocked(desk, 'documents')
    for s in societes:
        lus, rows = extraits(desk, s['siren'], matter_id)
        if lus and ouvert:
            s['extraits'] = lus
        elif rows and not ouvert:
            s.setdefault('notes', []).append('Statuts et actes du dossier non transmis à l’IA : ' + vueia5688.REFUSED + '.')
        fermes = desk.settings('inpi5688:confidentiels:%s:%s' % (matter_id, s['siren']), []) or []
        if fermes:
            s.setdefault('notes', []).append('Document(s) confidentiel(s) au registre, non accessible(s) par l’INPI : %s.' % ', '.join(str(x.get('libelle')) for x in fermes[:3]))
        illisibles = [r['libelle'] for r in rows if r['texte_etat'] == 'illisible']
        if illisibles:
            s.setdefault('notes', []).append('Document(s) du registre illisible(s), scan à vérifier : %s.' % ', '.join(illisibles[:3]))
        try:
            if demander(desk, s['siren'], matter_id) and not rows:
                s.setdefault('notes', []).append('Statuts, actes et comptes publics en cours de récupération dans « %s » (service gratuit de l’INPI) ; ils serviront à la prochaine question.' % DOSSIER)
        except Stop as ex:
            log.info('registre : mise en file refusée (%s)', str(ex).split(':')[0])


def pied_carte(desk, prefix, siren, matter_id):
    """Bas de carte : statuts déjà rangés, ou bouton de récupération. Sans dossier ni compte : ce qu'il manque, dit simplement."""
    ensure_schema(desk)
    if connexion(desk) is None:
        return '<p class="ax-muted">Statuts et actes : <a href="%s">connectez un compte INPI gratuit</a>.</p>' % e(prefix + '/parametres?rubrique=connexions#cx52-inpi_rne', quote=True)
    if not matter_id:
        return '<p class="ax-muted">Statuts et actes : ouvrez un dossier relié à cette société pour les y ranger. Rien n’est téléchargé ici.</p>'
    rows = desk.db.execute('SELECT libelle,date_depot,chemin,retire FROM registre_docs5688 WHERE matter=? AND siren=? ORDER BY date_depot DESC', (matter_id, siren)).fetchall()
    liste = ''.join('<li>%s <span class="ax-muted">%s</span>%s</li>' % (e(r['libelle']), e(r['date_depot']), ' <em>retiré par l’INPI</em>' if r['retire'] else '') for r in rows)
    return ('%s<form class="m5-form m5-inline" data-api="m5688/societe/registre" data-reload="1"><input type="hidden" name="siren" value="%s"><input type="hidden" name="matter" value="%s">'
            '<button class="ax-btn ghost" type="submit">%s</button><p role="status" class="m5-status"></p></form>') % (
        ('<ul class="v88-list">%s</ul>' % liste) if liste else '', e(siren, quote=True), e(matter_id, quote=True), 'Rechercher de nouveaux documents' if liste else 'Récupérer statuts, actes et comptes')


def etat_html(desk, prefix):
    state = 'Compte INPI connecté et vérifié.' if connexion(desk) is not None else 'Aucun compte INPI vérifié : le contrôle fonctionne sans, mais les statuts ne sont pas récupérés.'
    return '%s <a href="%s">Connexions</a>' % (e(state), e(prefix + '/parametres?rubrique=connexions#cx52-inpi_rne', quote=True))
