"""5.6.109 (lot R, renforcée à l'intégration I1) : garde statique : aucun module n'ouvre directement (``open``, ``Path.open``, ``read_bytes``,
``shutil.copy*``, ``io.FileIO``, ``zipfile.write``, ``tarfile.add``, ``cp`` par ``subprocess``…) un fichier de base SQLite vivant ou l'un de ses
journaux (``-wal``, ``-shm``). Sous Linux, refermer un tel descripteur annule tous les verrous POSIX que le processus tient sur la base (voir
``tests/essai_verrous56109.py``). Une base se lit par ``sqlite3`` (``mode=ro``) ou se reconnaît à son nom ; un en-tête se lit sur une COPIE.

CE QUE LA GARDE VOIT (analyse syntaxique, sans exécution) : un appel d'ouverture ou de copie dont le chemin dépend
- d'un nom de base littéral (``'….sqlite3'``, ``'….sqlite'``, ``'….db'``, ``'…-wal'``, ``'…-shm'``, motifs ``glob`` compris), d'une constante du
  module ou d'une variable qui en dérive, d'une fonction du module qui rend un tel chemin ;
- d'un attribut affecté d'un tel chemin (``self.base = etat / 'desk.sqlite3'`` puis ``open(self.base)``) ;
- d'un paramètre d'une fonction du module appelée avec un tel chemin (``lire(etat / 'desk.sqlite3')`` puis ``open(chemin)`` dans ``lire``) ;
- d'un filtre par suffixe (``if p.suffix == '.sqlite3': open(p)``, ``if nom.endswith('.db'): …``) ;
- d'un parcours du dossier d'état venu de la configuration (``for p in Path(c['state_dir']).iterdir(): open(p)``), sauf si la boucle écarte
  d'abord les bases (``if p.suffix in SUFFIXES: continue``, ``if _est_base(p): continue``, ``if fichier_sqlite(n): continue``…) ;
au niveau d'une fonction, d'une méthode, ou du module (blocs ``if __name__ == '__main__'`` compris) ; et la lecture d'un en-tête
``SQLite format 3`` sur un fichier ouvert par ``open``.

CE QU'ELLE NE PEUT PAS VOIR (d'où la garde à l'exécution de ``test_v56109_integration_i1.GardeExecution``) : un chemin calculé à
l'exécution sans trace syntaxique (nom lu dans un fichier, une base, une requête, un réglage dont la valeur est un nom de base) ; un
chemin qui passe par un autre module (appel inter-modules), un conteneur (liste, dictionnaire) ou ``getattr`` ; un parcours d'un dossier
quelconque choisi par l'utilisateur qui contiendrait une base (règle de nom à appliquer dans le module : ``dav.fichier_sqlite``) ; le code
d'une bibliothèque tierce ou d'un sous-processus non ``cp``/``tar``/``zip``. Elle peut aussi signaler à tort : l'exemption est alors
explicite, sur la ligne, avec sa raison.

Exemptions explicites et visibles, sur la ligne : ``lot R : hors service`` (fichier extrait d'une archive, copie temporaire, service arrêté)
ou ``garde56109 : base exclue`` (les bases ont été écartées par leur nom avant cette ligne, d'une façon que l'analyse ne suit pas)."""
import ast
from pathlib import Path
import re

LITTERAL = re.compile(r"""['"][^'"\n]*(?:\.(?:sqlite3|sqlite|db)|-wal|-shm|-journal)['"]""")
ENTETE = 'SQLite format 3'
EXEMPTIONS = ('lot R : hors service', 'garde56109 : base exclue')
EXEMPTION = EXEMPTIONS[0]
FONCTIONS = {'open', 'FileIO', 'copyfile', 'copy', 'copy2', 'copyfileobj', 'file_digest', 'move', 'copytree'}
METHODES = {'open', 'read_bytes', 'read_text', 'write_bytes', 'write_text'}
SOUS_PROCESSUS = re.compile(r'^(?:subprocess\.)?(?:run|call|check_call|check_output|Popen)$|^os\.(?:system|popen)$')
LISTAGES = re.compile(r'\b(?:iterdir|rglob|glob|listdir|scandir|walk)\(')
ETAT = re.compile(r"""state_dir|\bSTATE\b|['"]etat['"]""")
# parcours restreint par son motif à une extension qui n'est pas celle d'une base (``glob('*.json')``) : sans danger
MOTIF_SUR = re.compile(r"""glob\(\s*['"][^'"]*\.(?!sqlite|db\b)[A-Za-z0-9]+['"]\s*\)""")
# un contenu lu, une connexion SQLite ou une mesure n'est pas un chemin : il ne propage pas la teinte
CONTENU = re.compile(r'\.(?:read|read_bytes|read_text|readlines|hexdigest|stat|exists|is_file)\(|json\.loads?\(|\blen\(|connect\(|\.execute\(')
FILTRES_NOM = re.compile(r'(?:_?est_base|fichier_sqlite|_?base_sqlite|est_sqlite|_?est_sqlite|_exclu)\(')


def _exempte(ligne):
    return any(x in ligne for x in EXEMPTIONS)


def _motif_noms(noms):
    return re.compile(r'(?<![\w.])(?:%s)\b' % '|'.join(re.escape(n) for n in sorted(noms))) if noms else None


class _Analyse:
    def __init__(self, source):
        self.source = source
        self.arbre = ast.parse(source)
        self.lignes = source.splitlines()
        self._octets = [x.encode('utf-8') for x in source.splitlines(keepends=True)] + [b'']
        self._seg = {}
        self.fonctions = {}
        for n in ast.walk(self.arbre):
            if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)):
                self.fonctions.setdefault(n.name, []).append(n)
        self.constantes = set()
        for n in self.arbre.body:
            if isinstance(n, (ast.Assign, ast.AnnAssign)) and n.value is not None and LITTERAL.search(self.seg(n.value)):
                cibles = n.targets if isinstance(n, ast.Assign) else [n.target]
                self.constantes |= {m.id for t in cibles for m in ast.walk(t) if isinstance(m, ast.Name)}
        self.attributs = set()                  # self.base = … (teinté) : ``.base`` teinté dans tout le module
        self.parametres = {}                    # fonction -> paramètres teintés par un appel du module
        self.rendent = set()

    def seg(self, n):
        """Texte source d'un nœud (``ast.get_source_segment`` redécoupe tout le fichier à chaque appel : lignes découpées une fois ici)."""
        k = id(n)
        if k not in self._seg:
            try:
                l1, l2, c1, c2 = n.lineno - 1, n.end_lineno - 1, n.col_offset, n.end_col_offset
            except AttributeError:
                self._seg[k] = ''
                return ''
            if l1 == l2:
                texte = self._octets[l1][c1:c2]
            else:
                texte = self._octets[l1][c1:] + b''.join(self._octets[l1 + 1:l2]) + self._octets[l2][:c2]
            self._seg[k] = texte.decode('utf-8', 'replace')
        return self._seg[k]

    def teinte(self, texte, motif):
        if not texte:
            return False
        if LITTERAL.search(texte) or (motif is not None and motif.search(texte)):
            return True
        if self.attributs and re.search(r'\.(?:%s)\b(?!\s*\()' % '|'.join(map(re.escape, sorted(self.attributs))), texte):
            return True
        return bool(self.rendent) and re.search(r'(?<![\w.])(?:%s)\(' % '|'.join(map(re.escape, sorted(self.rendent))), texte) is not None

    @staticmethod
    def _noeuds(portee):
        """Nœuds d'une portée, sans descendre dans les fonctions et classes imbriquées (portées distinctes)."""
        corps = ([x for x in portee.body if not isinstance(x, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef))]
                 if isinstance(portee, ast.Module) else [portee])
        pile, out = list(corps), []
        while pile:
            n = pile.pop()
            out.append(n)
            for enfant in ast.iter_child_nodes(n):
                if isinstance(enfant, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef, ast.Lambda)) and n is not portee:
                    continue
                if isinstance(portee, ast.Module) and isinstance(enfant, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                    continue
                pile.append(enfant)
        return out

    def _noms_de(self, n):
        return {m.id for m in ast.walk(n) if isinstance(m, ast.Name)}

    def _assainie(self, boucle, cibles):
        """Une boucle qui écarte d'abord les bases (``if … suffix/nom … : continue``) n'expose plus sa variable."""
        for n in ast.walk(boucle):
            if isinstance(n, ast.If) and any(isinstance(x, ast.Continue) for x in n.body):
                test = self.seg(n.test)
                if (LITTERAL.search(test) or FILTRES_NOM.search(test) or 'SUFFIXES' in test or any(_mot(c, test) for c in self.constantes)) \
                        and any(_mot(c, test) for c in cibles):
                    return True
        return False

    def _cibles(self, n):
        out = set()
        for t in (n.targets if isinstance(n, ast.Assign) else [n.target]):
            if isinstance(t, ast.Attribute):
                self.attributs.add(t.attr)
            else:
                out |= {m.id for m in ast.walk(t) if isinstance(m, ast.Name)}
        return out

    def _source_teintee(self, texte, motif):
        """Expression qui désigne un chemin de base (et non un contenu lu, une connexion ou une mesure)."""
        return not CONTENU.search(texte) and (self.teinte(texte, motif) or bool(LISTAGES.search(texte) and ETAT.search(texte)
                                                                                 and not MOTIF_SUR.search(texte)))

    def noms_teintes(self, portee):
        """(noms teintés dans toute la portée, {id(nœud): noms teintés en plus dans le corps d'une boucle}). Une variable de boucle n'est
        teintée que dans le corps de SA boucle (un même nom réutilisé plus loin pour autre chose ne l'est pas)."""
        noms = set(self.constantes) | set(self.parametres.get(getattr(portee, 'name', ''), ()))
        noeuds = self._noeuds(portee)
        dans_boucle = set()
        for n in noeuds:
            if isinstance(n, (ast.For, ast.AsyncFor)):
                for st in n.body:
                    dans_boucle |= {id(m) for m in ast.walk(st)}
        for _ in range(3):
            motif = _motif_noms(noms)
            for n in noeuds:
                if isinstance(n, (ast.Assign, ast.AnnAssign)) and n.value is not None and id(n) not in dans_boucle \
                        and self._source_teintee(self.seg(n.value), motif):
                    noms |= self._cibles(n)
        extras = {}
        motif = _motif_noms(noms)
        for n in noeuds:
            if not isinstance(n, (ast.For, ast.AsyncFor)):
                continue
            it = self.seg(n.iter)
            cibles = self._noms_de(n.target)
            if not ((not CONTENU.search(it) and self.teinte(it, motif)) or (LISTAGES.search(it) and ETAT.search(it) and not MOTIF_SUR.search(it))) \
                    or self._assainie(n, cibles):
                continue
            local = set(noms) | cibles
            corps = [m for st in n.body for m in ast.walk(st)]
            for _ in range(2):
                ml = _motif_noms(local)
                for m in corps:
                    if isinstance(m, (ast.Assign, ast.AnnAssign)) and m.value is not None and self._source_teintee(self.seg(m.value), ml):
                        local |= self._cibles(m)
            for m in corps:
                extras.setdefault(id(m), set()).update(local - noms)
        # assignations dans une boucle non teintée : teintées si leur valeur l'est par les noms de la portée
        for _ in range(2):
            motif = _motif_noms(noms)
            for n in noeuds:
                if isinstance(n, (ast.Assign, ast.AnnAssign)) and n.value is not None and id(n) in dans_boucle and id(n) not in extras \
                        and self._source_teintee(self.seg(n.value), motif):
                    noms |= self._cibles(n)
        return noms, extras

    def propager_appels(self, portee, noms, extras):
        """Paramètres teintés des fonctions du module appelées avec un chemin teinté (jamais avec un contenu lu)."""
        change = False
        motifs = {}
        for n in self._noeuds(portee):
            if not isinstance(n, ast.Call):
                continue
            f = n.func
            nom_f = f.id if isinstance(f, ast.Name) else (f.attr if isinstance(f, ast.Attribute) and self.seg(f.value) in ('self', 'cls') else '')
            if nom_f not in self.fonctions:
                continue
            cle = frozenset(extras.get(id(n), ()))
            if cle not in motifs:
                motifs[cle] = _motif_noms(noms | cle)
            motif = motifs[cle]
            for definition in self.fonctions[nom_f]:
                params = [a.arg for a in definition.args.posonlyargs + definition.args.args]
                if params and params[0] in ('self', 'cls') and isinstance(f, ast.Attribute):
                    params = params[1:]
                paires = [(params[i], a) for i, a in enumerate(n.args) if i < len(params)] + [(k.arg, k.value) for k in n.keywords if k.arg in params]
                for param, arg in paires:
                    texte = self.seg(arg)
                    if not CONTENU.search(texte) and self.teinte(texte, motif) and param not in self.parametres.get(nom_f, set()):
                        self.parametres.setdefault(nom_f, set()).add(param)
                        change = True
        return change

    def _portees(self):
        return [self.arbre] + [d for ds in self.fonctions.values() for d in ds]

    def resultats(self):
        for n in ast.walk(self.arbre):
            if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)):
                for r in ast.walk(n):
                    if isinstance(r, ast.Return) and r.value is not None and (LITTERAL.search(self.seg(r.value)) or any(_mot(c, self.seg(r.value)) for c in self.constantes)):
                        self.rendent.add(n.name)
        teintes = {}
        for _ in range(4):                       # point fixe : attributs et paramètres teintés se propagent d'une portée à l'autre
            change = False
            for portee in self._portees():
                avant = (len(self.attributs), sum(len(v) for v in self.parametres.values()))
                teintes[id(portee)] = self.noms_teintes(portee)
                change |= self.propager_appels(portee, *teintes[id(portee)])
                change |= avant != (len(self.attributs), sum(len(v) for v in self.parametres.values()))
            if not change:
                break
        trouve = set()
        for portee in self._portees():
            noms, extras = teintes.get(id(portee)) or self.noms_teintes(portee)
            self._signaler(portee, noms, extras, trouve)
        return sorted(trouve)

    def _signaler(self, portee, noms, extras, trouve):
        motif = _motif_noms(noms)
        noeuds = self._noeuds(portee)
        # filtre par suffixe : dans la branche positive d'un ``if`` qui reconnaît une base, la variable testée désigne une base
        conditionnels = {}
        for n in noeuds:
            if isinstance(n, ast.If):
                test = self.seg(n.test)
                if LITTERAL.search(test) or FILTRES_NOM.search(test):
                    branche = n.orelse if isinstance(n.test, ast.UnaryOp) and isinstance(n.test.op, ast.Not) else n.body
                    for s in branche:
                        for m in ast.walk(s):
                            conditionnels.setdefault(id(m), set()).update(self._noms_de(n.test))
        for n in noeuds:
            if isinstance(n, ast.Call):
                f = n.func
                nom_f = f.attr if isinstance(f, ast.Attribute) else (f.id if isinstance(f, ast.Name) else '')
                recepteur = self.seg(f.value) if isinstance(f, ast.Attribute) else ''
                exprs = []
                if isinstance(f, ast.Name) and nom_f in FONCTIONS:
                    exprs = n.args[:1]
                elif isinstance(f, ast.Attribute) and nom_f in FONCTIONS and nom_f != 'open':
                    exprs = n.args[:1]
                elif isinstance(f, ast.Attribute) and nom_f == 'open' and recepteur in ('io', 'os', 'builtins', 'codecs', 'gzip', 'bz2', 'lzma'):
                    exprs = n.args[:1]
                elif isinstance(f, ast.Attribute) and nom_f in METHODES:
                    exprs = [f.value]
                elif isinstance(f, ast.Attribute) and nom_f == 'write' and re.search(r'(?i)zip|archive|\bzf?\b', recepteur):
                    exprs = n.args[:1]                                   # zipfile.ZipFile.write(chemin)
                elif isinstance(f, ast.Attribute) and nom_f == 'add' and re.search(r'(?i)tar|archive|\bt\b', recepteur):
                    exprs = n.args[:1]                                   # tarfile.TarFile.add(chemin)
                elif SOUS_PROCESSUS.search(self.seg(f)):
                    exprs = list(n.args[:1])                             # cp, rsync, tar, zip… par un sous-processus
                extra = conditionnels.get(id(n), set()) | extras.get(id(n), set())
                motif_n = _motif_noms(noms | extra) if extra else motif
                for x in exprs:
                    texte = self.seg(x)
                    if 'sqlite3.connect' not in texte and self.teinte(texte, motif_n) and not _exempte(self.lignes[n.lineno - 1]):
                        trouve.add((n.lineno, self.seg(n)[:160]))
            if isinstance(n, (ast.With, ast.AsyncWith)):     # with open(X) as f: … f.read(16) == b'SQLite format 3\x00'
                for item in n.items:
                    appel, alias = item.context_expr, item.optional_vars
                    if not (isinstance(appel, ast.Call) and isinstance(alias, ast.Name)):
                        continue
                    f = appel.func
                    if not ((isinstance(f, ast.Name) and f.id == 'open') or (isinstance(f, ast.Attribute) and f.attr == 'open')):
                        continue
                    for m in ast.walk(n):
                        if isinstance(m, ast.Compare) and ENTETE in self.seg(m) and re.search(r'\b%s\.read\(' % re.escape(alias.id), self.seg(m)) \
                                and not _exempte(self.lignes[m.lineno - 1]):
                            trouve.add((m.lineno, 'en-tête lu sur le fichier : ' + self.seg(m)[:140]))


def _mot(nom, texte):
    return re.search(r'(?<![\w.])%s\b' % re.escape(nom), texte) is not None


def ouvertures_directes(source, nom='<source>'):
    """[(ligne, extrait)] des ouvertures directes d'une base dans ``source``."""
    try:
        return _Analyse(source).resultats()
    except SyntaxError:
        return []


def analyser(racine, fichiers):
    racine = Path(racine)
    out = []
    for chemin in fichiers:
        rel = Path(chemin).resolve().relative_to(racine.resolve()).as_posix()
        for ligne, extrait in ouvertures_directes(Path(chemin).read_text(encoding='utf-8', errors='replace'), rel):
            out.append('%s:%d: %s' % (rel, ligne, extrait))
    return out


def fichiers_du_produit(racine):
    """Produit livré : ``agent/`` (hors bibliothèques embarquées), scripts de la racine (dont upgrade.py, installer.py, miseajour.py),
    ``deploy/`` (veille de sécurité, poste, Debian) et ``scripts/`` (exploitation, essais, construction)."""
    racine = Path(racine)
    agent = [p for p in sorted((racine / 'agent').rglob('*.py')) if '_vendor' not in p.parts]
    autres = [p for d in ('deploy', 'scripts') if (racine / d).is_dir() for p in sorted((racine / d).rglob('*.py')) if '_vendor' not in p.parts]
    return agent + scripts_de_la_racine(racine) + autres


def scripts_de_la_racine(racine):
    return sorted(p for p in Path(racine).glob('*.py') if p.is_file())


if __name__ == '__main__':
    import sys
    racine = Path(sys.argv[1] if len(sys.argv) > 1 else Path(__file__).resolve().parents[1])
    tous = [p for p in sorted(racine.rglob('*.py')) if not {'_vendor', '.git', 'node_modules'} & set(p.parts)]
    print('\n'.join(analyser(racine, tous)) or 'aucune ouverture directe')
