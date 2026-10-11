/* AxiorHub 5.6.34 — traduction des textes écrits par les scripts du navigateur (boutons, états, compteurs, confirmations).
   Les écrans sont traduits à la sortie du serveur (ecrans5632) ; ce que les scripts ajoutent ensuite échappait à cette traduction.
   Même dictionnaire, mêmes règles : phrase exacte (espaces normalisés, nombres variables « # »), modèles « %s » pour une donnée
   insérée (laissée telle quelle), segments séparés par « · ». Une phrase absente du dictionnaire reste en français.
   Jamais traduits : champs de saisie, zones éditables, code, et tout élément marqué translate="no". */
(function () {
  'use strict';
  var lang = (document.documentElement.getAttribute('lang') || 'fr').slice(0, 2);
  if (lang === 'fr' || ['en', 'de', 'es'].indexOf(lang) < 0) return;
  var meta = document.querySelector('meta[name="axiorhub-prefix"]');
  var prefix = meta ? meta.getAttribute('content') || '' : '';
  var css = document.querySelector('link[href*="app520.css?v="]');
  var sig = document.querySelector('meta[name="axiorhub-ecrans"]');   // empreinte du dictionnaire : une copie périmée n'est jamais réutilisée
  var version = (css ? css.getAttribute('href').split('v=')[1] : '5.6.34') + '-' + (sig ? sig.getAttribute('content') : '');
  var table = null, templates = [], ready = false, pending = [];
  var ATTRS = ['placeholder', 'title', 'aria-label', 'alt', 'data-confirm'];
  var SKIP = { SCRIPT: 1, STYLE: 1, TEXTAREA: 1, PRE: 1, CODE: 1, NOSCRIPT: 1 };

  function norm(s) { return String(s).replace(/\s+/g, ' ').trim(); }
  function esc(s) { return s.replace(/[.*+?^${}()|[\]\\]/g, '\\$&'); }

  function load(raw) {
    table = {};
    templates = [];
    Object.keys(raw).forEach(function (k) {
      var v = raw[k];
      if (typeof v !== 'string' || !v || (k.charAt(0) === '_' && k.indexOf('_prefix:') !== 0)) return;
      table[k] = v;
      if (k.indexOf('%s') >= 0) {
        var parts = k.split(/(%s|#)/), src = '^', kinds = [];
        parts.forEach(function (p) {
          if (p === '%s') { src += '(.+?)'; kinds.push('s'); } else if (p === '#') { src += '(\\d+)'; kinds.push('#'); } else src += esc(p);
        });
        templates.push({ re: new RegExp(src + '$'), value: v, kinds: kinds });
      }
    });
    ready = true;
  }

  function exact(text) {
    var numbers = text.match(/\d+/g) || [];
    var out = table[text.replace(/\d+/g, '#')];
    if (out === undefined) return null;
    var i = 0;
    return out.replace(/#/g, function () { return i < numbers.length ? numbers[i++] : '#'; });
  }

  function template(text) {
    for (var t = 0; t < templates.length; t++) {
      var m = templates[t].re.exec(text);
      if (!m) continue;
      var g = 1, kinds = templates[t].kinds;
      return templates[t].value.replace(/%s|#/g, function (tok) {
        var got = m[g], kind = kinds[g - 1];
        g++;
        if (got === undefined) return tok;
        return kind === 's' ? (phrase(got) || got) : got;
      });
    }
    return null;
  }

  function phrase(text) {
    var n = norm(text);
    if (!n || !/[A-Za-zÀ-ÿ]/.test(n)) return null;
    var out = exact(n);
    if (out === null) out = template(n);
    if (out === null && n.indexOf(' · ') > 0) {
      var changed = false;
      out = n.split(' · ').map(function (seg) { var got = exact(seg) || template(seg); if (got !== null) changed = true; return got !== null ? got : seg; }).join(' · ');
      if (!changed) out = null;
    }
    if (out === null) {   // puce ou séparateur en tête (« · tout le cabinet · sources : … », « → Rédaction »)
      var deco = /^([^\p{L}\p{N}_«“„"(\[]+?\s+)(.+)$/u.exec(n);   // puce, séparateur ou icône (« ✏️ Modifier »)
      if (deco) { var rest = phrase(deco[2]); if (rest !== null) out = deco[1] + rest.trim(); }
      var tail = out === null ? /^(.+?)(\s*[·—–:]\s*)$/.exec(n) : null;   // séparateur en fin (« 2 brouillon(s) préparé(s) · »)
      if (tail) { var head = phrase(tail[1]); if (head !== null) out = head.trim() + tail[2]; }
    }
    if (out === null) {   // message composé de plusieurs phrases ajoutées l'une après l'autre (« … terminée. Cliquez sur Actualiser … »)
      var sentences = n.match(/[^.!?…]+(?:[.!?…]+|$)\s*/g) || [];
      if (sentences.length > 1) {
        var any = false;
        out = sentences.map(function (s) { var core = s.trim(), got = exact(core) || template(core); if (got !== null) any = true; return (got !== null ? got : core); }).join(' ');
        if (!any) out = null;
      }
    }
    if (out === null) return null;
    var lead = text.match(/^\s*/)[0], trail = text.match(/\s*$/)[0];
    return lead + out + trail;
  }

  function frozen(el) {
    for (var n = el; n && n.nodeType === 1; n = n.parentNode) {
      if (SKIP[n.nodeName] || n.isContentEditable || n.getAttribute('translate') === 'no' || (n.classList && n.classList.contains('notranslate'))) return true;
    }
    return false;
  }

  function textNode(node) {
    if (!node.parentNode || frozen(node.parentNode)) return;
    var got = phrase(node.data);
    if (got !== null && got !== node.data) node.data = got;
  }

  function attrs(el) {
    // un champ de saisie garde son contenu, mais son exemple (placeholder) et son libellé accessible sont traduits
    if (el.nodeName === 'TEXTAREA' ? frozen(el.parentNode) : frozen(el)) return;
    ATTRS.forEach(function (a) {
      var v = el.getAttribute(a);
      if (v) { var got = phrase(v); if (got !== null && got !== v) el.setAttribute(a, got); }
    });
    if (el.nodeName === 'INPUT' && /^(submit|button|reset)$/i.test(el.type) && el.value) {
      var got = phrase(el.value);
      if (got !== null && got !== el.value) el.value = got;
    }
  }

  function walk(root) {
    if (!root) return;
    if (root.nodeType === 3) { textNode(root); return; }
    if (root.nodeType !== 1 && root.nodeType !== 11) return;
    if (root.nodeType === 1) { attrs(root); if (frozen(root)) return; }
    var it = document.createTreeWalker(root, NodeFilter.SHOW_ELEMENT | NodeFilter.SHOW_TEXT, null), node;
    while ((node = it.nextNode())) { if (node.nodeType === 3) textNode(node); else attrs(node); }
  }

  function apply(records) {
    records.forEach(function (r) {
      if (r.type === 'characterData') textNode(r.target);
      else if (r.type === 'attributes') attrs(r.target);
      else Array.prototype.forEach.call(r.addedNodes, walk);
    });
  }

  // Boîtes de dialogue natives : le message est traduit au moment de l'affichage.
  ['alert', 'confirm', 'prompt'].forEach(function (name) {
    var native = window[name];
    if (typeof native !== 'function') return;
    window[name] = function (message) {
      var args = Array.prototype.slice.call(arguments);
      if (ready && typeof message === 'string') {
        args[0] = message.split('\n').map(function (line) { var got = phrase(line); return got !== null ? got : line; }).join('\n');
      }
      return native.apply(window, args);
    };
  });

  var observer = new MutationObserver(function (records) { if (ready) apply(records); else pending.push(records.length); });
  function start() {
    observer.observe(document.documentElement, { childList: true, subtree: true, characterData: true, attributes: true, attributeFilter: ATTRS.concat(['value']) });
    if (ready) walk(document.body);
  }
  if (document.body) start(); else document.addEventListener('DOMContentLoaded', start);

  var key = 'ax5634-ecrans-' + lang + '-' + version, cached = null;
  try { cached = window.sessionStorage.getItem(key); } catch (err) { cached = null; }
  if (cached) {
    try { load(JSON.parse(cached)); } catch (err) { cached = null; }
  }
  if (!cached) {
    fetch(prefix + '/static/ecrans-' + lang + '.json?v=' + encodeURIComponent(version), { credentials: 'same-origin' })
      .then(function (r) { if (!r.ok) throw new Error(String(r.status)); return r.text(); })
      .then(function (text) {
        load(JSON.parse(text));
        try { window.sessionStorage.setItem(key, text); } catch (err) { /* stockage plein ou interdit : sans conséquence */ }
        pending = [];   // la passe complète ci-dessous couvre aussi les ajouts survenus pendant le chargement
        if (document.body) walk(document.body);
      })
      .catch(function () { /* dictionnaire indisponible : textes laissés en français */ });
  }
  window.ax5634Translate = function (text) { return ready ? (phrase(String(text)) || String(text)) : String(text); };
})();
