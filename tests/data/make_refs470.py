"""Génère le jeu de test synthétique de références 4.7.0 (corpus fictif + 100 références).

ATTENTION : le corpus est SYNTHÉTIQUE. Les numéros de pourvoi sont fictifs ; les dates d'entrée en vigueur
d'articles servent à tester la logique, pas à faire foi. Ce jeu mesure le comportement du vérificateur,
pas la couverture des bases réelles.
"""
import json
from pathlib import Path

T1240 = "Tout fait quelconque de l'homme, qui cause à autrui un dommage, oblige celui par la faute duquel il est arrivé à le réparer."
ARTICLES = []
def art(code, number, versions):
    ARTICLES.append({'code': code, 'number': number, 'versions': versions})
def v(start, end, state, text, i):
    return {'id': 'LEGIARTI%012d' % i, 'start': start, 'end': end, 'state': state, 'text': text,
            'url': 'https://www.legifrance.gouv.fr/codes/article_lc/LEGIARTI%012d' % i}

# 10 articles en vigueur (une version)
CURRENT = [('Code civil', '1240', T1240), ('Code civil', '1103', 'Les contrats légalement formés tiennent lieu de loi à ceux qui les ont faits.'),
           ('Code civil', '1104', 'Les contrats doivent être négociés, formés et exécutés de bonne foi.'),
           ('Code de procédure civile', '700', "Le juge condamne la partie tenue aux dépens ou qui perd son procès à payer à l'autre partie la somme qu'il détermine."),
           ('Code de commerce', 'L441-10', 'Les conditions de règlement doivent obligatoirement préciser les conditions d application et le taux des pénalités de retard.'),
           ('Code de procédure civile', '56', "L'assignation contient à peine de nullité les mentions prescrites par l'article 54."),
           ('Code de procédure civile', '768', 'Les conclusions comprennent distinctement un exposé des faits et de la procédure.'),
           ('Code de commerce', 'L110-3', "A l'égard des commerçants, les actes de commerce peuvent se prouver par tous moyens."),
           ('Code du travail', 'L1234-1', 'Lorsque le licenciement n est pas motivé par une faute grave, le salarié a droit à un préavis.'),
           ('Code de la consommation', 'L212-1', 'Dans les contrats conclus entre professionnels et consommateurs, sont abusives les clauses qui créent un déséquilibre significatif.')]
for i, (c, n, t) in enumerate(CURRENT):
    art(c, n, [v('2016-10-01' if c == 'Code civil' else '2001-01-01', '', 'VIGUEUR', t, 100 + i)])
# 10 articles abrogés
ABROGES = [('Code civil', '1382', T1240), ('Code civil', '1134', 'Les conventions légalement formées tiennent lieu de loi à ceux qui les ont faites.'),
           ('Code civil', '1147', "Le débiteur est condamné au paiement de dommages et intérêts en cas d'inexécution."),
           ('Code civil', '1383', "Chacun est responsable du dommage qu'il a causé non seulement par son fait, mais encore par sa négligence."),
           ('Code de commerce', 'L441-6', 'Les conditions de règlement précisent les pénalités applicables.'),
           ('Code civil', '1165', "Les conventions n'ont d'effet qu'entre les parties contractantes."),
           ('Code civil', '1315', "Celui qui réclame l'exécution d'une obligation doit la prouver."),
           ('Code civil', '1184', 'La condition résolutoire est toujours sous-entendue dans les contrats synallagmatiques.'),
           ('Code de procédure civile', '1317', 'Ancienne rédaction fictive.'),
           ('Code du travail', 'L1233-3', 'Ancienne rédaction fictive du licenciement économique.')]
for i, (c, n, t) in enumerate(ABROGES):
    art(c, n, [v('1804-03-21' if c == 'Code civil' else '2001-01-01', '2016-10-01' if c == 'Code civil' else '2019-07-01', 'ABROGE', t, 200 + i)])
# 4 articles modifiés (deux versions)
MODIFIES = [('Code de commerce', 'L123-1'), ('Code du travail', 'L1221-1'), ('Code de procédure civile', '54'), ('Code de commerce', 'L145-1')]
for i, (c, n) in enumerate(MODIFIES):
    art(c, n, [v('2001-01-01', '2020-01-01', 'MODIFIE', 'Rédaction ancienne de %s.' % n, 300 + i * 2),
               v('2020-01-01', '', 'VIGUEUR', 'Rédaction actuelle de %s.' % n, 301 + i * 2)])

DECISIONS = []
CH = ['civ1', 'civ2', 'civ3', 'com', 'soc', 'crim']
LAB = {'civ1': 'Cass. civ. 1re', 'civ2': 'Cass. civ. 2e', 'civ3': 'Cass. civ. 3e', 'com': 'Cass. com.', 'soc': 'Cass. soc.', 'crim': 'Cass. crim.'}
for i in range(26):
    ch = CH[i % 6]
    DECISIONS.append({'id': 'JURITEXT%012d' % i, 'court': 'cass', 'chamber': ch, 'date': '20%02d-%02d-%02d' % (15 + i % 9, 1 + i % 12, 3 + i % 25),
                      'pourvoi': '%02d-%02d.%03d' % (15 + i % 9, 10 + i % 9, 100 + i * 7), 'title': 'Décision fictive %d' % i,
                      'url': 'https://www.courdecassation.fr/decision/JURITEXT%012d' % i})
for i in range(4):
    DECISIONS.append({'id': 'CA%012d' % i, 'court': 'ca', 'chamber': '', 'date': '2021-0%d-1%d' % (i + 1, i), 'rg': '19/0%d%03d' % (i + 1, 100 + i), 'title': 'Arrêt fictif CA', 'url': ''})
CORPUS = {'as_of': '2026-09-30', 'articles': ARTICLES, 'decisions': DECISIONS}

def fr(d):
    y, m, dd = d.split('-')
    return '%d/%s/%s' % (int(dd), m, y)

CASES = []
def case(kind, text, expected, fact_date='', why=''):
    CASES.append({'n': len(CASES) + 1, 'kind': kind, 'text': text, 'fact_date': fact_date, 'expected': expected, 'why': why})

# --- 40 exactes
for c, n, t in CURRENT[:10]:
    case('article_exact', "Le demandeur invoque l'article %s du %s." % (n, c), 'verifiee')
for c, n in MODIFIES:
    case('article_exact_version_actuelle', "Il se fonde sur l'article %s du %s." % (n, c), 'verifiee')
for c, n, t in CURRENT[:6]:
    case('article_exact_citation', "L'article %s du %s dispose : « %s »." % (n, c, t.rstrip('.')), 'verifiee', why='citation textuelle exacte')
for c, n, t in ABROGES[:2]:
    case('article_abroge_applicable_aux_faits', "Aux termes de l'article %s du %s." % (n, c), 'verifiee', fact_date='2010-05-04', why='abrogé mais applicable aux faits de 2010')
for d in DECISIONS[:14]:
    if d['court'] == 'cass':
        case('decision_exacte', "%s, %s, n° %s." % (LAB[d['chamber']], '%d mars %d' % (int(d['date'][8:]), int(d['date'][:4])) if False else fr(d['date']), d['pourvoi']), 'verifiee')
# complète à 40 avec décisions CA
for d in DECISIONS[26:30]:
    case('decision_ca_exacte', "CA Lyon, %s, RG n° %s." % (fr(d['date']), d['rg']), 'verifiee')
assert len(CASES) == 40, len(CASES)

# --- 60 erreurs volontaires
for c, n, t in ABROGES[:10]:
    case('article_abroge', "Le défendeur se prévaut de l'article %s du %s." % (n, c), 'flag', why='abrogé, aucune date des faits')
for i in range(8):
    case('article_inexistant', "Voir l'article %s du Code civil." % (9000 + i * 13), 'flag', why='article inexistant')
for c, n, t in CURRENT[:6]:
    case('article_citation_alteree', "L'article %s du %s dispose : « %s »." % (n, c, 'Toute personne qui cause un préjudice quelconque à autrui est dispensée de toute réparation en toutes circonstances'), 'flag', why='citation textuelle altérée')
for c, n, t in CURRENT[:3] + [CURRENT[5], CURRENT[6], CURRENT[7]]:
    case('article_avant_entree_en_vigueur', "Aux termes de l'article %s du %s." % (n, c), 'flag', fact_date='1995-06-01', why='non en vigueur à la date des faits')
for i in range(14):
    case('pourvoi_inexistant', "Cass. com., 12/03/2019, n° 77-%02d.%03d." % (10 + i, 900 + i), 'flag', why='pourvoi inexistant')
for d in DECISIONS[:20:2]:
    year = int(d['date'][:4]) - 1
    case('date_fausse', "%s, %s, n° %s." % (LAB[d['chamber']], fr('%d%s' % (year, d['date'][4:])), d['pourvoi']), 'flag', why='date fausse')
for d in DECISIONS[:12:2]:
    wrong = CH[(CH.index(d['chamber']) + 1) % 6]
    case('chambre_fausse', "%s, %s, n° %s." % (LAB[wrong], fr(d['date']), d['pourvoi']), 'flag', why='chambre fausse')
assert len(CASES) == 100, len(CASES)

out = Path(__file__).parent
(out / 'refs470_corpus.json').write_text(json.dumps(CORPUS, ensure_ascii=False, indent=1), encoding='utf-8')
(out / 'refs470_jeu_100.json').write_text(json.dumps(CASES, ensure_ascii=False, indent=1), encoding='utf-8')
print('cas', len(CASES), 'dont erreurs', sum(c['expected'] == 'flag' for c in CASES))
