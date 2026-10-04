"""Identité du cabinet (AxiorHub 5.6.0) : une seule source, le profil de l'avocat (saisi dans l'assistant d'installation ou dans
Pièces et bordereaux). Aucune identité n'est écrite dans le code : signature et consignes données à l'IA en sont tirées."""


def profile(desk):
    try:
        from .pieces510 import profile as p
        return p(desk)
    except Exception:
        return {}


def identity(desk):
    p = profile(desk)
    first, last = p.get('prenom_avocat', '').strip(), p.get('nom_avocat', '').strip()
    name = ' '.join(x for x in (first, last) if x)
    city = p.get('ville_avocat', '').strip()
    specialty = str(desk.settings('cabinet560:specialite', '') or '').strip()
    lines = [name] if name else []
    if city:
        lines.append('Avocat au Barreau de %s' % city)
    if p.get('adresse_cabinet_avocat'):
        lines.append(p['adresse_cabinet_avocat'].strip())
    contact = ' – '.join(x for x in (('Tél. : ' + p['numero_telephone_avocat'].strip()) if p.get('numero_telephone_avocat') else '',
                                    p.get('email_avocat', '').strip()) if x)
    if contact:
        lines.append(contact)
    return {'name': name, 'title': ('Maître ' + name) if name else 'l’avocat du cabinet', 'city': city, 'specialty': specialty,
            'signature': '\n'.join(lines), 'configured': bool(name and city)}


def writer_intro(desk):
    """Début de consigne : « Tu rédiges pour Maître X, avocat au Barreau de Y » (ou une formule neutre si le profil est vide)."""
    i = identity(desk)
    who = i['title'] + (', avocat' + ((' en ' + i['specialty']) if i['specialty'] else '') + ((' au Barreau de ' + i['city']) if i['city'] else '') if i['name'] else '')
    return who
