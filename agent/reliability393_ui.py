"""Single operational status page for AxiorHub 3.9.3."""
from html import escape as e
import json

from .reliability393 import status_snapshot


STATUS = {
  'verified': ('Vérifié', 'ok'),
  'warning': ('À contrôler', 'warning'),
  'error': ('Incident', 'error'),
  'unknown': ('Non vérifié', 'unknown'),
}


def _badge(status):
    label,css=STATUS.get(status,(status,'unknown'))
    return '<span class="rel-badge rel-'+css+'">'+e(label)+'</span>'


def _stamp(value):
    return e(str(value or 'jamais').replace('T',' ')[:19])


def _reason(code, reasons):
    return reasons.get(str(code),str(code or 'Erreur sans motif exploitable.'))


PERMISSION_REASONS={'fichier_absent':'fichier absent',
  'type_ou_permissions_inadaptees':'droits trop ouverts ou type inattendu (conseillé : sudo chmod 600, propriétaire root, groupe du service)',
  'permission_stat_refusee':'le service ne peut pas lire les droits de ce fichier'}


def _detail(desk, key, current, reasons):
    """5.6.2 : ce qui est en cause sous une carte en incident (chemins et motifs, jamais le contenu des fichiers)."""
    items=[]
    if key=='permissions':
        for f in (current.get('files') or []):
            if f.get('status') not in ('verified','optional'):
                items.append('<code>'+e(f.get('path',''))+'</code> — '+e(PERMISSION_REASONS.get(f.get('reason'),f.get('reason') or ''))+
                             (' (droits '+e(f['mode'])+')' if f.get('mode') else ''))
    elif key=='nextcloud_outputs':
        evidence=current.get('evidence') or {}
        for code in (evidence.get('errors') or [])[:5]:
            items.append('Erreur Nextcloud : '+e(_reason(code,reasons)))
        try:
            rows=desk.db.execute("SELECT target_path,status FROM reliability_verifications_v393 WHERE target_kind='nextcloud_file' "
                                 "AND status IN ('missing','error') ORDER BY checked DESC LIMIT 8").fetchall()
        except Exception:
            rows=[]
        for r in rows:
            items.append('<code>'+e(r['target_path'])+'</code> — '+('absent de Nextcloud (déplacé, renommé ou supprimé)' if r['status']=='missing' else 'dossier illisible'))
    if not items:return ''
    return '<details class="rel-detail"><summary>Voir le détail</summary><ul>'+''.join('<li>'+x+'</li>' for x in items[:10])+'</ul></details>'


def page(desk, args, form, link, reasons, job_labels):
    info=status_snapshot(desk);jobs=info['jobs'];checks={x['check_key']:x for x in info['checks']}
    cards=[]
    for key,label,current in (
      ('services','Services',info['services']),('permissions','Permissions et secrets',info['permissions']),
      ('jobs','File de traitements',jobs),('index','Index documentaire',info['index']),
      ('ollama','Ollama',checks.get('ollama')),('imap_drafts','Brouillons IMAP',checks.get('imap_drafts')),
      ('nextcloud_outputs','Fichiers Nextcloud',checks.get('nextcloud_outputs')),
      ('openrouter','OpenRouter',checks.get('openrouter'))):
        if current:
            cards.append('<article class="rel-card">'+_badge(current.get('status','unknown'))+
              '<h3>'+e(label)+'</h3><p>'+e(current.get('summary',''))+'</p>'+
              (_detail(desk,key,current,reasons) if current.get('status') not in ('verified',) else '')+'<small>Contrôlé : '+
              _stamp(current.get('checked_at') or current.get('checked'))+'</small></article>')
        else:
            cards.append('<article class="rel-card">'+_badge('unknown')+'<h3>'+e(label)+
              '</h3><p>Aucun contrôle réel enregistré.</p><small>Contrôlé : jamais</small></article>')
    out='<section class="rel-hero"><div><p class="eyebrow">3.9.3 · EXPLOITATION FIABLE</p><h2>État du système</h2><p>Un voyant vert signifie que le résultat a été relu depuis systemd, IMAP, Nextcloud, l’index ou le fournisseur concerné. Une simple déclaration du producteur ne suffit pas.</p></div><div class="rel-actions">'
    out+=form('run_system_checks393','Contrôler maintenant',{'back':'system'})
    out+=form('test_openrouter393','Tester OpenRouter sans donnée de dossier',{'back':'system'})
    out+='</div></section><div class="rel-grid">'+''.join(cards)+'</div>'

    stale=jobs.get('stale',[]);loops=jobs.get('loops',[])
    out+='<section class="rel-section"><h2>Incidents de traitements</h2>'
    if stale:
        out+='<div class="notice"><strong>Traitements trop anciens</strong><ul>'+''.join(
          '<li>#'+str(x['id'])+' · '+e(job_labels.get(x['kind'],x['kind']))+' · '+
          e(str(x.get('age_hours','?')))+' h · '+e(x['status'])+'</li>' for x in stale)+'</ul></div>'
    if loops:
        out+='<div class="notice"><strong>Boucles détectées sur 24 heures</strong><ul>'+''.join(
          '<li>'+e(job_labels.get(x['kind'],x['kind']))+' · '+str(x['count'])+' échecs · '+
          e(_reason(x.get('error_reason'),reasons))+'</li>' for x in loops)+'</ul></div>'
    if not stale and not loops:out+='<p class="success">Aucun traitement trop ancien ou en boucle dans la fenêtre contrôlée.</p>'
    out+='</section>'

    out+='<section class="rel-section"><h2>Traitements récents</h2><div class="table rel-table"><table><thead><tr><th>Opération</th><th>État</th><th>Âge</th><th>Motif réel</th><th>Action</th></tr></thead><tbody>'
    for item in jobs.get('recent',[])[:50]:
        status=item.get('status','');reason=''
        if status=='error':reason=_reason(item.get('error_reason'),reasons)
        elif item.get('stale'):reason='Le traitement dépasse le délai de surveillance configuré.'
        action=''
        if status=='error':action=form('retry_job393','Relancer',{'job':item['id'],'back':'system'})
        elif status in ('pending','running','cancel_requested'):
            action=form('cancel_job','Annuler',{'job':item['id'],'back':'system'})
        out+='<tr><td><strong>#'+str(item['id'])+'</strong><br>'+e(job_labels.get(item['kind'],item['kind']))+'</td><td>'+e(status)+'</td><td>'+e(str(item.get('age_hours','?')))+' h</td><td>'+e(reason or '—')+'</td><td>'+action+'</td></tr>'
    out+='</tbody></table></div></section>'

    out+='<section class="rel-section"><h2>Preuves de destination</h2><p>Les contrôles IMAP et Nextcloud ne relisent ni n’affichent le contenu : ils vérifient l’identifiant AxiorHub, le drapeau Brouillon, le chemin, la taille et l’empreinte de l’ETag.</p>'
    rows=desk.db.execute('SELECT * FROM reliability_verifications_v393 ORDER BY checked DESC LIMIT 100').fetchall()
    if rows:
        out+='<div class="table rel-table"><table><thead><tr><th>Destination</th><th>Référence</th><th>État</th><th>Relu</th></tr></thead><tbody>'
        for row in rows:
            target=(row['target_path'] or row['target_id'])
            out+='<tr><td>'+e('IMAP' if row['target_kind']=='imap_draft' else 'Nextcloud')+'</td><td>'+e(target)+'</td><td>'+_badge(row['status'])+'</td><td>'+_stamp(row['checked'])+'</td></tr>'
        out+='</tbody></table></div>'
    else:out+='<p class="empty">Aucune preuve de destination enregistrée. Lancez « Contrôler maintenant ».</p>'
    out+='</section>'

    out+='<section class="rel-section"><h2>Services et permissions</h2><div class="rel-two">'
    out+='<div><h3>Services</h3><ul class="rel-list">'+''.join('<li>'+_badge('verified' if x['verified'] else 'error')+' <code>'+e(x['unit'])+'</code> · '+e(x['state'])+'</li>' for x in info['services'].get('services',[]))+'</ul></div>'
    out+='<div><h3>Fichiers sensibles</h3><ul class="rel-list">'+''.join('<li>'+_badge(x['status'])+' <code>'+e(x['path'])+'</code> · '+e(x.get('mode','—'))+'</li>' for x in info['permissions'].get('files',[]))+'</ul></div></div></section>'

    index=info['index'].get('evidence',{});ollama=checks.get('ollama',{}).get('evidence',{})
    out+='<section class="rel-section"><h2>Index et modèles</h2><div class="rel-two"><div><h3>Fraîcheur de l’index</h3><dl><dt>Documents</dt><dd>'+str(index.get('documents',0))+'</dd><dt>Fragments</dt><dd>'+str(index.get('chunks',0))+'</dd><dt>Dernière indexation</dt><dd>'+e(str(index.get('last_indexed_at') or 'inconnue'))+'</dd><dt>Inventaires incomplets</dt><dd>'+str(index.get('incomplete_inventories',0))+'</dd></dl></div><div><h3>Ollama</h3><dl><dt>Modèles sélectionnés</dt><dd>'+e(', '.join(ollama.get('selected_models',[])) or 'Non contrôlé')+'</dd><dt>Installés</dt><dd>'+e(', '.join(ollama.get('available_models',[])) or 'Non contrôlé')+'</dd><dt>Actuellement en mémoire</dt><dd>'+e(', '.join(ollama.get('loaded_models',[])) or 'Aucun ou non contrôlé')+'</dd></dl></div></div></section>'
    return out
