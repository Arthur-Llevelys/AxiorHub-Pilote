"""5.6.71 : « Ce que fait l'agent » lisible (retour du cabinet du 22 octobre 2026 : « je ne comprends pas à quoi servent… »).

- chaque travail en file ou en cours dit en une ligne à quoi il sert (sauf message de progression plus précis) ;
- chaque travail terminé dit ce qu'il a produit (« 11 courriels analysés · 2 réponses mises en préparation · 6 dossier à confirmer »,
  « 8 pièces vérifiées · pièces nouvelles ou modifiées »…) ; un passage automatique qui n'a rien produit n'est pas listé ;
- les travaux purement techniques (contrôles d'intégrité, tableau de bord, index de recherche, nettoyage de la boîte, tour périodique
  des dossiers, actualisation interne des données) ne sont plus listés : ils restent visibles dans l'historique des travaux ;
- les contrôles d'intégrité (« Tests métier continus », sans IA) tournent une fois par nuit.
"""
import json
import time

# Travaux techniques : jamais listés dans « Ce que fait l'agent ».
TECHNICAL = frozenset(('run_continuous_business_tests', 'refresh_cabinet_pilotage', 'search_index490', 'reconcile_inbox', 'production_cycle391',
                       'classify_portfolio', 'apprentissage5667', 'actualiser5655', 'snapshot_metrics420'))

PURPOSES = {
    'live_mail430': 'Relève les nouveaux courriels et les trie (dossier, réponse attendue, publicité…)',
    'run': 'Relève les nouveaux courriels et les trie (dossier, réponse attendue, publicité…)',
    'orchestrator_mail_sweep': 'Examine les courriels récents d’un dossier et prépare les réponses utiles',
    'monitor_matter': 'Repère les pièces nouvelles ou modifiées du dossier, ses échéances et courriels sans réponse',
    'index': 'Lit les pièces du dossier pour que l’agent et la recherche puissent s’en servir',
    'prepare_reply': 'Rédige un projet de réponse, le fait contrôler, puis le dépose dans Brouillons (jamais envoyé)',
    'refresh_brief': 'Met à jour la synthèse du dossier',
    'extract_facts460': 'Relève les faits, dates et montants des pièces',
    'crm_collecte5657': 'Ajoute aux contacts les correspondants de la boîte mail',
}

ORCHESTRATOR_LABEL = 'Préparation des réponses aux courriels'

NIGHT_HOURS = range(1, 6)        # entre 1 h et 5 h 59 (heure du serveur)
MIN_GAP = 20 * 3600              # pas deux fois la même nuit
MAX_GAP = 36 * 3600              # serveur éteint la nuit : au plus tard 36 heures après le précédent


def integrity_due(now_ts, last_ts, hour=None):
    """Contrôles d'intégrité (sans IA) : une fois par nuit, ou après 36 heures sans contrôle."""
    gap = float(now_ts) - float(last_ts or 0)
    if gap >= MAX_GAP:
        return True
    hour = time.localtime(now_ts).tm_hour if hour is None else hour
    return gap >= MIN_GAP and hour in NIGHT_HOURS


def hidden(kind, args=None):
    """Travail technique, ou tour périodique des dossiers : non listé."""
    if kind in TECHNICAL:
        return True
    if kind == 'monitor_matter':
        try:
            data = json.loads(args or '{}') if isinstance(args, str) else (args or {})
        except ValueError:
            data = {}
        return bool(data.get('tour'))
    return False


def purpose(kind):
    return PURPOSES.get(kind, '')


def _plural(n, one, many):
    return '%d %s' % (n, one if n == 1 else many)


def done_detail(kind, raw):
    """(à afficher ?, détail) d'un travail terminé ; un passage automatique sans résultat n'est pas affiché."""
    try:
        res = json.loads(raw or '{}') if isinstance(raw, str) else (raw or {})
    except ValueError:
        res = {}
    if not isinstance(res, dict):
        return True, ''
    if kind in ('run', 'live_mail430'):
        n = int(res.get('examined') or 0)
        return (n > 0), (_plural(n, 'courriel examiné', 'courriels examinés') if n else '')
    if kind == 'orchestrator_mail_sweep':
        fresh = [x for x in (res.get('items') or []) if isinstance(x, dict) and not x.get('idempotent')]
        if not fresh:
            return False, ''
        replies = sum(1 for x in fresh if (x.get('reply_proposal') or {}).get('automatic_draft_job_id'))
        blocked = sum(1 for x in fresh if x.get('status') == 'blocked')
        parts = [_plural(len(fresh), 'courriel analysé', 'courriels analysés')]
        if replies:
            parts.append(_plural(replies, 'réponse mise en préparation', 'réponses mises en préparation'))
        if blocked:
            parts.append('%d en attente de votre décision (dossier à confirmer ou point à trancher)' % blocked)
        if not replies and not blocked:
            parts.append('aucune réponse attendue')
        return True, ' · '.join(parts)
    if kind == 'monitor_matter':
        if res.get('first_observation'):
            return True, _plural(int(res.get('documents') or 0), 'pièce recensée', 'pièces recensées')
        if res.get('changed'):
            return True, 'Pièces nouvelles ou modifiées repérées · ' + _plural(int(res.get('signals_refreshed') or 0), 'point à vérifier', 'points à vérifier')
        return False, ''
    if kind == 'index':
        if not res.get('contenu_modifie'):
            return False, ''
        return True, _plural(int(res.get('fichiers') or 0), 'pièce lue', 'pièces lues')
    if kind == 'prepare_reply':
        if res.get('brouillon_imap') == 'verifie':
            return True, 'Brouillon déposé et vérifié dans Brouillons' + (' (« à compléter »)' if res.get('a_completer') else '')
        if res.get('projet_prepare'):
            return True, 'Projet prêt, en attente de votre décision (À relire)'
        return True, str(res.get('message') or '')[:160]
    return True, ''
