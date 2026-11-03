"""5.6.89 (chapitre 46, UX04) : état de chaque fonction, avec sa preuve, et conservation des 85 fonctions.

**État** : non configurée, déclarée, testée en simulation, qualifiée sur service réel, disponible dans le périmètre autorisé, dégradée. Il se calcule à la lecture, à partir des preuves de
``connector_readiness5689`` (pas de second circuit, pas de cache) ; un relevé conserve seulement les changements constatés.

- Une fonction interne n'a pas de service à prouver : ses essais automatiques valent pour la version qui les a passés, et pour elle seule.
- Une fonction qui dépend d'un service externe ne passe « qualifiée sur service réel » que par ``QUALIFIEES_REEL``, liste tenue à la publication, sur preuve remise par une personne. **Elle est
  vide en 5.6.89** : aucun comparatif réel, aucun compte réel n'a été éprouvé. Une preuve de connexion du cabinet montre qu'un compte répond, pas que la fonction a été qualifiée.
- Une révocation (déjà relue par ``connector_readiness5689`` avant chaque effet) ou une version incompatible (``SEUILS_ADAPTATEUR``, ``SEUILS_FONCTION``) dégrade la fonction et suspend les seules étapes concernées.
- Le tableau décrit des fonctions livrées. Les propositions d'assistance IA de l'annexe n'y figurent pas : une proposition n'est pas une fonction.

**Conservation** : ``conservation`` compare le registre en service à ``fonctions_reference5688`` (identifiants, routes, alias, droits, prérequis). Tout ajout est listé, tout retrait ou écart fait échouer le contrôle.
"""
from datetime import datetime, timezone
from pathlib import Path
import uuid

from . import connector_readiness5689 as prep
from . import fonctions5631 as registre
from . import fonctions_reference5688 as reference
from .common import Stop

SCHEMA = '''CREATE TABLE IF NOT EXISTS fonction_releves5689(id TEXT PRIMARY KEY, fonction TEXT NOT NULL, etat TEXT NOT NULL, motif TEXT NOT NULL DEFAULT '', preuve_nature TEXT NOT NULL DEFAULT '', preuve_version TEXT NOT NULL DEFAULT '',
  preuve_date TEXT NOT NULL DEFAULT '', version_logiciel TEXT NOT NULL, at TEXT NOT NULL, par TEXT NOT NULL);
CREATE INDEX IF NOT EXISTS fonction_releves5689_fonction ON fonction_releves5689(fonction, at)'''
RELEVES_CONSERVES = 5000

ETATS = {'non_configuree': 'Non configurée', 'declaree': 'Déclarée', 'simulee': 'Testée en simulation', 'qualifiee_reel': 'Qualifiée sur service réel', 'disponible': 'Disponible dans le périmètre autorisé', 'degradee': 'Dégradée'}
MOTIFS = {'service_non_configure': 'Service non configuré', 'aucune_preuve_valide': 'Aucune preuve valide', 'non_qualifiee_sur_service_reel': 'Non qualifiée sur service réel', 'preuve_d_une_autre_version': 'Preuve d’une autre version',
          'version_incompatible': 'Version incompatible : à requalifier', 'preuve_du_cabinet_a_renouveler': 'Preuve du cabinet à renouveler', 'capacite_revoquee': 'Capacité révoquée'}
# Version et date des essais automatiques de cette publication. 5.6.93 : tirées de la version publiée et de son VALIDATION ; en 5.6.92,
# la valeur écrite à la main était restée à la version précédente et les fonctions internes s'affichaient « Déclarée ».
def _preuve_de_publication():
    import json
    from . import __version__
    fichier = Path(__file__).resolve().parent.parent / ('VALIDATION-%s.json' % __version__)
    try:
        date = str(json.loads(fichier.read_text(encoding='utf-8')).get('date_utc') or '')[:10]
    except (OSError, ValueError):
        date = ''
    return __version__, date


VERSION_PREUVE, DATE_PREUVE = _preuve_de_publication()
# Fonction → {'version', 'date', 'reference'} : preuve sur service réel, remise par une personne. Vide en 5.6.89.
QUALIFIEES_REEL = {}
# Publication d'une version incompatible : preuve antérieure à ce seuil nulle, étapes concernées suspendues jusqu'à requalification. Vide en 5.6.89.
SEUILS_ADAPTATEUR = {}      # capacité → version minimale de l'adaptateur
SEUILS_FONCTION = {}        # fonction → version minimale de la preuve sur service réel

# Prérequis du registre → capacité prouvable par ``connector_readiness5689``. Téléphonie et Invoice Ninja n'ont pas de preuve : ils restent au mieux « déclarés ».
CAPACITES = {'storage': 'stockage.lecture', 'mail': 'mail.read', 'ai': 'ai.generate', 'calendar': 'calendar.read', 'legal': 'legal.search'}
# Services externes dont dépend une fonction sans que le registre en fasse un prérequis (elle s'ouvre sans eux).
EXTERNES = {'planning': ('calendar',), 'recherche_juridique': ('legal',), 'sources': ('legal',), 'veille': ('legal',), 'talk': ('storage',), 'relances': ('invoice',), 'accueil_administratif': ('telephony',)}
# Fonction → mission type : étapes à suspendre (``connector_readiness5689.MISSIONS``).
MISSIONS = {'documents': 'redaction', 'production': 'redaction', 'courriels': 'courriel', 'planning': 'agenda', 'recherche_juridique': 'recherche', 'missions_complexes': 'contentieux', 'contentieux': 'contentieux', 'dossiers': 'lecture'}

# Chapitre 21 : ce qui reste à éprouver sur de vrais services. Aucune de ces lignes n'est levée par un test local.
LIMITES = (
    {'id': 'drive', 'titre': 'Google Drive', 'garantie': 'Suspendue tant que le contrôle d’aptitude n’a pas réussi sur le compte du cabinet : AxiorHub transmet désormais la version attendue pour le remplacement et la corbeille, mais cette voie n’a été exercée que sur un faux service.',
     'essai': 'Lecture, création exclusive, remplacement concurrent, corbeille et reprise, sur un compte d’essai.', 'fonctions': ('dossiers', 'documents')},
    {'id': 'graph', 'titre': 'OneDrive et SharePoint', 'garantie': 'Non annoncée pour les gros fichiers : la version est vérifiée à l’ouverture de la session d’envoi et relue avant le dernier bloc, ce qui ne vaut pas condition atomique au moment de la publication.',
     'essai': 'Gros fichiers, sessions de transfert, modification concurrente pendant l’envoi.', 'fonctions': ('dossiers', 'documents')},
    {'id': 'agenda', 'titre': 'Outlook et Google Agenda', 'garantie': 'Protection par séquence contre un changement local pendant une copie, exercée sur un faux service.', 'essai': 'Copies, modification, annulation, changements de fuseau et conflits réels.', 'fonctions': ('planning',)},
    {'id': 'restauration', 'titre': 'Sauvegarde et restauration', 'garantie': 'Essai automatisé sur un cabinet fictif, sur une instance isolée. L’archive ne remplace pas la sauvegarde de Nextcloud et des courriels.', 'essai': 'Restaurer sur une machine isolée et vérifier cohérence, secrets, pièces et périmètre exact.', 'fonctions': ()},
    {'id': '2fa', 'titre': 'Double authentification et Nextcloud', 'garantie': 'Exercée sur des faux services seulement.', 'essai': 'Téléphone réel, codes de secours, révocation des sessions, liaison Nextcloud.', 'fonctions': ('mon_compte',)},
    {'id': 'factures', 'titre': 'Factures et signatures', 'garantie': 'Aucun envoi réel n’a été effectué.', 'essai': 'Comptes d’essai des prestataires : statuts, erreurs, doublons et documents exacts.', 'fonctions': ('facturation', 'relances')},
    {'id': 'eurooffice', 'titre': 'EuroOffice', 'garantie': 'Les essais du cœur JavaScript ne valent pas parcours complet.', 'essai': 'Version et image épinglées, modifications de l’utilisateur, deux éditeurs, coupures réseau, sauvegarde réouverte.', 'fonctions': ('documents',)},
)


def _now():
    return datetime.now(timezone.utc).isoformat(timespec='microseconds')


def _version():
    from . import __version__
    return __version__


def _tuple(version):
    try:
        return tuple(int(x) for x in str(version).split('.'))
    except ValueError:
        return (0,)


def ensure_schema(desk):
    if getattr(desk, '_schema5689fn', False):
        return
    if not desk.db.execute("SELECT 1 FROM sqlite_master WHERE name='fonction_releves5689_fonction'").fetchone():
        for instruction in SCHEMA.split(';'):
            desk.db.execute(instruction)
        desk.db.commit()
    desk._schema5689fn = True


# ------------------------------------------------------------------------------------------------ versions incompatibles
def adaptateur_compatible(desk, capability):
    """Vrai sans seuil publié. Avec un seuil, il faut une preuve réussie d'un adaptateur au moins à ce niveau : l'absence de preuve ne suffit pas."""
    seuil = SEUILS_ADAPTATEUR.get(capability)
    if not seuil:
        return True
    prep.ensure_schema(desk)
    ligne = desk.db.execute("SELECT adapter_version FROM capability_proofs5689 WHERE capability=? AND result='reussi' ORDER BY checked_at DESC LIMIT 1", (capability,)).fetchone()
    return ligne is not None and _tuple(ligne[0]) >= _tuple(seuil)


def garde_version(desk, capability):
    """Relue par ``connector_readiness5689.avant_effet`` avant chaque effet, comme la révocation."""
    if not adaptateur_compatible(desk, capability):
        raise Stop('version_incompatible')


def etapes(desk, owner, role, mission):
    """Préparation d'une mission, avec les étapes suspendues par une version incompatible en plus de celles que suspend déjà une révocation. Les autres étapes continuent."""
    out = prep.preparation(desk, owner, role, mission)
    for etape in out['etapes']:
        bloquees = [c for c in etape['capacites'] if not adaptateur_compatible(desk, c)]
        if bloquees:
            etape.update(suspendue=True, prete=False, manque=sorted(set(etape['manque']) | set(bloquees)), motif='version_incompatible')
    return out


# ------------------------------------------------------------------------------------------------ état
def _besoins(fid):
    return tuple(registre.INDEX[fid][4]) + tuple(x for x in EXTERNES.get(fid, ()) if x not in registre.INDEX[fid][4])


def _preuve_connexion(lignes):
    """Dernière preuve de connexion valide : sa date et la version de son adaptateur, jamais une preuve périmée."""
    dates = [(p['date'], p['version_adaptateur']) for l in lignes for p in l['preuves'].values() if p['valide']]
    if not dates:
        return None
    date, version = max(dates)
    return {'nature': 'preuve_de_connexion', 'version': version, 'date': date}


def etat_fonction(desk, fid, owner='cabinet', role='administrateur', _cache=None):
    if fid not in registre.INDEX:
        raise Stop('fonction_inconnue')
    cache = {} if _cache is None else _cache
    config = cache.setdefault('__config', registre.capabilities(desk.c))
    besoins = _besoins(fid)
    perimetre = registre.visible(role, registre.INDEX[fid][2])
    actuelle = _version() == VERSION_PREUVE
    essais = {'nature': 'essais_automatiques' if not besoins else 'essais_simules', 'version': VERSION_PREUVE, 'date': DATE_PREUVE} if actuelle else None
    limites = [l['id'] for l in LIMITES if fid in l['fonctions']]

    def fini(etat, motif='', preuve=None, capacites=()):
        return {'id': fid, 'groupe': registre.INDEX[fid][1], 'route': registre.INDEX[fid][2], 'etat': etat, 'etat_libelle': ETATS[etat], 'motif': motif, 'motif_libelle': MOTIFS.get(motif, 'Refusée par le service ou la politique' if motif else ''), 'externe': bool(besoins), 'perimetre_autorise': bool(perimetre), 'preuve': preuve,
                'capacites': list(capacites), 'limites': limites}

    if not besoins:
        return fini('disponible', '', essais) if actuelle else fini('declaree', 'preuve_d_une_autre_version')
    lignes, sans_preuve, manquants = [], [], []
    for besoin in besoins:
        capacite = CAPACITES.get(besoin)
        if capacite is None:
            (sans_preuve if besoin in config else manquants).append(besoin)
            continue
        if capacite not in cache:
            cache[capacite] = prep.etat_logique(desk, capacite, owner, role)
        lignes.append(cache[capacite])
    capacites = [{'capacite': l['capacite_requise'], 'etat': l['etat'], 'etat_libelle': l['etat_libelle'], 'code': l.get('code', '')} for l in lignes]
    etats = {l['etat'] for l in lignes}
    incompatibles = [l['capacite_requise'] for l in lignes if not adaptateur_compatible(desk, l['capacite_requise'])]
    if incompatibles:
        return fini('degradee', 'version_incompatible', None, capacites)
    panne = next((l for l in lignes if l['etat'] in ('revoquee', 'refusee', 'politique_refuse')), None)
    if panne:
        return fini('degradee', panne.get('code') or panne['etat'], None, capacites)
    if 'non_configuree' in etats or manquants:
        return fini('non_configuree', 'service_non_configure', None, capacites)
    if 'droits_insuffisants' in etats:
        perimetre = False
    qualifiee = QUALIFIEES_REEL.get(fid)
    if qualifiee:
        seuil = SEUILS_FONCTION.get(fid)
        if seuil and _tuple(qualifiee['version']) < _tuple(seuil):
            return fini('degradee', 'version_incompatible', None, capacites)
        preuve = {'nature': 'service_reel', **{k: qualifiee[k] for k in ('version', 'date', 'reference')}}
        if etats <= {'prouvee'} and not sans_preuve and perimetre:
            return fini('disponible', '', preuve, capacites)
        return fini('qualifiee_reel', 'preuve_du_cabinet_a_renouveler', preuve, capacites)
    if not etats <= {'prouvee'} or sans_preuve:
        return fini('declaree', 'aucune_preuve_valide', None, capacites)
    if not actuelle:
        return fini('declaree', 'preuve_d_une_autre_version', _preuve_connexion(lignes), capacites)
    return fini('simulee', 'non_qualifiee_sur_service_reel', {**essais, 'connexion': _preuve_connexion(lignes)}, capacites)


def tableau(desk, owner='cabinet', role='administrateur'):
    """Toutes les fonctions du registre. Lecture, sans écriture : rien n'est figé à l'ouverture de la page."""
    cache = {}
    lignes = [etat_fonction(desk, row[0], owner, role, cache) for row in registre.FUNCTIONS]
    compte = dict.fromkeys(ETATS, 0)
    for l in lignes:
        compte[l['etat']] += 1
    return {'version_logiciel': _version(), 'fonctions': lignes, 'compte': compte, 'libelles': ETATS}


def relever(desk, owner):
    """Conserve l'état de chaque fonction quand il a changé. Rétention : les ``RELEVES_CONSERVES`` lignes les plus récentes."""
    ensure_schema(desk)
    nouveaux = 0
    at = _now()
    for ligne in tableau(desk)['fonctions']:
        derniere = desk.db.execute('SELECT etat,motif FROM fonction_releves5689 WHERE fonction=? ORDER BY at DESC LIMIT 1', (ligne['id'],)).fetchone()
        if derniere is not None and (derniere['etat'], derniere['motif']) == (ligne['etat'], ligne['motif']):
            continue
        preuve = ligne['preuve'] or {}
        desk.db.execute('INSERT INTO fonction_releves5689(id,fonction,etat,motif,preuve_nature,preuve_version,preuve_date,version_logiciel,at,par) VALUES(?,?,?,?,?,?,?,?,?,?)',
                        (uuid.uuid4().hex, ligne['id'], ligne['etat'], ligne['motif'], preuve.get('nature', ''), preuve.get('version', ''), preuve.get('date', ''), _version(), at, str(owner)[:200]))
        nouveaux += 1
    desk.db.execute('DELETE FROM fonction_releves5689 WHERE id NOT IN (SELECT id FROM fonction_releves5689 ORDER BY at DESC LIMIT ?)', (RELEVES_CONSERVES,))
    desk.db.commit()
    return {'changements': nouveaux}


def historique(desk, fid, limite=20):
    ensure_schema(desk)
    return [dict(r) for r in desk.db.execute('SELECT etat,motif,preuve_nature,preuve_version,preuve_date,version_logiciel,at FROM fonction_releves5689 WHERE fonction=? ORDER BY at DESC LIMIT ?', (fid, limite))]


# ------------------------------------------------------------------------------------------------ conservation (UX04)
def droits_actuels(route):
    """Mêmes relevés que la référence : rôles admis en lecture sur la route, droit fin éventuel."""
    from . import membres5650, standalone_auth
    chemin = route.split('?', 1)[0]
    return tuple(r for r in ('administrateur', 'avocat', 'assistant') if standalone_auth.allowed(r, 'GET', chemin)), membres5650.category('GET', chemin)


def conservation(fonctions=None):
    """Comparaison avec le registre figé. ``ok`` faux dès qu'une fonction, une route, un alias, un droit ou un prérequis a disparu ou changé ; une fonction ajoutée est listée sans faute."""
    actuel = {f[0]: f for f in (registre.FUNCTIONS if fonctions is None else fonctions)}
    retraits, ecarts = [], []
    for fid, groupe, route, alias, requis, roles, droit in reference.REGISTRE:
        ligne = actuel.get(fid)
        if ligne is None:
            retraits.append(fid)
            continue

        def ecart(nature, attendu, trouve):
            ecarts.append({'fonction': fid, 'nature': nature, 'attendu': attendu, 'trouve': trouve})

        if ligne[1] != groupe:
            ecart('groupe', groupe, ligne[1])
        if ligne[2] != route:
            ecart('route', route, ligne[2])
        perdus = [a for a in alias if a not in ligne[3]]
        if perdus:
            ecart('alias', perdus, '')
        if tuple(ligne[4]) != tuple(requis):
            ecart('prerequis', list(requis), list(ligne[4]))
        roles_actuels, droit_actuel = droits_actuels(ligne[2])
        if tuple(roles_actuels) != tuple(roles) or droit_actuel != droit:
            ecart('droits', [list(roles), droit], [list(roles_actuels), droit_actuel])
    connus = {f[0] for f in reference.REGISTRE}
    return {'ok': not (retraits or ecarts), 'reference_version': reference.VERSION, 'reference': len(reference.REGISTRE), 'actuel': len(actuel), 'conservees': len(reference.REGISTRE) - len(retraits),
            'ajouts': sorted(set(actuel) - connus), 'retraits': retraits, 'ecarts': ecarts}
