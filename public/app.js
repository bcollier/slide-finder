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
  stacks = d.results; lastResult = d;
  for (const r of d.results) for (const v of (r.versions || [r])) evidenceById[v.slide.slide_id] = v;
  qstems = new Set((d.question || '').split(/\s+/).filter(w => w.length > 2 && !STOP.has(w.toLowerCase())).map(stem));
  results.innerHTML = d.results.map((r, i) => {
    const vs = r.versions || [r];
    return `<article class="hit${vs.length > 1 ? ' stack' : ''}" data-i="${i}" data-id="${esc(r.slide.slide_id)}">${cardInner(r, i, 0)}</article>`;
  }).join('') || '<p class="empty">Nothing stored yet.</p>';
}
let stacks = [];
const termLabel = t => { const m = /^(\d{4})\s+(\w+)\s*(.*)$/.exec(t || ''); return m ? `${m[2]} ${m[1]}${m[3] ? ' ' + m[3] : ''}` : (t || ''); };
function cardInner(stackTop, i, k) {
  const vs = stackTop.versions || [stackTop], r = vs[k], s = r.slide, w = r.why || {};
    const tabs = '';
  return `${r.thumb ? `<img src="${esc(r.thumb)}" alt="" loading="lazy">` : ''}
      <div class="body">
        <div class="title">${i + 1}. ${esc(s.title || '(untitled)')}</div>
        <div class="where">${esc(where(s))}${vs.length > 1 ? ` · <b>also in ${vs.length - 1} other ${vs.length - 1 === 1 ? 'course or term' : 'courses or terms'}</b>` : (s.used_in && s.used_in.length > 1 ? ` · <b>used in ${s.used_in.length} sessions</b>` : '')}</div>
        <div class="scores" title="score: fused rank score (higher is better). distance: cosine distance of the closest item (lower is better). meaning/keyword: best rank of this slide's items on each side.">
          <span>score <b>${r.score.toFixed(4)}</b></span>
          <span>distance <b>${r.distance == null ? '–' : r.distance.toFixed(3)}</b></span>
          <span>meaning <b>${r.vec_rank == null ? '–' : '#' + r.vec_rank}</b></span>
          <span>keyword <b>${r.fts_rank == null ? '–' : '#' + r.fts_rank}</b></span>
          <span>${r.items} item${r.items === 1 ? '' : 's'} hit</span>
        </div>
        ${bestLine(r)}
      </div>`;
}
const evidenceById = {};
function bestLine(r) {
  const it = (r.evidence || {}).items?.[0], w = r.why || {};
  const best = it && it.sentences && it.sentences.length ? it.sentences.slice().sort((a, b) => a.dist - b.dist)[0] : null;
  const LABEL = { card: 'Slide text says', said: 'I said', note: 'My note says', desc: 'The image shows' };
  if (best) return `<div class="why"><b>${LABEL[it.kind]}:</b> ${esc(best.text)}</div>`;
  const whyLabel = LABEL[w.kind] || '';
  return `<div class="why"><b>${whyLabel}:</b> ${esc(w.text || '').replace(/^\[[^\]]*\]\s*/, '')}</div>`;
}
const STOP = new Set('a an the of to in on for and or is are was were be been how do does did i my we you it this that what which with by as at from into can should would will when where why'.split(' '));
const stem = w => w.toLowerCase().replace(/[^a-z0-9]/g, '').slice(0, 5);
let qstems = new Set();
const KIND = { card: 'slide text', desc: 'image description', said: 'narration (what I said)', note: 'my note' };
const ordinal = n => n + (['th', 'st', 'nd', 'rd'][(n % 100 > 10 && n % 100 < 14) ? 0 : Math.min(n % 10, 4)] || 'th');
const markWords = t => esc(t).split(' ').map(w => qstems.has(stem(w)) && stem(w).length >= 3 ? `<mark>${w}</mark>` : w).join(' ');
const closeness = d => Math.max(0, Math.min(1, (0.68 - d) / (0.68 - 0.34)));
const gpos = d => Math.max(0, Math.min(100, (d - 0.30) / (0.70 - 0.30) * 100));
function evidence(r, s) {
  const ev = r.evidence || { items: [], misses: {} }, items = ev.items, it = items[0];
  if (!it) return '';
  const d = lastResult, W = 0.5;
  // D. in words, with the score split
  let why;
  if (it.vec_rank != null && it.vec_rank <= 3) why = `Ranked here because its <b>${KIND[it.kind]}</b> was the <b>${ordinal(it.vec_rank)} closest item in the whole index</b> (distance ${it.distance.toFixed(2)})`;
  else if (it.vec_rank != null) why = `Its <b>${KIND[it.kind]}</b> was the ${ordinal(it.vec_rank)} closest item by meaning (distance ${it.distance.toFixed(2)})`;
  else why = `Here on <b>keywords alone</b>: its ${KIND[it.kind]} contains every word of the question (keyword #${it.fts_rank}), but nothing on this slide is close in meaning`;
  why += it.fts_rank != null && it.vec_rank != null ? `, and it also matched the keywords (#${it.fts_rank}).` : (it.vec_rank != null ? '; no keyword match.' : '.');
  const others = items.length - 1;
  if (others > 0) why += ` ${others} other item${others > 1 ? 's' : ''} of this slide also matched${items[1].distance != null ? ` (next closest ${items[1].distance.toFixed(2)})` : ''}.`;
  if (r.distance != null && d && r.distance > d.weak_distance) why += ` <span class="warn">Past the weak-match line.</span>`;
  const mPart = it.vec_rank != null ? 1 / (60 + it.vec_rank) : 0, kPart = it.fts_rank != null ? W / (60 + it.fts_rank) : 0, max = 1 / 61 + W / 61;
  const D = `<div class="because">${why}</div>
    <div class="score-split" title="score ${(mPart + kPart).toFixed(4)} of a possible ${max.toFixed(4)}"><i class="m" style="width:${(mPart / max * 100).toFixed(1)}%">${mPart ? 'meaning' : ''}</i><i class="k" style="width:${(kPart / max * 100).toFixed(1)}%">${kPart ? 'keyword' : ''}</i></div>`;
  // C. distance gauge
  const weak = d ? d.weak_distance : 0.55, pageBest = d ? d.best_distance : null;
  const dots = items.filter(x => x.distance != null).map((x, i) => `<span class="dot ${x.kind}${i === 0 ? ' best' : ''}" style="left:${gpos(x.distance)}%" title="${KIND[x.kind]} · distance ${x.distance.toFixed(3)}"></span>`).join('');
  const ticks = [0.3, 0.4, 0.5, 0.6, 0.7].map(v => `<span class="tick" style="left:${gpos(v)}%">${v.toFixed(1)}</span>`).join('');
  const C = `<div class="gauge"><div class="axis"></div><div class="weakzone" style="left:${gpos(weak)}%"></div><span class="wk" style="left:${gpos(weak)}%" title="weak beyond ${weak}"></span>${ticks}${dots}
    ${r.distance != null ? `<span class="lbl" style="left:${gpos(r.distance)}%">this slide ${r.distance.toFixed(2)}</span>` : ''}
    ${pageBest != null ? `<span class="lbl best" style="left:${gpos(pageBest)}%">▲ best on page</span>` : ''}</div>`;
  // A. evidence ledger
  const rows = items.map(x => {
    const dist = x.distance, w = dist == null ? 0 : Math.round(closeness(dist) * 100);
    const ranks = [x.vec_rank != null ? `${ordinal(x.vec_rank)} closest item in the index by meaning` : '', x.fts_rank != null ? `keyword rank #${x.fts_rank}` : ''].filter(Boolean).join(' · ');
    const part = x.kind === 'said' ? (/-said-(?:[a-z0-9]+-)?(\d+)$/.exec(x.id) || [])[1] : null;
    const label = x.kind === 'said' ? `narration${part != null ? ` part ${+part + 1}` : ''}<br><small>${esc(termLabel(x.term))}</small>` : KIND[x.kind];
    return `<div class="ev"><span class="kind ${x.kind}">${label}</span><div class="bar${dist != null && dist > weak ? ' weakish' : ''}"><i style="width:${w}%"></i></div><span class="num">${dist == null ? '–' : dist.toFixed(2)}</span><span class="ranks">${ranks}</span></div>`;
  }).join('');
  const missed = Object.keys(ev.misses).length ? `<div class="miss-line">Did not match: ${Object.entries(ev.misses).map(([k, n]) => `${n} ${KIND[k]}${k === 'said' ? (n > 1 ? ' chunks' : ' chunk') : ''}`).join(', ')}. Narration is stored in ~120-word chunks, each matched on its own.</div>` : '<div class="miss-line">Every item of this slide matched. Narration is stored in ~120-word chunks, each matched on its own.</div>';
  const A = `<div class="ledger">${rows}</div>${missed}`;
  // B. heat text of the best item
  let B;
  if (it.sentences && it.sentences.length) {
    const sents = it.sentences.slice().sort((a, b) => it.text.indexOf(a.text) - it.text.indexOf(b.text));
    B = sents.map(x => `<span class="s" style="background:rgba(var(--heat),${Math.max(0, Math.min(.85, (0.62 - x.dist) / 0.3)).toFixed(2)})" title="distance ${x.dist.toFixed(2)}">${markWords(x.text)}</span>`).join(' ');
  } else B = markWords(it.text);
  const best = it.sentences && it.sentences.length ? it.sentences.slice().sort((a, b) => a.dist - b.dist)[0] : null;
  const quote = best ? `<div class="why"><b>Best sentence</b> (${best.dist.toFixed(2)}): ${esc(best.text)}</div>` : `<div class="why"><b>${KIND[it.kind]}:</b> ${markWords(it.text.slice(0, 200))}</div>`;
  return `${D}${C}${quote}<div class="evidence">${A}
    <div class="heat-head"><span class="kind ${it.kind}">${KIND[it.kind]}</span> <span class="hint">each sentence shaded by its own closeness · <mark>underlined</mark> = a word from the question</span></div><div class="heat">${B}</div></div>`;
}
let lastResult = null;
results.addEventListener('click', e => {
  const v = e.target.closest('.ver');
  if (!v) return;
  e.stopPropagation();
  const h = v.closest('.hit'), i = +h.dataset.i, k = +v.dataset.k;
  h.innerHTML = cardInner(stacks[i], i, k);
  h.dataset.id = stacks[i].versions[k].slide.slide_id;
}, true);
results.addEventListener('click', e => { const h = e.target.closest('.hit'); if (h) openSlide(h.dataset.id); });
$('#versions').addEventListener('click', e => { const b = e.target.closest('.open-version'); if (b) openSlide(b.dataset.id); });

// ---- slide view ------------------------------------------------------------
let currentSlide = null;
function occBlock(o) {
  const win = (o.windows || [])[0];
  const t = win ? `${hms(win[0])}–${hms(win[win.length - 1] ?? win[1])}` : '';
  const mins = (o.windows || []).reduce((a, w) => a + (w[1] - w[0]), 0);
  return `<div class="occ collapsed"><div class="occ-head"><span><b>${esc(o.term)}</b> · session ${o.session}</span>
    <span>${t}${mins ? ` · ${Math.round(mins / 60)} min on screen` : ''}${o.method ? ` · matched by ${o.method === 'frame' ? 'video' : o.method + ' (lower confidence)'}` : ''}</span></div>
    <p>${esc(o.transcript)}</p><button class="more">Show all</button></div>`;
}
async function openSlide(id) {
  const d = await api('/api/slide/' + encodeURIComponent(id));
  currentSlide = d.slide;
  $('#slide-img').src = d.image || ''; $('#open-img').href = d.image || '#';
  $('#slide-title').textContent = d.slide.title || '(untitled)';
  const ev = evidenceById[id];
  $('#why').innerHTML = ev && lastResult ? `<h3>Why this slide was chosen</h3><p class="hint">for “${esc(lastResult.question)}”</p>${evidence(ev, ev.slide)}` : '';
  $('#slide-where').textContent = where(d.slide) + (d.slide.used_in?.length > 1 ? `\nAlso used in: ${d.slide.used_in.slice(1).join('; ')}` : '');
  $('#said').innerHTML = d.occurrences.length ? d.occurrences.map(occBlock).join('') : '<p class="hint">No recording matched to this slide yet.</p>';
  $('#versions').innerHTML = (d.versions || []).length ? `<h3>Also in other courses</h3><p class="hint">The same slide, as taught elsewhere, with that course's narration.</p>` + d.versions.map(v => `
    <div class="version"><div class="version-head">${v.thumb ? `<img src="${esc(v.thumb)}" alt="">` : ''}<div><b>${esc(v.slide.course_name)}</b> · ${esc(termLabel(v.slide.term))}<br><small class="hint">${esc(v.slide.session_title)}, slide ${v.slide.slide_number}${v.slide.used_in?.length > 1 ? ` · used in ${v.slide.used_in.length} sessions` : ''}</small></div><button class="ghost open-version" data-id="${esc(v.slide.slide_id)}">Open</button></div>
    ${v.occurrences.length ? v.occurrences.map(o => occBlock(o)).join('') : '<p class="hint">No recording matched to this version.</p>'}</div>`).join('') : '';
  // "said" chunks: the narration from one term is cut into ~120-word pieces, each its own item.
  const parts = {};
  for (const it of d.items || []) if (it.kind === 'said') parts[it.occurrence_id || it.term] = (parts[it.occurrence_id || it.term] || 0) + 1;
  const seen = {};
  $('#items').innerHTML = (d.items || []).map(it => {
    let head = { said: 'narration', desc: 'image description' }[it.kind] || it.kind;
    if (it.kind === 'said') {
      const key = it.occurrence_id || it.term, n = parts[key]; seen[key] = (seen[key] || 0) + 1;
      head += ` · ${esc(it.term || '')}` + (n > 1 ? ` · part ${seen[key]} of ${n}` : '');
    }
    return `<div class="item"><div class="item-head">${head} · ${esc(it.id)} · ${it.text.split(/\s+/).length} words</div><pre>${esc(it.text)}</pre></div>`;
  }).join('') || '<p class="hint">Nothing indexed.</p>';
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

const EXAMPLES = [
  'how do I group customers into meaningful segments',
  'how do I predict demand in the future',
  'teach the machine by rewarding it when it does well',
  'how does a computer read a photo',
  'why does the chatbot make things up',
  'how do I get an AI to do a multi-step job on its own',
  'what should a company do first with AI',
  'it fits the data it has seen perfectly and falls apart on anything new',
  'how are the words in a sentence turned into numbers',
  'how do I find the main themes in a pile of documents',
];

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
  else results.innerHTML = `<div class="empty"><p>Try one of these:</p>` + EXAMPLES.map(x => `<button class="ghost ex">${esc(x)}</button>`).join('') + `</div>`;
}
results.addEventListener('click', e => { if (e.target.classList.contains('ex')) { q.value = e.target.textContent; run(); } });
init();
