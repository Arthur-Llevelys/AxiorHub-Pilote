"""Préférences explicites et briefing calculé, sans inférence supplémentaire."""
from datetime import datetime, timedelta, timezone
import hashlib
import json
import math
import re
import shutil
import subprocess

from .common import Stop, load_matters, matter_display

DEFAULTS = {'name': 'Pilote', 'tone': 'sobre', 'length': 'courte', 'initiative': 'preparer',
            'lexicon': [], 'speech_enabled': True, 'speech_rate': 1.0, 'discreet': True,
            'avatar': 'icone', 'numeric_success_rates': False}


def profile(desk, owner='cabinet'):
    base = {**DEFAULTS, **(desk.settings('assistant567:profile:cabinet', {}) or {})}
    if owner != 'cabinet':
        base.update(desk.settings('assistant567:profile:' + owner, {}) or {})
    return base


def save_profile(desk, data, owner='cabinet'):
    old = profile(desk, owner)
    value = dict(old)
    for name, choices in {'tone': ('sobre', 'direct', 'pedagogique'), 'length': ('courte', 'developpee'),
                          'initiative': ('preparer', 'proposer'), 'avatar': ('icone', 'aucun')}.items():
        if name in data:
            if data[name] not in choices:
                raise Stop('preference_assistant_invalide')
            value[name] = data[name]
    if 'name' in data:
        name = str(data['name']).strip()
        if not 1 <= len(name) <= 40 or re.search(r'[<>\x00-\x1f]', name):
            raise Stop('nom_assistant_invalide')
        value['name'] = name
    for name in ('speech_enabled', 'discreet'):
        if name in data:
            if not isinstance(data[name], bool):
                raise Stop('preference_assistant_invalide')
            value[name] = data[name]
    if 'speech_rate' in data:
        try:
            rate = float(data['speech_rate'])
            if not math.isfinite(rate):
                raise ValueError('non finite')
            value['speech_rate'] = max(0.7, min(rate, 1.5))
        except (TypeError, ValueError):
            raise Stop('vitesse_voix_invalide') from None
    if 'lexicon' in data:
        if not isinstance(data['lexicon'], list) or len(data['lexicon']) > 100:
            raise Stop('lexique_invalide')
        terms = []
        for term in data['lexicon']:
            if not isinstance(term, dict):
                raise Stop('lexique_invalide')
            heard, written = str(term.get('heard') or '').strip(), str(term.get('written') or '').strip()
            if not heard or not written or max(len(heard), len(written)) > 100 or any(c.isdigit() for c in heard + written):
                raise Stop('lexique_numerique_ou_invalide')
            terms.append({'heard': heard, 'written': written})
        value['lexicon'] = terms
    # Une préférence ne peut modifier ni la confidentialité ni les permissions.
    value['numeric_success_rates'] = False
    desk.setting('assistant567:profile:' + owner, value)
    desk.audit('preferences_assistant567', {'owner_hash': hashlib.sha256(owner.encode()).hexdigest(), 'fields': sorted(data)})
    return value


def drafting_preferences(desk, owner='cabinet'):
    p = profile(desk, owner)
    return {'ton': p['tone'], 'longueur': p['length'], 'ne_pas_chiffrer_les_chances_de_succes': True,
            'formulations': 'Respecter les règles et modèles validés du cabinet. Ces préférences ne sont pas des faits.'}


def briefing(desk, owner='cabinet'):
    """Instantané des données du cabinet : ne prétend pas avoir interrogé les services."""
    p = profile(desk, owner)
    stamp = datetime.now(timezone.utc)
    from zoneinfo import ZoneInfo
    from .settings568 import profile as proactive_profile
    tz=proactive_profile(desk,owner)['timezone'];local=stamp.astimezone(ZoneInfo(tz))
    labels = {m['id']: matter_display(m) for m in load_matters(desk.c)}
    end = (stamp + timedelta(days=1)).isoformat()
    def rows(sql, args=()):
        return [dict(r) for r in desk.db.execute(sql, args)]
    events = rows('SELECT title,starts,matter,fetched FROM calendar_cache WHERE starts>=? AND starts<? ORDER BY starts LIMIT 8', (stamp.isoformat(), end))
    tasks = rows("SELECT title,due,matter FROM tasks WHERE status IN ('open','todo') AND due IS NOT NULL AND due<=? ORDER BY due LIMIT 8", (local.date().isoformat(),))
    ready = rows("SELECT label,matter,target,updated FROM production_deliverables_v420 WHERE status='verified' ORDER BY updated DESC LIMIT 6")
    incidents = rows("SELECT id,kind,finished FROM jobs WHERE status='error' ORDER BY id DESC LIMIT 6")
    key = 'assistant567:brief:' + owner
    signature = hashlib.sha256(json.dumps([local.date().isoformat(),tz, events, tasks, ready, incidents, p['discreet']], sort_keys=True).encode()).hexdigest()
    cached = desk.settings(key, {}) or {}
    if cached.get('signature') == signature and cached.get('generated_at', '') > (stamp - timedelta(minutes=5)).isoformat():
        return {**cached, 'cached': True}
    chapters = []
    def local_hour(value):
        try:return datetime.fromisoformat(value.replace('Z','+00:00')).astimezone(ZoneInfo(tz)).strftime('%H:%M')
        except (ValueError,TypeError):return 'heure à vérifier'
    names = lambda mid: 'un dossier' if p['discreet'] else labels.get(mid, 'le cabinet')
    chapters.append({'title': 'Agenda', 'text': ('. '.join(('Un événement' if p['discreet'] else r['title']) + ' à ' + local_hour(r['starts']) + ', pour ' + names(r['matter']) for r in events)
                                               if events else 'Aucun événement pour les prochaines vingt-quatre heures dans le dernier instantané de l’agenda.')})
    chapters.append({'title': 'Échéances et tâches', 'text': '. '.join(('Une tâche' if p['discreet'] else r['title']) + ' arrive à échéance dans ' + names(r['matter']) for r in tasks)
                                                    if tasks else 'Aucune tâche locale échue ou à échéance aujourd’hui dans le registre.'})
    chapters.append({'title': 'Préparé pour vous', 'text': '. '.join(('Un livrable vérifié' if p['discreet'] else r['label']) + ', pour ' + names(r['matter']) for r in ready)
                                                      if ready else 'Aucun nouveau livrable avec une preuve de dépôt vérifiée dans le registre.'})
    chapters.append({'title': 'Incidents', 'text': str(len(incidents)) + ' incident(s) récent(s) à examiner.' if incidents else 'Aucun incident dans la dernière consultation du registre.'})
    last = desk.db.execute('SELECT MAX(fetched) FROM calendar_cache').fetchone()[0]
    stale = not last or last < (stamp - timedelta(minutes=10)).isoformat()
    intro = 'Briefing du ' + local.strftime('%d/%m/%Y à %H:%M') + ', fuseau '+tz+'. '
    if stale:
        intro += 'Attention : le dernier instantané de l’agenda est ancien ou absent. '
    value = {'signature': signature, 'generated_at': stamp.isoformat(), 'calendar_fetched_at': last,
             'calendar_stale': stale, 'chapters': chapters, 'text': intro + ' '.join(c['title'] + '. ' + c['text'] for c in chapters),
             'cached': False, 'llm_calls': 0, 'discreet': p['discreet']}
    desk.setting(key, value)
    return value


def speech(desk, text, owner='cabinet'):
    """eSpeak NG local ; aucun fournisseur distant, commande ou voix fournis par l'utilisateur."""
    p = profile(desk, owner)
    if not p['speech_enabled']:
        raise Stop('lecture_vocale_desactivee')
    text = str(text or '').strip()
    if not 1 <= len(text) <= 7000:
        raise Stop('texte_lecture_trop_long_7000_maximum')
    binary = shutil.which('espeak-ng')
    if not binary:
        raise Stop('synthese_locale_indisponible_installer_espeak_ng')
    try:
        out = subprocess.run([binary, '--stdout', '-v', 'fr', '-s', str(round(175 * p['speech_rate'])), '--stdin'],
                             input=text.encode('utf-8'), stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=20, check=True)
    except (subprocess.TimeoutExpired, subprocess.CalledProcessError, OSError):
        raise Stop('synthese_locale_echouee') from None
    if not out.stdout.startswith(b'RIFF') or len(out.stdout) > 32_000_000:
        raise Stop('audio_synthese_invalide')
    return out.stdout
