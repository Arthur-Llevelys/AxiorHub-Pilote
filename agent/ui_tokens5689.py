"""5.6.89 : jetons de l'interface (audit 5.6.88, §§ 15 et 16). Une seule palette, aucune par page.

Les valeurs viennent du paquet verrouillé ``@carbon/themes`` (fichier généré ``ui_jetons_carbon5689``) : Gray 10 en clair, Gray 100 en sombre. Trois repères de la charte passent
devant le thème (succès, IA, avertissement écrit) ; ils sont déclarés ici, et leurs contrastes sont contrôlés par ``tests/test_v5689_carbon.py``.

Les pages non migrées suivent sans modification : les variables CSS existantes (``--ws-*``, ``--ax-*``…) reçoivent une valeur de jeton. La feuille ne s'applique que sous
``body[data-carbon]`` : éteindre le drapeau du thème rend la présentation d'origine, sans rien retirer. Elle est générée par ``scripts/build-css.py`` dans ``v5689-carbon-jetons.css``.

Couleurs personnelles (``tableau5688``) : la palette fermée de la 5.6.88 est reprise comme jeton de marque, après contrôle de contraste sur les fonds de chaque thème.
Une couleur qui échoue n'est pas émise : la personne garde le bleu d'origine plutôt qu'un texte illisible.
"""
from pathlib import Path
import re

from .ui_jetons_carbon5689 import THEMES

STATIC = Path(__file__).resolve().parent / 'static'
CLAIR, SOMBRE = 'g10', 'g100'
# Charte du chapitre 16, par-dessus le thème Carbon. Le texte de succès Carbon en clair (#24a148) ne passe pas 4,5:1 sur Gray 10 ; le repère #198038 du cahier, si.
CHARTE = {CLAIR: {'support-success': '#198038', 'text-error': '#da1e28', 'support-error': '#da1e28', 'link-primary': '#0f62fe', 'interactive': '#0f62fe',
                  'ui-ia': '#6929c4', 'ui-ia-doux': '#f6f2ff', 'ui-action-doux': '#edf5ff', 'ui-succes-doux': '#defbe6', 'ui-erreur-doux': '#fff1f1', 'ui-attention-doux': '#fcf4d6',
                  'ui-attention-texte': '#684e00', 'ui-nav': '#161616', 'ui-nav-bord': '#393939', 'ui-nav-texte': '#f4f4f4', 'ui-nav-actif': '#393939', 'ui-nav-accent': '#78a9ff', 'ui-sur-action': '#ffffff'},
          SOMBRE: {'support-success': '#42be65', 'text-error': '#ff8389', 'support-error': '#ff8389', 'link-primary': '#78a9ff', 'interactive': '#78a9ff',
                   'ui-ia': '#be95ff', 'ui-ia-doux': '#31135e', 'ui-action-doux': '#001d6c', 'ui-succes-doux': '#022d0d', 'ui-erreur-doux': '#520408', 'ui-attention-doux': '#302400',
                   'ui-attention-texte': '#f1c21b', 'ui-nav': '#262626', 'ui-nav-bord': '#525252', 'ui-nav-texte': '#f4f4f4', 'ui-nav-actif': '#393939', 'ui-nav-accent': '#78a9ff', 'ui-sur-action': '#161616'}}
# Jetons de contexte : Carbon les pose par couche ; la page reste sur la couche 1, le champ de saisie sur la couche 2 (blanc sur blanc serait invisible).
CONTEXTE = {'layer': 'layer-01', 'layer-hover': 'layer-hover-01', 'layer-active': 'layer-active-01', 'layer-selected': 'layer-selected-01', 'layer-selected-hover': 'layer-selected-hover-01',
            'layer-accent': 'layer-accent-01', 'layer-background': 'layer-background-01', 'field': 'field-02', 'field-hover': 'field-hover-02', 'border-subtle': 'border-subtle-01',
            'border-strong': 'border-strong-01', 'border-tile': 'border-tile-01', 'popover-background-color': 'layer-01', 'popover-text-color': 'text-primary'}
# Variables existantes -> jeton. Les pages anciennes les lisent sans le savoir. ``--ws-accent`` sert à la fois de fond de bouton et de couleur de texte : en sombre, c'est une teinte claire, avec une encre sombre dessus.
HERITAGE = {'--ws-paper': 'background', '--ws-surface': 'layer-01', '--ws-surface-alt': 'layer-02', '--ws-card': 'layer-01', '--ws-panel': 'layer-01', '--ws-soft': 'layer-02', '--ws-ink': 'text-primary', '--ws-text': 'text-primary',
            '--ws-muted': 'text-secondary', '--ws-accent': 'interactive', '--ws-accent-hover': 'link-primary-hover', '--ws-accent-soft': 'ui-action-doux', '--ws-border': 'border-subtle-01', '--ws-line': 'border-subtle-01',
            '--ws-outline': 'focus', '--ws-sidebar': 'ui-nav', '--ws-sidebar-border': 'ui-nav-bord', '--ws-sidebar-text': 'ui-nav-texte', '--ws-ai-blue': 'interactive', '--ui-text': 'text-primary', '--ui-secondary-text': 'text-secondary',
            '--ax-bg': 'background', '--ax-card': 'layer-01', '--ax-surface': 'layer-01', '--ax-surface-2': 'layer-02', '--ax-ink': 'text-primary', '--ax-text': 'text-primary', '--ax-mute': 'text-secondary', '--ax-muted': 'text-secondary',
            '--ax-muted-text': 'text-secondary', '--ax-line': 'border-subtle-01', '--ax-border': 'border-subtle-01', '--ax-accent': 'interactive', '--ax-accent-ink': 'ui-sur-action', '--ax-ok': 'support-success', '--ax-bad': 'text-error',
            '--ax-warn': 'ui-attention-texte', '--ax-soft': 'ui-action-doux', '--v391-blue': 'interactive', '--v391-blue-soft': 'ui-action-doux', '--v391-line': 'border-subtle-01', '--ux392-accent': 'interactive', '--ux392-accent-soft': 'ui-action-doux',
            '--ux392-line': 'border-subtle-01', '--ux392-muted': 'text-secondary',
            # Alias calculés une fois pour toutes sur la racine par v430.css et v363.css : les jetons étant posés sur <body>, ils sont repris ici.
            '--ink': 'text-primary', '--accent': 'interactive', '--paper': 'background', '--background': 'background', '--panel': 'layer-01', '--surface': 'layer-01', '--surface-2': 'layer-02', '--text': 'text-primary',
            '--border': 'border-subtle-01', '--muted': 'text-secondary'}
# 5.6.96 (lot affichage, U03) : typographie centralisée. Figtree par défaut (décision du cabinet du 9 novembre 2026, fichier local, aucun CDN),
# IBM Plex Sans et la police du système en variantes (``<html data-police="plex|systeme">``, préférence par compte dans ``tableau5688``).
# Chaque pile se termine par les polices du système : un fichier absent ou bloqué laisse un texte lisible. Tailles en rem (15 px de corps,
# 14 px au plus petit, titres hiérarchisés), deux graisses seulement (400 et 600), interlignage 1,5.
REPLI = '"Segoe UI", system-ui, -apple-system, Roboto, "Helvetica Neue", Arial, sans-serif'
POLICES = {'figtree': '"Figtree", ' + REPLI, 'plex': '"IBM Plex Sans", ' + REPLI, 'systeme': REPLI}
POLICE_DEFAUT = 'figtree'
TAILLES = {'corps': '.9375rem', 'petit': '.875rem', 'titre-1': '1.5rem', 'titre-2': '1.125rem', 'titre-3': '1rem', 'interligne': '1.5'}
# (famille, graisses déclarées, fichier) : le fichier Figtree est variable (300 à 900) ; Plex a un fichier par graisse.
FICHIERS_POLICE = (('Figtree', '300 900', 'figtree-latin.woff2'), ('IBM Plex Sans', '400', 'plex-sans-400.woff2'), ('IBM Plex Sans', '600', 'plex-sans-600.woff2'))


def jetons(theme):
    """Valeurs complètes d'un thème : Carbon verrouillé, puis la charte, puis les jetons de contexte."""
    table = {**THEMES[theme], **CHARTE[theme]}
    table.update({k: table[v] for k, v in CONTEXTE.items()})
    return table


def _canal(valeur):
    valeur /= 255
    return valeur / 12.92 if valeur <= .03928 else ((valeur + .055) / 1.055) ** 2.4


def luminance(couleur):
    couleur = couleur.lstrip('#')
    if len(couleur) == 3:
        couleur = ''.join(c * 2 for c in couleur)
    rouge, vert, bleu = (int(couleur[i:i + 2], 16) for i in (0, 2, 4))
    return .2126 * _canal(rouge) + .7152 * _canal(vert) + .0722 * _canal(bleu)


def contraste(a, b):
    """Rapport WCAG entre deux couleurs hexadécimales opaques."""
    haut, bas = sorted((luminance(a), luminance(b)), reverse=True)
    return (haut + .05) / (bas + .05)


def accent_conforme(nom):
    """Une couleur personnelle ne devient jeton de marque que si elle se lit partout où elle sert : texte sur les deux fonds de page, et texte posé sur elle (bouton)."""
    from .tableau5688 import ACCENTS, ENCRE_SOMBRE
    clair, sombre = ACCENTS[nom][1], ACCENTS[nom][2]
    if not clair:
        return True
    fonds = jetons(CLAIR), jetons(SOMBRE)
    return all(contraste(clair[i], fonds[0]['layer-01']) >= 4.5 and contraste(clair[i], fonds[0]['background']) >= 4.5 and contraste(clair[i], '#ffffff') >= 4.5 for i in (0, 1)) and all(
        contraste(sombre[i], fonds[1]['layer-01']) >= 4.5 and contraste(sombre[i], fonds[1]['background']) >= 4.5 and contraste(sombre[i], ENCRE_SOMBRE) >= 4.5 for i in (0, 1))


def _employes():
    """Jetons réellement lus par les composants embarqués ou nos feuilles : le reste n'est pas émis."""
    texte = ''
    for name in ('carbon5682.js', 'v5689-carbon.css'):
        path = STATIC / name
        if path.is_file():
            texte += path.read_text(encoding='utf-8')
    return set(re.findall(r'var\(--cds-([a-z0-9-]+)', texte))


def _declarations(table, employes):
    cds = ';'.join('--cds-%s:%s' % (k, table[k]) for k in sorted(employes) if k in table)
    propres = ';'.join('--%s:%s' % (k, v) for k, v in sorted(table.items()) if k.startswith('ui-'))
    anciens = ';'.join('%s:%s' % (k, table[v]) for k, v in sorted(HERITAGE.items()))
    return cds + ';' + propres + ';' + anciens + ';--ws-hero:' + table['layer-01']


def _accents():
    from .tableau5688 import ACCENTS, ENCRE_SOMBRE
    regles = []
    for nom, (_, clair, sombre) in ACCENTS.items():
        if not clair or not accent_conforme(nom):
            continue
        for portee, teinte, encre in ((':root', clair, '#ffffff'), (':root[data-theme=dark]', sombre, ENCRE_SOMBRE)):
            valeurs = ('--ui-marque:%s;--ui-marque-survol:%s;--ui-marque-doux:%s;--ui-sur-marque:%s;--ws-accent:%s;--ws-accent-hover:%s;--ws-accent-soft:%s;--ax-accent:%s;--ax-accent-ink:%s;'
                       '--ax-soft:%s;--cds-link-primary:%s;--cds-interactive:%s') % (teinte[0], teinte[1], teinte[2], encre, teinte[0], teinte[1], teinte[2], teinte[0], encre, teinte[2], teinte[0], teinte[0])
            regles.append('%s body[data-carbon] .c530[data-accent=%s]{%s}' % (portee, nom, valeurs))
    return regles


def feuille():
    """Texte de ``v5689-carbon-jetons.css``. Le thème sombre suit le réglage de la personne, sinon celui du système (même règle que ``v320.js``)."""
    employes = _employes() | set(CONTEXTE)
    clair = _declarations(jetons(CLAIR), employes)
    sombre = _declarations(jetons(SOMBRE), employes)
    faces, typo, piles = polices()   # 5.6.96 (U03) : la typographie entre dans le bloc des jetons ; les variantes suivent les thèmes
    return ('/* 5.6.89 : fichier généré par scripts/build-css.py depuis agent/ui_tokens5689.py. Ne pas modifier : modifier le module. */\n' + faces +
            'body[data-carbon]{color-scheme:light;' + typo + '--ui-marque:var(--cds-interactive);--ui-marque-survol:var(--cds-link-primary-hover);--ui-marque-doux:var(--ui-action-doux);--ui-sur-marque:var(--ui-sur-action);' + clair + '}\n'
            ':root[data-theme=dark] body[data-carbon]{color-scheme:dark;' + sombre + '}\n'
            '@media (prefers-color-scheme:dark){:root:not([data-theme=light]) body[data-carbon]{color-scheme:dark;' + sombre + '}}\n' + piles + '\n'.join(_accents()) + '\n')


def version_fichier(nom):
    """5.6.96 (U07) : empreinte de contenu d'un fichier statique (12 caractères de sha256) : l'adresse change avec le fichier, jamais sans lui."""
    from hashlib import sha256
    return sha256((STATIC / nom).read_bytes()).hexdigest()[:12]


def polices():
    """5.6.96 (U03, U07) : déclarations ``@font-face`` aux adresses versionnées par le contenu (cache privé immuable, comme les ``?v=``),
    puis les déclarations de la pile par défaut (``--ui-police``) et des tailles (``--ui-taille-*``), posées dans le bloc ``body[data-carbon]``
    des jetons, et les variantes (``<html data-police>``). Retour : (faces, déclarations, variantes)."""
    faces = ''.join('@font-face{font-family:"%s";font-style:normal;font-weight:%s;font-display:swap;src:url(%s?v=%s) format("woff2")}\n' % (
        famille, graisses, nom, version_fichier(nom)) for famille, graisses, nom in FICHIERS_POLICE)
    tailles = ';'.join('--ui-%s:%s' % (k if k == 'interligne' else 'taille-' + k, v) for k, v in TAILLES.items())
    piles = ''.join(':root[data-police=%s] body[data-carbon]{--ui-police:%s}\n' % (k, v) for k, v in POLICES.items() if k != POLICE_DEFAUT)
    return faces, '--ui-police:%s;%s;' % (POLICES[POLICE_DEFAUT], tailles), piles


def ecrire():
    (STATIC / 'v5689-carbon-jetons.css').write_text(feuille(), encoding='utf-8', newline='\n')
