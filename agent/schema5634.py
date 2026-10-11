"""5.6.34 (B12) : validation JSON Schema côté serveur, stricte et complète pour le sous-ensemble utilisé par AxiorHub.

Choix documenté : AxiorHub s'installe hors ligne (paquet Debian, poste Windows empaqueté) sans dépendance Python tierce ; plutôt que
d'embarquer une bibliothèque, ce module met en œuvre COMPLÈTEMENT le sous-ensemble de mots-clés que nos schémas emploient, et REFUSE
tout schéma qui emploierait un autre mot-clé (un schéma futur ne peut donc pas être « validé » par un contrôle partiel silencieux).

Mots-clés pris en charge : type (chaîne ou liste ; ``integer`` refuse flottants et booléens, ``number`` refuse les booléens), enum,
const, properties, required, additionalProperties (booléen ou schéma), items, minItems, maxItems, uniqueItems, minLength, maxLength,
pattern, minimum, maximum, exclusiveMinimum, exclusiveMaximum, anyOf, oneOf, description, title, default, format (annotation).

Plafonds de sécurité (non standard, conservés de la 5.6.33 et documentés) : une chaîne sans maxLength est bornée à 18 000 caractères,
un tableau sans maxItems à 20 éléments, pour qu'une sortie de modèle démesurée ne passe jamais pour un résultat valide.

Chaque erreur porte un chemin précis (``/faits/3/page``) ; ``validate`` lève ``SchemaError`` (une ``Stop('schema_ia_invalide')``) qui
conserve la liste des erreurs pour le diagnostic ; rien n'est converti en résultat réussi.
"""
import re

from .common import Stop

SUPPORTED = {'type', 'enum', 'const', 'properties', 'required', 'additionalProperties', 'items', 'minItems', 'maxItems', 'uniqueItems',
             'minLength', 'maxLength', 'pattern', 'minimum', 'maximum', 'exclusiveMinimum', 'exclusiveMaximum', 'anyOf', 'oneOf',
             'description', 'title', 'default', 'format', '$schema', '$id'}
STRING_CAP = 18000
ARRAY_CAP = 20
_TYPES = {'object', 'array', 'string', 'integer', 'number', 'boolean', 'null'}


class SchemaError(Stop):
    def __init__(self, errors, code='schema_ia_invalide'):
        super().__init__(code)
        self.errors = list(errors)


class UnsupportedSchema(Exception):
    """Erreur de programmation : le schéma emploie un mot-clé que ce validateur ne met pas en œuvre."""


def _is_type(value, typ):
    if typ == 'object':
        return isinstance(value, dict)
    if typ == 'array':
        return isinstance(value, list)
    if typ == 'string':
        return isinstance(value, str)
    if typ == 'integer':
        return type(value) is int                       # ni bool, ni float (3.5 comme 3.0 sont refusés)
    if typ == 'number':
        return type(value) in (int, float) and value == value and value not in (float('inf'), float('-inf'))
    if typ == 'boolean':
        return type(value) is bool
    if typ == 'null':
        return value is None
    raise UnsupportedSchema('type inconnu : %s' % typ)


def check_schema(schema, path='#'):
    """Refuse un schéma qui sortirait du sous-ensemble mis en œuvre (contrôlé une fois, dans les tests)."""
    if not isinstance(schema, dict):
        raise UnsupportedSchema('%s : schéma non objet' % path)
    unknown = set(schema) - SUPPORTED
    if unknown:
        raise UnsupportedSchema('%s : mots-clés non pris en charge %s' % (path, sorted(unknown)))
    types = schema.get('type')
    for t in ([types] if isinstance(types, str) else (types or [])):
        if t not in _TYPES:
            raise UnsupportedSchema('%s : type %s' % (path, t))
    for key, sub in (schema.get('properties') or {}).items():
        check_schema(sub, path + '/properties/' + key)
    if isinstance(schema.get('additionalProperties'), dict):
        check_schema(schema['additionalProperties'], path + '/additionalProperties')
    if 'items' in schema:
        check_schema(schema['items'], path + '/items')
    for key in ('anyOf', 'oneOf'):
        for i, sub in enumerate(schema.get(key) or []):
            check_schema(sub, '%s/%s/%d' % (path, key, i))
    missing = set(schema.get('required') or []) - set(schema.get('properties') or {}) if schema.get('additionalProperties') is False else set()
    if missing:
        raise UnsupportedSchema('%s : champs requis absents de properties %s' % (path, sorted(missing)))


def errors(value, schema, path=''):
    """Liste des erreurs (chemin, code) ; vide si la valeur est conforme."""
    out = []
    types = schema.get('type')
    if types is not None:
        allowed = [types] if isinstance(types, str) else list(types)
        if not any(_is_type(value, t) for t in allowed):
            return [(path or '/', 'type_attendu_' + '_ou_'.join(allowed))]
    if 'const' in schema and value != schema['const']:
        out.append((path or '/', 'valeur_imposee'))
    if 'enum' in schema and value not in schema['enum']:
        out.append((path or '/', 'valeur_hors_liste'))
    if isinstance(value, str):
        limit = schema.get('maxLength', STRING_CAP)
        if len(value) > limit:
            out.append((path or '/', 'texte_trop_long_%d' % limit))
        if len(value) < schema.get('minLength', 0):
            out.append((path or '/', 'texte_trop_court'))
        if 'pattern' in schema and not re.search(schema['pattern'], value):
            out.append((path or '/', 'format_invalide'))
    if type(value) in (int, float) and type(value) is not bool:
        if 'minimum' in schema and value < schema['minimum']:
            out.append((path or '/', 'inferieur_au_minimum'))
        if 'maximum' in schema and value > schema['maximum']:
            out.append((path or '/', 'superieur_au_maximum'))
        if 'exclusiveMinimum' in schema and value <= schema['exclusiveMinimum']:
            out.append((path or '/', 'inferieur_au_minimum_strict'))
        if 'exclusiveMaximum' in schema and value >= schema['exclusiveMaximum']:
            out.append((path or '/', 'superieur_au_maximum_strict'))
    if isinstance(value, list):
        limit = schema.get('maxItems', ARRAY_CAP)
        if len(value) > limit:
            out.append((path or '/', 'trop_d_elements_%d' % limit))
        if len(value) < schema.get('minItems', 0):
            out.append((path or '/', 'pas_assez_d_elements'))
        if schema.get('uniqueItems'):
            seen = []
            for item in value:
                if item in seen:
                    out.append((path or '/', 'elements_en_double'))
                    break
                seen.append(item)
        if 'items' in schema:
            for i, item in enumerate(value):
                out += errors(item, schema['items'], '%s/%d' % (path, i))
    if isinstance(value, dict):
        props = schema.get('properties') or {}
        for key in schema.get('required') or []:
            if key not in value:
                out.append(('%s/%s' % (path, key), 'champ_requis_absent'))
        extra = schema.get('additionalProperties', True)
        for key, item in value.items():
            if key in props:
                out += errors(item, props[key], '%s/%s' % (path, key))
            elif extra is False:
                out.append(('%s/%s' % (path, key), 'champ_non_autorise'))
            elif isinstance(extra, dict):
                out += errors(item, extra, '%s/%s' % (path, key))
    if 'anyOf' in schema and not any(not errors(value, sub, path) for sub in schema['anyOf']):
        out.append((path or '/', 'aucune_variante_conforme'))
    if 'oneOf' in schema and sum(1 for sub in schema['oneOf'] if not errors(value, sub, path)) != 1:
        out.append((path or '/', 'une_seule_variante_attendue'))
    return out


def validate(value, schema, code='schema_ia_invalide'):
    found = errors(value, schema)
    if found:
        raise SchemaError(found, code)
    return value


def describe(found, limit=6):
    """Résumé lisible et sans contenu des erreurs (chemins et motifs seulement), pour un journal ou un bilan."""
    return ' ; '.join('%s : %s' % (p, c) for p, c in list(found)[:limit]) + (' …' if len(found) > limit else '')
