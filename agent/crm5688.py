"""5.6.88 : fiche Contacts remplie depuis les registres publics, et veille des sociétés suivies (BODACC).

Fiche : la recherche par nom ou SIREN PROPOSE des champs ; la personne coche ce qu'elle reprend. Une valeur déjà saisie et différente ne
change que si « Remplacer » est coché pour ce champ ; une fiche modifiée entre-temps refuse la confirmation. Les valeurs sont relues sur le
serveur, jamais reprises du navigateur. Chaque valeur reprise garde sa source, sa date de lecture et la personne qui l'a confirmée.

Veille : un passage par jour au plus, sans modèle, depuis la boucle des automatismes (``schedule``), borné à ``PAR_PASSAGE`` sociétés. Le classement
repose sur les champs officiels ``familleavis`` et, pour les modifications, ``modificationsgenerales.descriptif`` ; jamais sur le nom de la
société. Une seule alerte par annonce, même si les deux parties d'une cession sont suivies. Une panne de la source n'écrit aucune annonce : elle se lit
sur la page, dans Aujourd'hui et sur chaque ligne, et ``dernier_ok`` n'avance pas (la période manquée sera relue). Annonces gardées ``RETENTION_JOURS``.
Les appels réseau sont tous dans ``registres5688`` : le SIREN ne part que vers ces sources publiques.
"""
from datetime import datetime, timedelta, timezone
import hashlib
from html import escape as e
import json
import re
import time
import uuid

from . import registres5688 as registres
from .common import Stop, fold

SCHEMA = '''
CREATE TABLE IF NOT EXISTS fiche_registre5688(contact TEXT NOT NULL, champ TEXT NOT NULL, valeur TEXT NOT NULL, source TEXT NOT NULL, lu_le TEXT NOT NULL,
  confirme_par TEXT NOT NULL, confirme_le TEXT NOT NULL, PRIMARY KEY(contact, champ));
CREATE TABLE IF NOT EXISTS societes5688(id TEXT PRIMARY KEY, siren TEXT NOT NULL UNIQUE, nom TEXT NOT NULL, contact TEXT NOT NULL DEFAULT '', evenements TEXT NOT NULL,
  actif INTEGER NOT NULL DEFAULT 1, cree_par TEXT NOT NULL, cree_le TEXT NOT NULL, modifie_le TEXT NOT NULL, dernier_essai TEXT NOT NULL DEFAULT '',
  dernier_ok TEXT NOT NULL DEFAULT '', echecs INTEGER NOT NULL DEFAULT 0, panne_depuis TEXT NOT NULL DEFAULT '');
CREATE TABLE IF NOT EXISTS annonces5688(annonce TEXT PRIMARY KEY, sirens TEXT NOT NULL, nom TEXT NOT NULL, date TEXT NOT NULL, famille TEXT NOT NULL, famille_libelle TEXT NOT NULL,
  type_libelle TEXT NOT NULL, evenements TEXT NOT NULL, detail TEXT NOT NULL DEFAULT '', url TEXT NOT NULL, releve TEXT NOT NULL, vu_par TEXT NOT NULL DEFAULT '', vu_le TEXT NOT NULL DEFAULT '');
CREATE INDEX IF NOT EXISTS annonces5688_releve ON annonces5688(releve);
'''
# champ : (libellé, colonne de contacts5636 ou None). Le nom va dans « Société » pour une personne.
CHAMPS = (('nom', 'Dénomination', 'name'), ('siren', 'SIREN', 'siren'), ('adresse', 'Adresse du siège', 'address'), ('forme_juridique', 'Forme juridique', None),
          ('rcs', 'RCS', None), ('capital', 'Capital', None), ('dirigeants', 'Dirigeants', None), ('etat', 'État', None), ('date_creation', 'Date de création', None))
ETATS = {'A': 'Active', 'C': 'Cessée'}
EVENEMENTS = {'procedure_collective': 'Procédure collective, conciliation ou rétablissement professionnel', 'cession': 'Vente ou cession',
              'siege': 'Changement de siège', 'dirigeant': 'Changement de dirigeant', 'radiation': 'Radiation'}
# Valeurs officielles de ``familleavis`` ; un même événement « modification » couvre le siège et les dirigeants, séparés ensuite par ``descriptif``.
FAMILLES = {'procedure_collective': ('collective', 'conciliation', 'retablissement_professionnel'), 'cession': ('vente',), 'radiation': ('radiation',),
            'siege': ('modification',), 'dirigeant': ('modification',)}
PAR_PASSAGE = 40            # sociétés lues par passage ; les suivantes le jour d'après
PAUSE = 0.4                 # secondes entre deux lectures : la source est publique et partagée
PANNES_AVANT_ARRET = 2      # pannes de suite : le passage s'arrête, inutile d'insister
REPOS_SECONDES = 20 * 3600  # une société relue avec succès ne l'est plus avant ce délai, quel que soit le déclencheur
REESSAI_SECONDES = 3600     # après une panne, nouvel essai possible au bout d'une heure
MARGE_JOURS = 7             # recouvrement : une annonce parue tard est relue, sans doublon
PREMIERE_LECTURE_JOURS = 30
RETENTION_JOURS = 730
MAX_SOCIETES = 500


def _now():
    return datetime.now(timezone.utc).isoformat(timespec='seconds')


def ensure_schema(desk):
    if not desk.db.in_transaction:
        desk.db.executescript(SCHEMA)


# ------------------------------------------------------------------------------------------------ fiche : proposition
def _signature(contact):
    """Jeton de la fiche telle qu'elle a été lue : une modification entre la proposition et la confirmation refuse la reprise."""
    from .crm5636 import FIELDS
    return hashlib.sha256(json.dumps([str(contact.get(k) or '') for k in FIELDS], ensure_ascii=False).encode('utf-8')).hexdigest()[:24]


def _valeurs(ent):
    capital = ent.get('capital') or {}
    dirigeants = ['%s : %s' % (d['qualite'], (d['nom'] + ' ' + d['prenoms']).strip()) if d['qualite'] else (d['nom'] + ' ' + d['prenoms']).strip()
                  for d in ent.get('dirigeants') or [] if d.get('nom')]
    return {'nom': ent.get('nom', ''), 'siren': ent.get('siren', ''), 'adresse': ent.get('adresse', ''), 'forme_juridique': ent.get('forme_juridique', ''), 'rcs': ent.get('rcs', ''),
            'capital': ('%s %s' % (capital.get('montant', ''), capital.get('devise', ''))).strip(), 'dirigeants': ' ; '.join(dirigeants)[:1500],
            'etat': ETATS.get(ent.get('etat', ''), ''), 'date_creation': ent.get('date_creation', '') if ent.get('date_creation', '') != '1900-01-01' else ''}


def _colonne(contact, champ, colonne):
    if champ == 'nom' and contact.get('kind') == 'personne':
        return 'company'
    return colonne


def _pareil(champ, a, b):
    if champ == 'siren':
        return re.sub(r'\D', '', a)[:9] == re.sub(r'\D', '', b)[:9]
    return re.sub(r'\W+', ' ', fold(a)).strip() == re.sub(r'\W+', ' ', fold(b)).strip()


def provenance(desk, contact):
    ensure_schema(desk)
    return {r['champ']: dict(r) for r in desk.db.execute('SELECT * FROM fiche_registre5688 WHERE contact=?', (str(contact),))}


def proposition(desk, contact, siren):
    """Champs proposés pour une fiche : état « vide » (repris si coché), « different » (repris seulement si « Remplacer » est coché) ou « identique »."""
    from . import crm5636
    fiche = crm5636.get(desk, contact)         # contrôle les dossiers réservés
    ent = registres.entreprise(desk, siren)
    if ent is None:
        raise Stop('entreprise_inconnue_du_registre')
    saved = provenance(desk, fiche['id'])
    values = _valeurs(ent)
    rows = []
    for champ, libelle, colonne in CHAMPS:
        value = str(values.get(champ) or '').strip()
        if not value:
            continue                           # la source ne le donne pas : rien n'est proposé, rien n'est inventé
        colonne = _colonne(fiche, champ, colonne)
        actuel = str(fiche.get(colonne) or '').strip() if colonne else str((saved.get(champ) or {}).get('valeur') or '')
        etat = 'vide' if not actuel else ('identique' if _pareil(champ, actuel, value) else 'different')
        rows.append({'champ': champ, 'libelle': libelle, 'actuel': actuel, 'propose': value, 'etat': etat})
    return {'contact': fiche['id'], 'siren': ent['siren'], 'nom': ent['nom'], 'source': ent['source'] + ((' ; ' + ent['source_complements']) if ent['source_complements'] else ''),
            'date': ent['date_lecture'], 'champs': rows, 'jeton': _signature(fiche), 'complements_indisponibles': ent['complements_indisponibles']}


def confirmer(desk, owner, contact, siren, choisis, remplacer, jeton):
    """Applique les champs cochés. Une valeur différente n'est remplacée que si son « Remplacer » est coché ; le reste est listé dans ``ignores``."""
    from . import crm5636
    fiche = crm5636.get(desk, contact)
    if str(jeton or '') != _signature(fiche):
        raise Stop('version_en_conflit')
    prop = proposition(desk, contact, siren)
    data = {k: fiche.get(k, '') for k in crm5636.FIELDS}
    colonnes = dict((c, k) for c, _, k in CHAMPS)
    repris, ignores, extra = [], [], []
    for row in prop['champs']:
        champ = row['champ']
        if row['etat'] == 'identique' or champ not in choisis:
            continue
        if row['etat'] == 'different' and champ not in remplacer:
            ignores.append(row['libelle'])
            continue
        colonne = _colonne(fiche, champ, colonnes[champ])
        if colonne:
            data[colonne] = row['propose']
        extra.append((champ, row['propose']))
        repris.append(row['libelle'])
    if repris:
        crm5636.save(desk, {**data, 'id': fiche['id']}, owner)
        now = _now()
        for champ, value in extra:
            desk.db.execute('INSERT OR REPLACE INTO fiche_registre5688 VALUES(?,?,?,?,?,?,?)', (fiche['id'], champ, value, prop['source'], prop['date'], owner, now))
        desk.db.commit()
        desk.audit('crm5688_fiche_registre', {'contact': fiche['id'], 'champs': len(repris)})
    return {'repris': repris, 'ignores': ignores, 'message': ('%d champ(s) repris.' % len(repris) if repris else 'Aucun champ repris.') + (
        ' Non remplacés, faute de confirmation : %s.' % ', '.join(ignores) if ignores else '')}


# ------------------------------------------------------------------------------------------------ veille : classement
def classer(a):
    """Événements d'une annonce normalisée, d'après les champs officiels seulement (le nom de la société ni le texte libre n'entrent en compte)."""
    famille = a.get('famille', '')
    for event in ('procedure_collective', 'cession', 'radiation'):
        if famille in FAMILLES[event]:
            return [event]
    if famille != 'modification':
        return []
    modif = a.get('modifications') if isinstance(a.get('modifications'), dict) else {}
    desc = fold(str(modif.get('descriptif') or ''))
    out = []
    if re.search(r"transfert du siege|adresse du siege|siege social|transfert de l.etablissement principal", desc):
        out.append('siege')
    if re.search(r"\badministration\b|\bdirigeant|\bgerance\b|\bgerant", desc):
        out.append('dirigeant')
    return out


def _detail(a, siren):
    jug = a['jugement'] if isinstance(a['jugement'], dict) else {}
    rad = a['radiation'] if isinstance(a['radiation'], dict) else {}
    mod = a['modifications'] if isinstance(a['modifications'], dict) else {}
    if a['famille'] == 'vente':
        cedant = any(re.sub(r'\D', '', str((p.get('numeroImmatriculation') or {}).get('numeroIdentification') or '')) == siren
                     for p in a['precedents_proprietaires'] + a['precedents_exploitants'] if isinstance(p.get('numeroImmatriculation'), dict))
        return 'Société citée comme cédante.' if cedant else 'Société citée dans la vente ; le rôle est à lire dans l’annonce.'
    return str(jug.get('nature') or rad.get('commentaire') or mod.get('descriptif') or '')[:300]


def enregistrer(desk, siren, nom, a, evenements):
    """Annonce relevée : nouvelle (1) ou déjà connue (0, le SIREN est ajouté à la même ligne : une seule alerte par annonce)."""
    mark = ',%s,' % siren
    row = desk.db.execute('SELECT sirens FROM annonces5688 WHERE annonce=?', (a['id'],)).fetchone()
    if row:
        if mark not in row['sirens']:
            desk.db.execute('UPDATE annonces5688 SET sirens=? WHERE annonce=?', (row['sirens'] + siren + ',', a['id']))
        return 0
    desk.db.execute('INSERT INTO annonces5688(annonce,sirens,nom,date,famille,famille_libelle,type_libelle,evenements,detail,url,releve) VALUES(?,?,?,?,?,?,?,?,?,?,?)',
                    (a['id'], mark, a['commercant'] or nom, a['date'], a['famille'], a['famille_libelle'], a['type_libelle'], json.dumps(evenements), _detail(a, siren), a['url'], _now()))
    return 1


def _depuis(row, now):
    if row['dernier_ok']:
        start = datetime.fromisoformat(row['dernier_ok']) - timedelta(days=MARGE_JOURS)
    else:
        start = datetime.fromisoformat(row['cree_le']) - timedelta(days=PREMIERE_LECTURE_JOURS)
    return min(start, now).date().isoformat()


def verifier(desk, now=None, attente=None):
    """Travail « veille5688 » : lit les sociétés dues, sans modèle. Rien n'est écrit pour une société dont la lecture a échoué."""
    ensure_schema(desk)
    now = now or datetime.now(timezone.utc)
    attente = time.sleep if attente is None else attente
    out = {'lues': 0, 'nouvelles': 0, 'en_panne': 0, 'reportees': 0}
    rows = desk.db.execute("SELECT * FROM societes5688 WHERE actif=1 ORDER BY dernier_essai").fetchall()
    due = []
    for r in rows:
        ok_age = (now - datetime.fromisoformat(r['dernier_ok'])).total_seconds() if r['dernier_ok'] else None
        try_age = (now - datetime.fromisoformat(r['dernier_essai'])).total_seconds() if r['dernier_essai'] else None
        if (ok_age is not None and ok_age < REPOS_SECONDES) or (r['echecs'] and try_age is not None and try_age < REESSAI_SECONDES):
            continue
        due.append(r)
    out['reportees'] = max(0, len(due) - PAR_PASSAGE)
    pannes = 0
    stamp = now.isoformat(timespec='seconds')
    for i, r in enumerate(due[:PAR_PASSAGE]):
        wanted = set(json.loads(r['evenements']))
        familles = sorted({f for ev in wanted for f in FAMILLES[ev]})
        try:
            got = registres.annonces(desk, r['siren'], familles=familles, limit=100, depuis=_depuis(r, now))
        except registres.SourceIndisponible:
            desk.db.execute("UPDATE societes5688 SET dernier_essai=?,echecs=echecs+1,panne_depuis=CASE WHEN panne_depuis='' THEN ? ELSE panne_depuis END WHERE id=?", (stamp, stamp, r['id']))
            desk.db.commit()
            out['en_panne'] += 1
            pannes += 1
            if pannes >= PANNES_AVANT_ARRET:
                break
            continue
        pannes = 0
        for a in got['annonces']:
            events = [ev for ev in classer(a) if ev in wanted]
            if events:
                out['nouvelles'] += enregistrer(desk, r['siren'], r['nom'], a, events)
        desk.db.execute("UPDATE societes5688 SET dernier_essai=?,dernier_ok=?,echecs=0,panne_depuis='' WHERE id=?", (stamp, stamp, r['id']))
        desk.db.commit()
        out['lues'] += 1
        if i + 1 < min(len(due), PAR_PASSAGE):
            attente(PAUSE)
    out['retirees'] = purger(desk, now)
    return out


def purger(desk, now=None):
    now = now or datetime.now(timezone.utc)
    days = max(30, int(desk.settings('veille5688:retention_jours', RETENTION_JOURS) or RETENTION_JOURS))
    n = desk.db.execute('DELETE FROM annonces5688 WHERE releve<?', ((now - timedelta(days=days)).isoformat(timespec='seconds'),)).rowcount
    desk.db.commit()
    return n


def schedule(desk, now_ts):
    """Boucle des automatismes : un passage par jour au plus, et aucun s'il n'y a rien à suivre."""
    ensure_schema(desk)
    if not desk.db.execute('SELECT 1 FROM societes5688 WHERE actif=1 LIMIT 1').fetchone():
        return None
    from . import cadence5662
    if now_ts - float(desk.settings('auto:veille5688', 0) or 0) < 86400 or not cadence5662.allow(desk, 'veille5688', 1440, now_ts):
        return None
    desk.setting('auto:veille5688', now_ts)
    return desk.enqueue('veille5688', {}, priority=75)


# ------------------------------------------------------------------------------------------------ veille : liste
def _evenements(data):
    if isinstance(data.get('evenements'), list):
        chosen = [str(x) for x in data['evenements']]
    else:
        chosen = [k[len('evt:'):] for k, v in data.items() if str(k).startswith('evt:') and v is True]
    chosen = [k for k in EVENEMENTS if k in chosen]
    if not chosen:
        raise Stop('veille_evenement_requis')
    return chosen


def suivre(desk, owner, data):
    """Depuis une fiche (``contact``) ou un SIREN. Suivre de nouveau une société met à jour ses événements."""
    from . import crm5636
    ensure_schema(desk)
    contact = str(data.get('contact') or '')
    fiche = crm5636.get(desk, contact) if contact else None
    siren = registres.norm_siren(data.get('siren') or (fiche or {}).get('siren'))
    events = _evenements(data)
    nom = re.sub(r'\s+', ' ', str(data.get('nom') or (fiche or {}).get('label') or '')).strip()[:200]
    now = _now()
    row = desk.db.execute('SELECT id,contact FROM societes5688 WHERE siren=?', (siren,)).fetchone()
    if row:
        if row['contact'] and crm5636._restricted(desk, row['contact']):
            raise Stop('dossier_reserve')
        desk.db.execute('UPDATE societes5688 SET evenements=?,actif=1,modifie_le=?,contact=CASE WHEN ?<>\'\' THEN ? ELSE contact END WHERE id=?',
                        (json.dumps(events), now, contact, contact, row['id']))
        ident = row['id']
    else:
        if desk.db.execute('SELECT COUNT(*) FROM societes5688 WHERE actif=1').fetchone()[0] >= MAX_SOCIETES:
            raise Stop('trop_de_societes_suivies')
        if not nom:
            try:
                nom = (registres.entreprise(desk, siren) or {}).get('nom', '')
            except registres.SourceIndisponible:
                nom = ''                       # nom complété plus tard : suivre ne dépend pas de la source
        ident = uuid.uuid4().hex
        desk.db.execute('INSERT INTO societes5688(id,siren,nom,contact,evenements,cree_par,cree_le,modifie_le) VALUES(?,?,?,?,?,?,?,?)',
                        (ident, siren, nom or 'SIREN ' + siren, contact, json.dumps(events), owner, now, now))
    desk.db.commit()
    desk.audit('crm5688_veille_suivre', {'societe': ident})
    return {'id': ident, 'message': 'Société suivie. Première lecture au prochain passage quotidien.'}


def _societe(desk, ident):
    from . import crm5636
    ensure_schema(desk)
    row = desk.db.execute('SELECT * FROM societes5688 WHERE id=?', (str(ident or ''),)).fetchone()
    if not row or (row['contact'] and crm5636._restricted(desk, row['contact'])):
        raise Stop('societe_absente')
    return row


def retirer(desk, ident):
    """La société n'est plus lue. Ses annonces restent consultables jusqu'à la fin de la rétention."""
    row = _societe(desk, ident)
    desk.db.execute('UPDATE societes5688 SET actif=0,modifie_le=? WHERE id=?', (_now(), row['id']))
    desk.db.commit()
    desk.audit('crm5688_veille_retirer', {'societe': row['id']})
    return {'message': 'Société retirée de la veille.'}


def vu(desk, owner, annonce):
    """Alerte traitée : elle quitte Aujourd'hui pour tout le cabinet et reste sur la fiche et dans l'historique."""
    ensure_schema(desk)
    row = desk.db.execute('SELECT * FROM annonces5688 WHERE annonce=?', (str(annonce or ''),)).fetchone()
    if not row or row['annonce'] not in {a['annonce'] for a in alertes(desk, inclure_vues=True, limite=1000)}:
        raise Stop('annonce_absente')
    desk.db.execute("UPDATE annonces5688 SET vu_par=?,vu_le=? WHERE annonce=? AND vu_le=''", (owner, _now(), row['annonce']))
    desk.db.commit()
    return {'message': 'Alerte marquée comme vue.'}


def _masques(desk):
    """Sociétés reliées à une fiche que le lecteur ne voit pas (dossier réservé) : leurs alertes lui sont soustraites."""
    from . import crm5636
    return {r['siren'] for r in desk.db.execute("SELECT siren,contact FROM societes5688 WHERE contact<>''") if crm5636._restricted(desk, r['contact'])}


def alertes(desk, inclure_vues=False, limite=20, siren=''):
    ensure_schema(desk)
    masques = _masques(desk)
    sql = 'SELECT * FROM annonces5688' + ('' if inclure_vues else " WHERE vu_le=''") + ' ORDER BY date DESC, releve DESC'
    out = []
    for r in desk.db.execute(sql):
        sirens = {s for s in r['sirens'].split(',') if s}
        if (masques and sirens & masques) or (siren and siren not in sirens):
            continue
        out.append({**dict(r), 'evenements': json.loads(r['evenements'])})
        if len(out) >= limite:
            break
    return out


def etat_source(desk):
    """Panne visible : sociétés dont la dernière lecture a échoué, depuis quand. Rien n'est conclu pour elles."""
    ensure_schema(desk)
    row = desk.db.execute("SELECT COUNT(*) n, MIN(panne_depuis) d FROM societes5688 WHERE actif=1 AND echecs>0").fetchone()
    return {'en_panne': row['n'], 'depuis': row['d'] or ''}


def _panne_html(desk):
    st = etat_source(desk)
    if not st['en_panne']:
        return ''
    return ('<p class="v88-panne" role="alert">Source BODACC injoignable depuis le %s : %d société(s) non vérifiée(s). Aucune conclusion à en tirer : la lecture reprendra '
            'et la période manquée sera relue.</p>') % (e(st['depuis'][:10]), st['en_panne'])


def _ligne(a, prefix, bouton=True):
    labels = ', '.join(EVENEMENTS.get(x, x) for x in a['evenements'])
    link = ('<a href="%s" target="_blank" rel="noopener noreferrer">Voir l’annonce officielle</a>' % e(a['url'], quote=True)) if a['url'] else ''
    action = ('<form class="m5-form m5-inline" data-api="m5688/veille/vu" data-reload="1"><input type="hidden" name="annonce" value="%s">'
              '<button class="ax-btn ghost" type="submit">Marquer comme vue</button></form>' % e(a['annonce'], quote=True)) if bouton and not a['vu_le'] else ''
    return ('<li><strong>%s</strong> <span class="ax-muted">%s · %s%s</span><br>%s%s %s %s</li>') % (
        e(a['nom']), e(a['date']), e(a['famille_libelle'] or labels), (' · ' + e(a['type_libelle'])) if a['type_libelle'] and a['type_libelle'] != 'Avis initial' else '',
        (e(a['detail']) + ' ') if a['detail'] else '', '', link, action)


def today_html(desk, prefix):
    """Bloc d'Aujourd'hui : vide tant qu'il n'y a ni alerte ni panne, pour ne rien ajouter à une page sans veille."""
    rows = alertes(desk, limite=8)
    panne = _panne_html(desk)
    if not rows and not panne:
        return ''
    return ('<section class="c530-card v88-veille" id="v88-societes" aria-labelledby="v88-t-societes"><div class="c530-head"><h2 id="v88-t-societes">Veille des sociétés</h2>'
            '<a href="%s">Toutes les sociétés suivies</a></div>%s%s</section>') % (
        e(prefix + '/contacts-crm?vue=veille', quote=True), panne, ('<ul class="v88-list">%s</ul>' % ''.join(_ligne(a, prefix) for a in rows)) if rows else '')


def _coches(actifs):
    return ''.join('<label class="m5-check"><input type="checkbox" name="evt:%s"%s> %s</label>' % (k, ' checked' if k in actifs else '', e(v)) for k, v in EVENEMENTS.items())


def page_html(desk, prefix):
    ensure_schema(desk)
    masques = _masques(desk)
    rows = [r for r in desk.db.execute('SELECT * FROM societes5688 WHERE actif=1 ORDER BY lower(nom)') if r['siren'] not in masques]
    items = ''
    for r in rows:
        events = ', '.join(EVENEMENTS[k] for k in json.loads(r['evenements']))
        state = ('Dernière lecture réussie : %s' % e(r['dernier_ok'][:10])) if r['dernier_ok'] else 'Pas encore lue'
        state += (' · dernière tentative échouée (%s)' % e(r['dernier_essai'][:10])) if r['echecs'] else ''
        name = ('<a href="%s">%s</a>' % (e(prefix + '/contacts-crm?id=' + r['contact'], quote=True), e(r['nom']))) if r['contact'] else e(r['nom'])
        items += ('<li><strong>%s</strong> <span class="ax-muted">SIREN %s · %s</span><br><small>%s</small>'
                  '<details><summary>Événements écoutés</summary><form class="m5-form" data-api="m5688/veille/suivre" data-reload="1"><input type="hidden" name="siren" value="%s">%s'
                  '<button class="ax-btn ghost" type="submit">Enregistrer</button></form></details>'
                  '<form class="m5-form m5-inline" data-api="m5688/veille/retirer" data-reload="1"><input type="hidden" name="id" value="%s"><button class="ax-btn ghost" type="submit">Retirer</button></form></li>') % (
            name, e(r['siren']), state, e(events), e(r['siren'], quote=True), _coches(json.loads(r['evenements'])), e(r['id'], quote=True))
    history = ''.join(_ligne(a, prefix) for a in alertes(desk, inclure_vues=True, limite=100))
    return ('<h1>Veille des sociétés</h1><p class="ax-muted">Lecture du BODACC une fois par jour au plus, sans IA. Le SIREN ne part que vers cette source publique. '
            'Les annonces relevées sont gardées %d jours.</p>%s<div class="crm-layout"><section class="ax-card"><h2>%d société(s) suivie(s)</h2><ul class="v88-list">%s</ul></section>'
            '<section class="ax-card"><h2>Suivre une société</h2><form class="m5-form" data-api="m5688/veille/suivre" data-reload="1"><label>SIREN<input name="siren" required inputmode="numeric" '
            'maxlength="17" placeholder="9 chiffres"></label><label>Nom (facultatif)<input name="nom" maxlength="200"></label><fieldset><legend>Événements à écouter</legend>%s</fieldset>'
            '<button class="ax-btn" type="submit">Suivre</button></form></section></div><section class="ax-card"><h2>Annonces relevées</h2><ul class="v88-list">%s</ul></section>') % (
        max(30, int(desk.settings('veille5688:retention_jours', RETENTION_JOURS) or RETENTION_JOURS)), _panne_html(desk), len(rows), items or '<li class="ax-muted">Aucune société suivie.</li>',
        _coches(EVENEMENTS), history or '<li class="ax-muted">Aucune annonce relevée.</li>')


# ------------------------------------------------------------------------------------------------ bloc de la fiche
def _proposition_html(desk, c, siren):
    try:
        prop = proposition(desk, c['id'], siren)
    except registres.SourceIndisponible:
        return '<p class="v88-panne" role="alert">Le registre ne répond pas. La fiche n’a pas été modifiée ; réessayez plus tard.</p>'
    except Stop as ex:
        if str(ex) in ('entreprise_inconnue_du_registre', 'siren_invalide'):
            return '<p class="notice">Le registre ne connaît pas ce SIREN.</p>'
        raise
    rows = ''
    for r in prop['champs']:
        name = e(r['champ'], quote=True)
        if r['etat'] == 'identique':
            action = '<span class="ax-muted">Déjà à jour</span>'
        elif r['etat'] == 'vide':
            action = '<label class="m5-check"><input type="checkbox" name="champ:%s" checked> Reprendre</label>' % name
        else:
            action = ('<label class="m5-check"><input type="checkbox" name="champ:%s"> Reprendre</label>'
                      '<label class="m5-check"><input type="checkbox" name="remplacer:%s"> Remplacer ma valeur</label>') % (name, name)
        rows += '<tr><th scope="row">%s</th><td>%s</td><td>%s</td><td>%s</td></tr>' % (e(r['libelle']), e(r['actuel']) or '<span class="ax-muted">vide</span>', e(r['propose']), action)
    note = ('<p class="ax-muted">Capital et RCS non vérifiés : le BODACC ne répond pas pour l’instant.</p>') if prop['complements_indisponibles'] else ''
    return ('<h3>Proposition pour %s</h3><p class="ax-muted">Source : %s. Lu le %s. Rien n’est repris sans votre confirmation ; une valeur déjà saisie n’est remplacée que si vous le cochez.</p>%s'
            '<form class="m5-form" data-api="m5688/fiche/confirmer" data-reload="1"><input type="hidden" name="contact" value="%s"><input type="hidden" name="siren" value="%s"><input type="hidden" name="jeton" value="%s">'
            '<div class="v88-scroll"><table class="ax-table"><thead><tr><th scope="col">Champ</th><th scope="col">Sur la fiche</th><th scope="col">Registre</th><th scope="col">Décision</th></tr></thead><tbody>%s</tbody></table></div>'
            '<button class="ax-btn" type="submit">Confirmer la reprise</button></form>') % (
        e(prop['nom']), e(prop['source']), e(prop['date'][:10]), note, e(c['id'], quote=True), e(prop['siren'], quote=True), e(prop['jeton'], quote=True), rows)


def _candidats_html(desk, prefix, c, texte):
    try:
        found = registres.chercher(desk, texte)
    except registres.SourceIndisponible:
        return '<p class="v88-panne" role="alert">Le registre ne répond pas. La fiche n’a pas été modifiée ; réessayez plus tard.</p>'
    except Stop:
        return '<p class="notice">Saisissez au moins trois caractères.</p>'
    if not found['resultats']:
        return '<p class="ax-muted">Aucune entreprise trouvée pour cette recherche.</p>'
    items = ''.join('<li><a href="%s">%s</a> <span class="ax-muted">SIREN %s · %s</span></li>' % (
        e('%s/contacts-crm?id=%s&registre=%s' % (prefix, c['id'], r['siren']), quote=True), e(r['nom_complet'] or r['nom']), e(r['siren']), e(r['adresse'])) for r in found['resultats'])
    return '<h3>Choisissez l’entreprise</h3><p class="ax-muted">Source : %s. Un homonyme reste possible : vérifiez l’adresse.</p><ul class="v88-list">%s</ul>' % (e(found['source']), items)


def fiche_html(desk, prefix, c, args):
    """Sections de la fiche contact : registres, provenance, veille et annonces."""
    ensure_schema(desk)
    texte = str(args.get('registre') or '').strip()
    current = c.get('siren') or ''
    result = ''
    if texte:
        try:
            result = _proposition_html(desk, c, registres.norm_siren(texte))
        except Stop as ex:
            if str(ex) != 'siren_invalide':
                raise
            result = _candidats_html(desk, prefix, c, texte)
    saved = provenance(desk, c['id'])
    labels = {k: v for k, v, _ in CHAMPS}
    prov = ''.join('<li><strong>%s</strong> : %s <small class="ax-muted">%s, lu le %s, confirmé par %s</small></li>' % (
        e(labels.get(k, k)), e(p['valeur']), e(p['source']), e(p['lu_le'][:10]), e(p['confirme_par'])) for k, p in saved.items())
    out = ('<section class="ax-card" id="v88-registre"><h2>Registres publics</h2><form method="get" class="crm-search"><input type="hidden" name="id" value="%s">'
           '<label class="m50-sr" for="v88-q">Nom ou SIREN</label><input id="v88-q" type="search" name="registre" value="%s" maxlength="120" placeholder="Nom ou SIREN"><button class="ax-btn ghost" type="submit">Chercher dans les registres</button></form>'
           '<p class="ax-muted">La recherche interroge l’annuaire public des entreprises et le BODACC. Elle ne modifie rien.</p>%s%s</section>') % (
        e(c['id'], quote=True), e(texte or current, quote=True), result, ('<h3>Valeurs reprises des registres</h3><ul class="v88-list">%s</ul>' % prov) if prov else '')
    try:
        siren = registres.norm_siren(current)
    except Stop:
        return out                             # sans SIREN : rien à suivre
    row = desk.db.execute('SELECT * FROM societes5688 WHERE siren=? AND actif=1', (siren,)).fetchone()
    events = json.loads(row['evenements']) if row else list(EVENEMENTS)
    rows = alertes(desk, inclure_vues=True, limite=30, siren=siren)
    out += ('<section class="ax-card" id="v88-suivi"><h2>Veille de cette société</h2>%s<form class="m5-form" data-api="m5688/veille/suivre" data-reload="1"><input type="hidden" name="contact" value="%s">%s'
            '<button class="ax-btn ghost" type="submit">%s</button></form><h3>Annonces relevées</h3><ul class="v88-list">%s</ul></section>') % (
        _panne_html(desk), e(c['id'], quote=True), _coches(events), 'Enregistrer' if row else 'Suivre cette société',
        ''.join(_ligne(a, prefix) for a in rows) or '<li class="ax-muted">Aucune annonce relevée.</li>')
    return out
