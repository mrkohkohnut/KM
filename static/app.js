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
