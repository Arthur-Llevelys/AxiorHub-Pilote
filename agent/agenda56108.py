"""5.6.108 (lot G, cahier « Dossiers documents agenda et démarrage » · Agenda local) : éditeur prérempli lisible et dates à vérifier.

- Éditeur compact de la grille (``grille5697.editeur_html``) : la fenêtre ouverte par un clic, une plage glissée ou le clavier montre,
  préremplis et lisibles, la date, l'heure et la DURÉE (liste synchronisée avec la fin), le RAPPEL (choix explicite, défaut : la veille
  et une heure avant, comme l'Agenda personnel), le FUSEAU de saisie, le RESPONSABLE (le compte qui inscrit l'événement ; une tâche a
  son propre responsable) et la RÉCURRENCE (un événement créé ici est unique ; une série se gère dans son calendrier d'origine).
  Rien n'est écrit ici : l'enregistrement passe par les routes existantes (``agenda5697.creer`` / ``modifier`` : CSRF, clé
  d'idempotence, version).
- Visio et Speakr : proposés seulement s'ils sont configurés (``rendezvous5697``) ; leur absence ne bloque jamais le rendez-vous
  (« Sans visio » et « Coller un lien » restent possibles).
- Dates extraites à vérifier : une échéance proposée (« À confirmer », « À compléter ») n'est jamais dans la grille confirmée
  (``echeances450.INSCRITES``) ; elle est montrée À PART, avec son dossier et un lien vers Échéances. Droits du dossier appliqués
  AVANT le total et la limite ; registre illisible : l'état est dit (jamais un faux zéro). Aucune écriture, aucune table créée.
- Derrière ``interface56100.active`` : la présentation précédente reste entière quand l'interrupteur est éteint.
"""
from datetime import datetime
from html import escape
import sqlite3
from urllib.parse import urlencode

from .common import Stop

# (valeur du formulaire, libellé) — minutes avant le début, parmi ``agenda5634.REMINDERS`` ; la valeur vide n'existe pas : le choix est explicite
RAPPELS = (('1440,60', 'La veille et 1 h avant'), ('60', '1 h avant'), ('30', '30 min avant'), ('10', '10 min avant'), ('1440', 'La veille'),
           ('2880', 'Deux jours avant'), ('10080', 'Une semaine avant'), ('aucun', 'Aucun rappel'))
RAPPEL_DEFAUT = '1440,60'
# durées proposées (minutes) ; « Autre durée » quand la fin saisie ne correspond à aucune
DUREES = ((15, '15 min'), (30, '30 min'), (45, '45 min'), (60, '1 h'), (90, '1 h 30'), (120, '2 h'), (180, '3 h'), (240, '4 h'))
ETATS_A_VERIFIER = ('a_confirmer', 'a_completer')
LIMITE_LISTE = 8


def e(valeur):
    return escape(str(valeur if valeur is not None else ''), quote=True)


def nouvelle(desk, owner):
    """Interrupteur de la nouvelle interface (5.6.100) ; réglage illisible : la présentation par défaut (active)."""
    try:
        from .interface56100 import active
        return active(desk, owner)
    except Exception:   # noqa: BLE001
        return True


def rappels(valeur):
    """Valeur du formulaire → liste de minutes ; ``Stop('rappel_invalide')`` hors des valeurs proposées (contrôle serveur, pas seulement la liste)."""
    from .agenda5634 import REMINDERS
    if isinstance(valeur, list):
        try:
            out = sorted({int(x) for x in valeur})
        except (TypeError, ValueError):
            raise Stop('rappel_invalide') from None
    else:
        texte = str(valeur or '').strip()
        if texte == 'aucun':
            return []
        try:
            out = sorted({int(x) for x in texte.split(',') if x.strip()})
        except ValueError:
            raise Stop('rappel_invalide') from None
    if any(x not in REMINDERS for x in out) or len(out) > 4:
        raise Stop('rappel_invalide')
    return out


def _nom(desk, compte):
    try:
        from .membres5650 import directory
        fiche = directory(desk).get(compte) or {}
        return fiche.get('name') or compte
    except Exception:   # noqa: BLE001 — annuaire illisible : le compte reste affiché tel quel
        return compte


def editeur_complements_html(desk, acteur, zone):
    """Lignes ajoutées à l'éditeur compact : durée, rappel, fuseau, responsable et récurrence. Libellés et données séparés (traduction
    des libellés à la sortie, données jamais traduites)."""
    durees = ''.join('<option value="%d">%s</option>' % (m, e(lib)) for m, lib in DUREES) + '<option value="">Autre durée</option>'
    choix = ''.join('<option value="%s"%s>%s</option>' % (e(v), ' selected' if v == RAPPEL_DEFAUT else '', e(lib)) for v, lib in RAPPELS)
    return ('<div class="ag97-ligne ag108-ligne" data-ag97-pour="evenement" data-ag108-horaire>'
            '<label>Durée<select name="duree" data-ag108-duree>%s</select></label>'
            '<label>Rappel<select name="rappel" data-ag108-rappel>%s</select></label></div>'
            '<dl class="ag108-lisible" data-ag108-lisible>'
            '<div data-ag97-pour="evenement"><dt>Fuseau horaire</dt><dd data-ag108-zone>%s</dd></div>'
            '<div data-ag97-pour="evenement" data-ag108-resp-ev><dt>Responsable</dt><dd>%s</dd></div>'
            '<div data-ag97-pour="evenement"><dt>Récurrence</dt><dd data-ag108-recurrence>Aucune (événement unique)</dd></div></dl>') % (
        durees, choix, e(zone), e(_nom(desk, acteur.compte)))


# ------------------------------------------------------------------------------------------------ dates extraites à vérifier
def _libelle_regle(rule_id, note):
    if note:
        return str(note)[:120]
    try:
        from . import deadlines450
        return str(deadlines450.rule(rule_id)['label'])[:120]
    except Exception:   # noqa: BLE001 — règle inconnue : l'identifiant lisible
        return str(rule_id or 'Échéance').replace('_', ' ')[:120]


def dates_a_verifier(desk, acteur, jour, limite=LIMITE_LISTE):
    """Échéances proposées (non confirmées) à venir ou sans date, des dossiers visibles de l'acteur.
    Rend ``{'etat': 'ok'|'indisponible', 'total': n, 'items': [...]}`` ; lecture seule (aucune table créée pendant l'affichage)."""
    from .acces5697 import dossiers_autorises
    try:
        clause, params = dossiers_autorises(desk, acteur).sql('matter')
    except Stop:
        return {'etat': 'indisponible', 'total': 0, 'items': []}       # registre des droits illisible : jamais une liste élargie
    etats = ','.join('?' * len(ETATS_A_VERIFIER))
    where = " WHERE status IN (%s) AND (due='' OR due>=?) AND %s" % (etats, clause)
    args = ETATS_A_VERIFIER + (jour.isoformat(),) + tuple(params)
    try:
        total = desk.db.execute('SELECT COUNT(*) FROM deadlines450' + where, args).fetchone()[0]
        rows = desk.db.execute('SELECT id,matter,rule_id,note,due,status FROM deadlines450' + where +
                               " ORDER BY CASE WHEN due='' THEN 1 ELSE 0 END, due, id LIMIT ?", args + (int(limite),)).fetchall()
    except sqlite3.OperationalError as ex:
        if 'no such table' in str(ex):
            return {'etat': 'ok', 'total': 0, 'items': []}             # aucune échéance jamais extraite sur ce poste
        return {'etat': 'indisponible', 'total': 0, 'items': []}
    except sqlite3.Error:
        return {'etat': 'indisponible', 'total': 0, 'items': []}
    return {'etat': 'ok', 'total': int(total), 'items': [
        {'id': r['id'], 'dossier': r['matter'], 'libelle': _libelle_regle(r['rule_id'], r['note']), 'date': str(r['due'] or '')[:10],
         'etat': r['status']} for r in rows]}


def _date_fr(valeur):
    try:
        return datetime.strptime(str(valeur)[:10], '%Y-%m-%d').strftime('%d/%m/%Y')
    except ValueError:
        return ''


def a_verifier_html(desk, acteur, prefix, jour):
    """Bandeau d'une ligne au-dessus de la grille (rien quand il n'y a rien à vérifier) et liste du volet. Les dates proposées ne sont
    jamais placées dans la grille : la grille montre l'agenda confirmé."""
    data = dates_a_verifier(desk, acteur, jour)
    lien = prefix + '/echeances'
    if data['etat'] != 'ok':
        bandeau = ('<p class="ag108-verifier" role="status" data-ag108-verifier="indisponible"><span>Dates extraites à vérifier</span> '
                   '<strong>indisponible pour l’instant</strong></p>')
        return bandeau, ''
    if not data['total']:
        return '', ''
    noms = {}
    try:
        from .common import load_matters, matter_display
        noms = {m['id']: matter_display(m) for m in load_matters(desk.c)}
    except Exception:   # noqa: BLE001 — registre illisible : l'identifiant du dossier reste affiché
        noms = {}
    bandeau = ('<p class="ag108-verifier" role="status" data-ag108-verifier="%d"><span class="ag108-marque">À vérifier</span> '
               '<strong>%d</strong> <span>date(s) extraite(s), hors de l’agenda confirmé</span> <a href="%s">Vérifier les dates</a></p>') % (
        data['total'], data['total'], e(lien))
    items = ''.join('<li><span class="ag108-marque">À vérifier</span> <strong>%s</strong> <span>%s</span> <a href="%s">%s</a></li>' % (
        e(_date_fr(x['date']) or 'Date à compléter'), e(x['libelle']), e(prefix + '/echeances?' + urlencode({'matter': x['dossier']})),
        e(noms.get(x['dossier'], x['dossier']))) for x in data['items'])
    volet = ('<details open><summary>Dates extraites à vérifier (%d)</summary><ul class="ag108-liste-verifier">%s</ul>'
             '<p class="ag97-aide">Une date proposée n’entre dans l’agenda qu’après confirmation.</p></details>') % (data['total'], items)
    return bandeau, volet
