"""Primitive PDF du navigateur existant ; aucun nouveau navigateur ou réseau.

Le service appelant doit vérifier autorisation, page, compte, absence de saisie
secrète et identité de la page avant et après l'impression. Le contrôle fourni
doit propager pause/annulation/révocation ; pas de callback facultatif.
"""
import base64
import binascii
import time
from .common import Stop


def imprimer_pdf(cdp, session, controle, max_octets=32*1024*1024, delai=60):
    if (not callable(controle) or type(max_octets) is not int or not 1 <= max_octets <= 64*1024*1024
            or type(delai) not in (int,float) or not 1 <= delai <= 120):
        raise Stop('parametres_pdf_invalides')
    controle()
    limite = time.monotonic()+delai
    def reste():
        controle()
        n = limite-time.monotonic()
        if n <= 0:
            raise Stop('conversion_pdf_hors_budget')
        return min(n, 20)
    result = cdp.send('Page.printToPDF', {'printBackground':True,'preferCSSPageSize':True,
                        'transferMode':'ReturnAsStream'}, session=session, timeout=reste())
    handle = result.get('stream')
    if not handle:
        raise Stop('impression_pdf_flux_indisponible')
    chunks, total, empty = [], 0, 0
    try:
        while True:
            size = min(65536,max_octets-total+1)
            item = cdp.send('IO.read', {'handle':handle,'size':size}, session=session, timeout=reste())
            text = item.get('data')
            if not isinstance(text,str) or len(text)>size*2+16:
                raise Stop('flux_pdf_invalide')
            try:
                part = base64.b64decode(text,validate=True) if item.get('base64Encoded') else text.encode('utf-8')
            except (binascii.Error,ValueError):
                raise Stop('flux_pdf_invalide') from None
            if len(part)>size:
                raise Stop('flux_pdf_invalide')
            total += len(part)
            if total>max_octets:
                raise Stop('pdf_trop_grand')
            chunks.append(part)
            empty = empty+1 if not part else 0
            if empty>3 and not item.get('eof'):
                raise Stop('flux_pdf_sans_progression')
            if item.get('eof') is True:
                break
    finally:
        try:
            cdp.send('IO.close', {'handle':handle}, session=session, timeout=3)
        except Exception:
            pass  # Nettoyage seulement ; ne masque pas l'arrêt ou l'erreur initiale.
    controle()
    body=b''.join(chunks)
    if not body.startswith(b'%PDF-') or b'%%EOF' not in body[-2048:]:
        raise Stop('pdf_incomplet_ou_invalide')
    return body
