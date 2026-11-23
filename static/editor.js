// Notion-style block editor (Editor.js). Every paragraph, heading, list, image, file, table and diagram is its
// own block: drag the ⋮⋮ handle to move it, press "/" or "+" for a new one, drop files anywhere to add them.
// Pages are still stored as HTML, so viewing, search and history don't change:
// htmlToBlocks() runs when the editor opens and blocksToHtml() when the page is saved.
const holder = document.getElementById('blocks');
const form = holder.closest('form');
const field = form.querySelector('[name=html]');
const folderId = holder.dataset.folder;
const DRAWIO = 'https://embed.diagrams.net/?embed=1&proto=json&spin=1&noSaveBtn=1&saveAndExit=1';
const IMAGE = /\.(png|jpe?g|gif|webp)$/i;
const AUDIO = /\.(wav|mp3|ogg|m4a)$/i;
const esc = s => String(s ?? '').replace(/[&<>"]/g, c => ({'&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;'}[c]));
const ICON = {
  diagram: '<svg width="17" height="17" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8"><rect x="3" y="3" width="7" height="6" rx="1.5"/><rect x="14" y="15" width="7" height="6" rx="1.5"/><path d="M6.5 9v4.5a1.5 1.5 0 0 0 1.5 1.5h6"/></svg>',
  page: '<svg width="17" height="17" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8"><path d="M14 3H7a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h10a2 2 0 0 0 2-2V8z"/><path d="M14 3v5h5M10 15l4-4M11 11h3v3"/></svg>',
};

// ---------- uploads ----------

// Every file goes to PC1 (POST /upload), which saves it under data/uploads and returns its URL.
async function upload(file) {
  const fd = new FormData();
  fd.append('file', file, file.name || 'pasted.png');
  fd.append('folder_id', folderId);
  const r = await fetch('/upload', {method: 'POST', body: fd});
  if (!r.ok) throw new Error(r.status === 403 ? 'no edit rights for this folder' : 'server said ' + r.status);
  return r.json();
}
const fileData = (url, name, size) => ({url, name, title: name, size, extension: name.split('.').pop().toLowerCase()});
const sizeText = n => n < 1024 ? `${n} B` : n < 1048576 ? `${Math.round(n / 1024)} KB` : n < 1073741824 ? `${Math.round(n / 1048576)} MB` : `${(n / 1073741824).toFixed(1)} GB`;
const sizeBytes = t => { const m = /([\d.]+)\s*(B|KB|MB|GB)/.exec(t || ''); return m ? Math.round(m[1] * 1024 ** ['B', 'KB', 'MB', 'GB'].indexOf(m[2])) : undefined; };

// ---------- draw.io ----------

function dataUrlToFile(dataUrl, name) {
  const bin = atob(dataUrl.split(',')[1]);
  const bytes = new Uint8Array(bin.length);
  for (let i = 0; i < bin.length; i++) bytes[i] = bin.charCodeAt(i);
  return new File([bytes], name, {type: 'image/png'});
}
async function toDataUrl(src) {
  const blob = await (await fetch(src)).blob();
  return new Promise(res => { const fr = new FileReader(); fr.onload = () => res(fr.result); fr.readAsDataURL(blob); });
}

// Opens draw.io full screen with `xml` (a diagram PNG as a data URL, .drawio XML, or '' for a new one).
// Resolves with the uploaded PNG's URL (the PNG keeps the editable diagram inside it), or null if closed unsaved.
function drawio(xml) {
  return new Promise(resolve => {
    const frame = document.createElement('iframe');
    frame.className = 'drawio-frame';
    frame.src = DRAWIO;
    document.body.appendChild(frame);
    const send = m => frame.contentWindow.postMessage(JSON.stringify(m), '*');
    const done = url => { window.removeEventListener('message', onMessage); frame.remove(); resolve(url); };
    async function onMessage(e) {
      if (e.source !== frame.contentWindow || typeof e.data !== 'string') return;
      let m; try { m = JSON.parse(e.data); } catch { return; }
      if (m.event === 'init') send({action: 'load', autosave: 0, xml});
      else if (m.event === 'save') send({action: 'export', format: 'xmlpng', spinKey: 'saving'});
      else if (m.event === 'export') {
        try { done((await upload(dataUrlToFile(m.data, 'diagram.png'))).url); }
        catch (err) { alert('Saving the diagram failed: ' + err.message); }
      } else if (m.event === 'exit') done(null);
    }
    window.addEventListener('message', onMessage);
  });
}

// ---------- custom blocks ----------

class Diagram {
  static get toolbox() { return {title: 'Diagram', icon: ICON.diagram}; }
  constructor({data, api, block}) { this.data = data || {}; this.api = api; this.block = block; }
  render() {
    this.el = document.createElement('div');
    this.el.className = 'diagram-block';
    this.draw();
    if (!this.data.url) setTimeout(() => this.edit(''));  // a new diagram block opens draw.io straight away
    return this.el;
  }
  draw() {
    this.el.innerHTML = this.data.url
      ? `<img src="${esc(this.data.url)}" alt="diagram"><button type="button" class="plain">Edit diagram</button>`
      : '<p class="note">Drawing in draw.io…</p>';
    this.el.querySelector('button')?.addEventListener('click', async () => this.edit(await toDataUrl(this.data.url)));
  }
  async edit(xml) {
    const url = await drawio(xml);
    if (url) { this.data.url = url; this.draw(); this.block?.dispatchChange(); }
    else if (!this.data.url) this.api.blocks.delete(this.api.blocks.getBlockIndex(this.block.id));  // closed without saving
  }
  save() { return {url: this.data.url}; }
  validate(data) { return Boolean(data.url); }
}

class PageLink {
  static get toolbox() { return {title: 'Link to page', icon: ICON.page}; }
  constructor({data, block}) { this.data = data || {}; this.block = block; }
  render() {
    this.el = document.createElement('div');
    this.el.className = 'pagelink-block';
    this.data.url ? this.draw() : this.choose();
    return this.el;
  }
  draw() { this.el.innerHTML = `<a href="${esc(this.data.url)}">↗ ${esc(this.data.title)}</a>`; }
  async choose() {
    const select = document.createElement('select');
    select.innerHTML = '<option value="">Choose a page, folder or AAR…</option>';
    for (const group of await (await fetch('/links.json')).json()) {
      const og = document.createElement('optgroup');
      og.label = group.title;
      group.menu.forEach(i => og.append(new Option(i.title, i.value)));
      select.append(og);
    }
    select.addEventListener('change', () => {
      if (!select.value) return;
      this.data = {url: select.value, title: select.selectedOptions[0].text};
      this.draw();
      this.block?.dispatchChange();
    });
    this.el.replaceChildren(select);
    select.focus();
  }
  save() { return this.data; }
  validate(data) { return Boolean(data.url); }
}

// ---------- HTML <-> blocks ----------

function listItems(list) {
  return [...list.children].filter(li => li.tagName === 'LI').map(li => {
    const sub = li.querySelector(':scope > ul, :scope > ol');
    const copy = li.cloneNode(true);
    copy.querySelector(':scope > ul, :scope > ol')?.remove();
    return {content: copy.innerHTML.trim(), meta: li.dataset.checked ? {checked: li.dataset.checked === 'true'} : {},
            items: sub ? listItems(sub) : []};
  });
}

function htmlToBlocks(html) {
  // Editor.js keeps only its own inline tags (<b>, <i>, <a>), so map the usual equivalents onto them first.
  html = html.replace(/<(\/?)strong\b[^>]*>/gi, '<$1b>').replace(/<(\/?)em\b[^>]*>/gi, '<$1i>');
  const body = new DOMParser().parseFromString(`<body>${html}</body>`, 'text/html').body;
  const blocks = [];
  const add = (type, data) => blocks.push({type, data});
  const paragraph = h => { if (h.replace(/<br\s*\/?>|&nbsp;|\s/g, '')) add('paragraph', {text: h.trim()}); };
  for (const node of body.childNodes) {
    if (node.nodeType === Node.TEXT_NODE) { paragraph(esc(node.textContent)); continue; }
    if (node.nodeType !== Node.ELEMENT_NODE) continue;
    const el = node, tag = el.tagName;
    if (/^H[1-6]$/.test(tag)) add('header', {text: el.innerHTML.trim(), level: Math.min(Math.max(+tag[1], 2), 4)});
    else if (tag === 'UL' || tag === 'OL') add('list', {style: tag === 'OL' ? 'ordered' : el.classList.contains('checklist') ? 'checklist' : 'unordered', meta: {}, items: listItems(el)});
    else if (tag === 'TABLE') {
      const rows = [...el.rows];
      add('table', {withHeadings: Boolean(rows[0]?.querySelector('th')), content: rows.map(r => [...r.cells].map(c => c.innerHTML.trim()))});
    } else if (tag === 'PRE') add('code', {code: el.textContent});
    else if (tag === 'BLOCKQUOTE') {
      const caption = el.querySelector(':scope > footer');
      const copy = el.cloneNode(true); copy.querySelector(':scope > footer')?.remove();
      add('quote', {text: copy.innerHTML.trim(), caption: caption ? caption.innerHTML : '', alignment: 'left'});
    } else if (tag === 'HR') add('delimiter', {});
    else if (tag === 'FIGURE' && el.querySelector('img')) {
      add('image', {file: {url: el.querySelector('img').getAttribute('src')}, caption: el.querySelector('figcaption')?.innerHTML || '',
                    withBorder: false, withBackground: false, stretched: false});
    } else {
      const links = el.querySelectorAll('a'), text = el.textContent.trim();
      const only = links.length === 1 && !el.querySelector('img, audio') ? links[0] : null;
      if (only && /^📎/.test(only.textContent.trim()) && text.startsWith(only.textContent.trim())) {
        const name = only.textContent.replace('📎', '').trim();
        add('attaches', {file: fileData(only.getAttribute('href'), name, sizeBytes(el.querySelector('small')?.textContent)), title: name});
      } else if (only && /^↗/.test(text) && text === only.textContent.trim()) {
        add('pagelink', {url: only.getAttribute('href'), title: only.textContent.replace('↗', '').trim()});
      } else if (el.querySelector('img, audio')) {  // media become their own blocks; audio is redrawn with its file
        for (const img of el.querySelectorAll('img')) {
          if (img.hasAttribute('data-drawio')) add('diagram', {url: img.getAttribute('src')});
          else add('image', {file: {url: img.getAttribute('src')}, caption: '', withBorder: false, withBackground: false, stretched: false});
        }
        const rest = el.cloneNode(true);
        rest.querySelectorAll('img, audio').forEach(m => m.remove());
        paragraph(rest.innerHTML);
      } else paragraph(el.innerHTML);
    }
  }
  return blocks;
}

function listHtml(style, items) {
  const tag = style === 'ordered' ? 'ol' : 'ul';
  return `<${tag}${style === 'checklist' ? ' class="checklist"' : ''}>` + items.map(i =>
    `<li${style === 'checklist' ? ` data-checked="${Boolean(i.meta?.checked)}"` : ''}>${i.content}${i.items?.length ? listHtml(style, i.items) : ''}</li>`
  ).join('') + `</${tag}>`;
}

const RENDER = {
  paragraph: d => `<p>${d.text}</p>`,
  header: d => `<h${d.level}>${d.text}</h${d.level}>`,
  list: d => listHtml(d.style, d.items),
  table: d => '<table>' + d.content.map((row, i) => '<tr>' + row.map(c => d.withHeadings && i === 0 ? `<th>${c}</th>` : `<td>${c}</td>`).join('') + '</tr>').join('') + '</table>',
  code: d => `<pre>${esc(d.code)}</pre>`,
  quote: d => `<blockquote>${d.text}${d.caption ? `<footer>${d.caption}</footer>` : ''}</blockquote>`,
  delimiter: () => '<hr>',
  image: d => `<figure><img src="${esc(d.file.url)}" alt="">${d.caption ? `<figcaption>${d.caption}</figcaption>` : ''}</figure>`,
  diagram: d => `<p><img data-drawio="1" src="${esc(d.url)}" alt="diagram"></p>`,
  pagelink: d => `<p><a href="${esc(d.url)}">↗ ${esc(d.title)}</a></p>`,
  attaches: d => {
    const f = d.file, name = d.title || f.name;
    return `<p><a href="${esc(f.url)}">📎 ${esc(name)}</a>${f.size ? ` <small>${sizeText(f.size)}</small>` : ''}</p>`
      + (AUDIO.test(f.url) ? `<p><audio controls preload="none" src="${esc(f.url)}"></audio></p>` : '');
  },
};
const blocksToHtml = blocks => blocks.map(b => RENDER[b.type]?.(b.data) ?? '').join('\n');

// ---------- the editor ----------

const editor = new EditorJS({
  holder,
  placeholder: 'Type here. Press / for headings, lists, images, files, diagrams and more.',
  data: {blocks: htmlToBlocks(field.value)},
  tools: {
    header: {class: Header, inlineToolbar: true, config: {levels: [2, 3, 4], defaultLevel: 2}},
    list: {class: EditorjsList, inlineToolbar: true, config: {defaultStyle: 'unordered'}},
    image: {class: ImageTool, config: {uploader: {
      uploadByFile: async file => ({success: 1, file: {url: (await upload(file)).url}}),
      uploadByUrl: async url => ({success: 1, file: {url}}),
    }}},
    attaches: {class: AttachesTool, config: {buttonText: 'Choose a file to attach', uploader: {
      uploadByFile: async file => ({success: 1, file: fileData((await upload(file)).url, file.name, file.size)}),
    }}},
    diagram: Diagram,
    pagelink: PageLink,
    table: {class: Table, inlineToolbar: true, config: {withHeadings: true}},
    quote: {class: Quote, inlineToolbar: true},
    code: CodeTool,
    delimiter: Delimiter,
  },
  onReady: () => new DragDrop(editor),  // drag a block by its ⋮⋮ handle to move it
});

// Files dropped from the computer become image or file blocks where they land.
holder.addEventListener('drop', async e => {
  const files = [...(e.dataTransfer?.files || [])];
  if (!files.length) return;  // a block being moved: leave it to the drag-and-drop plugin
  e.preventDefault();
  e.stopPropagation();
  const target = e.target.closest('.ce-block');
  let index = target ? [...holder.querySelectorAll('.ce-block')].indexOf(target) + 1 : editor.blocks.getBlocksCount();
  for (const f of files) {
    try {
      const u = await upload(f);
      if (IMAGE.test(f.name)) editor.blocks.insert('image', {file: {url: u.url}, caption: ''}, {}, index++, false);
      else editor.blocks.insert('attaches', {file: fileData(u.url, f.name, f.size), title: f.name}, {}, index++, false);
    } catch (err) {
      alert(`Uploading ${f.name} failed: ${err.message}`);
    }
  }
}, true);

// Double-click an attached .drawio file (e.g. from the Drive import) to turn it into an editable diagram block.
holder.addEventListener('dblclick', async e => {
  const block = e.target.closest('.ce-block');
  const link = block?.querySelector('a[href$=".drawio"]');
  if (!link) return;
  e.preventDefault();
  const url = await drawio(await (await fetch(link.getAttribute('href'))).text());
  if (url) editor.blocks.insert('diagram', {url}, {}, [...holder.querySelectorAll('.ce-block')].indexOf(block), false, true);
});

form.addEventListener('submit', async e => {
  e.preventDefault();
  field.value = blocksToHtml((await editor.save()).blocks);
  form.submit();  // not requestSubmit(): that is ignored while this submit event is still being handled
});
