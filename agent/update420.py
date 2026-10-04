"""Runtime policy for signed AxiorHub updates (no secret is stored here)."""
from urllib.parse import urlsplit

from .common import Stop


SETTING = 'updates:policy420'


def policy(desk):
    configured=dict(desk.c.get('updates',{}))
    saved=desk.settings(SETTING,{})
    if isinstance(saved,dict):configured.update(saved)
    configured['channel']=configured.get('channel') if configured.get('channel') in ('stable','test') else 'stable'
    configured['require_signature']=True
    configured['minisign_public_key_file']=str(
      configured.get('minisign_public_key_file') or '/etc/axiorhub-mail-agent/update-minisign.pub')
    return configured


def save_policy(desk,channel,metadata_url):
    channel=str(channel or '')
    if channel not in ('stable','test'):raise Stop('canal_mise_a_jour_invalide')
    metadata_url=str(metadata_url or '').strip()
    if metadata_url:
        parsed=urlsplit(metadata_url)
        if (parsed.scheme!='https' or not parsed.hostname or parsed.username or parsed.password
              or parsed.hostname in ('localhost','127.0.0.1','::1')):
            raise Stop('url_mise_a_jour_invalide')
    value={'channel':channel,'metadata_url':metadata_url,'require_signature':True}
    desk.setting(SETTING,value)
    desk.audit('update_policy_saved',{'channel':channel,'metadata_configured':bool(metadata_url)})
    return value
