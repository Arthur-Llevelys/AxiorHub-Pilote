/* AxiorHub 5.6.89 — FormAdapter : lecture commune des formulaires, composants Carbon adjoints aux champs natifs.
   Le natif reste la référence : le serveur le rend complet, les scripts existants le lisent, et il continue seul si Carbon ne charge pas. Le composant adjoint n'a pas de nom : il n'ajoute rien
   à un envoi natif (FormData) et ne se lit jamais en double. Aucun HTML venant du serveur n'est injecté ; les messages passent par textContent. Événements : ceux de @carbon/web-components 2.66.0. */
(function (root) {
  'use strict';
  var CHAMPS = 'input,select,textarea,cds-checkbox,cds-radio-button-group,cds-select,cds-text-input,cds-textarea';
  var MAX_FICHIER = 6 * 1024 * 1024;

  function chaque(liste, fn) { Array.prototype.forEach.call(liste, fn); }
  function balise(f) { return String(f.tagName || '').toLowerCase(); }
  function nomDe(f) { return f.name || (f.getAttribute && f.getAttribute('name')) || ''; }
  function inactif(f) { return !!(f.disabled || (f.hasAttribute && f.hasAttribute('disabled')) || (f.closest && f.closest('fieldset[disabled]'))); }
  function adjoint(f) { return !!(f.closest && f.closest('[data-ui89-adjoint]')); }

  /* Une valeur par nom, comme le contrat des routes : case décochée = false explicite, radio sans choix = absent, liste multiple = tableau, champ désactivé = absent (règle HTML). */
  function collect(form) {
    var data = {};
    chaque(form.querySelectorAll(CHAMPS), function (f) {
      var nom = nomDe(f), tag = balise(f);
      if (!nom || inactif(f) || adjoint(f) || (f.closest && f.closest('.m5-party-row'))) return;
      if (tag === 'cds-checkbox') { data[nom] = !!f.checked; return; }
      if (tag.indexOf('cds-') === 0) { if (f.value !== undefined && f.value !== null) data[nom] = f.value; return; }
      if (f.type === 'file') return;
      if (f.type === 'checkbox') { data[nom] = !!f.checked; return; }
      if (f.type === 'radio') { if (f.checked) data[nom] = f.value; return; }
      if (tag === 'select' && f.multiple) { data[nom] = Array.prototype.filter.call(f.options, function (o) { return o.selected; }).map(function (o) { return o.value; }); return; }
      data[nom] = f.value;
    });
    return data;
  }

  /* Fichiers : seulement sur demande explicite du champ, lus en adresse de données. Un fichier trop lourd arrête l'envoi avant tout effet. */
  function fichiers(form, data) {
    var lectures = [];
    chaque(form.querySelectorAll('input[type=file][data-envoi-fichier]'), function (f) {
      var nom = nomDe(f), fichier = f.files && f.files[0];
      if (!nom || !fichier || inactif(f)) return;
      if (fichier.size > MAX_FICHIER) { var trop = new Error('Fichier trop lourd (6 Mo au plus).'); trop.champ = nom; lectures.push(Promise.reject(trop)); return; }
      lectures.push(new Promise(function (ok, ko) {
        var r = new root.FileReader();
        r.onload = function () { data[nom] = { nom: fichier.name, type: fichier.type, taille: fichier.size, contenu: String(r.result || '') }; ok(); };
        r.onerror = function () { var e = new Error('Fichier illisible.'); e.champ = nom; ko(e); };
        r.readAsDataURL(fichier);
      }));
    });
    return Promise.all(lectures).then(function () { return data; });
  }

  function champ(form, nom) {
    var trouve = null;
    chaque(form.querySelectorAll(CHAMPS), function (f) { if (!trouve && nomDe(f) === nom && !adjoint(f)) trouve = f; });
    return trouve;
  }
  function visible(f) {
    var bloc = f.closest ? (f.closest('.ui89-champ') || f.closest('label') || f) : f;
    var suivant = bloc.nextElementSibling;       /* cds-select est posé à côté du bloc natif, masqué */
    if (suivant && suivant.hasAttribute && suivant.hasAttribute('data-ui89-adjoint')) return suivant;
    return bloc.querySelector && bloc.querySelector('[data-ui89-adjoint]') || f;
  }
  function retirer(form) {
    chaque(form.querySelectorAll('.ui89-erreur'), function (n) { n.parentNode.removeChild(n); });
    chaque(form.querySelectorAll('[aria-invalid="true"]'), function (n) { n.removeAttribute('aria-invalid'); });
    chaque(form.querySelectorAll('[data-ui89-adjoint][invalid]'), function (n) { n.removeAttribute('invalid'); n.removeAttribute('invalid-text'); });
  }
  /* Erreur près du champ : texte lisible, lié au champ, focus posé dessus. Sans champ connu : message d'ensemble du formulaire, jamais un succès. */
  function signaler(form, nom, message) {
    var f = nom ? champ(form, nom) : null, doc = form.ownerDocument;
    var note = doc.createElement('p');
    note.className = 'ui89-erreur'; note.setAttribute('role', 'alert'); note.textContent = message;
    if (!f) {
      var etat = form.querySelector('.m5-status');
      if (etat) etat.parentNode.insertBefore(note, etat); else form.appendChild(note);
      return null;
    }
    var bloc = (f.closest && (f.closest('.ui89-champ') || f.closest('label'))) || f;
    var vu = visible(f), repere = vu !== f && vu.parentNode === bloc.parentNode ? vu : bloc;
    note.id = 'ui89-err-' + nom.replace(/[^A-Za-z0-9_-]/g, '') + '-' + Math.floor(Math.random() * 1e6);
    if (repere.parentNode) repere.parentNode.insertBefore(note, repere.nextSibling);
    f.setAttribute('aria-invalid', 'true');
    var decrit = f.getAttribute('aria-describedby');
    f.setAttribute('aria-describedby', (decrit ? decrit + ' ' : '') + note.id);
    if (vu !== f && vu.setAttribute) { vu.setAttribute('invalid', ''); vu.setAttribute('invalid-text', message); }
    return vu;
  }

  /* Contrainte « requis » : le navigateur la vérifie pour le natif visible ; ici pour un champ hors de sa portée (composant Carbon, radios sans choix). */
  function manquants(form) {
    var noms = [];
    chaque(form.querySelectorAll('[required]'), function (f) {
      var nom = nomDe(f), tag = balise(f);
      if (!nom || inactif(f) || adjoint(f) || noms.indexOf(nom) >= 0) return;
      var vide;
      if (f.type === 'radio') vide = !form.querySelector('input[type=radio][name="' + nom.replace(/"/g, '') + '"]:checked');
      else if (f.type === 'checkbox' || tag === 'cds-checkbox') vide = !f.checked;
      else if (f.type === 'file') vide = !(f.files && f.files.length);
      else vide = !String(f.value === undefined || f.value === null ? '' : f.value).trim();
      if (vide) noms.push(nom);
    });
    return noms;
  }

  /* Avant l'envoi : refuse une seconde soumission tant que la première n'est pas revenue, et contrôle les champs requis. Rend false si rien ne doit partir. */
  function avant(form) {
    if (form.getAttribute('data-ui89-envoi') === '1') return false;
    retirer(form);
    var absents = manquants(form), premier = null;
    absents.forEach(function (nom) { var vu = signaler(form, nom, 'Ce champ est obligatoire.'); premier = premier || vu; });
    if (form.noValidate) {     /* composants adjoints : le navigateur ne contrôle plus, et ne doit pas viser un natif masqué ; format et longueur restent vérifiés ici */
      chaque(form.querySelectorAll('input,select,textarea'), function (f) {
        if (nomDe(f) && !inactif(f) && absents.indexOf(nomDe(f)) < 0 && f.checkValidity && !f.checkValidity()) { var vu = signaler(form, nomDe(f), f.validationMessage || 'Valeur invalide.'); premier = premier || vu; absents.push(nomDe(f)); }
      });
    }
    if (absents.length) {
      if (premier && premier.focus) premier.focus();
      return false;
    }
    form.setAttribute('data-ui89-envoi', '1');
    return true;
  }
  function apres(form) { form.removeAttribute('data-ui89-envoi'); }
  function erreur(form, e) {
    var vu = signaler(form, e && (e.champ || e.field) || '', e && e.message || 'Erreur : rien n’a été enregistré.');
    if (vu && vu.focus) vu.focus();
  }

  /* ---- Composants Carbon adjoints ---- */
  function cacher(n) { n.classList.add('ui89-natif'); n.setAttribute('tabindex', '-1'); n.setAttribute('aria-hidden', 'true'); }
  function suivre(natif, composant) {            /* désactivation posée après coup par un script existant */
    if (typeof root.MutationObserver !== 'function') return;
    new root.MutationObserver(function () {
      composant.toggleAttribute('disabled', natif.disabled);
      composant.toggleAttribute('required', natif.required);
    }).observe(natif, { attributes: true, attributeFilter: ['disabled', 'required'] });
  }
  function signale(natif) { natif.dispatchEvent(new root.Event('change', { bubbles: true })); }
  function libelle(bloc) { var s = bloc.querySelector('label span, label > span, span'); return s ? s.textContent.trim() : ''; }
  var compteur = 0;
  function ident(c) { compteur += 1; c.id = 'ui89-carbon-' + compteur; return c; }
  var ADJOINDRE = {
    case: function (bloc, doc) {
      var natif = bloc.querySelector('input[type=checkbox]');
      if (!natif) return;
      var c = ident(doc.createElement('cds-checkbox'));
      c.setAttribute('label-text', libelle(bloc)); c.setAttribute('data-ui89-adjoint', '');
      if (natif.checked) c.setAttribute('checked', ''); if (natif.disabled) c.setAttribute('disabled', '');
      c.addEventListener('cds-checkbox-changed', function (ev) { natif.checked = !!ev.detail.checked; signale(natif); });
      natif.addEventListener('change', function () { c.checked = natif.checked; });
      bloc.querySelector('label').after(c); bloc.querySelector('label').classList.add('ui89-natif-bloc'); cacher(natif); suivre(natif, c);
    },
    radios: function (bloc, doc) {
      var natifs = bloc.querySelectorAll('input[type=radio]'), legende = bloc.querySelector('legend');
      if (!natifs.length) return;
      var g = ident(doc.createElement('cds-radio-button-group'));
      g.setAttribute('legend-text', legende ? legende.textContent : ''); g.setAttribute('orientation', 'vertical'); g.setAttribute('data-ui89-adjoint', '');
      chaque(natifs, function (natif) {
        var r = ident(doc.createElement('cds-radio-button'));
        r.setAttribute('label-text', natif.closest('label').textContent.trim()); r.setAttribute('value', natif.value);
        if (natif.checked) r.setAttribute('checked', ''); if (natif.disabled) r.setAttribute('disabled', '');
        g.appendChild(r);
        natif.closest('label').classList.add('ui89-natif-bloc'); cacher(natif);
      });
      g.addEventListener('cds-radio-button-group-changed', function (ev) {
        chaque(natifs, function (natif) { natif.checked = natif.value === ev.detail.value; });
        var choisi = bloc.querySelector('input[type=radio]:checked'); if (choisi) signale(choisi);
      });
      if (legende) legende.classList.add('ui89-natif-bloc');
      bloc.appendChild(g);
    }
    /* 5.6.94 (P05) : plus de cds-select adjoint aux listes (« liste ») : le gabarit de ce composant (Carbon 2.66.0) pose un attribut style
       dans son ombre, refusé par la CSP de la page (style-src 'self') : une violation par liste affichée. La liste native reste seule, lue
       et envoyée comme avant ; la feuille de style lui donne l'apparence Carbon. */
  };
  function adjoindre(form) {
    if (form.getAttribute('data-ui89-adjoint-fait')) return;
    form.setAttribute('data-ui89-adjoint-fait', '1');
    form.noValidate = true;
    form.ownerDocument.addEventListener('formdata', function (ev) { if (ev.target === form) ev.formData.delete(''); });   /* un composant adjoint, sans nom, n'ajoute rien à un envoi natif */
    chaque(form.querySelectorAll('.ui89-champ[data-champ]'), function (bloc) {
      var genre = bloc.getAttribute('data-champ');
      if (ADJOINDRE[genre]) ADJOINDRE[genre](bloc, form.ownerDocument);
    });
  }
  function demarrer(doc, registre) {
    var forms = doc.querySelectorAll('form[data-carbon]');
    if (!forms.length || !(doc.body && doc.body.hasAttribute('data-carbon')) || !registre) return Promise.resolve(false);
    /* Délai dépassé ou fichier absent : le natif reste, sans message ni perte de fonction. */
    var delai = new Promise(function (ok) { root.setTimeout(function () { ok(false); }, 6000); });
    var pret = Promise.all(['cds-checkbox', 'cds-select', 'cds-radio-button-group'].map(function (n) { return registre.whenDefined(n); })).then(function () { return true; });
    return Promise.race([pret, delai]).then(function (ok) {
      if (ok) chaque(forms, adjoindre);
      return ok;
    });
  }

  var api = { collect: collect, fichiers: fichiers, avant: avant, apres: apres, erreur: erreur, signaler: signaler, manquants: manquants, adjoindre: adjoindre, demarrer: demarrer };
  root.ax89Form = api;
  if (typeof module !== 'undefined' && module.exports) module.exports = api;
  if (root.document && root.document.addEventListener) {
    root.document.addEventListener('DOMContentLoaded', function () { demarrer(root.document, root.customElements); });
  }
})(typeof window !== 'undefined' ? window : globalThis);
