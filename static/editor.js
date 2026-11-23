// Notion-style block editor (Editor.js). Every paragraph, heading, list, image, file, table and diagram is its
// own block: drag the ⋮⋮ handle to move it, press "/" or "+" for a new one, drop files anywhere to add them.
// Pages are still stored as HTML, so viewing, search and history don't change:
// htmlToBlocks() runs when an editor opens and blocksToHtml() when it saves.
// Used two ways (bottom of this file): the full page editor, and each overview section on a folder page.
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
async function upload(file, folderId) {
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
// silent: load `xml` in a hidden draw.io and save it straight away (importing a .drawio file without any clicks).
function drawio(xml, folderId, silent = false) {
  return new Promise(resolve => {
    const frame = document.createElement('iframe');
    frame.className = silent ? 'drawio-frame silent' : 'drawio-frame';
    frame.src = DRAWIO;
    document.body.appendChild(frame);
    const send = m => frame.contentWindow.postMessage(JSON.stringify(m), '*');
    const timer = silent && setTimeout(() => { alert('draw.io could not read that file.'); done(null); }, 30000);
    const done = url => { clearTimeout(timer); window.removeEventListener('message', onMessage); frame.remove(); resolve(url); };
    async function onMessage(e) {
      if (e.source !== frame.contentWindow || typeof e.data !== 'string') return;
      let m; try { m = JSON.parse(e.data); } catch { return; }
      if (m.event === 'init') send({action: 'load', autosave: 0, xml});
      else if (m.event === 'load' && silent) send({action: 'export', format: 'xmlpng'});
      else if (m.event === 'save') send({action: 'export', format: 'xmlpng', spinKey: 'saving'});
      else if (m.event === 'export') {
        try { done((await upload(dataUrlToFile(m.data, 'diagram.png'), folderId)).url); }
        catch (err) { alert('Saving the diagram failed: ' + err.message); }
      } else if (m.event === 'exit') done(null);
    }
    window.addEventListener('message', onMessage);
  });
}

// ---------- custom blocks ----------

class Diagram {
  static get toolbox() { return {title: 'Diagram', icon: ICON.diagram}; }
  constructor({data, api, block, config}) { this.data = data || {}; this.api = api; this.block = block; this.folderId = config.folderId; }
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
    const url = await drawio(xml, this.folderId);
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

// Turns `holder` into a block editor holding `html`. Returns {html(): Promise<string>, destroy()}.
function createEditor(holder, html, folderId) {
  const editor = new EditorJS({
    holder,
    placeholder: 'Type here. Press / for headings, lists, images, files, diagrams and more.',
    data: {blocks: htmlToBlocks(html)},
    tools: {
      header: {class: Header, inlineToolbar: true, config: {levels: [2, 3, 4], defaultLevel: 2}},
      list: {class: EditorjsList, inlineToolbar: true, config: {defaultStyle: 'unordered'}},
      image: {class: ImageTool, config: {uploader: {
        uploadByFile: async file => ({success: 1, file: {url: (await upload(file, folderId)).url}}),
        uploadByUrl: async url => ({success: 1, file: {url}}),
      }}},
      attaches: {class: AttachesTool, config: {buttonText: 'Choose a file to attach', uploader: {
        uploadByFile: async file => ({success: 1, file: fileData((await upload(file, folderId)).url, file.name, file.size)}),
      }}},
      diagram: {class: Diagram, config: {folderId}},
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
        const u = await upload(f, folderId);
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
    const url = await drawio(await (await fetch(link.getAttribute('href'))).text(), folderId);
    if (url) editor.blocks.insert('diagram', {url}, {}, [...holder.querySelectorAll('.ce-block')].indexOf(block), false, true);
  });

  return {html: async () => blocksToHtml((await editor.save()).blocks), destroy: () => editor.destroy()};
}

// Full page editor: the page's HTML sits in a hidden textarea and is swapped for the blocks' HTML on save.
const pageHolder = document.getElementById('blocks');
if (pageHolder) {
  const form = pageHolder.closest('form');
  const field = form.querySelector('[name=html]');
  const ed = createEditor(pageHolder, field.value, pageHolder.dataset.folder);
  form.addEventListener('submit', async e => {
    e.preventDefault();
    field.value = await ed.html();
    form.submit();  // not requestSubmit(): that is ignored while this submit event is still being handled
  });
}

// Folder overview, Network diagram: no text, just the draw.io diagram.
//  - empty: drop a .drawio file on it, use a .drawio file attached to the folder, or draw a new one
//  - the + button imports a .drawio file from the computer (replacing the diagram, after asking)
//  - clicking the diagram opens it in draw.io to edit
const DIAGRAM_FILE = /\.(drawio|xml|png)$/i;

function readDiagramFile(file) {  // .drawio/.xml as text; a .png exported from draw.io as a data URL (it holds the diagram)
  return new Promise((res, rej) => {
    const fr = new FileReader();
    fr.onload = () => res(fr.result);
    fr.onerror = () => rej(fr.error);
    /\.png$/i.test(file.name) ? fr.readAsDataURL(file) : fr.readAsText(file);
  });
}

function diagramSection(section) {
  const body = section.querySelector('.ov-body');
  const add = section.querySelector('.pen');
  const picker = section.querySelector('input[type=file]');
  const folder = section.dataset.folder;

  async function show(url) {
    const fd = new FormData();
    fd.append('html', `<p><img data-drawio="1" src="${esc(url)}" alt="Network diagram"></p>`);
    const r = await fetch(section.dataset.save, {method: 'POST', body: fd});
    if (!r.ok) return alert(r.status === 403 ? 'You no longer have edit rights for this folder.' : 'Saving failed (' + r.status + ').');
    body.innerHTML = (await r.json()).html;
    section.dataset.empty = '';
  }
  async function importXml(xml) {
    body.classList.add('busy');
    const url = await drawio(xml, folder, true);
    body.classList.remove('busy');
    if (url) await show(url);
  }
  async function importFile(file) {
    if (!DIAGRAM_FILE.test(file.name)) return alert('Choose a draw.io file (.drawio).');
    if (!section.dataset.empty && !confirm('Replace the current network diagram with ' + file.name + '?')) return;
    await importXml(await readDiagramFile(file));
  }

  // buttons for .drawio files already attached to the folder (e.g. from the Drive import)
  const drop = body.querySelector('.diagram-drop .inline');
  const seen = new Set();
  document.querySelectorAll('.overview a[href$=".drawio"]').forEach(a => {
    const href = a.getAttribute('href');
    if (!drop || seen.has(href)) return;
    seen.add(href);
    const b = document.createElement('button');
    b.type = 'button';
    b.dataset.use = href;
    b.textContent = 'Use ' + a.textContent.replace('📎', '').trim();
    drop.insertBefore(b, drop.querySelector('[data-new]'));
  });

  add.addEventListener('click', () => picker.click());
  picker.addEventListener('change', () => { if (picker.files[0]) importFile(picker.files[0]); picker.value = ''; });
  body.addEventListener('click', async e => {
    const img = e.target.closest('img[data-drawio]');
    const use = e.target.closest('[data-use]');
    if (img) { const url = await drawio(await toDataUrl(img.getAttribute('src')), folder); if (url) await show(url); }
    else if (use) importXml(await (await fetch(use.dataset.use)).text());
    else if (e.target.closest('[data-new]')) { const url = await drawio('', folder); if (url) await show(url); }
  });
  body.addEventListener('dragover', e => {
    if (!section.dataset.empty || !e.dataTransfer.types.includes('Files')) return;
    e.preventDefault();
    body.classList.add('dragging');
  });
  body.addEventListener('dragleave', () => body.classList.remove('dragging'));
  body.addEventListener('drop', e => {
    body.classList.remove('dragging');
    const file = e.dataTransfer?.files?.[0];
    if (!file || !section.dataset.empty) return;
    e.preventDefault();
    importFile(file);
  });
}

// Folder overview, other sections: the pen turns just that section into a block editor, saved in place.
const EMPTY = '<p class="note">Nothing here yet.</p>';
document.querySelectorAll('.ov-section[data-save]').forEach(section => {
  const pen = section.querySelector('.pen');
  const body = section.querySelector('.ov-body');
  if (section.id === 'diagram') return diagramSection(section);
  pen.addEventListener('click', () => {
    const shown = body.innerHTML;
    const holder = document.createElement('div');
    holder.className = 'section-editor';
    const bar = document.createElement('div');
    bar.className = 'section-actions';
    bar.innerHTML = '<button type="button" class="plain">Cancel</button><button type="button">Save</button>';
    body.replaceChildren(holder);
    body.after(bar);
    section.classList.add('editing');
    pen.hidden = true;
    const ed = createEditor(holder, section.dataset.empty ? '' : shown, section.dataset.folder);
    const close = html => {
      ed.destroy();
      bar.remove();
      body.innerHTML = html || EMPTY;
      section.dataset.empty = html ? '' : '1';
      section.classList.remove('editing');
      pen.hidden = false;
    };
    const [cancel, save] = bar.querySelectorAll('button');
    cancel.addEventListener('click', () => close(section.dataset.empty ? '' : shown));
    save.addEventListener('click', async () => {
      save.disabled = true;
      const fd = new FormData();
      fd.append('html', await ed.html());
      const r = await fetch(section.dataset.save, {method: 'POST', body: fd});
      if (!r.ok) { save.disabled = false; return alert(r.status === 403 ? 'You no longer have edit rights for this folder.' : 'Saving failed (' + r.status + ').'); }
      close((await r.json()).html);
    });
  });
});
