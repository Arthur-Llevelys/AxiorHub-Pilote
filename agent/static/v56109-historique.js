/* AxiorHub 5.6.109 (lot H, F09 et F10) : « Historique » dans l'assistant existant et dans sa vue agrandie (/assistant).
   - Volet : bouton Historique (horloge + libellé), Nouvelle discussion et Ouvrir en grand dans l'en-tête ; la liste et le fil choisi
     s'affichent dans la zone centrale qui défile ; la saisie, Envoyer et le micro restent en place (aucun déplacement du composeur).
   - Lecture seule : les GET ne lancent rien (le serveur ne met rien en file, ne synchronise ni ne purge) ; « Reprendre cette instruction »
     recopie le texte dans la zone de saisie, sans l'envoyer.
   - Retour : filtres (adresse), discussion choisie, position et état du volet restaurés pour l'onglet (sessionStorage : aucun contenu de
     discussion) ; brouillon de la vue agrandie gardé côté serveur, privé au compte (aucun localStorage).
   Texte inséré par textContent uniquement ; aucun HTML reçu n'est injecté. */
(function () {
  'use strict';
  var meta = function (n) { var m = document.querySelector('meta[name="' + n + '"]'); return m ? m.content : ''; };
  var prefix = meta('axiorhub-prefix') || '';
  var csrf = meta('axiorhub-csrf') || '';
  var compte = meta('axiorhub-compte') || 'c';
  var API = prefix + '/api440/m56109/historique/';
  var CLE_VUE = 'h109-vue:' + compte;

  function el(tag, texte, cls) { var n = document.createElement(tag); if (texte !== undefined && texte !== null) n.textContent = texte; if (cls) n.className = cls; return n; }
  function lire(k) { try { return JSON.parse(sessionStorage.getItem(k) || 'null'); } catch (e) { return null; } }
  function ecrire(k, v) { try { sessionStorage.setItem(k, JSON.stringify(v)); } catch (e) { /* navigation privée */ } }
  function obtenir(chemin) {
    return fetch(API + chemin, { credentials: 'same-origin', headers: { Accept: 'application/json' } }).then(function (r) {
      return r.json().catch(function () { return {}; }).then(function (d) { if (!r.ok) { throw new Error(d.message || 'Demande refusée.'); } return d; });
    });
  }
  function envoyer(chemin, corps, garder) {
    return fetch(API + chemin, { method: 'POST', credentials: 'same-origin', keepalive: !!garder,
      headers: { 'Content-Type': 'application/json', 'X-CSRF-Token': csrf }, body: JSON.stringify(corps) });
  }
  function q(o) { return Object.keys(o).filter(function (k) { return o[k] !== '' && o[k] !== undefined && o[k] !== null; })
    .map(function (k) { return encodeURIComponent(k) + '=' + encodeURIComponent(o[k]); }).join('&'); }
  function dateLocale(iso) { var d = new Date(iso); return isNaN(d) ? '' : d.toLocaleString('fr-FR', { dateStyle: 'short', timeStyle: 'short' }); }

  /* ------------------------------------------------------------------ volet de l'assistant (toutes les pages) */
  function volet() {
    var dock = document.getElementById('ws-ai-dock');
    if (!dock || dock.querySelector('#h109-historique')) return;
    var tete = dock.querySelector('.ws-ai-tete'), corps = dock.querySelector('.ws-ai-body');
    if (!tete || !corps) return;
    var etat = lire(CLE_VUE + ':volet') || { ouvert: false, fil: '', q: '' };
    var bouton = el('button', null, 'h109-tete-bouton'); bouton.type = 'button'; bouton.id = 'h109-historique';
    bouton.setAttribute('aria-expanded', 'false'); bouton.setAttribute('aria-controls', 'h109-panneau');
    bouton.append(el('span', '🕘', 'h109-icone'), el('span', 'Historique'));
    bouton.querySelector('.h109-icone').setAttribute('aria-hidden', 'true');
    var nouvelle = el('button', 'Nouvelle discussion', 'h109-tete-bouton'); nouvelle.type = 'button'; nouvelle.id = 'h109-nouvelle';
    var grand = el('a', 'Ouvrir en grand', 'h109-tete-bouton'); grand.id = 'h109-grand'; grand.href = prefix + '/assistant?vue=historique';
    var barre = el('div', null, 'h109-tete-barre'); barre.setAttribute('role', 'group'); barre.setAttribute('aria-label', 'Discussions');
    barre.append(bouton, nouvelle, grand);
    var avant = tete.querySelector('#ws-ai-wide') || tete.querySelector('#ws-ai-close');
    tete.insertBefore(barre, avant);

    var panneau = el('section', null, 'h109-panneau'); panneau.id = 'h109-panneau'; panneau.hidden = true; panneau.setAttribute('aria-label', 'Historique des discussions');
    var recherche = el('input'); recherche.type = 'search'; recherche.maxLength = 200; recherche.placeholder = 'Rechercher une discussion'; recherche.setAttribute('aria-label', 'Rechercher une discussion');
    var liste = el('ul', null, 'h109-fils'); liste.setAttribute('aria-label', 'Discussions');
    var suite = el('button', 'Discussions suivantes', 'h109-suite'); suite.type = 'button'; suite.hidden = true;
    var fil = el('div', null, 'h109-fil-volet'); fil.hidden = true;
    var statut = el('p', '', 'h109-statut'); statut.setAttribute('role', 'status');
    panneau.append(recherche, statut, liste, suite, fil);
    corps.insertBefore(panneau, corps.firstChild);
    var curseur = null;

    function memoriser() { ecrire(CLE_VUE + ':volet', { ouvert: !panneau.hidden, fil: fil.hidden ? '' : (fil.dataset.fil || ''), q: recherche.value, y: corps.scrollTop }); }
    function ligne(x) {
      var li = el('li', null, 'h109-ligne'), b = el('button', null, 'h109-lien'); b.type = 'button';
      b.append(el('span', x.title, 'h109-titre'), el('span', dateLocale(x.updated) + ' · ' + x.dossier + ' · ' + x.etat_libelle, 'h109-meta'));
      if (x.extrait) b.append(el('span', x.extrait, 'h109-extrait'));
      b.addEventListener('click', function () { ouvrirFil(x.id); });
      li.append(b); return li;
    }
    function charger(ajouter) {
      statut.textContent = 'Chargement…';
      var params = { limite: 20, q: recherche.value.trim() };
      if (ajouter && curseur) params.apres = curseur;
      return obtenir('fils?' + q(params)).then(function (d) {
        if (!ajouter) liste.textContent = '';
        (ajouter ? [] : d.epinglees || []).concat(d.items || []).forEach(function (x) { liste.append(ligne(x)); });
        curseur = d.next || null; suite.hidden = !curseur;
        statut.textContent = liste.children.length ? '' : 'Aucune discussion pour le moment.';
      }).catch(function (e) { statut.textContent = e.message || 'Historique indisponible pour le moment.'; });
    }
    function ouvrirFil(id) {
      fil.hidden = false; liste.hidden = true; suite.hidden = true; recherche.hidden = true; fil.dataset.fil = id; fil.textContent = '';
      statut.textContent = 'Chargement…';
      grand.href = prefix + '/assistant?' + q({ vue: 'historique', thread: id });
      obtenir('messages?' + q({ id: id, limite: 50 })).then(function (d) {
        statut.textContent = '';
        var retour = el('button', '← Toutes les discussions', 'h109-retour'); retour.type = 'button';
        retour.addEventListener('click', function () { fil.hidden = true; liste.hidden = false; recherche.hidden = false; suite.hidden = !curseur; grand.href = prefix + '/assistant?vue=historique'; memoriser(); });
        fil.append(retour, el('h3', d.thread.title || 'Discussion', 'h109-fil-titre'),
          el('p', 'Rouvrir une discussion ne relance aucune instruction.', 'h109-note'));
        (d.items || []).forEach(function (m) {
          var a = el('article', null, 'h109-message h109-' + m.role);
          a.append(el('p', (m.role === 'user' ? 'Vous' : 'Assistant — à relire') + ' · ' + dateLocale(m.created) + ' · ' + m.etat_libelle, 'h109-meta'), el('p', m.contenu, 'h109-texte'));
          if (m.role === 'user' && !m.masque && d.thread.peut_continuer) {
            var r = el('button', 'Reprendre cette instruction', 'h109-reprendre'); r.type = 'button';
            r.addEventListener('click', function () { var t = document.getElementById('ws-ai-question'); if (t) { t.value = m.contenu; t.dispatchEvent(new Event('input', { bubbles: true })); t.focus(); } });
            a.append(r);
          }
          fil.append(a);
        });
        var continuer = el('a', d.thread.peut_continuer ? 'Continuer cette discussion (vue agrandie)' : 'Ouvrir en grand', 'h109-continuer');
        continuer.href = grand.href; fil.append(continuer);
        memoriser();
      }).catch(function (e) { statut.textContent = e.message || 'Discussion introuvable.'; });
    }
    function basculer(ouvrir) {
      panneau.hidden = !ouvrir; bouton.setAttribute('aria-expanded', String(ouvrir));
      if (ouvrir && !liste.children.length) charger(false);
      if (ouvrir) { corps.scrollTop = 0; recherche.focus({ preventScroll: true }); }
      memoriser();
    }
    bouton.addEventListener('click', function () { basculer(panneau.hidden); });
    nouvelle.addEventListener('click', function () {
      // un fil vide : rien n'est supprimé ni relancé, le brouillon reste ; l'affichage du fil précédent est simplement replié
      basculer(false);
      var effacer = dock.querySelector('#ws-ai-clear'); if (effacer) effacer.click();
      var t = document.getElementById('ws-ai-question'); if (t) t.focus();
    });
    var minuterie = 0;
    recherche.addEventListener('input', function () { clearTimeout(minuterie); minuterie = setTimeout(function () { curseur = null; charger(false); memoriser(); }, 300); });
    suite.addEventListener('click', function () { charger(true); });
    corps.addEventListener('scroll', function () { if (!panneau.hidden) { clearTimeout(corps._h109); corps._h109 = setTimeout(memoriser, 200); } }, { passive: true });
    // retour : l'état du volet est repris à la réouverture (même onglet, même compte), jamais le contenu
    if (etat && etat.ouvert) {
      recherche.value = etat.q || '';
      var rendre = function () {
        if (dock.hidden) return;
        basculer(true);
        var suiteFil = etat.fil ? ouvrirFil(etat.fil) : null;
        if (etat.y) setTimeout(function () { corps.scrollTop = etat.y; }, 400);
        etat = null; return suiteFil;
      };
      new MutationObserver(function (_, obs) { if (!dock.hidden && etat) { rendre(); obs.disconnect(); } }).observe(dock, { attributes: true, attributeFilter: ['hidden'] });
      if (!dock.hidden) rendre();
    }
  }

  /* ------------------------------------------------------------------ vue agrandie (/assistant?vue=historique) */
  function vueAgrandie() {
    var racine = document.querySelector('[data-h109]');
    if (!racine || racine.dataset.h109Pret) return;
    racine.dataset.h109Pret = '1';
    var cleVue = CLE_VUE + ':page:' + location.pathname;
    // position : gardée au départ de la page, rendue au retour (filtres et discussion choisie sont dans l'adresse)
    window.addEventListener('pagehide', function () {
      var l = racine.querySelector('[data-h109-liste]'), m = racine.querySelector('[data-h109-messages]');
      ecrire(cleVue, { url: location.search, y: window.scrollY, liste: l ? l.scrollTop : 0, messages: m ? m.scrollTop : 0 });
    });
    var sauve = lire(cleVue);
    if (sauve && sauve.url === location.search) {
      window.scrollTo(0, sauve.y || 0);
      var l = racine.querySelector('[data-h109-liste]'), m = racine.querySelector('[data-h109-messages]');
      if (l) l.scrollTop = sauve.liste || 0;
      if (m) m.scrollTop = sauve.messages || 0;
    } else {
      var m2 = racine.querySelector('[data-h109-messages]'); if (m2) m2.scrollTop = m2.scrollHeight;
    }
    var choisi = racine.querySelector('.h109-choisi a'); if (choisi && choisi.scrollIntoView) choisi.scrollIntoView({ block: 'nearest' });

    // brouillon privé, côté serveur (un par fil, et un pour « Nouvelle discussion »)
    var zone = racine.querySelector('[data-h109-brouillon]');
    if (zone) {
      var contexte = 'fil:' + zone.dataset.h109Brouillon, envoye = 'h109-envoye:' + compte + ':' + contexte;
      if (lire(envoye)) { try { sessionStorage.removeItem(envoye); } catch (e) { /* */ } }
      else if (!zone.value) {
        obtenir('brouillon?' + q({ contexte: contexte })).then(function (d) { if (d.texte && !zone.value) zone.value = d.texte; }).catch(function () { /* brouillon facultatif */ });
      }
      var minute = 0;
      zone.addEventListener('input', function () { clearTimeout(minute); minute = setTimeout(function () { envoyer('brouillon', { contexte: contexte, texte: zone.value }); }, 1200); });
      if (zone.form) zone.form.addEventListener('submit', function () { clearTimeout(minute); ecrire(envoye, 1); envoyer('brouillon', { contexte: contexte, texte: '' }, true); });
      racine.querySelectorAll('[data-h109-reprendre]').forEach(function (b) {
        b.addEventListener('click', function () {
          var art = b.closest('[data-h109-message]'), t = art && art.querySelector('[data-h109-texte]');
          if (t) { zone.value = t.textContent; zone.focus(); zone.dispatchEvent(new Event('input', { bubbles: true })); }
        });
      });
    }

    // messages suivants (archive complète, au-delà de 100 messages)
    var detail = racine.querySelector('[data-h109-detail]');
    racine.addEventListener('click', function (ev) {
      var b = ev.target.closest('[data-h109-messages-suivants]');
      if (!b || !detail) return;
      b.disabled = true;
      obtenir('messages?' + q({ id: detail.dataset.h109Detail, apres: b.dataset.h109MessagesSuivants, limite: 50 })).then(function (d) {
        var boite = racine.querySelector('[data-h109-messages]');
        (d.items || []).forEach(function (m) {
          var a = el('article', null, 'h109-message h109-' + m.role);
          a.append(el('p', (m.role === 'user' ? 'Vous' : 'Assistant — à relire') + ' · ' + dateLocale(m.created) + ' · ' + m.etat_libelle, 'h109-meta'), el('div', m.contenu, 'h109-texte'));
          boite.append(a);
        });
        if (d.next) { b.dataset.h109MessagesSuivants = d.next; b.disabled = false; } else { b.remove(); }
      }).catch(function (e) { b.disabled = false; b.textContent = e.message || 'Réessayer'; });
    });

    // recherche dans les messages (titres et texte des seules discussions autorisées)
    var bRecherche = racine.querySelector('[data-h109-recherche-messages]'), resultats = racine.querySelector('[data-h109-resultats]');
    if (bRecherche && resultats) {
      bRecherche.addEventListener('click', function () {
        var champ = racine.querySelector('[data-h109-filtres] input[name=q]'), terme = champ ? champ.value.trim() : '';
        resultats.textContent = '';
        if (!terme) { resultats.append(el('p', 'Saisissez d’abord un terme à rechercher.', 'h109-note')); if (champ) champ.focus(); return; }
        resultats.append(el('p', 'Recherche…', 'h109-note'));
        obtenir('recherche?' + q({ q: terme })).then(function (d) {
          resultats.textContent = '';
          resultats.append(el('p', d.total + ' discussion(s) trouvée(s) dans vos titres et messages.', 'h109-note'));
          var ul = el('ul', null, 'h109-fils');
          (d.items || []).forEach(function (x) {
            var li = el('li', null, 'h109-ligne'), a = el('a', null, 'h109-lien'); a.href = prefix + '/assistant?' + q({ vue: 'historique', thread: x.id });
            a.append(el('span', x.title, 'h109-titre'), el('span', x.dossier + ' · ' + x.etat_libelle, 'h109-meta'));
            if (x.extrait) a.append(el('span', x.extrait, 'h109-extrait'));
            li.append(a); ul.append(li);
          });
          resultats.append(ul);
        }).catch(function (e) { resultats.textContent = e.message || 'Recherche indisponible.'; });
      });
    }
  }

  function demarrer() { volet(); vueAgrandie(); }
  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', demarrer); else demarrer();
})();
