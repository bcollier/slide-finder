// Slide Finder front end. Talks to /api/*; no keys live here.
const $ = (s, el = document) => el.querySelector(s);
const results = $('#results'), status = $('#status'), q = $('#q');
let picksCount = 0;

async function api(path, opts = {}) {
  const r = await fetch(path, { headers: { 'Content-Type': 'application/json' }, ...opts });
  if (r.status === 401) { $('#login').showModal(); throw new Error('passcode'); }
  if (!r.ok) throw new Error((await r.json().catch(() => ({}))).detail || r.statusText);
  return r.json();
}
const esc = s => String(s ?? '').replace(/[&<>"]/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c]));
const where = s => `${s.course} ${s.course_name} · ${s.term} · ${s.session_title}, slide ${s.slide_number}`;

// ---- login -----------------------------------------------------------------
$('#login-form').addEventListener('submit', async e => {
  e.preventDefault();
  try {
    await api('/api/login', { method: 'POST', body: JSON.stringify({ passcode: $('#passcode').value }) });
    $('#login').close(); init();
  } catch (err) { $('#login-err').textContent = err.message; }
});

// ---- search ----------------------------------------------------------------
$('#search').addEventListener('submit', e => { e.preventDefault(); run(); });
$('#mode').addEventListener('change', () => q.value.trim() && run());
const nEl = $('#n'), nVal = $('#n-val');
try { const saved = localStorage.getItem('sf_n'); if (saved) { nEl.value = saved; nVal.textContent = saved; } } catch {}
nEl.addEventListener('input', () => nVal.textContent = nEl.value);
nEl.addEventListener('change', () => { try { localStorage.setItem('sf_n', nEl.value); } catch {} if (q.value.trim()) run(); });
$('#course').addEventListener('change', () => q.value.trim() && run());

async function run() {
  const question = q.value.trim();
  if (!question) return;
  history.replaceState(null, '', '#' + encodeURIComponent(question));
  status.textContent = 'Searching…'; results.innerHTML = '';
  try {
    const p = new URLSearchParams({ q: question, course: $('#course').value, mode: $('#mode').value, n: nEl.value });
    const d = await api('/api/search?' + p);
    render(d);
  } catch (err) { if (err.message !== 'passcode') status.textContent = 'Error: ' + err.message; }
}

function render(d) {
  const n = d.results.length;
  status.innerHTML = d.weak
    ? `<span class="weak">I don't think I taught this. The closest slides are below, but treat them as weak matches` +
      (d.best_distance != null ? ` (best distance ${d.best_distance.toFixed(2)}).` : '.') + `</span>`
    : `${n} slide${n === 1 ? '' : 's'}` + (d.best_distance != null ? ` · best distance ${d.best_distance.toFixed(2)}` : '');
  results.innerHTML = d.results.map((r, i) => {
    const s = r.slide, w = r.why || {};
    const whyLabel = { card: 'Slide says', said: 'I said', note: 'My note' }[w.kind] || '';
    return `<article class="hit" data-id="${esc(s.slide_id)}">
      ${r.thumb ? `<img src="${esc(r.thumb)}" alt="" loading="lazy">` : ''}
      <div class="body">
        <div class="title">${i + 1}. ${esc(s.title || '(untitled)')}</div>
        <div class="where">${esc(where(s))}</div>
        <div class="kinds">${r.kinds.map(k => `<span class="kind ${k}">${k === 'said' ? 'what I said' : k}</span>`).join('')}</div>
        <div class="scores" title="score: fused rank score (higher is better). distance: cosine distance of the closest item (lower is better). meaning/keyword: best rank of this slide's items on each side.">
          <span>score <b>${r.score.toFixed(4)}</b>${r.score !== r.raw_score ? ` <small>(${r.raw_score.toFixed(4)} +${Math.round(d.kind_bonus * 100)}%)</small>` : ''}</span>
          <span>distance <b>${r.distance == null ? '–' : r.distance.toFixed(3)}</b></span>
          <span>meaning <b>${r.vec_rank == null ? '–' : '#' + r.vec_rank}</b></span>
          <span>keyword <b>${r.fts_rank == null ? '–' : '#' + r.fts_rank}</b></span>
          <span>${r.items} item${r.items === 1 ? '' : 's'} hit</span>
        </div>
        <div class="why"><b>${whyLabel}:</b> ${esc(w.text || '').replace(/^\[[^\]]*\]\s*/, '')}</div>
      </div></article>`;
  }).join('') || '<p class="empty">Nothing stored yet.</p>';
}
results.addEventListener('click', e => { const h = e.target.closest('.hit'); if (h) openSlide(h.dataset.id); });

// ---- slide view ------------------------------------------------------------
let currentSlide = null;
async function openSlide(id) {
  const d = await api('/api/slide/' + encodeURIComponent(id));
  currentSlide = d.slide;
  $('#slide-img').src = d.image || ''; $('#open-img').href = d.image || '#';
  $('#slide-title').textContent = d.slide.title || '(untitled)';
  $('#slide-where').textContent = where(d.slide) + (d.slide.used_in?.length > 1 ? ` · used in ${d.slide.used_in.join(', ')}` : '');
  $('#said').innerHTML = d.occurrences.length ? d.occurrences.map(o => {
    const win = (o.windows || [])[0];
    const t = win ? `${hms(win[0])}–${hms(win[win.length - 1] ?? win[1])}` : '';
    const mins = (o.windows || []).reduce((a, w) => a + (w[1] - w[0]), 0);
    return `<div class="occ collapsed"><div class="occ-head"><span><b>${esc(o.term)}</b> · session ${o.session}</span>
      <span>${t}${mins ? ` · ${Math.round(mins / 60)} min on screen` : ''}${o.method ? ` · matched by ${o.method === 'frame' ? 'video' : o.method}` : ''}</span></div>
      <p>${esc(o.transcript)}</p><button class="more">Show all</button></div>`;
  }).join('') : '<p class="hint">No recording matched to this slide yet.</p>';
  $('#items').innerHTML = (d.items || []).map(it => `<div class="item"><div class="item-head">${it.kind === 'said' ? 'what I said' : it.kind} · ${esc(it.id)} · ${it.text.split(/\s+/).length} words</div><pre>${esc(it.text)}</pre></div>`).join('') || '<p class="hint">Nothing indexed.</p>';
  $('#notes').innerHTML = d.notes.map(n => `<li>${esc(n.text.replace(/^\[[^\]]*\]\s*/, ''))}</li>`).join('') || '<li class="hint">None yet.</li>';
  $('#slide').showModal();
}
$('#said').addEventListener('click', e => {
  if (e.target.classList.contains('more')) { const o = e.target.closest('.occ'); o.classList.toggle('collapsed'); e.target.textContent = o.classList.contains('collapsed') ? 'Show all' : 'Show less'; }
});
const hms = s => { s = Math.round(s); const h = Math.floor(s / 3600), m = Math.floor(s % 3600 / 60), x = s % 60; return (h ? h + ':' : '') + String(m).padStart(h ? 2 : 1, '0') + ':' + String(x).padStart(2, '0'); };

$('#note-form').addEventListener('submit', async e => {
  e.preventDefault();
  const text = $('#note-text').value.trim(); if (!text || !currentSlide) return;
  await api('/api/notes', { method: 'POST', body: JSON.stringify({ slide_id: currentSlide.slide_id, text }) });
  $('#note-text').value = '';
  openSlide(currentSlide.slide_id);
});
$('#pick-btn').addEventListener('click', async () => {
  if (!currentSlide) return;
  const r = await api('/api/picks', { method: 'POST', body: JSON.stringify({ slide_id: currentSlide.slide_id }) });
  $('#pick-btn').textContent = r.already ? 'Already picked' : 'Picked ✓';
  setTimeout(() => $('#pick-btn').textContent = 'Pick for new deck', 1500);
  refreshPicks();
});

// ---- picks -----------------------------------------------------------------
async function refreshPicks() {
  const p = await api('/api/picks'); picksCount = p.length; $('#picks-count').textContent = p.length;
  $('#picks-list').innerHTML = p.map(x => `<li>${x.thumb ? `<img src="${esc(x.thumb)}" alt="">` : ''}<span>${esc(x.slide.title || '(untitled)')}<br><small class="hint">${esc(where(x.slide))}</small></span>
    <button class="rm ghost" data-pos="${x.position}">Remove</button></li>`).join('') || '<p class="hint">Nothing picked yet. Open a slide and press “Pick for new deck”.</p>';
  $('#export').style.display = p.length ? '' : 'none';
}
$('#picks-btn').addEventListener('click', async () => { await refreshPicks(); $('#picks').showModal(); });
$('#picks-list').addEventListener('click', async e => {
  const b = e.target.closest('.rm'); if (!b) return;
  await api('/api/picks/' + b.dataset.pos, { method: 'DELETE' }); refreshPicks();
});

// ---- add deck (browser renders the PDF, uploads page images, server embeds the text) ----
$('#add-deck-btn').addEventListener('click', () => $('#add-deck').showModal());
$('#deck-form').addEventListener('submit', async e => {
  e.preventDefault();
  const file = $('#deck-file').files[0]; if (!file) return;
  const st = $('#deck-status'), btn = $('#deck-go'); btn.disabled = true;
  try {
    const pdfjs = await import('https://cdnjs.cloudflare.com/ajax/libs/pdf.js/4.6.82/pdf.min.mjs');
    pdfjs.GlobalWorkerOptions.workerSrc = 'https://cdnjs.cloudflare.com/ajax/libs/pdf.js/4.6.82/pdf.worker.min.mjs';
    const pdf = await pdfjs.getDocument({ data: await file.arrayBuffer() }).promise;
    const name = $('#deck-name').value.trim() || file.name.replace(/\.pdf$/i, '');
    const course = $('#deck-course').value.trim() || 'mine';
    st.textContent = `Preparing ${pdf.numPages} pages…`;
    const begin = await api('/api/decks/begin', { method: 'POST', body: JSON.stringify({ name, pages: pdf.numPages }) });
    const pages = [];
    for (let n = 1; n <= pdf.numPages; n++) {
      const page = await pdf.getPage(n);
      const vp = page.getViewport({ scale: 1600 / page.getViewport({ scale: 1 }).width });
      const canvas = Object.assign(document.createElement('canvas'), { width: vp.width, height: vp.height });
      await page.render({ canvasContext: canvas.getContext('2d'), viewport: vp }).promise;
      const blob = await new Promise(res => canvas.toBlob(res, 'image/webp', 0.82));
      const u = begin.uploads[n - 1];
      const put = await fetch(u.url, { method: 'PUT', headers: { 'Content-Type': 'image/webp' }, body: blob });
      if (!put.ok) throw new Error('upload failed on page ' + n);
      const tc = await page.getTextContent();
      const lines = []; let last = null;
      for (const it of tc.items) { if (last && Math.abs(it.transform[5] - last) > 2) lines.push('\n'); lines.push(it.str); last = it.transform[5]; }
      const text = lines.join(' ').replace(/[ \t]+/g, ' ').replace(/\s*\n\s*/g, '\n').trim();
      pages.push({ page: n, title: (text.split('\n')[0] || '').slice(0, 120), text: text.slice(0, 8000) });
      st.textContent = `Uploaded page ${n} of ${pdf.numPages}`;
    }
    st.textContent = 'Indexing…';
    const r = await api('/api/decks/finish', { method: 'POST', body: JSON.stringify({ deck_id: begin.deck_id, name, course, course_name: course === 'mine' ? 'My decks' : course, term: 'added', pages }) });
    st.textContent = `Done: ${r.slides} slides are searchable now.`;
    loadCourses();
  } catch (err) { st.textContent = 'Error: ' + err.message; }
  btn.disabled = false;
});

// ---- misc ------------------------------------------------------------------
document.querySelectorAll('[data-close]').forEach(b => b.addEventListener('click', () => b.closest('dialog').close()));
document.querySelectorAll('dialog').forEach(d => d.addEventListener('click', e => { if (e.target === d) d.close(); }));

async function loadCourses() {
  const cs = await api('/api/courses');
  const sel = $('#course'), cur = sel.value;
  sel.innerHTML = '<option value="">All courses</option>' + cs.map(c => `<option value="${esc(c.course)}">${esc(c.course)} ${esc(c.course_name)}</option>`).join('');
  sel.value = cur;
}

async function init() {
  const me = await api('/api/me');
  if (me.needs_passcode && !me.logged_in) { $('#login').showModal(); return; }
  await Promise.all([loadCourses(), refreshPicks()]);
  const h = decodeURIComponent(location.hash.slice(1));
  if (h) { q.value = h; run(); }
  else results.innerHTML = `<div class="empty"><p>Try one of these:</p>
    <button class="ghost ex">it aced the practice problems but bombed the real test</button>
    <button class="ghost ex">which shoppers behave alike</button>
    <button class="ghost ex">how does a computer read a photo</button></div>`;
}
results.addEventListener('click', e => { if (e.target.classList.contains('ex')) { q.value = e.target.textContent; run(); } });
init();
