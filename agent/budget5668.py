"""5.6.68 : plafond mensuel des fournisseurs d'IA externes — compté juste, visible, et jamais « sans IA » quand un modèle local existe.

Constat du 21 octobre 2026 : plafond mensuel du fournisseur externe atteint depuis la veille ; routines « rédigées sans IA »,
réponses de l'assistant en erreur, aucun brouillon, alors que le modèle local (Ollama) fonctionnait. Désormais :
- plafond atteint : la demande passe au modèle local (Ollama) de la même fonction au lieu d'une production « sans IA ».
  Rien n'est transmis au fournisseur externe : le refus du plafond a lieu avant tout envoi ;
- en outre, un appel en échec gardait sa réservation « incertaine » jusqu'à la fin du mois (budget567, depuis 5.6.7), même quand
  le fournisseur n'avait rien facturé : un échec certain non facturé (refus 4xx, panne 5xx, connexion impossible) la libère
  (``not_billed``) ; un délai dépassé ou une coupure reste « incertain » ;
- Paramètres › IA affiche, par fournisseur, le dépensé confirmé, le réservé incertain, le plafond et le reste, et l'administrateur peut
  libérer les réservations incertaines après contrôle de la facture du fournisseur (journalisé) ;
- Aujourd'hui affiche un bandeau quand un plafond est atteint (ou à 90 %).
"""
from datetime import datetime, timedelta, timezone
from html import escape as e
import json
import re
import sqlite3
from pathlib import Path

from .common import Stop

BUDGET_CODES = frozenset(('budget_mensuel_fournisseur_depasse', 'budget_requete_fournisseur_depasse', 'quota_fournisseur_depasse'))
FALLBACK_KEY = 'budget5668:dernier_repli'
WARN_RATIO = 0.9
STALE_SECONDS = 3600   # une réservation « reserved » plus ancienne qu'une heure appartient à un appel interrompu sans retour


def not_billed(code):
    """Échec certain sans génération facturée : refus HTTP (sauf délai 408), panne du fournisseur (5xx), connexion impossible."""
    code = str(code or '').split(':')[0]
    m = re.fullmatch(r'http_(\d{3})', code)
    if m:
        status = int(m.group(1))
        return status != 408 and 400 <= status <= 599
    return code in ('connexion_http_indisponible',)


def _month():
    return datetime.now(timezone.utc).strftime('%Y-%m-') + '%'


def _external(config):
    from .ai_gateway import provider_registry
    try:
        registry = provider_registry(config)
    except Exception:   # noqa: BLE001
        return {}
    return {pid: p for pid, p in registry.items() if str(p.get('type') or '') in ('openai', 'mistral', 'openrouter', 'anthropic')}


def status(desk):
    """Par fournisseur externe actif : plafond, dépensé confirmé, réservé incertain, reste, épuisé."""
    from .ai_gateway import _usage_db
    from .budget567 import SCHEMA
    providers = _external(desk.c)
    if not providers:
        return []
    db = _usage_db(desk.c['state_dir'])
    try:
        db.executescript(SCHEMA)
        stale = (datetime.now(timezone.utc) - timedelta(seconds=STALE_SECONDS)).isoformat()
        out = []
        for pid, p in sorted(providers.items()):
            monthly = float(p.get('monthly_budget_usd') or 0)
            spent = float(db.execute("SELECT COALESCE(SUM(estimated_cost_usd),0) FROM ai_usage_v391 WHERE provider=? AND at LIKE ? AND status='done'",
                                     (pid, _month())).fetchone()[0] or 0)
            unc = db.execute("SELECT COUNT(*),COALESCE(SUM(amount),0) FROM ai_reservations_v567 WHERE provider=? AND at LIKE ? AND "
                             "(state='uncertain' OR (state='reserved' AND at<?))", (pid, _month(), stale)).fetchone()
            live = float(db.execute("SELECT COALESCE(SUM(amount),0) FROM ai_reservations_v567 WHERE provider=? AND at LIKE ? AND state='reserved' AND at>=?",
                                    (pid, _month(), stale)).fetchone()[0] or 0)
            used = spent + float(unc[1] or 0) + live
            out.append({'provider': pid, 'type': p.get('type', ''), 'enabled': bool(p.get('enabled')), 'monthly': monthly, 'spent': round(spent, 4),
                        'uncertain': round(float(unc[1] or 0), 4), 'uncertain_count': int(unc[0] or 0), 'in_flight': round(live, 4),
                        'remaining': round(max(0.0, monthly - used), 4) if monthly else None,
                        'ratio': (used / monthly) if monthly else 0.0, 'exhausted': bool(monthly) and used >= monthly * 0.999})
        return out
    finally:
        db.close()


def release(desk, provider_id, actor='cabinet'):
    """Libère les réservations incertaines du mois (après contrôle de la facture du fournisseur) ; journalisé, réversible par l'historique."""
    from .ai_gateway import _usage_db
    from .budget567 import SCHEMA
    provider_id = str(provider_id or '').strip()
    if provider_id not in _external(desk.c):
        raise Stop('fournisseur_ia_inconnu')
    stale = (datetime.now(timezone.utc) - timedelta(seconds=STALE_SECONDS)).isoformat()
    db = _usage_db(desk.c['state_dir'])
    try:
        db.executescript(SCHEMA)
        db.execute('BEGIN IMMEDIATE')
        n, amount = db.execute("SELECT COUNT(*),COALESCE(SUM(amount),0) FROM ai_reservations_v567 WHERE provider=? AND at LIKE ? AND "
                               "(state='uncertain' OR (state='reserved' AND at<?))", (provider_id, _month(), stale)).fetchone()
        db.execute("UPDATE ai_reservations_v567 SET state='closed' WHERE provider=? AND at LIKE ? AND (state='uncertain' OR (state='reserved' AND at<?))",
                   (provider_id, _month(), stale))
        db.commit()
    except BaseException:
        db.rollback()
        raise
    finally:
        db.close()
    desk.audit('budget5668_reservations_liberees', {'provider': provider_id, 'nombre': int(n or 0), 'montant_usd': round(float(amount or 0), 4),
                                                    'par': str(actor)[:80]})
    return {'ok': True, 'liberees': int(n or 0), 'montant': round(float(amount or 0), 4),
            'message': '%d réservation(s) incertaine(s) libérée(s) (%.2f $) pour %s.' % (int(n or 0), float(amount or 0), provider_id)}


# ------------------------------------------------------------------------------------------------ repli local
def local_fallback(base, config, providers, purpose, role):
    """Configuration du modèle local de la même fonction, utilisée seulement si le plafond du fournisseur externe est atteint."""
    local = providers.get('ollama')
    if not isinstance(local, dict) or not local.get('enabled', True) or str(local.get('type') or 'ollama') != 'ollama' or not local.get('url'):
        return base
    routes = config.get('model_routing') or {}
    model = ''
    if str(routes.get(role + '_provider') or 'ollama') == 'ollama':
        model = str(routes.get(role + '_model') or '')
    model = model or str((config.get('ollama') or {}).get('model') or local.get('model') or '')
    if not model:
        return base
    fb = {k: base[k] for k in ('purpose', 'temperature', 'sampling', 'profile', 'profile_version', 'subprofile', 'state_dir', 'pseudo',
                               'skill_instructions') if k in base}
    fb.update(dict(local))
    fb.update({'model': model, 'provider_id': 'ollama', 'provider_type': 'ollama'})
    try:
        from .profil5634 import resolve
        params = resolve(config, purpose, role)
        fb['context_mode'] = params['context_mode']
        if params['context_mode'] == 'fixe':
            fb['num_ctx'] = params['num_ctx']
    except Exception:   # noqa: BLE001 — réglages par défaut du modèle local
        pass
    out = dict(base)
    out['budget_fallback'] = fb
    return out


def note_fallback(cfg, code):
    """Trace du dernier repli (fournisseur, fonction, motif) pour le bandeau d'Aujourd'hui ; ne lève jamais d'exception."""
    try:
        state = str(cfg.get('state_dir') or '')
        if not state:
            return
        value = json.dumps({'at': datetime.now(timezone.utc).isoformat(), 'provider': str(cfg.get('provider_id') or ''),
                            'purpose': str(cfg.get('purpose') or ''), 'code': str(code)}, ensure_ascii=False)
        db = sqlite3.connect(Path(state) / 'desk.sqlite3', timeout=5)
        try:
            db.execute('INSERT OR REPLACE INTO settings VALUES(?,?)', (FALLBACK_KEY, value))
            db.commit()
        finally:
            db.close()
    except Exception:   # noqa: BLE001
        pass


# ------------------------------------------------------------------------------------------------ affichage
def _money(x):
    return ('%.2f $' % float(x)).replace('.', ',')


def banner_html(desk, prefix, role=''):
    """Bandeau d'Aujourd'hui : plafond atteint (ou presque), avec ce que fait AxiorHub et où régler."""
    try:
        rows = [r for r in status(desk) if r['enabled'] and r['monthly'] and (r['exhausted'] or r['ratio'] >= WARN_RATIO)]
    except Exception:   # noqa: BLE001 — la page s'affiche toujours
        return ''
    if not rows:
        return ''
    last = desk.settings(FALLBACK_KEY, {}) or {}
    recent = bool(last.get('at')) and str(last['at']) >= (datetime.now(timezone.utc) - timedelta(days=2)).isoformat()
    parts = []
    for r in rows:
        unc = (' dont %s réservés pour %d appel(s) en échec' % (_money(r['uncertain']), r['uncertain_count'])) if r['uncertain_count'] else ''
        parts.append('<li><strong>%s</strong> : %s utilisés sur %s%s.</li>' % (
            e(r['provider']), _money(r['spent'] + r['uncertain'] + r['in_flight']), _money(r['monthly']), e(unc)))
    exhausted = any(r['exhausted'] for r in rows)
    if exhausted and recent:
        what = 'Le modèle local (Ollama) prend le relais (dernier relais : %s UTC).' % e(str(last['at'])[:16].replace('T', ' '))
    elif exhausted:
        what = ('Le modèle local (Ollama) prend le relais s’il est installé sur le serveur ; sinon, plus rien n’est rédigé par l’IA externe '
                'avant le 1er du mois ou le relèvement du plafond.')
    else:
        what = 'Au-delà du plafond, le modèle local (Ollama) prendra le relais s’il est installé.'
    return ('<div class="b5668-banner%s" role="status"><p><strong>%s</strong> %s</p><ul>%s</ul><p><a class="ax-btn ghost" href="%s">Voir et régler le plafond</a></p></div>') % (
        ' b5668-full' if exhausted else '', 'Plafond mensuel de l’IA externe atteint.' if exhausted else 'Plafond mensuel de l’IA externe bientôt atteint.',
        what, ''.join(parts), e(prefix + '/parametres?tab=ia#b5668-budget', quote=True))


def settings_html(desk, prefix, role):
    """Paramètres › IA : tableau du mois par fournisseur, libération des réservations incertaines (administrateur)."""
    try:
        rows = status(desk)
    except Exception:   # noqa: BLE001
        return ''
    if not rows:
        return ''
    body = []
    for r in rows:
        act = ''
        if role == 'administrateur' and r['uncertain_count']:
            act = ('<form class="m5-form m5-inline m5-mini" data-api="m5668/budget/liberer" data-reload="1" data-confirm="Libérer %d réservation(s) '
                   'incertaine(s) (%s) ? Faites-le seulement si la facture du fournisseur ne montre pas ces appels."><input type="hidden" name="provider" '
                   'value="%s"><button class="ax-btn ghost" type="submit">Libérer</button></form>') % (
                r['uncertain_count'], e(_money(r['uncertain'])), e(r['provider'], quote=True))
        body.append('<tr><td>%s</td><td>%s</td><td>%s</td><td>%s%s</td><td>%s</td><td>%s</td></tr>' % (
            e(r['provider']), _money(r['monthly']) if r['monthly'] else 'non fixé', _money(r['spent']),
            _money(r['uncertain']), (' (%d appel(s)) %s' % (r['uncertain_count'], act)) if r['uncertain_count'] else '',
            _money(r['remaining']) if r['remaining'] is not None else '—',
            '<strong class="b5668-ko">atteint</strong>' if r['exhausted'] else ('%d %%' % round(r['ratio'] * 100))))
    return ('<section id="b5668-budget"><h3>Plafond du mois</h3><p class="hint">« Dépensé » : appels terminés. « Réservé incertain » : appels coupés '
            '(délai dépassé, interruption) dont le fournisseur a pu facturer une partie ; depuis 5.6.68, un refus ou une panne du fournisseur ne '
            'réserve plus rien. Plafond atteint : AxiorHub utilise le modèle local de la même fonction.</p>'
            '<div class="table"><table><thead><tr><th>Fournisseur</th><th>Plafond</th><th>Dépensé</th><th>Réservé incertain</th><th>Reste</th>'
            '<th>Utilisation</th></tr></thead><tbody>%s</tbody></table></div></section>') % ''.join(body)
