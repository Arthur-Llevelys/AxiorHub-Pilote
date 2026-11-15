/* AxiorHub 5.6.101 (lot M · M01, M02) — mesure facultative du temps de traitement, par personne.
   Ce script n'est servi qu'à la personne qui a activé la mesure dans Mon compte (attribut data-chrono-actif="1" posé par le serveur) ;
   sans cet attribut, il n'écoute rien et n'envoie rien. Il écoute seulement, sur document, les deux événements du lecteur (lot R) :
   ax101:ouvert {type} à l'ouverture d'un élément prêt, ax101:decide {type, commande} après une décision acceptée par le serveur.
   Il envoie le type, la commande, la durée et une clé tirée au hasard (idempotence), jamais d'objet, de dossier, de contenu ni
   d'identifiant d'élément ; une durée de plus de 30 minutes (élément laissé ouvert) est écartée. Envoi par navigator.sendBeacon (POST,
   corps text/plain sans en-tête possible : le jeton CSRF existant voyage dans le corps), sinon fetch keepalive avec l'en-tête. */
(function (racine) {
  'use strict';
  var AX101 = racine.AX101 = racine.AX101 || {};
  if (AX101.chrono) return;

  var TYPES = { courriel: true, gmail: true, document: true, note_ia: true };
  var LIMITE_MS = 30 * 60 * 1000;
  var COMMANDE = /^[a-z][a-z0-9_]{0,39}$/;

  function typeValide(type) { return typeof type === 'string' && Object.prototype.hasOwnProperty.call(TYPES, type); }

  /* Contrôleur sans DOM (testé dans Node) : o.maintenant() en millisecondes monotones, o.envoyer(mesure), o.nouvelleCle(). */
  function creerChrono(o) {
    var ouvert = null;
    return {
      ouvert: function (detail) {
        var type = detail && detail.type;
        ouvert = typeValide(type) ? { type: type, debut: o.maintenant() } : null;   // un nouvel élément remplace le précédent sans décision
      },
      decide: function (detail) {
        var cours = ouvert;
        ouvert = null;
        if (!cours || !detail || detail.type !== cours.type) return null;
        var commande = String(detail.commande || '');
        if (!COMMANDE.test(commande)) return null;
        var duree = Math.round(o.maintenant() - cours.debut);
        if (!(duree > 0) || duree > LIMITE_MS) return null;                        // élément laissé ouvert : écarté, rien n'est envoyé
        var mesure = { type: cours.type, commande: commande, duree_ms: duree, cle: o.nouvelleCle() };
        o.envoyer(mesure);
        return mesure;
      },
      enCours: function () { return ouvert ? ouvert.type : ''; }
    };
  }

  function cleAleatoire(crypto) {
    var octets = [];
    if (crypto && crypto.getRandomValues) {
      var t = new Uint8Array(16);
      crypto.getRandomValues(t);
      for (var i = 0; i < t.length; i++) octets.push(t[i]);
    } else {
      for (var j = 0; j < 16; j++) octets.push(Math.floor(Math.random() * 256));
    }
    return octets.map(function (b) { return (b < 16 ? '0' : '') + b.toString(16); }).join('');
  }

  /* Envoi d'une mesure : sendBeacon d'abord (survit à la fermeture de la page), fetch keepalive sinon. Aucune erreur montrée. */
  function creerEnvoi(o) {
    var url = (o.prefix || '') + '/api440/m56101/chrono/mesure';
    return function (mesure) {
      var corps = { type: mesure.type, commande: mesure.commande, duree_ms: mesure.duree_ms, cle: mesure.cle };
      try {
        if (o.navigator && typeof o.navigator.sendBeacon === 'function' && o.Blob) {
          var avecJeton = JSON.stringify({ type: corps.type, commande: corps.commande, duree_ms: corps.duree_ms, cle: corps.cle, jeton: o.csrf || '' });
          if (o.navigator.sendBeacon(url, new o.Blob([avecJeton], { type: 'text/plain;charset=UTF-8' }))) return 'balise';
        }
      } catch (err) { /* navigateur qui refuse la balise : envoi ordinaire ci-dessous */ }
      if (typeof o.fetch === 'function') {
        try {
          var r = o.fetch(url, { method: 'POST', keepalive: true, credentials: 'same-origin',
                                 headers: { 'Content-Type': 'application/json', 'X-CSRF-Token': o.csrf || '' }, body: JSON.stringify(corps) });
          if (r && typeof r.catch === 'function') r.catch(function () { /* mesure perdue : sans conséquence */ });
          return 'fetch';
        } catch (err2) { return ''; }
      }
      return '';
    };
  }

  /* Démarrage dans une page : seulement si le serveur a posé data-chrono-actif="1" sur la balise de ce script. */
  function demarrer(doc, env) {
    var balise = env.script || doc.currentScript || doc.querySelector('script[data-chrono-actif]');
    if (!balise || balise.getAttribute('data-chrono-actif') !== '1') return null;
    var meta = function (nom) { var m = doc.querySelector('meta[name="' + nom + '"]'); return m ? m.content : ''; };
    var horloge = env.performance && typeof env.performance.now === 'function' ? function () { return env.performance.now(); } : function () { return Date.now(); };
    var chrono = creerChrono({
      maintenant: horloge,
      nouvelleCle: function () { return cleAleatoire(env.crypto); },
      envoyer: creerEnvoi({ prefix: meta('axiorhub-prefix'), csrf: meta('axiorhub-csrf'), navigator: env.navigator, Blob: env.Blob, fetch: env.fetch })
    });
    doc.addEventListener('ax101:ouvert', function (ev) { chrono.ouvert(ev && ev.detail); });
    doc.addEventListener('ax101:decide', function (ev) { chrono.decide(ev && ev.detail); });
    return chrono;
  }

  AX101.chrono = { creerChrono: creerChrono, creerEnvoi: creerEnvoi, demarrer: demarrer, cleAleatoire: cleAleatoire, LIMITE_MS: LIMITE_MS };

  if (typeof document !== 'undefined' && document.addEventListener) {
    AX101.chrono.actif = demarrer(document, {
      script: document.currentScript, performance: racine.performance, crypto: racine.crypto, navigator: racine.navigator,
      Blob: typeof Blob !== 'undefined' ? Blob : null, fetch: typeof racine.fetch === 'function' ? racine.fetch.bind(racine) : null
    });
  }
  if (typeof module !== 'undefined' && module.exports) module.exports = AX101.chrono;
})(typeof window !== 'undefined' ? window : globalThis);
