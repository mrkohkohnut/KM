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
