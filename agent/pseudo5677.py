"""5.6.77 : pseudonymisation plus sûre avant tout envoi à une IA non locale (complète ``pseudo540``).

Ce qui change dans ``pseudo540`` :
1. **Numéros validés par leur clé**, ce qui permet d'en reconnaître davantage sans masquer n'importe quel nombre :
   - IBAN par la clé modulo 97, y compris coupé sur deux lignes (la fin d'un IBAN ne part plus en clair) ;
   - NIR (sécurité sociale) : séparateurs espace, point ou tiret, Corse 2A/2B, masqué même si la clé est erronée ;
     la clé modulo 97 sert à reconnaître un NIR coupé par un retour à la ligne ;
   - SIREN par la formule de Luhn, même sans la mention « SIREN » ; carte bancaire collée (Luhn, ou 16 chiffres) ; SIRET : les 14 chiffres partent
     toujours en marqueur, même avec une coquille d'OCR ;
   - numéros RG, Portalis, RPPS, sinistre, immatriculation de véhicule.
2. **Codes de raisonnement préservés** : IDCC, CCN, CSG/CRDS, PMSS, réduction Fillon, AGIRC-ARRCO, DSN, ECLI, rôles (DRH…) ne sont
   plus pris pour des noms ; les articles de loi, dates et montants restent intacts.
3. **Personnes** : « Paul DURAND » et « DURAND Paul » reçoivent le même marqueur ; un « DURAND » isolé reçoit le marqueur de la seule
   personne connue de ce nom ; s'il y a des homonymes (Sophie DURAND et Paul DURAND), il reçoit son propre marqueur pour éviter toute
   erreur d'imputation. « Maître DURAND » n'est jamais rapproché d'un homonyme (avocat, notaire ou commissaire de justice).
   « XARVOL SAS » et « la société XARVOL » sont des sociétés.
4. **Autotest** (``autotest``) : un corpus fictif vérifie ces règles ; la page Modèles d'IA en affiche le résultat. Les tests de la
   version cassent volontairement le moteur (tests de mutation) et vérifient que l'autotest le voit à chaque fois.
"""
import re

# Sigles et codes utiles au raisonnement, jamais pris pour des noms.
CODES = set('''
IDCC CCN KALI SYNTEC HCR BTP CHR CSG CRDS PMSS PASS FILLON ALLEGEMENT ALLÈGEMENT REDUCTION RÉDUCTION AGIRC ARRCO DSN DPAE DRH DAF PDG DG
RRH RH DPO RSSI IRP OPCO SST CSSCT RTT CPF AT MP ATMP IJ IJSS CARSAT MDPH AAH APEC UNEDIC AGS CGEA ECLI FR CCASS CETAT JURITEXT RPPS
ADELI SIV FNI BOSS KBIS PV AG AGE AGO SMIC CDD CDI CTT CSE BDESE NAO PSE RCC PLAN RUPTURE CONVENTIONNELLE ANI ARE ASP
CSS CTRAV CTRAVAIL PRUD PRUDHOMMES PRUD'HOMMES CPH TJ TC CA SOC CIV COMM CRIM ASS PLEN PLÉN MIXTE QPC CJUE CEDH JORF BOAMP INSEE NAF APE
'''.split())

IBAN_CANDIDATE = re.compile(r'\b[A-Z]{2}\d{2}(?:[ \t]{0,2}(?:\r?\n[ \t]{0,2})?[A-Z0-9]{1,4}){3,8}\b')
NIR_WRAPPED = re.compile(r'(?<!\d)([1-478])[ .\-]?(\d{2})[ .\-]?(\d{2})[ .\-]?(\d{2}|2[AB])[ .\-]?(\d{3})[ .\-]?(\d{3})'
                         r'[ \t]*\r?\n[ \t]*(\d{2})(?!\d)')
SIREN_FREE = re.compile(r'(?<![\d,.])(\d{3})([ .]?)(\d{3})\2(\d{3})(?![\d,]|\.\d|\s*(?:€|euros?\b|EUR\b|%|k€|M€))')
CARD_FREE = re.compile(r'(?<!\d)\d{13,19}(?!\d)')


def luhn(digits):
    digits = re.sub(r'\D', '', str(digits or ''))
    if not digits:
        return False
    total = 0
    for i, ch in enumerate(reversed(digits)):
        n = int(ch) * (2 if i % 2 else 1)
        total += n - 9 if n > 9 else n
    return total % 10 == 0


def siren_ok(value):
    digits = re.sub(r'\D', '', str(value or ''))
    if len(digits) == 9:
        return luhn(digits)
    if len(digits) == 14:   # SIRET : Luhn, sauf La Poste (somme des chiffres multiple de 5)
        return luhn(digits) or (digits.startswith('356000000') and sum(map(int, digits)) % 5 == 0)
    return False


def iban_ok(value):
    s = re.sub(r'\s', '', str(value or '')).upper()
    if not re.fullmatch(r'[A-Z]{2}\d{2}[A-Z0-9]{11,30}', s):
        return False
    s = s[4:] + s[:4]
    return int(''.join(str(int(c, 36)) for c in s)) % 97 == 1


def nir_key_ok(value):
    s = re.sub(r'[\s.\-]', '', str(value or '')).upper()
    if not re.fullmatch(r'[1-478]\d{4}(?:\d{2}|2[AB])\d{6}\d{2}', s):
        return False
    body, key = s[:13], int(s[13:])
    body = body.replace('2A', '19', 1) if body[5:7] == '2A' else body.replace('2B', '18', 1) if body[5:7] == '2B' else body
    return 97 - int(body) % 97 == key


def iban_spans(text):
    """[(début, fin)] des IBAN valides (clé modulo 97), y compris coupés sur deux lignes : le plus long préfixe valide l'emporte."""
    out = []
    for m in IBAN_CANDIDATE.finditer(text):
        raw = m.group(0)
        if raw.count('\n') > 1:
            continue
        cuts = [g.end() for g in re.finditer(r'[A-Z0-9]+', raw)]
        for end in reversed(cuts):
            if iban_ok(raw[:end]):
                out.append((m.start(), m.start() + end))
                break
    return out


# ---------------------------------------------------------------------------------------------------- autotest
# Corpus entièrement fictif : noms inventés, numéros de démonstration publiés (IBAN d'exemple) ou calculés pour l'occasion.
CORPUS = ('Monsieur Paul KERVILAC, salarié de la société ZORVAL SAS (SIREN 123 456 782), relève de la convention SYNTEC (IDCC 1486).\n'
          'Mme Sophie KERVILAC, DRH, lui a notifié la rupture. KERVILAC conteste. Maître ORLANDEC représente la société ; Pierre ORLANDEC, gérant, signe.\n'
          'Assiette CSG/CRDS, plafond PMSS, réduction FILLON, AGIRC-ARRCO, DSN mensuelle, article L. 1234-9 du code du travail.\n'
          'NIR 2 91 12 75 115 042 10 ; IBAN FR76 3000 6000 0112 3456 7890\n189 ; référence 111222337 ; RG 24/01234.\n'
          'Lucien XARVOL écrit ; plus tard XARVOL Lucien confirme ; XARVOL signe.')
EXPECT_TOKENS = (('SIREN sans clé de forme', '123 456 782'), ('NIR', '2 91 12 75 115 042 10'), ('IBAN coupé sur deux lignes', '189'),
                 ('SIREN validé par Luhn', '111222337'), ('Numéro RG', '24/01234'), ('Personne', 'Paul KERVILAC'),
                 ('Société', 'ZORVAL'))
EXPECT_KEPT = ('SYNTEC', 'IDCC 1486', 'DRH', 'CSG/CRDS', 'PMSS', 'FILLON', 'AGIRC-ARRCO', 'DSN', 'L. 1234-9')


def autotest():
    """[{contrôle, ok, détail}] sur le corpus fictif. Rien n'est journalisé ni envoyé."""
    from .pseudo540 import Pseudonymizer
    checks = []

    def check(label, ok, detail=''):
        checks.append({'controle': label, 'ok': bool(ok), 'detail': detail})

    check('Clé de Luhn', luhn('123456782') and not luhn('123456783'))
    check('Clé IBAN modulo 97', iban_ok('FR76 3000 6000 0112 3456 7890 189') and not iban_ok('FR76 3000 6000 0112 3456 7890 188'))
    check('Clé NIR (dont Corse)', nir_key_ok('2 91 12 75 115 042 10') and nir_key_ok('1 85 03 2A 123 456 01')
          and not nir_key_ok('2 91 12 75 115 042 11'))
    ps = Pseudonymizer(known=[])
    out = ps.text(CORPUS)
    for label, value in EXPECT_TOKENS:
        check('Masqué : ' + label, value not in out, value if value in out else '')
    for value in EXPECT_KEPT:
        check('Conservé : ' + value, value in out, '' if value in out else value)
    paul, sophie = ps.forward.get(('PERSONNE', 'kervilac paul')), ps.forward.get(('PERSONNE', 'kervilac sophie'))
    isolated = re.search(r'\. (\[PERSONNE_\d+\]) conteste', out)
    check('Homonymes : marqueur propre au nom isolé', paul and sophie and isolated and isolated.group(1) not in (paul, sophie))
    lucien = ps.forward.get(('PERSONNE', 'lucien xarvol'))
    check('Même personne quel que soit l’ordre des noms', lucien and out.count(lucien) == 3)
    pierre, maitre = ps.forward.get(('PERSONNE', 'orlandec pierre')), re.search(r'Maître (\[PERSONNE_\d+\])', out)
    check('Maître jamais rapproché d’un homonyme', 'ORLANDEC' not in out and pierre and maitre and maitre.group(1) != pierre)
    check('Société reconnue', re.search(r'\[SOCIETE_\d+\] \(SIREN', out))
    check('Retour exact des marqueurs', ps.restore(out).count('KERVILAC') >= 3 and '189' in ps.restore(out))
    return checks


def autotest_summary():
    checks = autotest()
    failed = [c for c in checks if not c['ok']]
    return {'total': len(checks), 'reussis': len(checks) - len(failed), 'echecs': [c['controle'] for c in failed]}


def autotest_html():
    """Ligne de la page Modèles d'IA : résultat de l'autotest du pseudonymiseur."""
    from html import escape as e
    try:
        s = autotest_summary()
    except Exception:  # noqa: BLE001 - un autotest qui plante est un échec, jamais une page cassée
        return '<p class="m5-warn">Autotest du pseudonymiseur : impossible à exécuter. Ne transmettez rien à une IA externe avant vérification.</p>'
    if not s['echecs']:
        return ('<p class="ok">Autotest du pseudonymiseur : %d contrôles sur %d réussis (numéros validés par leur clé, codes de raisonnement '
                'conservés, homonymes, retour exact).</p>' % (s['reussis'], s['total']))
    return ('<p class="m5-warn">Autotest du pseudonymiseur : %d contrôle(s) en échec sur %d — %s. Ne transmettez rien à une IA externe avant '
            'vérification.</p>' % (len(s['echecs']), s['total'], e(', '.join(s['echecs']))))
