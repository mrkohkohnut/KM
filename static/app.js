// Forms with data-confirm ask before submitting (inline handlers are blocked by the CSP).
document.addEventListener('submit', e => {
  const msg = e.target.dataset.confirm;
  if (msg && !confirm(msg)) e.preventDefault();
});

// "On this page": list the page's headings in the right column.
const toc = document.getElementById('toc');
if (toc) {
  const heads = document.querySelectorAll('.content h1:not(.pagetitle), .content h2, .content h3');
  heads.forEach((h, i) => {
    h.id ||= 'section-' + (i + 1);
    const a = document.createElement('a');
    a.href = '#' + h.id; a.textContent = h.textContent; a.className = 'lvl-' + h.tagName[1];
    toc.append(a);
  });
  document.getElementById('toc-box').hidden = heads.length < 2;
}

// AAR form: "Add another point" copies the last point with empty boxes.
document.getElementById('add-point')?.addEventListener('click', () => {
  const list = document.getElementById('points');
  const copy = list.lastElementChild.cloneNode(true);
  copy.querySelectorAll('textarea').forEach(t => { t.value = ''; });
  copy.querySelector('.n').textContent = list.children.length + 1;
  list.append(copy);
  copy.querySelector('textarea').focus();
});

// draw.io diagrams with several sheets: show one sheet at a time with a tab for each.
function setupSheets(box) {
  const sheets = [...box.querySelectorAll(':scope > figure')];
  if (sheets.length < 2 || box.querySelector('.sheet-tabs')) return;
  const tabs = document.createElement('div');
  tabs.className = 'sheet-tabs';
  tabs.setAttribute('role', 'tablist');
  const select = i => sheets.forEach((s, j) => {
    s.hidden = i !== j;
    tabs.children[j].setAttribute('aria-selected', String(i === j));
  });
  sheets.forEach((s, i) => {
    const b = document.createElement('button');
    b.type = 'button';
    b.setAttribute('role', 'tab');
    b.textContent = s.dataset.sheet || `Page ${i + 1}`;
    b.addEventListener('click', () => select(i));
    tabs.append(b);
  });
  box.prepend(tabs);
  select(0);
}
document.querySelectorAll('.drawio-sheets').forEach(setupSheets);

// Documentation and Slides: picking files uploads them straight away; files can also be dropped on the card.
document.querySelectorAll('.upload-form input[type=file]').forEach(input =>
  input.addEventListener('change', () => input.files.length && input.form.submit()));
document.querySelectorAll('.docs[data-upload], #resources[data-upload]').forEach(card => {
  card.addEventListener('dragover', e => {
    if (!e.dataTransfer.types.includes('Files')) return;
    e.preventDefault();
    card.classList.add('dragging');
  });
  card.addEventListener('dragleave', e => { if (!card.contains(e.relatedTarget)) card.classList.remove('dragging'); });
  card.addEventListener('drop', async e => {
    const files = [...(e.dataTransfer?.files || [])];
    if (!files.length) return;
    e.preventDefault();
    card.classList.remove('dragging');
    card.classList.add('busy');
    const fd = new FormData();
    files.forEach(f => fd.append('file', f, f.name));
    const r = await fetch(card.dataset.upload, {method: 'POST', body: fd, headers: {'X-Requested-With': 'fetch'}});
    if (!r.ok) alert(r.status === 403 ? 'You don\'t have edit rights for this folder.' : 'Uploading failed (' + r.status + ').');
    location.reload();
  });
});

// Explorer: remember which folders and groups this browser opened (the current folder always starts open).
const TREE_KEY = 'km-explorer-open';
let treeOpen = {};
try { treeOpen = JSON.parse(localStorage.getItem(TREE_KEY)) || {}; } catch {}
document.querySelectorAll('.explorer details[data-key]').forEach(d => {
  if (!d.open && treeOpen[d.dataset.key]) d.open = true;  // what the page opens itself stays open
  d.addEventListener('toggle', () => {
    treeOpen[d.dataset.key] = d.open;
    try { localStorage.setItem(TREE_KEY, JSON.stringify(treeOpen)); } catch {}
  });
});
