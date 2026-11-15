#!/usr/bin/env python3
"""5.6.101 (lot 0, T03) : rejoue des modules de tests avec une horloge simulée, en Python, sans rien télécharger.

Le 14/11/2026, la passe complète a échoué parce qu'une audience d'essai datée du 12/11/2026 était devenue passée ; la recette GitHub
tourne en heure universelle et un test a déjà dépendu du vendredi. Ce script avance l'horloge vue par les tests pour trouver ces
dates figées avant qu'elles n'échouent d'elles-mêmes :

- ``--decalage-jours N`` : le jour courant plus ``N`` jours (365, 730…), à l'heure courante ;
- ``--heure-paris HH:MM`` : la même journée (éventuellement décalée), à cette heure de Paris (23:30 : veille de bascule de date) ;
- ``--vendredi-soir`` : le prochain vendredi (aujourd'hui s'il l'est), à 21:00 heure de Paris sauf ``--heure-paris``.

L'horloge n'est pas figée : elle avance normalement à partir de l'instant simulé (les délais, attentes et durées restent justes).

Mécanisme : avant l'import du moindre test, ``datetime.datetime`` et ``datetime.date`` sont remplacés par des sous-classes dont
``now``, ``today`` et ``utcnow`` lisent l'horloge décalée (``isinstance`` reste vrai pour les objets des vraies classes), et
``time.time``, ``time.time_ns``, ``time.localtime``, ``time.gmtime``, ``time.ctime``, ``time.asctime`` et ``time.strftime`` (sans
argument) sont décalés de la même quantité. ``time.monotonic`` et ``time.perf_counter`` ne bougent pas (ce sont des durées).
Les adaptateurs ``sqlite3`` des dates sont déclarés pour les sous-classes. Les dates des fichiers suivent la même horloge :
``os.stat``, ``os.lstat``, ``os.fstat`` et ``DirEntry.stat`` (via ``os.scandir``) ajoutent le décalage, ``os.utime`` avec des dates
explicites le retire ; sans cela un fichier créé à l'instant paraîtrait vieux de N jours et tout verrou « abandonné depuis 60 s »
serait cassé, ce qui n'arrive jamais sur une vraie machine.

Limites (déclarées, jamais contournées en silence) :
- horloges lues en C sans passer par ces fonctions : SQLite (``CURRENT_TIMESTAMP``, ``datetime('now')``), certificats TLS,
  horloge d'OpenSSL, dates de fichiers lues par une bibliothèque C ; ``isinstance(entree, os.DirEntry)`` est faux pour les entrées
  rendues par ``os.scandir`` (enveloppe Python) ;
- processus enfants (``subprocess``) : ils voient l'horloge réelle ;
- un module qui a déjà importé ``datetime`` avant le remplacement garde les vraies classes : le script remplace avant tout import
  de test, mais une bibliothèque importée par le script lui-même n'est pas concernée ;
- le fuseau de la machine n'est pas changé : sous GitHub il est UTC, sur le poste du cabinet il est celui de Windows.

Usage :
``python scripts/tests-horloge.py [--decalage-jours N] [--heure-paris HH:MM] [--vendredi-soir] [-v] [--pile-si-bloque S] [module ...]``
Sans module, la liste ``MODULES_DATES`` (annexe B du cahier et modules corrigés en 5.6.101) est jouée. Code de sortie 0 si tout
passe, 1 sinon. Les chemins ``<racine>`` et ``<racine>/tests`` sont ajoutés au chemin d'import (comme ``PYTHONPATH`` de la recette).
"""
import argparse
import datetime as _datetime
import os
import sys
import time as _time
import unittest

RACINE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# Annexe B du cahier 5.6.101 (relevé du 14/11/2026), puis modules révélés par les cinq horloges.
MODULES_DATES = (
    'test_document_agents568', 'test_v440_notices', 'test_v450_deadlines', 'test_v460', 'test_v490', 'test_v500',
    'test_v56100_dossiers', 'test_v56100_ia', 'test_v56100_integrite', 'test_v56100_produire', 'test_v5636', 'test_v5637',
    'test_v5639', 'test_v5652', 'test_v5666', 'test_v5667', 'test_v5676', 'test_v5684', 'test_v5688_cloud', 'test_v5688_crm',
    'test_v5688_facturation', 'test_v5688_societe', 'test_v5689_cabinet', 'test_v5689_dossier', 'test_v5689_gouvernance',
    'test_v5689_livrables', 'test_v5689_qualif', 'test_v5693_operations', 'test_v5695_evaluation', 'test_v5695_lecture',
    # 5.6.101 : modules corrigés après les horloges (dates figées comparées au jour : seuil de mois, échéance, purge à 90 jours).
    'test_v5689_contentieux', 'test_workspace',
)

HEURE_VENDREDI = (21, 0)

_vrai_time = _time.time
_vrai_time_ns = _time.time_ns
_vrai_localtime = _time.localtime
_vrai_gmtime = _time.gmtime
_vrai_ctime = _time.ctime
_vrai_asctime = _time.asctime
_vrai_strftime = _time.strftime
_VraiDatetime = _datetime.datetime
_VraiDate = _datetime.date

_DECALAGE = {'s': 0.0, 'ns': 0}


def _maintenant():
    return _vrai_time() + _DECALAGE['s']


class _Meta(type):
    """``isinstance(objet_de_la_vraie_classe, date)`` doit rester vrai après le remplacement (dates venues de C, de sqlite3…)."""

    def __instancecheck__(cls, obj):
        reel = cls.__dict__.get('_reel')
        return isinstance(obj, reel) if reel is not None else type.__instancecheck__(cls, obj)

    def __subclasscheck__(cls, sub):
        reel = cls.__dict__.get('_reel')
        return issubclass(sub, reel) if reel is not None else type.__subclasscheck__(cls, sub)


class _Date(_VraiDate, metaclass=_Meta):
    _reel = _VraiDate

    @classmethod
    def today(cls):
        t = _VraiDatetime.fromtimestamp(_maintenant())
        return cls(t.year, t.month, t.day)


class _Datetime(_VraiDatetime, metaclass=_Meta):
    _reel = _VraiDatetime

    @classmethod
    def now(cls, tz=None):
        return cls.fromtimestamp(_maintenant(), tz)

    @classmethod
    def today(cls):
        return cls.fromtimestamp(_maintenant())

    @classmethod
    def utcnow(cls):
        return cls.fromtimestamp(_maintenant(), _datetime.timezone.utc).replace(tzinfo=None)

    def date(self):
        return _Date(self.year, self.month, self.day)


# Même nom que les vraies classes : pickle, repr et messages d'erreur restent lisibles et cohérents.
for _cls, _nom in ((_Date, 'date'), (_Datetime, 'datetime')):
    _cls.__name__ = _cls.__qualname__ = _nom
    _cls.__module__ = 'datetime'


def _localtime(secs=None):
    return _vrai_localtime(_maintenant() if secs is None else secs)


def _gmtime(secs=None):
    return _vrai_gmtime(_maintenant() if secs is None else secs)


def _ctime(secs=None):
    return _vrai_ctime(_maintenant() if secs is None else secs)


def _asctime(t=None):
    return _vrai_asctime(_localtime() if t is None else t)


def _strftime(fmt, t=None):
    return _vrai_strftime(fmt, _localtime() if t is None else t)


_vrai_stat = os.stat
_vrai_lstat = os.lstat
_vrai_fstat = os.fstat
_vrai_utime = os.utime
_vrai_scandir = os.scandir
_CHAMPS_DATES = ('st_atime', 'st_mtime', 'st_ctime', 'st_birthtime')


def _stat_decale(st):
    """Copie de ``st`` dont les dates (secondes entières, flottantes et nanosecondes) sont décalées comme l'horloge."""
    if not _DECALAGE['s']:
        return st
    cls, (visibles, extra) = st.__reduce__()
    visibles = list(visibles)
    for i in (7, 8, 9):   # st_atime, st_mtime, st_ctime entiers (champs visibles du tuple)
        visibles[i] = int(visibles[i] + _DECALAGE['s'])
    extra = dict(extra)
    for nom in _CHAMPS_DATES:
        if extra.get(nom) is not None:
            extra[nom] = extra[nom] + _DECALAGE['s']
        if extra.get(nom + '_ns') is not None:
            extra[nom + '_ns'] = extra[nom + '_ns'] + _DECALAGE['ns']
    return cls(visibles, extra)


def _stat(*a, **k):
    return _stat_decale(_vrai_stat(*a, **k))


def _lstat(*a, **k):
    return _stat_decale(_vrai_lstat(*a, **k))


def _fstat(*a, **k):
    return _stat_decale(_vrai_fstat(*a, **k))


def _utime(path, times=None, *, ns=None, **k):
    if times is not None:
        times = (times[0] - _DECALAGE['s'], times[1] - _DECALAGE['s'])
    if ns is not None:
        ns = (ns[0] - _DECALAGE['ns'], ns[1] - _DECALAGE['ns'])
    if ns is not None:
        return _vrai_utime(path, times, ns=ns, **k) if times is not None else _vrai_utime(path, ns=ns, **k)
    return _vrai_utime(path, times, **k)


class _Entree:
    """Enveloppe d'``os.DirEntry`` dont ``stat()`` suit l'horloge décalée ; le reste est délégué."""
    __slots__ = ('_e',)

    def __init__(self, e):
        self._e = e

    def stat(self, *, follow_symlinks=True):
        return _stat_decale(self._e.stat(follow_symlinks=follow_symlinks))

    def __getattr__(self, nom):
        return getattr(self._e, nom)

    def __fspath__(self):
        return self._e.__fspath__()

    def __repr__(self):
        return repr(self._e)


class _Scandir:
    def __init__(self, it):
        self._it = it

    def __iter__(self):
        return self

    def __next__(self):
        return _Entree(next(self._it))

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return self._it.__exit__(*exc)

    def close(self):
        self._it.close()


def _scandir(*a, **k):
    return _Scandir(_vrai_scandir(*a, **k))


def instant_simule(decalage_jours=0, heure_paris=None, vendredi_soir=False, reel=None):
    """Instant simulé (aware, Europe/Paris) à partir de l'instant réel ``reel`` (secondes, défaut : maintenant)."""
    from zoneinfo import ZoneInfo
    paris = ZoneInfo('Europe/Paris')
    reel = _vrai_time() if reel is None else reel
    cible = _VraiDatetime.fromtimestamp(reel, paris) + _datetime.timedelta(days=decalage_jours)
    if vendredi_soir:
        cible = cible + _datetime.timedelta(days=(4 - cible.weekday()) % 7)
        if heure_paris is None:
            heure_paris = HEURE_VENDREDI
    if heure_paris is not None:
        # Heure murale de Paris ; l'arithmétique en heure murale de zoneinfo garde l'heure à travers un changement d'heure.
        cible = cible.replace(hour=heure_paris[0], minute=heure_paris[1], second=0, microsecond=0)
    return cible


def installer_horloge(decalage_s):
    """Remplace les horloges Python par des versions décalées de ``decalage_s`` secondes (avant tout import de test)."""
    _DECALAGE['s'] = float(decalage_s)
    _DECALAGE['ns'] = int(round(decalage_s * 1e9))
    _time.time = _maintenant
    _time.time_ns = lambda: _vrai_time_ns() + _DECALAGE['ns']
    _time.localtime = _localtime
    _time.gmtime = _gmtime
    _time.ctime = _ctime
    _time.asctime = _asctime
    _time.strftime = _strftime
    _datetime.date = _Date
    _datetime.datetime = _Datetime
    os.stat, os.lstat, os.fstat, os.utime, os.scandir = _stat, _lstat, _fstat, _utime, _scandir
    try:
        import sqlite3
        sqlite3.register_adapter(_Date, lambda v: v.isoformat())
        sqlite3.register_adapter(_Datetime, lambda v: v.isoformat(' '))
    except Exception:   # noqa: BLE001 — sqlite3 absent : rien à adapter
        pass


def _heure(texte):
    try:
        h, m = texte.split(':')
        h, m = int(h), int(m)
        if not (0 <= h < 24 and 0 <= m < 60):
            raise ValueError
        return h, m
    except ValueError:
        raise argparse.ArgumentTypeError('heure attendue au format HH:MM (00:00 à 23:59)')


JOURS = ('lundi', 'mardi', 'mercredi', 'jeudi', 'vendredi', 'samedi', 'dimanche')


def main(argv=None):
    p = argparse.ArgumentParser(description="Tests avec une horloge simulée (lot 0, 5.6.101).")
    p.add_argument('--decalage-jours', type=int, default=0, help='jours ajoutés au jour courant (ex. 365, 730)')
    p.add_argument('--heure-paris', type=_heure, default=None, help='heure de Paris HH:MM (ex. 23:30)')
    p.add_argument('--vendredi-soir', action='store_true', help='prochain vendredi, 21:00 heure de Paris')
    p.add_argument('--liste', action='store_true', help='affiche la liste des modules par défaut et sort')
    p.add_argument('-v', '--verbeux', action='store_true', help='un test par ligne')
    p.add_argument('--pile-si-bloque', type=int, default=0, metavar='S',
                   help='écrit la pile de chaque fil toutes les S secondes (test bloqué), 0 : jamais')
    p.add_argument('modules', nargs='*', help='modules de tests (défaut : MODULES_DATES)')
    a = p.parse_args(argv)
    if a.liste:
        print('\n'.join(MODULES_DATES))
        return 0
    modules = a.modules or list(MODULES_DATES)
    reel = _vrai_time()
    cible = instant_simule(a.decalage_jours, a.heure_paris, a.vendredi_soir, reel)
    decalage = cible.timestamp() - reel
    installer_horloge(decalage)
    for chemin in (os.path.join(RACINE, 'tests'), RACINE):
        if chemin not in sys.path:
            sys.path.insert(0, chemin)
    utc = cible.astimezone(_datetime.timezone.utc)
    print('Horloge simulée : %s %s (Europe/Paris), soit %s UTC ; décalage %+.2f jours ; %d module(s).'
          % (JOURS[cible.weekday()], cible.strftime('%d/%m/%Y %H:%M'), utc.strftime('%d/%m/%Y %H:%M'),
             decalage / 86400.0, len(modules)), file=sys.stderr, flush=True)
    if a.pile_si_bloque > 0:
        import faulthandler
        faulthandler.dump_traceback_later(a.pile_si_bloque, repeat=True, file=sys.stderr)
    prog = unittest.main(module=None, argv=['tests-horloge'] + (['-v'] if a.verbeux else []) + modules, exit=False)
    r = prog.result
    print('Horloge %s %s : %d tests, %d échecs, %d erreurs, %d ignorés.'
          % (JOURS[cible.weekday()], cible.strftime('%d/%m/%Y %H:%M'), r.testsRun, len(r.failures), len(r.errors),
             len(r.skipped)), file=sys.stderr, flush=True)
    return 0 if r.wasSuccessful() else 1


if __name__ == '__main__':
    sys.exit(main())
