"""Private server-rendered Word model and letter workbench."""
from datetime import date
from html import escape
import json
from urllib.parse import urlencode

from .cabinet_docs33 import APPEL, ENVOI, FIN, project, templates
from .common import load_matters, Stop
from .improvements36 import matter_option


def e(x):return escape(str(x if x is not None else ''),quote=True)


def _select(name,choices):
    return '<select name="'+e(name)+'" required>'+''.join(
        '<option value="'+e(k)+'">'+e(v)+'</option>' for k,v in choices.items())+'</select>'


def _pages(url,oid,variant,count,title):
    result='<div class="word-preview"><h4>'+e(title)+' · '+str(count)+' page(s)</h4>'
    for n in range(1,count+1):
        result+='<figure><img loading="lazy" src="'+e(url('/documents/preview/'+oid+'/'+variant+'/'+str(n)+'.png'))+'" alt="'+e(title)+' — page '+str(n)+'"><figcaption>Page '+str(n)+'</figcaption></figure>'
    return result+'</div>'


def page(desk,args,form,link,url):
    all_templates=templates(desk)
    out='<section class="word-workbench"><p class="eyebrow">3.3 · DOCUMENTS DU CABINET</p><h2>Modèles Word et nouvelles versions</h2><p>Importez votre fichier DOCX privé. Contrôlez toutes les pages de l’original et de l’exemple avant d’autoriser son usage. Le document généré reste une proposition jusqu’à votre confirmation.</p>'
    out+='<section><h3>1 · Importer un modèle Word</h3><form id="word-template-upload" data-upload-url="'+e(url('/templates/upload'))+'"><label>Fichier Word .docx, 8 Mo maximum<input type="file" name="model" accept=".docx,application/vnd.openxmlformats-officedocument.wordprocessingml.document" required></label><button type="submit">Importer et prévisualiser</button><p role="status" id="word-upload-result"></p></form><p>Balises obligatoires : <code>{{destinataire}}</code>, <code>{{date}}</code>, <code>{{objet}}</code>, <code>{{envoi}}</code>, <code>{{appel}}</code>, <code>{{corps}}</code> (seul dans son paragraphe) et <code>{{fin}}</code>. Balises facultatives : <code>{{qualite}}</code>, <code>{{adresse}}</code>, <code>{{reference}}</code>, <code>{{signature}}</code>. Les balises peuvent traverser plusieurs segments Word. Conservez les en-têtes, logos, tableaux, marges et pieds de page dans le DOCX.</p><p>Une image de signature fixe déjà présente dans le modèle apparaîtra sur chaque courrier : vérifiez-la avant validation.</p></section>'
    if not all_templates:out+='<p class="notice">Aucun modèle privé importé. Les modèles génériques du logiciel ne sont pas utilisés pour vos courriers.</p>'
    for model in all_templates[:50]:
        ident=model['id'];pages=json.loads(model['pages']);info=json.loads(model['placeholders'])
        out+='<section id="modele-'+ident+'"><h3>'+e(model['label'])+' · version '+str(model['version'])+'</h3><p>État : '+e({'draft':'à examiner','approved':'approuvé','retired':'remplacé'}.get(model['status'],model['status']))+' · SHA-256 : <code>'+e(model['sha256'])+'</code></p><p>Logo ou image : '+('présent' if info['has_images'] else 'non détecté')+' · En-tête : '+('oui' if info['has_headers'] else 'non')+' · Pied de page : '+('oui' if info['has_footers'] else 'non')+'.</p>'
        if info.get('neutralized_hyperlinks'):
            out+='<p class="notice">'+str(info['neutralized_hyperlinks'])+' lien(s) hypertexte ou modèle(s) Word externe(s) neutralisé(s) lors de l’import. Le texte visible reste présent ; contrôlez les pages de cette copie avant de l’approuver.</p>'
        if args.get('template')==ident or model['status']=='draft':
            out+=_pages(url,ident,'original',pages['original'],'Modèle original')
            out+=_pages(url,ident,'sample',pages['sample'],'Exemple fictif rempli')
        else:out+=link('/modeles-word','Afficher les pages',template=ident)
        if model['status']=='draft':
            out+=form('approve_cabinet_template','Valider ce modèle',{'template_id':ident,'template_sha256':model['sha256']},'<label class="ws-check"><input type="checkbox" name="ack" value="yes" required>J’ai contrôlé toutes les pages du modèle et de l’exemple, ainsi que la signature, les dates, l’envoi et la mise en page.</label>')
        out+='</section>'
    approved=[m for m in all_templates if m['status']=='approved']
    matter_items=load_matters(desk.c)
    if approved and matter_items:
        selected=args.get('matter','')
        options='<option value="">Choisir le dossier exact</option>'+''.join('<option value="'+e(m['id'])+'"'+(' selected' if m['id']==selected else '')+'>'+e(matter_option(m))+'</option>' for m in matter_items)
        models='<option value="">Choisir un modèle approuvé</option>'+''.join('<option value="'+e(m['id'])+'">'+e(m['label']+' · v'+str(m['version']))+'</option>' for m in approved)
        fields='<label>Dossier<select name="matter" required>'+options+'</select></label><label>Modèle<select name="template_id" required>'+models+'</select></label>'
        fields+='<label>Destinataire<input name="destinataire" required maxlength="200"></label><label>Qualité<input name="qualite" maxlength="200"></label><label>Adresse<input name="adresse" maxlength="280"></label><label>Référence<input name="reference" maxlength="180"></label><label>Objet<input name="objet" maxlength="300" required></label><label>Date<input type="date" name="date" required value="'+date.today().isoformat()+'"></label>'
        fields+='<label>Mode d’envoi'+_select('envoi',ENVOI)+'</label><label>Formule d’appel'+_select('appel',APPEL)+'</label><label>Corps du courrier<textarea name="corps" rows="9" maxlength="14000" required></textarea></label><label>Formule de fin'+_select('fin',FIN)+'</label><label>Signature texte, si le modèle prévoit cette balise<input name="signature" maxlength="200"></label>'
        out+='<section><h3>2 · Préparer un courrier</h3><p>Choisissez une seule variante de chaque formule. La date du projet est figée ; aucun champ de date automatique n’est inséré.</p>'+form('prepare_cabinet_letter','Préparer l’aperçu',extra=fields)+'</section>'
    else:out+='<p class="notice">Il faut au moins un modèle approuvé et un dossier enregistré pour préparer un courrier.</p>'
    rows=[project(desk,r['id']) for r in desk.db.execute('SELECT id FROM cabinet_projects_v330 ORDER BY created DESC LIMIT 30')]
    if rows:
        out+='<section><h3>Projets et versions créées</h3>'
        for p in rows:
            d=p['data'];out+='<article><strong>'+e(d['kind']=='letter' and 'Courrier' or 'Nouvelle version')+' · '+e(d['matter'])+'</strong> · '+e(p['status'])+' · '+link('/modeles-word','Voir les pages et confirmer',project=p['id'])
            if p['status']=='done':
                edit=p['result'].get('created_files',[{}])[0].get('edit_url','')
                if edit.startswith('https://'):out+=' · <a href="'+e(edit)+'" target="_blank" rel="noopener noreferrer">Éditer depuis Nextcloud / OnlyOffice</a>'
            out+='</article>'
        out+='</section>'
    pid=args.get('project','')
    if not pid and args.get('job'):
        try:row=desk.db.execute('SELECT status,result FROM jobs WHERE id=?',(int(args['job']),)).fetchone()
        except ValueError:row=None
        if row and row['status']=='done':
            try:result=json.loads(row['result']);pid=result.get('project_id','')
            except (ValueError,TypeError):pass
            if pid and 'confirmation_code' in result:
                out+='<p class="notice">Code de confirmation de ce projet : <strong>'+e(result['confirmation_code'])+'</strong>. Vérifiez les pages ci-dessous puis saisissez ce code.</p>'
                out+=link('/modeles-word','Ouvrir le projet',project=pid)
    if pid:
        p=project(desk,pid);d=p['data']
        out+='<section id="projet"><h3>3 · Prévisualiser le document</h3><p>Fichier à créer : <code>'+e(d['destination'])+'</code></p><p>Empreinte du contenu : <code>'+e(d['sha256'])+'</code> · Modèle v'+str(d['template_version'])+' · Date et variantes figées à la préparation.</p>'
        if d['source_path']:out+='<p>Source Nextcloud éditée dans OnlyOffice : <code>'+e(d['source_path'])+'</code>. Elle sera relue avant confirmation ; aucune écriture dans cette source.</p>'
        out+=_pages(url,pid,'prepared',d['pages'],'Courrier à créer')
        if p['status'] in ('pending','partial','creating'):
            out+=form('create_cabinet_letter','Créer cette nouvelle version',{'project_id':pid,'destination':d['destination']},'<label>Code reçu à la préparation<input name="confirmation_code" inputmode="numeric" pattern="[0-9]{6}" required></label><label class="ws-check"><input type="checkbox" required>J’ai contrôlé toutes les pages et la destination ; je confirme la création de ce fichier.</label>')
        elif p['status']=='done':out+='<p class="success">Document créé. Le lien Nextcloud ci-dessus ouvre le document pour l’édition en ligne si OnlyOffice est installé sur ce serveur.</p>'
        out+='</section>'
    done=[p for p in rows if p['status']=='done']
    if done:
        out+='<section><h3>4 · Nouvelle version après édition OnlyOffice</h3><p>Après sauvegarde de l’édition dans Nextcloud, choisissez le fichier source exact ci-dessous. L’aperçu reprend ses octets Word ; la création produit un nouveau nom vNNN. Si la source change entre aperçu et confirmation, l’opération est refusée.</p>'
        for p in done[:20]:
            data=p['data']
            out+=form('prepare_cabinet_revision','Prévisualiser une nouvelle version',{'matter':p['matter'],'template_id':p['template_id'],'source_path':data['destination']})
        out+='</section>'
    return out+'</section>'
