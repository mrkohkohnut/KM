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
