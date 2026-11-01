"""5.6.88 : relevé automatique de la facturation électronique par la boucle des automatismes.

Jusqu'ici, factures reçues, suivi des factures transmises et comptes rendus (e-reporting) n'étaient relus qu'au bouton « Actualiser depuis les plateformes ».
- Lecture seule : ``efacture5670.sync`` n'appelle que ``einvoice.receive_detail``, ``ereporting.list`` et ``einvoice.status``. Rien n'est accepté, refusé,
  déposé ni transmis ; une transmission reste une opération à valider sur sa fiche (``facturation5653.submit`` puis ``connexions5652.validate``), que ce module
  n'appelle jamais.
- Désactivé tant qu'aucune plateforme vérifiée n'est configurée au niveau du cabinet (une connexion personnelle n'est pas utilisée en arrière-plan).
- Intervalle : 6 heures par défaut, de 1 à 24 heures, compté depuis la fin du relevé précédent (``cadence5662``), et différé si la file est chargée.
"""
from datetime import datetime, timezone
from html import escape as e

from .common import Stop

SETTINGS = 'efacture5688:reglages'
LAST = 'efacture5688:dernier'
DEFAULTS = {'enabled': True, 'hours': 6}
KIND = 'efacture5688'
OWNER = 'cabinet'                     # connexions du cabinet seulement


def settings(desk):
    raw = desk.settings(SETTINGS, {}) or {}
    out = dict(DEFAULTS)
    out.update({k: raw[k] for k in DEFAULTS if k in raw})
    return out


def save_settings(desk, owner, role, data):
    from .facturation5653 import _require
    _require(desk, owner, role, 'facturation')
    cur = settings(desk)
    cur['enabled'] = str(data.get('enabled') or '') in ('1', 'true', 'on', 'oui') or data.get('enabled') is True
    if str(data.get('hours', '')).strip():
        try:
            hours = int(data['hours'])
        except (TypeError, ValueError):
            raise Stop('intervalle_releve_invalide') from None
        if not 1 <= hours <= 24:
            raise Stop('intervalle_releve_invalide')
        cur['hours'] = hours
    desk.setting(SETTINGS, cur)
    desk.audit('efacture5688_reglages', {**cur, 'par': str(owner)[:80]})
    return {'ok': True, 'message': 'Relevé automatique %s.' % (('toutes les %d h' % cur['hours']) if cur['enabled'] else 'désactivé')}


def schedule(desk, now_ts):
    """Boucle des automatismes : met le relevé en file si une plateforme est configurée et que l'intervalle est écoulé."""
    s = settings(desk)
    if not s['enabled']:
        return None
    from . import cadence5662, efacture5670 as ef
    if not ef.connections(desk, OWNER):
        return None
    minutes = int(s['hours']) * 60
    if now_ts - float(desk.settings('auto:efacture5688', 0) or 0) < minutes * 60 or not cadence5662.allow(desk, KIND, minutes, now_ts):
        return None
    job = desk.enqueue(KIND, {}, priority=70)
    desk.setting('auto:efacture5688', now_ts)
    return job


def job(desk, args=None):
    """Relevé des plateformes (lecture), projection des statuts sur les factures, alertes sobres. N'écrit rien chez la plateforme."""
    from . import efacture5670 as ef, facturation5653 as fact
    from .live430 import emit
    ef.ensure_schema(desk)
    fact.ensure_schema(desk)
    before = desk.db.execute('SELECT COUNT(*) FROM ef5670_recues').fetchone()[0]
    seen = {tuple(r) for r in desk.db.execute('SELECT provider,remote_ref,code,at FROM ef5670_suivi')}
    try:
        report = ef.sync(desk, OWNER)
    except Stop as ex:
        if str(ex).split(':')[0] != 'aucune_plateforme_de_facturation_verifiee':
            raise
        return {'ignored': True, 'message': 'Aucune plateforme vérifiée : relevé non effectué.'}
    try:
        fact.sync_deliveries(desk)
    except Exception as ex:   # noqa: BLE001 — table des opérations absente : aucune transmission à projeter
        report['erreurs'].append('suivi des transmissions : %s' % str(ex)[:80])
    new = desk.db.execute('SELECT COUNT(*) FROM ef5670_recues').fetchone()[0] - before
    day = datetime.now(timezone.utc).date().isoformat()
    if new > 0:
        emit(desk, 'efacture', '%d nouvelle(s) facture(s) reçue(s) sur la plateforme de facturation électronique.' % new, dedupe='efacture5688-recues-%s-%d' % (day, before + new))
    for row in desk.db.execute('SELECT s.provider,s.remote_ref,s.document,s.code,s.libelle,s.at FROM ef5670_suivi s').fetchall():
        if (row['provider'], row['remote_ref'], row['code'], row['at']) not in seen and row['code'] in ef.BAD:
            emit(desk, 'efacture', 'Facture transmise : %s. À examiner dans Facturation électronique.' % row['libelle'], dedupe='efacture5688-%s-%s-%s' % (row['remote_ref'], row['code'], row['at']))
    if report['erreurs']:
        emit(desk, 'blocked', 'Relevé des plateformes de facturation incomplet : ' + ' ; '.join(report['erreurs'][:3]), dedupe='efacture5688-erreur-%s' % day)
    desk.setting(LAST, {'at': datetime.now(timezone.utc).isoformat(timespec='seconds'), 'recues': new, 'erreurs': report['erreurs'][:4]})
    return {'message': 'Relevé : %d nouvelle(s) facture(s), %d événement(s) de suivi, %d compte(s) rendu(s).' % (new, report['suivis'], report['rapports']), **report}


def status_html(desk, role):
    """Encart de la page Facturation électronique : état du relevé automatique et réglage."""
    s = settings(desk)
    last = desk.settings(LAST, {}) or {}
    line = ('Dernier relevé automatique : %s, %d nouvelle(s) facture(s).' % (e(str(last.get('at', ''))[:16].replace('T', ' ')), int(last.get('recues') or 0))) if last else 'Aucun relevé automatique pour l’instant.'
    if last.get('erreurs'):
        line += ' <span class="ef70-ko">Incidents : %s.</span>' % e(' ; '.join(last['erreurs']))
    form = ''
    if role in ('administrateur', 'avocat'):
        form = ('<form class="m5-form m5-inline" data-api="m5670/efacture/releve" data-reload><label class="m5-check"><input type="checkbox" name="enabled" value="1"%s> Relever '
                'automatiquement</label><label>Toutes les (heures)<input type="number" name="hours" min="1" max="24" value="%d"></label><button class="ax-btn ghost" type="submit">Enregistrer le relevé</button>'
                '<p role="status" class="m5-status"></p></form>') % (' checked' if s['enabled'] else '', int(s['hours']))
    return ('<p class="ax-muted">Relevé automatique des factures reçues, des statuts et des comptes rendus : lecture seule, rien n’est transmis, accepté ni refusé. %s</p>%s') % (line, form)
