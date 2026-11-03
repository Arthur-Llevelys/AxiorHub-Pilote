"""5.6.89 : registre des pièces par partie (audit 5.6.88, chapitre 37).

Le registre s'appuie sur ce qui existe : ``modele5651`` (classement des documents), ``ged5636`` (données relevées), ``exhibit_registry_v240`` (empreinte et présence des fichiers),
``pieces520`` (historique de communication). Il ne déplace, ne renomme et ne réécrit aucun fichier.

- Une pièce est identifiée par son côté, sa partie productrice et son numéro affiché : le n° 1 du cabinet et le n° 1 adverse sont deux pièces. Le numéro reste un texte : « 1 bis » reste « 1 bis ».
- Doublon : même contenu (empreinte) ; la provenance dit si c'est une vraie répétition ou une même pièce venue de deux sources. Le titre ne décide jamais.
- Renumérotation : proposée pour les seules pièces du cabinet, appliquée sur décision. Une pièce reçue n'est jamais renumérotée ni réécrite.
- Catégories : le classement existant reste conservé ; la catégorie est une proposition, à valider, et une incertitude reste « à reclasser ».
"""
import json
from pathlib import PurePosixPath
import re
import sqlite3
import uuid

from .common import Stop, fold
from . import assertions5689 as memoire, contentieux5689 as base

COTES = {'cabinet': 'Cabinet', 'adverse': 'Partie adverse', 'commun': 'Commune', 'inconnu': 'À préciser'}
CATEGORIES = {k: v for k, v in memoire.CATEGORIES.items() if k != 'saisie'}     # une seule liste : celle de la mémoire du dossier
NON_COMMUNICABLES = ('note_recherche', 'projet')
SUFFIXES = 'bis|ter|quater|quinquies|sexies'


def cle_numero(numero):
    """Forme de comparaison ; le numéro affiché, lui, n'est jamais retouché. « n° 1bis » et « 1 bis » désignent la même pièce, « 1 bis » et « 1 » deux pièces."""
    texte = re.sub(r'\s+', ' ', str(numero or '')).strip().casefold()
    texte = re.sub(r'^(pi[eè]ce\s*)?(n\s*[°o]\.?\s*)?', '', texte).strip()
    return re.sub(r'(\d)\s*(' + SUFFIXES + r')\b', r'\1 \2', texte)


def ordre_numero(numero):
    parties = re.split(r'(\d+)', cle_numero(numero))
    return [int(x) if x.isdigit() else x for x in parties]


def citation(piece):
    """Citation sans ambiguïté, pour un acte du cabinet : la pièce adverse le dit et nomme sa partie."""
    numero = piece['numero']
    if piece['cote'] == 'cabinet':
        return 'pièce n° ' + numero
    if piece['cote'] == 'adverse':
        return 'pièce adverse n° ' + numero + ((' (' + piece['partie'] + ')') if piece['partie'] else '')
    return 'pièce n° ' + numero + ' (' + COTES.get(piece['cote'], piece['cote']).lower() + ')'


def _piece(desk, mid, ident):
    row = desk.db.execute('SELECT * FROM contentieux_exhibits5689 WHERE id=? AND matter=?', (str(ident or ''), mid)).fetchone()
    if not row:
        raise Stop('piece_absente')
    return row


def _registre(desk, mid, path):
    """Ligne du registre des fichiers : empreinte, version, présence. Registre encore absent : rien de supposé."""
    if not path:
        return None
    try:
        row = desk.db.execute('SELECT sha256,etag,status FROM exhibit_registry_v240 WHERE matter=? AND path=?', (mid, path)).fetchone()
    except sqlite3.Error:
        row = None
    if row:
        return dict(row)
    memoire.ensure_schema(desk)
    version = desk.db.execute("SELECT sha256,etag FROM source_versions5689 WHERE matter=? AND path=? AND etat='courante' ORDER BY importe_le DESC LIMIT 1", (mid, path)).fetchone()
    return {'sha256': version['sha256'], 'etag': version['etag'], 'status': 'present'} if version else None


def ajouter(desk, mid, data, owner, role, origine='humain'):
    """Enregistre une pièce. L'empreinte du registre prévaut sur celle annoncée : un écart est refusé. Une pièce adverse est marquée reçue."""
    base.schema(desk)
    base.garde(desk, mid)
    cle = base.cle_de(data.get('cle'))
    cote, numero = str(data.get('cote') or ''), re.sub(r'\s+', ' ', str(data.get('numero') or '')).strip()[:30]
    if cote not in COTES:
        raise Stop('cote_invalide')
    if not numero or not cle_numero(numero):
        raise Stop('numero_piece_requis')
    partie, path = base._texte(data.get('partie'), 200), str(data.get('path') or '')[:800]
    sha = str(data.get('sha256') or '').lower()
    if sha and not re.fullmatch(r'[0-9a-f]{64}', sha):
        raise Stop('empreinte_invalide')
    fichier = _registre(desk, mid, path)
    if fichier:
        if sha and sha != fichier['sha256']:
            raise Stop('empreinte_differente_du_registre')
        sha = fichier['sha256']
    params = base.empreinte([mid, cote, partie, cle_numero(numero), data.get('titre'), path, sha])
    old = base.deja_fait(desk, 'contentieux_exhibits5689', cle, params)
    if old:
        return base._ligne(old)
    if desk.db.execute("SELECT 1 FROM contentieux_exhibits5689 WHERE matter=? AND cote=? AND partie=? AND numero_cle=? AND statut='actif'", (mid, cote, partie, cle_numero(numero))).fetchone():
        raise Stop('numero_deja_utilise')
    ident, now = uuid.uuid4().hex, base.maintenant()
    recue = int(cote == 'adverse' or data.get('recue') in (True, 'on', '1', 1, 'true'))
    with base.transaction(desk):
        desk.db.execute('INSERT INTO contentieux_exhibits5689(id,matter,cle,parametres,cote,partie,numero,numero_cle,titre,version_id,sha256,path,provenance,date_piece,recue,origine,a_confirmer,created,updated) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)',
                        (ident, mid, cle, params, cote, partie, numero, cle_numero(numero), base._texte(data.get('titre'), 300), str(data.get('version_id') or (fichier or {}).get('etag') or '')[:200], sha, path, base._texte(data.get('provenance'), 300), str(data.get('date_piece') or '')[:10], recue, origine, int(origine != 'humain'), now, now))
        base.journal(desk, mid, 'piece', ident, 'creation', owner, '', {}, {'cote': cote, 'numero': numero, 'partie': partie})
    return base._ligne(_piece(desk, mid, ident))


def nouvelle_version(desk, mid, ident, data, owner, role):
    """Une version nouvelle garde numéro, côté et partie : la précédente reste au registre, marquée remplacée. Le titre identique ne la fait pas passer pour un doublon."""
    base.schema(desk)
    base.garde(desk, mid)
    row = _piece(desk, mid, ident)
    base.version_attendue(row, data.get('version'))
    cle = base.cle_de(data.get('cle'))
    path, sha = str(data.get('path') or row['path'])[:800], str(data.get('sha256') or '').lower()
    fichier = _registre(desk, mid, path)
    sha = sha or (fichier or {}).get('sha256', '')
    if not re.fullmatch(r'[0-9a-f]{64}', sha):
        raise Stop('empreinte_invalide')
    if sha == row['sha256']:
        raise Stop('meme_contenu_pas_une_version')
    params = base.empreinte([mid, row['id'], path, sha])
    old = base.deja_fait(desk, 'contentieux_exhibits5689', cle, params)
    if old:
        return base._ligne(old)
    neuf, now = uuid.uuid4().hex, base.maintenant()
    with base.transaction(desk):
        desk.db.execute("UPDATE contentieux_exhibits5689 SET statut='remplacee',revision=revision+1,updated=? WHERE id=?", (now, row['id']))
        desk.db.execute('INSERT INTO contentieux_exhibits5689(id,matter,cle,parametres,cote,partie,numero,numero_cle,titre,version,version_id,sha256,path,provenance,date_piece,recue,remplace,origine,created,updated) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)',
                        (neuf, mid, cle, params, row['cote'], row['partie'], row['numero'], row['numero_cle'], base._texte(data.get('titre'), 300) or row['titre'], row['version'] + 1, str(data.get('version_id') or (fichier or {}).get('etag') or '')[:200], sha, path, base._texte(data.get('provenance'), 300) or row['provenance'], row['date_piece'], row['recue'], row['id'], 'humain', now, now))
        base.journal(desk, mid, 'piece', neuf, 'nouvelle_version', owner, '', {'id': row['id'], 'sha256': row['sha256']}, {'id': neuf, 'sha256': sha})
    return base._ligne(_piece(desk, mid, neuf))


def retirer(desk, mid, ident, data, owner, role):
    """Retrait du registre actif, jamais du dossier : le fichier reste, et une citation devient visible comme absente."""
    base.schema(desk)
    base.garde(desk, mid)
    base.garde_role(role)
    row = _piece(desk, mid, ident)
    base.version_attendue(row, data.get('version'))
    motif = base._texte(data.get('motif'), 500)
    if len(motif) < 3:
        raise Stop('motif_obligatoire')
    with base.transaction(desk):
        desk.db.execute("UPDATE contentieux_exhibits5689 SET statut='retiree',revision=revision+1,updated=? WHERE id=?", (base.maintenant(), row['id']))
        base.journal(desk, mid, 'piece', row['id'], 'retrait', owner, motif, {'statut': row['statut']}, {'statut': 'retiree'})
    return base._ligne(_piece(desk, mid, ident))


def importer_registre(desk, mid, owner='migration'):
    """Reprise répétable : numéro et partie du classement existant conservés tels quels, rien n'est certifié. Sans partie connue, le côté reste « à préciser » : jamais supposé être le cabinet."""
    from . import modele5651
    base.schema(desk)
    base.garde(desk, mid)
    reprises = 0
    for d in modele5651.documents(desk, mid):
        if not (d['piece_number'] and (d['usage_piece'] == 'oui' or d['usage_piece'] == 'inconnu')):
            continue
        partie = d['piece_party']
        cote = 'cabinet' if partie == 'cabinet' else ('inconnu' if not partie else 'adverse')
        reprises += _reprendre(desk, mid, 'registre:%s:%s' % (mid, d['path']), cote, '' if cote != 'adverse' else partie, d['piece_number'], d['name'], d['path'], d['etag'], 'Classement du dossier', d['doc_date'], owner)
    # Pièces qualifiées dans la mémoire du dossier : partie et numéro repris tels quels
    clients = {fold(p['display_name']) for p in modele5651.parties(desk, mid) if p['client']}
    for v in memoire.versions(desk, mid, 'piece'):
        if not v['piece_numero']:
            continue
        cabinet = v['piece_partie'] == 'cabinet' or fold(v['piece_partie']) in clients
        reprises += _reprendre(desk, mid, 'memoire:%s:%s' % (mid, v['version_id']), 'cabinet' if cabinet else ('adverse' if v['piece_partie'] else 'inconnu'), '' if cabinet else v['piece_partie'], v['piece_numero'], v['piece_titre'] or PurePosixPath(v['path']).name, v['path'], v['version_id'], 'Mémoire du dossier', v['document_date'], owner, v['sha256'])
    from . import pieces520
    for c in pieces520.history(desk, mid):
        reprises += _reprendre(desk, mid, 'communication:%s:%s' % (mid, c['sha256']), 'cabinet', '', str(c['number']), c['title'], c['path'], '', 'Communiqué le ' + c['day'], '', owner, c['sha256'])
    return {'reprises': reprises}


def _reprendre(desk, mid, cle, cote, partie, numero, titre, path, version_id, provenance, date_piece, owner, sha=''):
    if desk.db.execute('SELECT 1 FROM contentieux_exhibits5689 WHERE cle=?', (cle,)).fetchone():
        return 0
    if desk.db.execute("SELECT 1 FROM contentieux_exhibits5689 WHERE matter=? AND cote=? AND partie=? AND numero_cle=? AND statut='actif'", (mid, cote, partie, cle_numero(numero))).fetchone():
        return 0       # le numéro est déjà tenu : la pièce enregistrée n'est pas écrasée par la reprise
    ajouter(desk, mid, {'cle': cle, 'cote': cote, 'partie': partie, 'numero': numero, 'titre': titre, 'path': path, 'version_id': version_id, 'provenance': provenance, 'date_piece': date_piece, 'sha256': '' if _registre(desk, mid, path) else sha}, owner, 'administrateur', origine='migration')
    return 1


def liste(desk, mid, tous=False):
    """Pièces séparées par partie. Les données relevées (ged5636) enrichissent la lecture ; elles ne fondent ni le numéro ni l'identité."""
    from . import ged5636
    base.schema(desk)
    base.garde(desk, mid)
    releves = {r['path']: r for r in ged5636.piece_records(desk, mid)}
    sql = 'SELECT * FROM contentieux_exhibits5689 WHERE matter=?' + ('' if tous else " AND statut='actif'")
    lignes = []
    for r in desk.db.execute(sql, (mid,)):
        out = base._ligne(r)
        fichier = _registre(desk, mid, r['path'])
        out['citation'], out['type_releve'] = citation(r), (releves.get(r['path']) or {}).get('type', '')
        out['etat_fichier'] = 'inconnu' if not fichier else ('version_changee' if r['sha256'] and fichier['sha256'] != r['sha256'] else ('present' if fichier['status'] == 'present' else fichier['status']))
        out['interne'] = est_interne(desk, mid, r['path']) if r['path'] else False
        lignes.append(out)
    lignes.sort(key=lambda x: ordre_numero(x['numero']))
    groupes = {}
    for x in lignes:
        groupes.setdefault((x['cote'], x['partie']), []).append(x)
    return {'groupes': [{'cote': c, 'libelle': COTES.get(c, c) + ((' : ' + p) if p else ''), 'partie': p, 'pieces': v} for (c, p), v in sorted(groupes.items(), key=lambda kv: (('cabinet', 'adverse', 'commun', 'inconnu').index(kv[0][0]), kv[0][1]))], 'pieces': lignes}


def doublons(desk, mid):
    """Regroupe par contenu. Deux pièces de même titre et de contenu différent ne sont jamais signalées ; une nouvelle version non plus. Rien n'est supprimé."""
    base.schema(desk)
    base.garde(desk, mid)
    groupes = {}
    for r in desk.db.execute("SELECT * FROM contentieux_exhibits5689 WHERE matter=? AND statut='actif' AND sha256<>''", (mid,)):
        groupes.setdefault(r['sha256'], []).append(r)
    out = []
    for sha, rows in groupes.items():
        if len(rows) < 2:
            continue
        provenances = {(r['cote'], r['partie'], fold(r['provenance'])) for r in rows}
        out.append({'sha256': sha, 'nature': 'meme_provenance' if len(provenances) == 1 else 'provenances_differentes', 'pieces': [{'id': r['id'], 'citation': citation(r), 'titre': r['titre'], 'provenance': r['provenance']} for r in rows],
                    'message': 'Même contenu enregistré plusieurs fois par la même provenance : à examiner.' if len(provenances) == 1 else 'Même contenu produit par des provenances différentes : chaque pièce garde sa référence.'})
    return out


# ------------------------------------------------------------------------------------------------ citations dans un texte
_NUM = r'\d{1,4}(?:\s*(?:' + SUFFIXES + r'))?'
CITE = re.compile(r"\bpi[eè]ces?\s+(adverses?\s+)?(?:n\s*[°o]s?\.?\s*)?(" + _NUM + r"(?:\s*(?:,|et|à)\s*(?:n\s*[°o]\.?\s*)?" + _NUM + r")*)(?:\s*\(([^()]{2,80})\))?", re.I)
JETON = re.compile(r'\[\[piece:([0-9a-f]{32})\]\]')


def citations_du_texte(texte):
    """Citations écrites en toutes lettres, avec leur côté annoncé. « pièces 3 à 5 » se déplie seulement entre numéros entiers."""
    out = []
    for m in CITE.finditer(str(texte or '')):
        adverse, partie = bool(m.group(1)), (m.group(3) or '').strip()
        nums = re.findall(_NUM, m.group(2), re.I)
        separateurs = re.findall(r'(?:,|et|à)', re.sub(_NUM, '', m.group(2), flags=re.I))
        valeurs = []
        for i, n in enumerate(nums):
            n = re.sub(r'\s+', ' ', n)
            if i and i - 1 < len(separateurs) and separateurs[i - 1] == 'à' and valeurs and valeurs[-1].isdigit() and n.isdigit() and int(valeurs[-1]) < int(n) <= int(valeurs[-1]) + 200:
                valeurs += [str(x) for x in range(int(valeurs[-1]) + 1, int(n) + 1)]
            else:
                valeurs.append(n)
        extrait = re.sub(r'\s+', ' ', str(texte)[max(0, m.start() - 40):m.end() + 30]).strip()
        out += [{'numero': v, 'adverse': adverse, 'partie': partie, 'extrait': extrait} for v in valeurs]
    return out


def resoudre(pieces, cite):
    """Trouve la pièce citée, sans jamais prendre un numéro d'une partie pour celui d'une autre. Renvoie (pièce, code) : une citation nue vise le cabinet ; une pièce adverse doit se dire adverse."""
    cle = cle_numero(cite['numero'])
    if cite['adverse']:
        trouvees = [p for p in pieces if p['cote'] == 'adverse' and p['numero_cle'] == cle and (not cite['partie'] or fold(cite['partie']) == fold(p['partie']))]
        if len(trouvees) > 1:
            return None, 'partie_a_preciser'
        return (trouvees[0], '') if trouvees else (None, 'piece_absente')
    trouvees = [p for p in pieces if p['cote'] == 'cabinet' and p['numero_cle'] == cle]
    if trouvees:
        return trouvees[0], ''
    if any(p['numero_cle'] == cle for p in pieces):
        return None, 'numero_d_une_autre_partie'
    return None, 'piece_absente'


# ------------------------------------------------------------------------------------------------ renumérotation
def proposer_renumerotation(desk, mid, data, owner, role):
    """Proposition seulement. ``nouveaux`` : {identifiant de pièce: nouveau numéro}. Une pièce reçue ou d'une autre partie est refusée, sans exception."""
    base.schema(desk)
    base.garde(desk, mid)
    cle = base.cle_de(data.get('cle'))
    nouveaux = data.get('nouveaux')
    if not isinstance(nouveaux, dict) or not nouveaux:
        raise Stop('renumerotation_vide')
    params = base.empreinte([mid, nouveaux])
    old = base.deja_fait(desk, 'contentieux_renumerotations5689', cle, params)
    if old:
        return vue_renumerotation(old)
    plan = []
    for ident, numero in nouveaux.items():
        piece = _piece(desk, mid, ident)
        if piece['cote'] != 'cabinet' or piece['recue']:
            raise Stop('piece_recue_non_renumerotable')
        numero = re.sub(r'\s+', ' ', str(numero or '')).strip()[:30]
        if not cle_numero(numero):
            raise Stop('numero_piece_requis')
        if piece['statut'] != 'actif':
            raise Stop('piece_absente')
        if cle_numero(numero) != piece['numero_cle']:
            plan.append({'id': piece['id'], 'avant': piece['numero'], 'apres': numero, 'titre': piece['titre'], 'version': piece['revision']})
    bouges = {x['id'] for x in plan}
    fixes = {r['numero_cle'] for r in desk.db.execute("SELECT id,numero_cle FROM contentieux_exhibits5689 WHERE matter=? AND cote='cabinet' AND statut='actif'", (mid,)) if r['id'] not in bouges}
    cibles = [cle_numero(x['apres']) for x in plan]
    if len(set(cibles)) != len(cibles) or set(cibles) & fixes:
        raise Stop('numero_deja_utilise')
    references = _references_a_corriger(desk, mid, plan)
    ident, now = uuid.uuid4().hex, base.maintenant()
    with base.transaction(desk):
        desk.db.execute('INSERT INTO contentieux_renumerotations5689(id,matter,cle,parametres,plan,etat,propose_par,created,updated) VALUES(?,?,?,?,?,?,?,?,?)',
                        (ident, mid, cle, params, json.dumps({'pieces': plan, 'references': references}, ensure_ascii=False), 'proposee', str(owner)[:160], now, now))
        base.journal(desk, mid, 'renumerotation', ident, 'proposition', owner, '', {}, {'pieces': len(plan), 'references': len(references)})
    return vue_renumerotation(desk.db.execute('SELECT * FROM contentieux_renumerotations5689 WHERE id=?', (ident,)).fetchone())


def vue_renumerotation(row):
    out = base._ligne(row)
    out['plan'] = json.loads(row['plan'])
    return out


def _remplacer_citations(texte, correspondance):
    """Réécrit les numéros du cabinet écrits en toutes lettres ; une citation adverse ne bouge pas. Le remplacement est simultané : 1→2 et 2→1 ne se marchent pas dessus."""
    def citer(m):
        if m.group(1):
            return m.group(0)

        def numero(n):
            return correspondance.get(cle_numero(n.group(0)), n.group(0))
        debut, fin = m.span(2)
        d, f = debut - m.start(), fin - m.start()
        return m.group(0)[:d] + re.sub(_NUM, numero, m.group(2), flags=re.I) + m.group(0)[f:]
    return CITE.sub(citer, texte)


def _references_a_corriger(desk, mid, plan):
    correspondance = {cle_numero(x['avant']): x['apres'] for x in plan}
    out = []
    for s in desk.db.execute("SELECT s.id,s.titre,s.texte,a.titre AS acte FROM contentieux_sections5689 s JOIN contentieux_actes5689 a ON a.id=s.acte_id WHERE a.matter=? AND a.etat<>'annule' AND s.texte<>''", (mid,)):
        apres = _remplacer_citations(s['texte'], correspondance)
        if apres != s['texte']:
            out.append({'section': s['id'], 'acte': s['acte'], 'titre': s['titre'], 'avant': s['texte'][:300], 'apres': apres[:300]})
    return out


def appliquer_renumerotation(desk, mid, data, owner, role):
    """Sur décision. Les numéros changent au registre, jamais sur les fichiers ; l'ancien numéro reste noté. Une pièce modifiée depuis la proposition la rend périmée."""
    base.schema(desk)
    base.garde(desk, mid)
    base.garde_role(role)
    row = desk.db.execute('SELECT * FROM contentieux_renumerotations5689 WHERE id=? AND matter=?', (str(data.get('id') or ''), mid)).fetchone()
    if not row:
        raise Stop('renumerotation_absente')
    if row['etat'] == 'appliquee':
        return vue_renumerotation(row)
    if row['etat'] != 'proposee':
        raise Stop('renumerotation_close')
    plan = json.loads(row['plan'])
    for x in plan['pieces']:
        piece = _piece(desk, mid, x['id'])
        if piece['statut'] != 'actif' or piece['revision'] != x['version'] or piece['cote'] != 'cabinet' or piece['recue']:
            with base.transaction(desk):
                desk.db.execute("UPDATE contentieux_renumerotations5689 SET etat='perimee',updated=? WHERE id=?", (base.maintenant(), row['id']))
            raise Stop('renumerotation_perimee')
    now, correspondance = base.maintenant(), {cle_numero(x['avant']): x['apres'] for x in plan['pieces']}
    with base.transaction(desk):
        # Deux passes : l'index unique refuserait un échange de numéros (1↔2) fait d'un coup.
        for x in plan['pieces']:
            desk.db.execute('UPDATE contentieux_exhibits5689 SET numero_cle=? WHERE id=?', ('~' + x['id'], x['id']))
        for x in plan['pieces']:
            desk.db.execute('UPDATE contentieux_exhibits5689 SET numero=?,numero_cle=?,numero_precedent=?,revision=revision+1,updated=? WHERE id=?', (x['apres'], cle_numero(x['apres']), x['avant'], now, x['id']))
        for s in desk.db.execute("SELECT s.id,s.texte FROM contentieux_sections5689 s JOIN contentieux_actes5689 a ON a.id=s.acte_id WHERE a.matter=? AND a.etat<>'annule' AND s.texte<>''", (mid,)).fetchall():
            apres = _remplacer_citations(s['texte'], correspondance)
            if apres != s['texte']:
                desk.db.execute('UPDATE contentieux_sections5689 SET texte=?,revision=revision+1,updated=? WHERE id=?', (apres, now, s['id']))
        desk.db.execute("UPDATE contentieux_renumerotations5689 SET etat='appliquee',decide_par=?,updated=? WHERE id=?", (str(owner)[:160], now, row['id']))
        base.journal(desk, mid, 'renumerotation', row['id'], 'application', owner, '', {}, {'pieces': len(plan['pieces'])})
    return vue_renumerotation(desk.db.execute('SELECT * FROM contentieux_renumerotations5689 WHERE id=?', (row['id'],)).fetchone())


# ------------------------------------------------------------------------------------------------ catégories
def _categorie_de(doc):
    """Proposition depuis le classement existant. Ce qui n'est pas clair reste « à reclasser » : la migration ne tranche pas à la place de l'avocat."""
    from .modele5651 import DECISION_NATURES, EVIDENCE_NATURES, PROCEDURE_NATURES
    nature = doc['nature']
    if doc.get('categorie_memoire') and doc['categorie_memoire'] != 'a_reclasser':
        return doc['categorie_memoire'], 'Catégorie de la mémoire du dossier'
    if nature in DECISION_NATURES:
        return 'decision', 'Nature du document : décision'
    if nature in PROCEDURE_NATURES:
        return 'acte_procedure', 'Nature du document : acte de procédure'
    if nature == 'courriel':
        return 'correspondance', 'Nature du document : courriel'
    if nature in ('jurisprudence',):
        return 'note_recherche', 'Nature du document : jurisprudence ou doctrine'
    if nature == 'note_interne':
        return 'projet', 'Nature du document : note ou projet interne'
    if nature == 'courrier' and doc['usage_piece'] != 'oui':
        return 'correspondance', 'Nature du document : courrier'
    if doc['usage_piece'] == 'oui' or doc['piece_number'] or nature in EVIDENCE_NATURES:
        return 'piece', 'Usage ou nature de pièce'
    return 'a_reclasser', 'Classement incertain'


def migrer_categories(desk, mid):
    """Répétable, sans effet externe. Le classement antérieur est conservé tel quel ; rien n'est validé, et une décision prise n'est jamais refaite."""
    from . import modele5651
    base.schema(desk)
    memoire.ensure_schema(desk)
    creees, now = 0, base.maintenant()
    versions = {r['path']: r['categorie'] for r in desk.db.execute("SELECT path,categorie FROM source_versions5689 WHERE matter=? AND etat='courante' AND path<>'' ORDER BY importe_le", (mid,))}
    with base.transaction(desk):
        for d in modele5651.documents(desk, mid):
            categorie, motif = _categorie_de({**d, 'categorie_memoire': versions.get(d['path'], '')})
            anterieur = {'nature': d['nature'], 'nature_source': d['nature_source'], 'usage_piece': d['usage_piece'], 'piece_party': d['piece_party'], 'piece_number': d['piece_number']}
            creees += desk.db.execute('INSERT OR IGNORE INTO contentieux_categories5689(matter,path,categorie,motif,classement_anterieur,created,updated) VALUES(?,?,?,?,?,?,?)',
                                      (mid, d['path'], categorie, motif, json.dumps(anterieur, ensure_ascii=False), now, now)).rowcount
    return {'creees': creees}


def migrer(desk):
    """Reprise de tout le cabinet, pour l'intégrateur : additive, répétable, sans lecture du stockage ni effet externe. Un dossier en échec n'arrête pas les autres et reste compté."""
    from .common import load_matters
    bilan = {'dossiers': 0, 'categories': 0, 'pieces': 0, 'echecs': 0}
    for dossier in load_matters(desk.c, unfiltered=True):
        try:
            bilan['categories'] += migrer_categories(desk, dossier['id'])['creees']
            bilan['pieces'] += importer_registre(desk, dossier['id'])['reprises']
            bilan['dossiers'] += 1
        except (Stop, sqlite3.Error):
            bilan['echecs'] += 1
    return bilan


def categories(desk, mid, statut=''):
    base.schema(desk)
    base.garde(desk, mid)
    sql, params = 'SELECT * FROM contentieux_categories5689 WHERE matter=?', [mid]
    if statut:
        sql += ' AND statut=?'
        params.append(statut)
    return [{**dict(r), 'classement_anterieur': json.loads(r['classement_anterieur'] or '{}'), 'nom': PurePosixPath(r['path']).name, 'categorie_libelle': CATEGORIES.get(r['categorie'], r['categorie'])} for r in desk.db.execute(sql + ' ORDER BY categorie, path LIMIT 500', params)]


def decider_categorie(desk, mid, data, owner, role):
    """Valide ou corrige une catégorie. Une pièce écartée suit dans le classement du dossier, sur décision seulement ; l'état antérieur reste lisible."""
    from . import modele5651
    base.schema(desk)
    base.garde(desk, mid)
    base.garde_role(role)
    path = str(data.get('path') or '')
    row = desk.db.execute('SELECT * FROM contentieux_categories5689 WHERE matter=? AND path=?', (mid, path)).fetchone()
    if not row:
        raise Stop('document_absent')
    base.version_attendue(row, data.get('version'))
    categorie = str(data.get('categorie') or row['categorie'])
    if categorie not in CATEGORIES or categorie == 'a_reclasser':
        raise Stop('categorie_invalide')
    with base.transaction(desk):
        desk.db.execute("UPDATE contentieux_categories5689 SET categorie=?,statut='valide',valide_par=?,valide_le=?,revision=revision+1,updated=? WHERE matter=? AND path=?", (categorie, str(owner)[:160], base.maintenant(), base.maintenant(), mid, path))
        base.journal(desk, mid, 'categorie', path, 'validation', owner, '', {'categorie': row['categorie']}, {'categorie': categorie})
    anterieur = json.loads(row['classement_anterieur'] or '{}')
    if categorie != 'piece' and anterieur.get('usage_piece') == 'oui':
        modele5651.classify(desk, mid, path, owner, piece=False)
    # Même décision dans la mémoire du dossier : une pièce y exige partie et numéro, connus ou à compléter là-bas.
    version = desk.db.execute("SELECT * FROM source_versions5689 WHERE matter=? AND path=? AND etat='courante' ORDER BY importe_le DESC LIMIT 1", (mid, path)).fetchone()
    if version and (categorie != 'piece' or (version['piece_partie'] and version['piece_numero'])):
        memoire.qualifier_piece(desk, mid, version['version_id'], {'categorie': categorie, 'partie': version['piece_partie'], 'numero': version['piece_numero']}, owner, role)
    return {**dict(desk.db.execute('SELECT * FROM contentieux_categories5689 WHERE matter=? AND path=?', (mid, path)).fetchone()), 'classement_anterieur': json.loads(row['classement_anterieur'] or '{}')}


def est_interne(desk, mid, path):
    """Vrai pour ce qui ne part pas : correspondance confidentielle, note, projet. Une catégorie seulement proposée compte aussi : le doute retient le fichier."""
    from .confidentiel5664 import is_confidential
    if is_confidential(path):
        return True
    ligne = desk.db.execute('SELECT categorie,statut FROM contentieux_categories5689 WHERE matter=? AND path=?', (mid, path)).fetchone()
    if ligne:
        if ligne['categorie'] in NON_COMMUNICABLES:
            return True
        if ligne['statut'] == 'valide':
            return False
    memoire.ensure_schema(desk)
    version = desk.db.execute("SELECT categorie FROM source_versions5689 WHERE matter=? AND path=? AND etat='courante' ORDER BY importe_le DESC LIMIT 1", (mid, path)).fetchone()
    if version and version['categorie'] in NON_COMMUNICABLES:
        return True
    try:
        doc = desk.db.execute('SELECT nature FROM d51_documents WHERE matter=? AND path=?', (mid, path)).fetchone()
    except sqlite3.Error:
        return False
    return bool(doc) and doc['nature'] == 'note_interne'
