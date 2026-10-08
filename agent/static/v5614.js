/* AxiorHub 5.6.14 : barre des routines, centre « À décider » compact (panneau latéral, actions), page des missions complexes. */
(() => {
  'use strict';
  const meta = name => document.querySelector('meta[name="' + name + '"]')?.content || '';
  const prefix = meta('axiorhub-prefix'), csrf = meta('axiorhub-csrf');
  async function post(api, route, data) {
    const r = await fetch(prefix + '/api440/' + api + '/' + route, {method: 'POST', credentials: 'same-origin',
      headers: {'Content-Type': 'application/json', 'X-CSRF-Token': csrf}, body: JSON.stringify(data || {})});
    const v = await r.json();
    if (!r.ok) throw new Error(v.message || v.error || 'Demande refusée.');
    return v;
  }
  const status = document.querySelector('#c569-status');
  const note = text => { if (status) status.textContent = text; };

  // ---- routines (U01) : lancement sans double clic, pause des automatismes
  const routines = document.querySelector('#c5614-routines');
  if (routines) {
    const rs = document.querySelector('#c5614-routines-status');
    routines.addEventListener('click', async event => {
      const run = event.target.closest('[data-routine]');
      if (run) {
        if (run.disabled) return;
        run.disabled = true; run.textContent = 'Lancé…';
        try { await post('m530', 'routine', {kind: run.dataset.routine}); if (rs) rs.textContent = 'Routine lancée.'; }
        catch (e) { if (rs) rs.textContent = e.message; run.disabled = false; run.textContent = 'Lancer'; }
        return;
      }
      const view = event.target.closest('[data-tab-open]');
      if (view) {
        const tab = document.querySelector('#c530-routines [data-tab="' + view.dataset.tabOpen + '"]');
        document.querySelector('#c530-routines')?.scrollIntoView({behavior: 'smooth', block: 'start'});
        tab?.click();
        return;
      }
      const pause = event.target.closest('#c5614-pause');
      if (pause) {
        pause.disabled = true;
        try { const v = await post('m568', 'automatismes/pause', {paused: pause.dataset.paused !== '1'}); pause.dataset.paused = v.paused ? '1' : '0';
          pause.textContent = v.paused ? 'Automatismes en pause — reprendre' : 'Pause des automatismes'; pause.classList.toggle('warn', v.paused); pause.classList.toggle('ghost', !v.paused);
          pause.setAttribute('aria-pressed', String(v.paused)); if (rs) rs.textContent = v.message; }
        catch (e) { if (rs) rs.textContent = e.message; } finally { pause.disabled = false; }
      }
    });
  }

  // ---- À décider (U02) : panneau latéral, focus et état repliés conservés
  const open = document.querySelector('#c5614-open'), panel = document.querySelector('#c5614-panel'), close = document.querySelector('#c5614-close');
  let lastFocus = null;
  function showPanel(visible) {
    if (!panel) return;
    panel.hidden = !visible; open?.setAttribute('aria-expanded', String(visible));
    if (visible) { lastFocus = document.activeElement; (panel.querySelector('.c5614-card') || panel).focus(); }
    else if (lastFocus && lastFocus.isConnected) lastFocus.focus();
  }
  open?.addEventListener('click', () => showPanel(panel.hidden));
  close?.addEventListener('click', () => showPanel(false));
  document.addEventListener('keydown', e => { if (e.key === 'Escape' && panel && !panel.hidden) showPanel(false); });
  const badge = () => {
    const left = panel ? panel.querySelectorAll('.c5614-card').length : 0;
    const b = document.querySelector('#c5614-title .c530-badge'); if (b) b.textContent = String(left);
    if (!left && open) { open.textContent = 'Rien à décider'; open.classList.add('ghost'); open.disabled = true; showPanel(false); }
  };
  panel?.addEventListener('click', async event => {
    const button = event.target.closest('button[data-act]');
    if (!button) return;
    const card = button.closest('.c5614-card'), act = button.dataset.act, id = card.dataset.id, kind = card.dataset.kind;
    const field = name => card.querySelector('[data-field="' + name + '"]')?.value?.trim() || '';
    const answers = () => { const a = {}; card.querySelectorAll('[data-answer]').forEach(x => { if (x.value) a[x.dataset.answer] = x.value; }); return a; };
    if (button.dataset.confirm && !window.confirm(button.dataset.confirm)) return;
    button.disabled = true;
    try {
      let keep = false;
      if (act === 'defer') { await post('m568', 'decision/defer', {key: card.dataset.key, id, kind, days: 1}); note('Décision reportée à demain.'); }
      else if (act === 'm5614-validate') await post('m568', 'mission5614/control', {id: card.dataset.mission, action: 'validate', sha256: field('sha256'), motive: field('motive')});
      else if (act === 'm5614-answer') await post('m568', 'mission5614/decide', {id, answer: answers()});
      else if (act === 'm5614-reserve-fix') { await post('m568', 'mission5614/control', {id: card.dataset.mission, action: 'revise', instruction: 'Corriger les défauts signalés par le contrôle en conservant les passages validés.'}); }
      else if (act === 'm5614-budget') { if (!field('budget')) throw new Error('Indiquez le nouveau budget.'); await post('m568', 'mission5614/control', {id: card.dataset.mission, action: 'resume', budget_calls: Number(field('budget'))}); }
      else if (act === 'm5614-revise') { if (!field('instruction') && !field('codes')) throw new Error('Indiquez une consigne ou des codes de tâches.');
        await post('m568', 'mission5614/control', {id: card.dataset.mission, action: 'revise', instruction: field('instruction'), codes: field('codes').split(/[\s,;]+/).filter(Boolean)}); }
      else if (act === 'm5614-cancel') await post('m568', 'mission5614/control', {id: card.dataset.mission, action: 'cancel'});
      else if (act === 'mission-resolve') { if (!field('matter')) throw new Error('Choisissez le dossier.'); await post('m567', 'mission/control', {id, action: 'resolve', matter: field('matter')}); }
      else if (act === 'mission-resume') await post('m567', 'mission/control', {id, action: 'resume'});
      else if (act === 'mission-cancel') await post('m567', 'mission/control', {id, action: 'pause'});
      else if (act === 'mission-complete') { if (!field('instruction')) throw new Error('Indiquez l’instruction complémentaire.');
        await post('m567', 'mission/create', {request_key: crypto.randomUUID(), instruction: field('instruction'), matter: card.querySelector('[data-field="matter"]')?.value || '', context: {mission_id: id}, autonomy: 'prepare'}); keep = true; note('Mission de suite créée.'); }
      else if (act === 'docreq-resolve') await post('m568', 'docreq/control', {id, matter: field('matter')});
      else if (act === 'plan-resume') await post('m568', 'plan/control', {id, action: 'resume'});
      else if (act.startsWith('rule-')) await post('m567', 'rule/decide', {id, action: act === 'rule-adopt' ? 'adopt' : act === 'rule-adopt-matter' ? 'adopt_matter' : 'ignore'});
      else if (act === 'resolve') { const payload = {id, action: 'resolve'}; if (field('matter')) payload.matter = field('matter'); if (field('created')) { payload.created = field('created'); payload.proof = field('proof'); }
        if (!payload.matter && !payload.created) throw new Error('Choisissez un dossier ou indiquez la date de création avec sa preuve.'); await post('m568', 'document/control', payload); }
      else await post('m568', 'document/control', {id, action: act});
      if (act !== 'defer') note('Décision enregistrée ; l’agent reprend.');
      if (!keep) { const next = card.nextElementSibling || card.previousElementSibling; card.remove(); badge(); next?.focus?.(); }
      else button.disabled = false;
    } catch (e) { note(e.message); button.disabled = false; }
  });

  // ---- page des missions complexes
  const tree = document.querySelector('#m5614-page');
  if (tree) {
    tree.addEventListener('click', async event => {
      const b = event.target.closest('button[data-m5614]');
      if (!b) return;
      const act = b.dataset.m5614, id = b.dataset.id;
      if (b.dataset.confirm && !window.confirm(b.dataset.confirm)) return;
      b.disabled = true;
      try {
        if (act === 'revise') { const text = tree.querySelector('#m5614-instruction')?.value?.trim() || '', codes = (tree.querySelector('#m5614-codes')?.value || '').split(/[\s,;]+/).filter(Boolean);
          if (!text && !codes.length) throw new Error('Indiquez une consigne ou des codes de tâches.'); await post('m568', 'mission5614/control', {id, action: 'revise', instruction: text, codes}); }
        else if (act === 'validate') await post('m568', 'mission5614/control', {id, action: 'validate', sha256: b.dataset.sha || '', motive: tree.querySelector('#m5614-motive')?.value || ''});
        else if (act === 'run') await post('m568', 'mission5614/run', {id});
        else await post('m568', 'mission5614/control', {id, action: act});
        location.reload();
      } catch (e) { const s = tree.querySelector('#m5614-status'); if (s) s.textContent = e.message; b.disabled = false; }
    });
    tree.querySelectorAll('[data-m5614-filter]').forEach(f => f.addEventListener('click', () => {
      const mode = f.dataset.m5614Filter;
      tree.querySelectorAll('.m5614-task').forEach(t => { const st = t.dataset.state, oc = t.dataset.outcome;
        t.hidden = mode === 'blocages' ? !(st === 'en_erreur' || oc === 'bloque' || st === 'en_attente') : mode === 'resultats' ? !t.dataset.hasResult : false; });
      tree.querySelectorAll('[data-m5614-filter]').forEach(x => x.setAttribute('aria-pressed', String(x === f)));
    }));
  }
})();
