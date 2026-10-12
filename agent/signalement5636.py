"""5.6.36 : signalement d'anomalie anonymisé — la publication du code reste séparée des données du cabinet.

« Préparer un signalement » (page « Pourquoi rien n'est produit ? ») produit un texte à coller dans un ticket GitHub :
version, mode d'installation, état des services, CODES d'erreur et leur nombre, origines (module.fonction:ligne) des écritures
longues. Il ne contient JAMAIS : nom de dossier ou de partie, objet ou expéditeur de courriel, chemin, adresse, texte de pièce,
identifiant ou secret. Le texte est relu par un contrôle final (adresses, téléphones, chemins) avant d'être affiché.
"""
from collections import Counter
from datetime import datetime, timedelta, timezone
import json
import platform
import re

EMAIL = re.compile(r'[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}')
PHONE = re.compile(r'(?:\+33\s?|0)[1-9](?:[\s.-]?\d{2}){4}')
PATHLIKE = re.compile(r'(?:[A-Za-z]:\\|/)(?:[^\s/\\]+[/\\]){1,}[^\s]*')
URL = re.compile(r'https?://\S+')


def _clean(text):
    text = URL.sub('[adresse]', str(text))
    text = EMAIL.sub('[courriel]', text)
    text = PHONE.sub('[téléphone]', text)
    return PATHLIKE.sub('[chemin]', text)


def report(desk):
    from . import __version__
    from .web520 import diagnosis
    from .ecritures5636 import summary
    from .diagnostic5634 import install_mode
    d = diagnosis(desk)
    since = (datetime.now(timezone.utc) - timedelta(days=7)).isoformat()
    codes = Counter()
    for r in desk.db.execute("SELECT kind,result FROM jobs WHERE status='error' AND COALESCE(finished,created)>=?", (since,)):
        try:
            code = json.loads(r['result'] or '{}').get('erreur', 'inconnu')
        except ValueError:
            code = 'inconnu'
        codes[(r['kind'], re.sub(r'[^a-z0-9_:.-]', '', str(code).lower())[:60])] += 1
    lines = ['AxiorHub Pilote %s — signalement anonymisé (%s)' % (__version__, datetime.now(timezone.utc).strftime('%Y-%m-%d')),
             'Système : %s %s, Python %s ; mode : %s' % (platform.system(), platform.release(), platform.python_version(), install_mode(desk.c)),
             '', 'Services :']
    lines += ['- %s : %s' % (s['label'], 'actif' if s['verified'] else s['state']) for s in d.get('services', [])]
    lines += ['', 'Erreurs des 7 derniers jours (type de travail · code · nombre) :']
    lines += ['- %s · %s · %d' % (k, c, n) for (k, c), n in codes.most_common(20)] or ['- aucune']
    writes = summary(desk.c['state_dir'])
    lines += ['', 'Écritures longues (origine · nombre · plus longue) :']
    lines += ['- %s · %d · %.0f s' % (g['where'], g['count'], g['max']) for g in writes['transactions']] or ['- aucune']
    lines += ['Pages « base occupée » servies : %d' % writes['busy'], '',
              'Aucun nom de dossier, de partie, de courriel, de chemin ni de contenu n’est inclus. Relisez avant de publier.']
    return {'text': '\n'.join(_clean(x) for x in lines)}
