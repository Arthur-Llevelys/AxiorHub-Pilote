# Contribuer à AxiorHub Pilote

Le code est distribué sous licence GNU AGPL version 3 ou ultérieure. Les contributions doivent pouvoir être
redistribuées sous `AGPL-3.0-or-later`. Le nom, les marques et le logo restent régis séparément par `TRADEMARKS.md` et
`LOGO-LICENSE.md`. La mention « AxiorHub Pilote — créé par Timo RAINIO » (`NOTICE`, page « À propos », pied du menu) ne doit
pas être retirée.

## Aucune donnée réelle

Les contributions restent génériques : aucun dossier client, nom de partie, courriel, téléphone, identifiant, mot de
passe, jeton, clé d'API, nom de domaine privé ou chemin propre à une installation. Dans les tests et les exemples,
utilisez `example.test`, « SAS EXEMPLE », « Me Exemple », des numéros en `06 00 00 00 xx`.

Si vous travaillez à partir d'une installation réelle, créez à la racine un fichier `.privacy-denylist` (ignoré par Git)
listant les noms de vos clients et adversaires, un par ligne : le contrôle de confidentialité vérifiera qu'aucun n'a été
copié dans le code.

## Avant toute proposition

```bash
python3 scripts/privacy-scan.py
python3 -m unittest discover -s tests -p 'test_*.py' -q
python3 -m py_compile agent/*.py standalone.py docker/bootstrap.py
python3 scripts/build-css.py          # si une feuille de style a changé
```

Les changements qui touchent une action externe doivent conserver la confirmation explicite et la traçabilité.
