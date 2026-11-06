"""5.6.94 (V04) : texte préparé pour la lecture à voix haute, commun à toutes les voix locales (Brindille, Kokoro, eSpeak, Chatterbox).

Les moteurs lisent les nombres simples, mais ni les abréviations juridiques, ni les sigles, ni les symboles : « art. 1240 C. civ. » ou
« Cass. civ. 1re » deviennent des sons sans sens. Ce module développe, avant la synthèse et sur le serveur :
- articles et alinéas (« art. » → article, « al. 2 » → alinéa 2, « art. L. 1234-5 » → article L 1234 tiret 5) ;
- codes (« C. civ. » → Code civil ; CSP, CSS et CPI seulement après une référence d'article, sinon épelés) ;
- juridictions (« Cass. civ. 1re » → Cour de cassation, première chambre civile ; « CA Lyon » → cour d'appel de Lyon ; CE, TJ, JAF…) ;
- titres devant un nom (« Me », « M. », « Mme »…), jamais seuls (« section M. » reste tel quel) ;
- sigles épelés (RG, SARL, SCI…) ou lus comme un mot (URSSAF → Urssaf) ;
- « n° », « § », montants en euros, pourcentages, dates numériques, ordinaux, références de pourvoi lues par groupe.

Rien n'est développé dans une adresse électronique ou une URL, et les marqueurs de pseudonymisation (« [PERSONNE_1] ») restent intacts :
ils sont mis à l'abri avant tout traitement, puis restitués à l'identique. Aucune donnée n'est conservée ; aucun appel extérieur.
"""
import re

# ---------------------------------------------------------------- nombres en toutes lettres (orthographe traditionnelle)
UNITES = ('zéro', 'un', 'deux', 'trois', 'quatre', 'cinq', 'six', 'sept', 'huit', 'neuf', 'dix', 'onze', 'douze', 'treize', 'quatorze',
          'quinze', 'seize')
DIZAINES = {2: 'vingt', 3: 'trente', 4: 'quarante', 5: 'cinquante', 6: 'soixante'}
MOIS = ('', 'janvier', 'février', 'mars', 'avril', 'mai', 'juin', 'juillet', 'août', 'septembre', 'octobre', 'novembre', 'décembre')


def _moins_de_cent(n):
    if n < 17:
        return UNITES[n]
    if n < 20:
        return 'dix-' + UNITES[n - 10]
    dizaine, unite = divmod(n, 10)
    if dizaine == 7:                                   # soixante-dix… soixante et onze
        return 'soixante et onze' if unite == 1 else 'soixante-' + _moins_de_cent(10 + unite)
    if dizaine == 9:
        return 'quatre-vingt-' + _moins_de_cent(10 + unite)
    if dizaine == 8:
        return 'quatre-vingts' if unite == 0 else 'quatre-vingt-' + UNITES[unite]
    base = DIZAINES[dizaine]
    return base if unite == 0 else base + (' et un' if unite == 1 else '-' + UNITES[unite])


def _moins_de_mille(n):
    centaine, reste = divmod(n, 100)
    if not centaine:
        return _moins_de_cent(reste)
    tete = 'cent' if centaine == 1 else UNITES[centaine] + ' cent'
    if not reste:
        return tete + ('s' if centaine > 1 else '')
    return tete + ' ' + _moins_de_cent(reste)


def nombre(n):
    """Entier en toutes lettres : 1250 → « mille deux cent cinquante », 80 → « quatre-vingts », 200000 → « deux cent mille »."""
    n = int(n)
    if n < 0:
        return 'moins ' + nombre(-n)
    if n < 1000:
        return _moins_de_mille(n)
    parties = []
    for valeur, singulier, pluriel in ((10 ** 9, 'milliard', 'milliards'), (10 ** 6, 'million', 'millions')):
        quotient, n = divmod(n, valeur)
        if quotient:
            parties.append('un ' + singulier if quotient == 1 else nombre(quotient) + ' ' + pluriel)
    quotient, n = divmod(n, 1000)
    if quotient:   # « mille » est invariable et ôte le s de « cents » et « vingts » qui le précèdent
        parties.append('mille' if quotient == 1 else re.sub(r'(cent|vingt)s$', r'\1', _moins_de_mille(quotient)) + ' mille')
    if n:
        parties.append(_moins_de_mille(n))
    return ' '.join(parties)


def ordinal(n, feminin=False):
    """1 → premier (première), 2 → deuxième, 5 → cinquième, 9 → neuvième, 21 → vingt et unième, 80 → quatre-vingtième."""
    n = int(n)
    if n == 1:
        return 'première' if feminin else 'premier'
    mot = re.sub(r'(cent|vingt|million|milliard)s$', r'\1', nombre(n))
    if mot.endswith('cinq'):
        mot += 'u'
    elif mot.endswith('neuf'):
        mot = mot[:-1] + 'v'
    elif mot.endswith('e'):
        mot = mot[:-1]
    return mot + 'ième'


def _chiffres(texte):
    """Partie décimale : les zéros de tête sont lus (« 05 » → zéro cinq), le reste comme un nombre."""
    zeros = len(texte) - len(texte.lstrip('0'))
    reste = texte.lstrip('0')
    return ' '.join(['zéro'] * zeros + ([nombre(int(reste))] if reste else []))


def decimal(entier, fraction=''):
    texte = nombre(int(entier))
    return texte + (' virgule ' + _chiffres(fraction) if fraction else '')


def montant(entier, centimes=''):
    """1250 et « 50 » → « mille deux cent cinquante euros cinquante » ; 1 → un euro ; 0,50 → cinquante centimes ; 2 000 000 → deux millions d'euros."""
    euros = int(entier)
    cents = int((centimes + '0')[:2]) if centimes else 0           # « 12,5 € » : cinquante centimes
    if euros == 0 and cents:
        return nombre(cents) + (' centime' if cents == 1 else ' centimes')
    mots = nombre(euros)
    unite = ' euro' if euros in (0, 1) else (" d'euros" if re.search(r'(millions?|milliards?)$', mots) else ' euros')
    return mots + unite + (' ' + nombre(cents) if cents else '')


# ---------------------------------------------------------------- mise à l'abri : URL, adresses, marqueurs de pseudonymisation
# Les repères sont faits de caractères à usage privé seulement : aucune règle (chiffres, lettres, ponctuation) ne peut les toucher.
ABRI, MARQUEUR, FIN = '\ue000', '\ue002', '\ue001'
URL = re.compile(r'\b(?:https?://|www\.)[^\s<>"]+[^\s<>".,;:!?)\]]')
COURRIEL = re.compile(r'[\w.+-]+@[\w-]+(?:\.[\w-]+)+')
PSEUDONYME = re.compile(r'\[[A-Z][A-Z0-9_]*_\d+\]')
REPERE = re.compile('[' + ABRI + MARQUEUR + ']([\ue100-\uefff])' + FIN)


def _abriter(texte, gardes):
    def garder(debut):
        def remplacer(m):
            gardes.append(m.group(0))
            return debut + chr(0xE100 + len(gardes) - 1) + FIN
        return remplacer
    texte = PSEUDONYME.sub(garder(MARQUEUR), texte)
    texte = URL.sub(garder(ABRI), texte)
    return COURRIEL.sub(garder(ABRI), texte)


def _restituer(texte, gardes):
    return REPERE.sub(lambda m: gardes[ord(m.group(1)) - 0xE100], texte)


# ---------------------------------------------------------------- règles
ESPACE = '[ \u00a0\u202f]'                              # espace, insécable, fine insécable
MAJ = 'A-ZÀÂÄÇÉÈÊËÎÏÔÖÙÛÜ'
MIN = 'a-zàâäçéèêëîïôöùûüœæ'
VILLE = '[' + MAJ + '][' + MIN + '][' + MAJ + MIN + "'’-]*"     # Paris, Aix-en-Provence, Fort-de-France
NOM = '(?=[' + MAJ + ']|' + MARQUEUR + ')'               # un nom propre ou un marqueur « [PERSONNE_1] » suit
ORD_CHAMBRE = {'1re': 'première', '1ère': 'première', '2e': 'deuxième', '2ème': 'deuxième', '3e': 'troisième', '3ème': 'troisième'}


def _de(ville):
    return ("d'" if re.match('[AEIOUYÀÂÉÈÊÎÏÔÖÙÛÜaeiouy]', ville) else 'de ') + ville


def _references(texte):
    # SIREN, SIRET : chiffre par chiffre (« zéro zéro zéro… »), comme on les dicte
    texte = re.sub(r'\b(SIRE[NT])(\s?:?\s?)((?:\d' + ESPACE + r'?){8,13}\d)\b',
                   lambda m: m.group(1) + m.group(2) + ' '.join(UNITES[int(c)] for c in re.sub(r'\D', '', m.group(3))), texte)
    # Pourvoi en cassation « 22-12.345 » : lu par groupe (vingt-deux, douze, trois cent quarante-cinq).
    texte = re.sub(r'(?<![\d.,-])(\d{2})-(\d{2})\.(\d{3})(?![\d.,])', lambda m: ', '.join(nombre(int(g)) for g in m.groups()), texte)
    # Numéro de rôle « 24/01234 » : deux groupes, sans les zéros de tête.
    return re.sub(r'(?<![\d/])(\d{2})/(\d{4,5})(?![\d/])', lambda m: nombre(int(m.group(1))) + ' barre ' + nombre(int(m.group(2))), texte)


def _dates(texte):
    def jour_mois(jour, mois, annee):
        jour, mois, annee = int(jour), int(mois), int(annee)
        if not (1 <= mois <= 12 and 1 <= jour <= 31):
            return None
        return '%s %s %d' % ('1er' if jour == 1 else str(jour), MOIS[mois], annee)
    def francaise(m):
        annee = m.group(4)
        return jour_mois(m.group(1), m.group(3), int(annee) + (2000 if len(annee) == 2 else 0)) or m.group(0)
    texte = re.sub(r'(?<![\d/.])(\d{1,2})([/.])(\d{1,2})\2(\d{4}|\d{2})(?![\d/]|\.\d)', francaise, texte)
    return re.sub(r'(?<![\d-])(\d{4})-(\d{2})-(\d{2})(?![\d-])', lambda m: jour_mois(m.group(3), m.group(2), m.group(1)) or m.group(0), texte)


ENTIER = r'(\d{1,3}(?:' + ESPACE + r'\d{3})+|\d{1,3}(?:\.\d{3})+(?=,\d|' + ESPACE + r'?(?:€|euros?\b|EUR\b))|\d+)'


def _sans_separateurs(chiffres):
    return re.sub(r'[ \u00a0\u202f.]', '', chiffres)


def _argent(texte):
    def multiple(m):
        entier, fraction, unite = m.group(1), m.group(2) or '', m.group(3)
        base = decimal(_sans_separateurs(entier), fraction)
        if unite in ('k', 'K'):
            return base + ' mille euros'
        pluriel = 's' if fraction or int(_sans_separateurs(entier)) > 1 else ''
        return base + (' milliard' if unite == 'Md' else ' million') + pluriel + " d'euros"
    texte = re.sub(r'(?<![\d,])(\d+)(?:,(\d+))?' + ESPACE + r'?(k|K|M|Md)€', multiple, texte)
    return re.sub(r'(?<![\d,.])' + ENTIER + r'(?:,(\d{1,2}))?' + ESPACE + r'?(?:€|euros?\b|EUR\b)',
                  lambda m: montant(_sans_separateurs(m.group(1)), m.group(2) or ''), texte)


def _pourcentages(texte):
    return re.sub(r'(?<![\d,])(\d+)(?:,(\d+))?' + ESPACE + r'?%', lambda m: decimal(m.group(1), m.group(2) or '') + ' pour cent', texte)


def _milliers(texte):
    """« 3 000 » ou « 1 250,5 » (séparateur de milliers) : un seul nombre, sinon le moteur lirait « trois » puis « zéro »."""
    return re.sub(r'(?<![\d,])(\d{1,3}(?:' + ESPACE + r'\d{3})+)(?:,(\d+))?(?![\d])',
                  lambda m: decimal(_sans_separateurs(m.group(1)), m.group(2) or ''), texte)


def _ville_de(ville):
    """« de Paris », « d'Angers », « du Mans », « des Sables-d'Olonne », « de La Rochelle »."""
    if ville.startswith('Le '):
        return 'du ' + ville[3:]
    if ville.startswith('Les '):
        return 'des ' + ville[4:]
    return _de(ville)


def _juridictions(texte):
    chambre = '(1re|1ère|2e|2ème|3e|3ème)'
    civile = lambda m: 'Cour de cassation, ' + ORD_CHAMBRE[m.group(1)] + ' chambre civile'
    texte = re.sub(r'\b(?:Cass\.\s?)?[Cc]iv\.\s?' + chambre + r'(?![\w])', civile, texte)
    texte = re.sub(r'\bCass\.\s?' + chambre + r'\s?civ\.', civile, texte)
    texte = re.sub(r'\bCass\.\s?civ\.', 'Cour de cassation, chambre civile', texte)
    for court, long in (('com', 'chambre commerciale'), ('soc', 'chambre sociale'), ('crim', 'chambre criminelle')):
        texte = re.sub(r'\bCass\.\s?' + court + r'\.', 'Cour de cassation, ' + long, texte)
        texte = re.sub(r'\b' + court.capitalize() + r'\.(?=\s?,)', 'Cour de cassation, ' + long, texte)
    texte = re.sub(r'\b(?:Cass\.\s?)?[Aa]ss\.\s?plén\.', 'Cour de cassation, assemblée plénière', texte)
    texte = re.sub(r'\b(?:Cass\.\s?)?[Cc]h\.\s?mixte\b', 'Cour de cassation, chambre mixte', texte)
    texte = re.sub(r'\bCass\.', 'Cour de cassation', texte)
    texte = re.sub(r'\bCons\.\s?const\.', 'Conseil constitutionnel', texte)
    texte = re.sub(r'\bCons\.\s?(?:d’|d\')?[ÉE]tat\b', "Conseil d'État", texte)
    for sigle, long in (('CEDH', "Cour européenne des droits de l'homme"), ('CJUE', "Cour de justice de l'Union européenne"),
                        ('CJCE', 'Cour de justice des Communautés européennes'), ('JAF', 'juge aux affaires familiales'),
                        ('JEX', "juge de l'exécution"), ('JLD', 'juge des libertés et de la détention'), ('JME', 'juge de la mise en état'),
                        ('QPC', 'question prioritaire de constitutionnalité')):
        texte = re.sub(r'\b' + sigle + r'\b', long, texte)
    # Juridiction suivie de sa ville : « CA Lyon », « CA de Paris », « CA d'Angers », « TJ Le Mans ».
    ville = '((?:Les?|La) ' + VILLE + '|' + VILLE + ')'
    for sigle, long, seul in (('CAA', "cour administrative d'appel", True), ('CA', "cour d'appel", False), ('TJ', 'tribunal judiciaire', True),
                              ('TAE', 'tribunal des activités économiques', True), ('TC', 'tribunal de commerce', True),
                              ('TGI', 'tribunal de grande instance', True), ('TA', 'tribunal administratif', False),
                              ('CPH', "conseil de prud'hommes", True)):
        texte = re.sub(r'\b' + sigle + r'(?:\s+de\s+|\s+d[’\']\s?|\s+)' + ville, lambda m, l=long: l + ' ' + _ville_de(m.group(1)), texte)
        if seul:   # sans ville : « le TJ », « TJ, 3 mars » ; « CA » seul reste tel quel (chiffre d'affaires)
            texte = re.sub(r'\b' + sigle + r'\b', long, texte)
    texte = re.sub(r'\b(le|du|au|Le|Du|Au)\s+TA\b', lambda m: m.group(1) + ' tribunal administratif', texte)
    return _conseil_etat(texte)


def _conseil_etat(texte):
    """« CE » n'est développé que dans un contexte de juridiction : suivi d'une virgule, d'une date ou d'une formation (« CE, 3 mars »,
    « CE Sect. »), ou précédé de « le », « du », « au ». Ailleurs (« CE QUI EST JUGÉ », titre en capitales) il reste tel quel."""
    texte = re.sub(r'\bCE\b(?=\s?[,(]|\s+\d|\s+(?:[Ss]ect|[Aa]ss|réf)\.)', "Conseil d'État", texte)
    texte = re.sub(r'\b(le|du|au|Le|Du|Au)\s+CE\b', lambda m: m.group(1) + " Conseil d'État", texte)
    texte = re.sub(r"(Conseil d'État,?\s?)[Ss]ect\.", r'\1section', texte)
    texte = re.sub(r"(Conseil d'État,?\s?)[Aa]ss\.", r'\1assemblée', texte)
    return re.sub(r'\bréf\.', 'référé', texte)


CODES = (
    (r'C\.\s?pr\.\s?civ\b\.?|C\.\s?proc\.\s?civ\b\.?|\bCPC\b', 'Code de procédure civile'),
    (r'C\.\s?pr\.\s?pén\b\.?|C\.\s?proc\.\s?pén\b\.?|\bCPP\b', 'Code de procédure pénale'),
    (r'C\.\s?civ\b\.?', 'Code civil'), (r'C\.\s?com\b\.?', 'Code de commerce'), (r'C\.\s?trav\b\.?', 'Code du travail'),
    (r'C\.\s?pén\b\.?', 'Code pénal'), (r'C\.\s?consom\b\.?', 'Code de la consommation'), (r'C\.\s?assur\b\.?', 'Code des assurances'),
    (r'C\.\s?séc\.\s?soc\b\.?', 'Code de la sécurité sociale'), (r'C\.\s?sant\.\s?publ\b\.?', 'Code de la santé publique'),
    (r'C\.\s?urb\b\.?', "Code de l'urbanisme"), (r'C\.\s?env\b\.?', "Code de l'environnement"),
    (r'C\.\s?mon\.\s?fin\b\.?|\bCMF\b', 'Code monétaire et financier'), (r'\bCGI\b', 'Code général des impôts'),
    (r'\bCJA\b', 'Code de justice administrative'), (r'\bCGCT\b', 'Code général des collectivités territoriales'),
    (r'\bCCH\b', "Code de la construction et de l'habitation"),
)
# Sigles à plusieurs sens (CSP : catégorie socioprofessionnelle ; CSS ; CPI : Cour pénale internationale) : code seulement après un
# article (« art. L. 1110-4 CSP ») ou après « du »/« au » ; sinon le sigle est épelé.
CODES_CONTEXTE = (('CSP', 'Code de la santé publique'), ('CSS', 'Code de la sécurité sociale'), ('CPI', 'Code de la propriété intellectuelle'))
REF_ARTICLE = r'(?:\bart\.|\b[Aa]rticles?)\s?(?:[LRDA]\.?\s?)?\d[\d.-]*(?:er)?(?:\s?(?:al\.|alinéa)\s?\d+(?:er)?)?(?:\s?et\s?(?:s|ss|suiv)\.)?'


def _codes(texte):
    # « art. 1240 C. civ. » → « art. 1240 du Code civil » : l'article est relié au code ; « … du C. civ. » garde son « du ».
    for motif, long in CODES:
        texte = re.sub(r'(\d(?:er)?' + ESPACE + r'?)?(?:' + motif + ')', lambda m, l=long: (m.group(1) + 'du ' if m.group(1) else '') + l, texte)
    for sigle, long in CODES_CONTEXTE:
        texte = re.sub('(' + REF_ARTICLE + r'\s?(?:du\s|de\sla\s)?)' + sigle + r'\b',
                       lambda m, l=long: m.group(1) + ('' if re.search(r'(?:du|la)\s$', m.group(1)) else 'du ') + l, texte)
        texte = re.sub(r'\b(du|au)\s' + sigle + r'\b', lambda m, l=long: m.group(1) + ' ' + l, texte)
    return texte


def _articles(texte):
    texte = re.sub(r'\b([Aa])rt\.\s?(?=[LRDA]\.?\s?\d|\d|premier|1er)', lambda m: m.group(1) + 'rticle ', texte)
    texte = re.sub(r'\b([Aa])rt\.', lambda m: m.group(1) + 'rticle', texte)
    # « article L. 1234-5 » : la lettre du livre, puis le numéro, le tiret lu
    texte = re.sub(r'(\b[Aa]rticles?\s)([LRDA])\.?\s?(\d[\d-]*)', lambda m: m.group(1) + m.group(2) + ' ' + m.group(3).replace('-', ' tiret '), texte)
    texte = re.sub(r'(\b[Aa]rticles?\s)(\d+(?:-\d+)+)', lambda m: m.group(1) + m.group(2).replace('-', ' tiret '), texte)
    return re.sub(r'\bal\.\s?(?=\d|premier|1er)', 'alinéa ', texte)


TITRES = (('MM.', 'Messieurs'), ('M.', 'Monsieur'), ('Mmes', 'Mesdames'), ('Mme', 'Madame'), ('Mlles', 'Mesdemoiselles'), ('Mlle', 'Mademoiselle'),
          ('Mes', 'Maîtres'), ('Me', 'Maître'), ('Dr', 'Docteur'), ('Pr', 'Professeur'))
# Après « M. », ces mots ouvrent une nouvelle phrase (« … section M. Le lendemain ») : « M. » n'est alors pas un titre.
DEBUTS_PHRASE = ('Le', 'La', 'Les', 'Il', 'Elle', 'Ils', 'Elles', 'Nous', 'Vous', 'Je', 'On', 'Ce', 'Cette', 'Ces', 'Un', 'Une', 'Des', 'En', 'Au', 'Aux',
                 'Par', 'Pour', 'Dans', 'Sur', 'Mais', 'Et', 'Ou', 'Donc', 'Or', 'Car', 'Si', 'Selon', 'Depuis', 'Après', 'Avant', 'Toutefois',
                 'Cependant', 'Ainsi', 'À', 'Sa', 'Son', 'Ses', 'Leur', 'Leurs', 'De', 'Du', 'Cela', 'Puis', 'Enfin')


def _titres(texte):
    suite = '(?=[' + MAJ + ']|' + MARQUEUR + r'|l[ae]\s|l[’\'])'      # un nom, un marqueur « [PERSONNE_1] », ou « M. le Président »
    nouvelle = r'(?!(?:' + '|'.join(DEBUTS_PHRASE) + r')\b)'
    for court, long in TITRES:
        fin = '' if court.endswith('.') else r'\.?'
        garde = nouvelle if court.endswith('.') else ''
        texte = re.sub(r'(?<![\w.])' + re.escape(court) + fin + r'[ \u00a0]+' + garde + suite, long + ' ', texte)
    return texte


def _divers(texte):
    texte = re.sub(r'\bch\.', 'chambre', texte)
    texte = re.sub(r'\b[Nn][°º]s\s?', 'numéros ', texte)
    texte = re.sub(r'\b([Nn])[°º]\s?', lambda m: m.group(1) + 'uméro ', texte)
    texte = re.sub(r'§§\s?', 'paragraphes ', texte)
    texte = re.sub(r'§\s?', 'paragraphe ', texte)
    texte = re.sub(r'(?<=[\w' + FIN + r'])\s?c/\s?(?=\w|' + MARQUEUR + ')', ' contre ', texte)
    texte = re.sub(r'\bet\s(?:s|ss|suiv)\.', 'et suivants', texte)
    texte = re.sub(r'\bcf\.', 'voir', texte)
    texte = re.sub(r'\bpréc\.', 'précité', texte)
    texte = re.sub(r'\bpp\.\s?(?=\d)', 'pages ', texte)
    texte = re.sub(r'\bp\.\s?(?=\d)', 'page ', texte)
    texte = re.sub(r'\bBull\.\s?civ\.', 'Bulletin civil', texte)
    texte = re.sub(r'\bGaz\.\s?Pal\.', 'Gazette du Palais', texte)
    texte = re.sub(r'\bobs\.', 'observations', texte)
    texte = re.sub(r'\bet al\.', 'et autres', texte)
    return re.sub(r'\s&\s', ' et ', texte)


def _ordinaux(texte):
    def remplacer(m):
        n, suffixe = int(m.group(1)), m.group(2)
        if suffixe in ('nd', 'nde'):
            return 'second' if suffixe == 'nd' else 'seconde'
        return ordinal(n, feminin=suffixe in ('re', 'ère'))
    return re.sub(r'\b(\d+)(ère|re|er|ème|eme|e|nde|nd)\b', remplacer, texte)


LETTRES = {'A': 'a', 'B': 'bé', 'C': 'cé', 'D': 'dé', 'E': 'eu', 'F': 'effe', 'G': 'gé', 'H': 'ache', 'I': 'i', 'J': 'ji', 'K': 'ka', 'L': 'elle',
           'M': 'emme', 'N': 'enne', 'O': 'o', 'P': 'pé', 'Q': 'ku', 'R': 'erre', 'S': 'esse', 'T': 'té', 'U': 'u', 'V': 'vé', 'W': 'double vé',
           'X': 'ixe', 'Y': 'i grec', 'Z': 'zède'}
EPELES = ('RG', 'SARL', 'SAS', 'SASU', 'SCI', 'SNC', 'EURL', 'SCP', 'SCM', 'RCS', 'TVA', 'HT', 'TTC', 'PV', 'RGPD', 'CSE', 'CDI', 'CDD', 'PME', 'CGV',
          'JCP', 'SA', 'AGO', 'AGE', 'RIB', 'CSP', 'CSS', 'CPI', 'TA', 'TI')
MOTS = {'URSSAF': 'Urssaf', 'INSEE': 'Insée', 'UNEDIC': 'Unédic', 'CNIL': 'Cnil', 'INPI': 'Inpi', 'BODACC': 'Bodacc', 'SIREN': 'Siren',
        'SIRET': 'Siret', 'SMIC': 'Smic', 'SELARL': 'Sélarl', 'SELAS': 'Sélas', 'IBAN': 'Ibane', 'LRAR': 'lettre recommandée avec accusé de réception'}


def epeler(sigle):
    return ' '.join(LETTRES[c] for c in sigle)


def _sigles(texte):
    texte = re.sub(r'\b(' + '|'.join(sorted(MOTS, key=len, reverse=True)) + r')\b', lambda m: MOTS[m.group(1)], texte)
    return re.sub(r'\b(' + '|'.join(sorted(EPELES, key=len, reverse=True)) + r')\b', lambda m: epeler(m.group(1)), texte)


def developper(texte):
    """Texte prêt à être lu : abréviations, sigles, symboles et nombres développés ; URL, adresses et marqueurs intacts."""
    texte = str(texte or '')
    if not texte.strip():
        return ''
    gardes = []
    texte = _abriter(texte, gardes)
    for regle in (_references, _dates, _argent, _pourcentages, _milliers, _juridictions, _codes, _articles, _titres, _divers, _ordinaux, _sigles):
        texte = regle(texte)
    texte = re.sub(r'[ \u00a0\u202f]{2,}', ' ', texte)
    return _restituer(texte, gardes)


# ---------------------------------------------------------------- découpage pour une lecture au fil de l'eau
def nettoyer(texte):
    """Mise en forme d'une réponse (Markdown) retirée : elle ne se lit pas."""
    texte = re.sub(r'\*\*|__|`', '', str(texte or ''))
    texte = re.sub(r'^\s{0,3}#{1,6}\s+', '', texte, flags=re.M)
    texte = re.sub(r'^\s*[*•-]\s+', '', texte, flags=re.M)
    return re.sub(r'\[([^\]\n]+)\]\((?:https?://|/)[^)\s]*\)', r'\1', texte)


ABREGE_FINAL = re.compile(r'(?:^|[\s(])(?:art|al|M|MM|Me|Mes|C|civ|com|soc|crim|pén|pr|proc|Cass|ch|sect|ass|plén|p|pp|cf|préc|Bull|réf|n°|obs|s|suiv)\.$')


def phrases(texte, longueur=240):
    """Paragraphes (séparés par une ligne vide), chacun en morceaux d'une ou plusieurs phrases de ``longueur`` caractères au plus.
    La première phrase est courte : la lecture commence pendant que les suivantes se préparent."""
    sortie = []
    for paragraphe in re.split(r'\n\s*\n', str(texte or '')):
        paragraphe = re.sub(r'\s+', ' ', paragraphe).strip()
        if not paragraphe:
            continue
        morceaux, tampon, brutes = [], '', []
        for phrase in re.split(r'(?<=[.!?…;:])\s+', paragraphe):
            if brutes and ABREGE_FINAL.search(brutes[-1]):   # « art. 1240 » (texte non développé, ElevenLabs) : pas une fin de phrase
                brutes[-1] += ' ' + phrase
            else:
                brutes.append(phrase)
        for phrase in brutes:
            while len(phrase) > longueur:   # phrase trop longue : coupée à une virgule de la seconde moitié, sinon au dernier espace
                virgule = phrase.rfind(', ', 0, longueur)
                coupe = virgule if virgule > longueur // 2 else phrase.rfind(' ', 0, longueur)
                coupe = coupe if coupe > longueur // 3 else longueur
                if tampon:
                    morceaux.append(tampon)
                    tampon = ''
                morceaux.append(phrase[:coupe + 1].strip())
                phrase = phrase[coupe + 1:].strip()
            if tampon and (len(tampon) + 1 + len(phrase) > longueur or not morceaux):
                morceaux.append(tampon)
                tampon = phrase
            else:
                tampon = (tampon + ' ' + phrase).strip()
        if tampon:
            morceaux.append(tampon)
        sortie.append([m for m in morceaux if m])
    return sortie
