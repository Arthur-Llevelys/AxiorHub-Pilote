/* 5.6.107 (lot V) : calque de la vidéo de démonstration, injecté par Playwright (add_init_script) — jamais servi par le produit.
 * Curseur visible (le navigateur sans interface n'en dessine pas), onde au clic, légende brève en haut, au centre du bandeau, carton de fin.
 * Construit uniquement par le DOM et le CSSOM (element.style.*) : compatible avec la CSP du produit (style-src 'self'), sans
 * attribut style ni balise <style>. Rien n'est envoyé : aucune requête, aucun stockage hors de sessionStorage (position, légende). */
(function () {
  'use strict';
  if (window.top !== window || window.__axhVideo) return;   // cadres (lecteur isolé) : rien
  var Z = '2147483647', etat = { x: 960, y: 540, legende: '' };
  try {
    var memo = JSON.parse(sessionStorage.getItem('axhVideo56107') || '{}');
    if (typeof memo.x === 'number') { etat.x = memo.x; etat.y = memo.y; }
    if (typeof memo.legende === 'string') etat.legende = memo.legende;
  } catch (e) { /* stockage indisponible : position par défaut */ }
  function memoriser() {
    try { sessionStorage.setItem('axhVideo56107', JSON.stringify(etat)); } catch (e) { /* sans effet */ }
  }
  function regler(el, styles) { for (var k in styles) el.style[k] = styles[k]; return el; }
  var NS = 'http://www.w3.org/2000/svg', curseur = null, legende = null, fin = null;

  function monter() {
    if (curseur || !document.body) return;
    curseur = document.createElementNS(NS, 'svg');
    curseur.setAttribute('width', '28'); curseur.setAttribute('height', '28'); curseur.setAttribute('viewBox', '0 0 28 28');
    curseur.setAttribute('aria-hidden', 'true'); curseur.setAttribute('data-axh-video', 'curseur');
    var fleche = document.createElementNS(NS, 'path');
    fleche.setAttribute('d', 'M3 2 L3 22 L8.5 17 L12.5 26 L16 24.5 L12 15.8 L19.5 15.8 Z');
    fleche.setAttribute('fill', '#111'); fleche.setAttribute('stroke', '#fff'); fleche.setAttribute('stroke-width', '1.6');
    fleche.setAttribute('stroke-linejoin', 'round');
    curseur.appendChild(fleche);
    regler(curseur, { position: 'fixed', left: '0', top: '0', zIndex: Z, pointerEvents: 'none', overflow: 'visible',
                      transform: 'translate(' + etat.x + 'px,' + etat.y + 'px)', filter: 'drop-shadow(0 1px 2px rgba(0,0,0,.45))' });
    document.documentElement.appendChild(curseur);
    legende = regler(document.createElement('div'), {
      position: 'fixed', top: '4px', left: '50%', maxWidth: '760px', whiteSpace: 'nowrap', overflow: 'hidden', textOverflow: 'ellipsis',
      zIndex: Z, pointerEvents: 'none', background: 'rgba(15, 30, 54, 0.94)', color: '#fff',
      font: '600 22px/1.25 "Segoe UI", "Noto Sans", Arial, sans-serif', padding: '8px 22px', borderRadius: '999px',
      boxShadow: '0 6px 20px rgba(0,0,0,.25)', border: '2px solid #4f9cf9',
      opacity: '0', transform: 'translate(-50%, -6px)', transition: 'opacity .35s ease, transform .35s ease' });
    legende.setAttribute('data-axh-video', 'legende'); legende.setAttribute('aria-hidden', 'true');
    document.documentElement.appendChild(legende);
    if (etat.legende) afficherLegende(etat.legende, true);
  }

  function afficherLegende(texte, sansTransition) {
    monter();
    etat.legende = texte || ''; memoriser();
    if (!legende) return;
    if (sansTransition) legende.style.transition = 'none';
    legende.textContent = etat.legende;
    legende.style.opacity = etat.legende ? '1' : '0';
    legende.style.transform = etat.legende ? 'translate(-50%, 0)' : 'translate(-50%, -6px)';
    if (sansTransition) { void legende.offsetWidth; legende.style.transition = 'opacity .35s ease, transform .35s ease'; }
  }

  function placer(x, y) {
    etat.x = x; etat.y = y;
    if (curseur) curseur.style.transform = 'translate(' + x + 'px,' + y + 'px)';
  }

  function onde(x, y) {
    monter();
    var o = regler(document.createElement('div'), {
      position: 'fixed', left: (x - 22) + 'px', top: (y - 22) + 'px', width: '44px', height: '44px', borderRadius: '50%',
      border: '3px solid #4f9cf9', background: 'rgba(79,156,249,.18)', zIndex: Z, pointerEvents: 'none',
      transform: 'scale(.3)', opacity: '1', transition: 'transform .45s ease-out, opacity .45s ease-out' });
    o.setAttribute('aria-hidden', 'true');
    document.documentElement.appendChild(o);
    requestAnimationFrame(function () { requestAnimationFrame(function () { o.style.transform = 'scale(1.4)'; o.style.opacity = '0'; }); });
    setTimeout(function () { o.remove(); }, 700);
  }

  function carton(marque, devise, adresse, logo) {
    monter();
    afficherLegende('');
    if (fin) fin.remove();
    fin = regler(document.createElement('div'), {
      position: 'fixed', inset: '0', zIndex: Z, display: 'flex', flexDirection: 'column', alignItems: 'center', justifyContent: 'center',
      gap: '26px', background: 'linear-gradient(160deg, #0f1e36 0%, #16335c 100%)', color: '#fff', pointerEvents: 'none',
      opacity: '0', transition: 'opacity .6s ease', font: '400 30px/1.35 "Segoe UI", "Noto Sans", Arial, sans-serif', textAlign: 'center' });
    fin.setAttribute('data-axh-video', 'fin');
    if (logo) {
      var img = regler(document.createElement('img'), { width: '168px', height: '168px', borderRadius: '36px', boxShadow: '0 12px 40px rgba(0,0,0,.35)' });
      img.alt = ''; img.src = logo;
      fin.appendChild(img);
    }
    var t = regler(document.createElement('div'), { font: '700 76px/1.1 "Segoe UI", "Noto Sans", Arial, sans-serif', letterSpacing: '-0.5px' });
    t.textContent = marque;
    var d = regler(document.createElement('div'), { font: '400 40px/1.3 "Segoe UI", "Noto Sans", Arial, sans-serif', color: '#d7e6ff' });
    d.textContent = devise;
    var a = regler(document.createElement('div'), { font: '600 44px/1.2 "Segoe UI", "Noto Sans", Arial, sans-serif', color: '#8fc1ff',
                                                     marginTop: '18px', padding: '10px 30px', border: '2px solid rgba(143,193,255,.6)', borderRadius: '999px' });
    a.textContent = adresse;
    fin.appendChild(t); fin.appendChild(d); fin.appendChild(a);
    document.documentElement.appendChild(fin);
    if (curseur) curseur.style.opacity = '0';
    requestAnimationFrame(function () { requestAnimationFrame(function () { fin.style.opacity = '1'; }); });
  }

  document.addEventListener('mousemove', function (ev) { monter(); placer(ev.clientX, ev.clientY); memoriser(); }, true);
  document.addEventListener('mousedown', function (ev) { onde(ev.clientX, ev.clientY); }, true);
  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', monter); else monter();
  window.__axhVideo = { legende: afficherLegende, placer: function (x, y) { monter(); placer(x, y); memoriser(); }, onde: onde, carton: carton,
                        pret: function () { monter(); return !!curseur; } };
})();
