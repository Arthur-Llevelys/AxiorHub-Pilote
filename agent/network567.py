"""Adresse exacte du proxy : passerelle Docker explicitement demandée, jamais '*'."""
import ipaddress
import socket
import struct
from pathlib import Path


def trusted_proxy(value, *, container=None, routes=None):
    if value == '*':
        raise RuntimeError('Un proxy exact est requis ; * est interdit.')
    if value != 'auto-gateway':
        if not value or not str(value).strip():
            raise RuntimeError('Adresse du proxy absente.')
        return str(value)
    if container is None:
        container = Path('/.dockerenv').exists() or Path('/run/.containerenv').exists()
    if not container:
        raise RuntimeError('auto-gateway est réservé au conteneur ; indiquez l’adresse de votre proxy.')
    if routes is None:
        routes = Path('/proc/net/route').read_text()
    gateways = []
    for line in routes.splitlines()[1:]:
        parts = line.split()
        if len(parts) >= 8 and parts[1] == '00000000' and int(parts[3],16) & 2:
            address = socket.inet_ntoa(struct.pack('<I', int(parts[2],16)))
            ip = ipaddress.ip_address(address)
            if not ip.is_unspecified and not ip.is_multicast:
                gateways.append(address)
    if len(set(gateways)) != 1:
        raise RuntimeError('Passerelle Docker non unique ; renseignez AXIORHUB_TRUSTED_PROXY explicitement.')
    return gateways[0]
