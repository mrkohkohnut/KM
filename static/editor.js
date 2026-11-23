const dark = window.matchMedia('(prefers-color-scheme: dark)').matches;
const folderId = document.getElementById('editor').dataset.folder;
const DRAWIO = 'https://embed.diagrams.net/?embed=1&proto=json&spin=1&noSaveBtn=1&saveAndExit=1';
const esc = s => s.replace(/[&<>"]/g, c => ({'&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;'}[c]));

// Every file goes to PC1 (POST /upload), which saves it under data/uploads and returns its URL.
async function upload(file) {
  const fd = new FormData();
  fd.append('file', file, file.name || 'pasted.png');
  fd.append('folder_id', folderId);
  const r = await fetch('/upload', {method: 'POST', body: fd});
  if (!r.ok) throw new Error(r.status === 403 ? 'no edit rights for this folder' : 'server said ' + r.status);
  return r.json();
}

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

// draw.io embed: the diagram is saved as a PNG with the editable diagram data inside it.
// img: an existing diagram to edit. xml + replace: an imported .drawio file and the link to swap for the diagram.
function openDrawio(ed, img, xml = '', replace = null) {
  const frame = document.createElement('iframe');
  frame.className = 'drawio-frame';
  frame.src = DRAWIO;
  document.body.appendChild(frame);
  const send = m => frame.contentWindow.postMessage(JSON.stringify(m), '*');
  const close = () => { window.removeEventListener('message', onMessage); frame.remove(); };
  async function onMessage(e) {
    if (e.source !== frame.contentWindow || typeof e.data !== 'string') return;
    let m; try { m = JSON.parse(e.data); } catch { return; }
    if (m.event === 'init') {
      send({action: 'load', autosave: 0, xml: img ? await toDataUrl(img.src) : xml});
    } else if (m.event === 'save') {
      send({action: 'export', format: 'xmlpng', spinKey: 'saving'});
    } else if (m.event === 'export') {
      try {
        const u = await upload(dataUrlToFile(m.data, 'diagram.png'));
        if (img) { ed.dom.setAttribs(img, {src: u.url, 'data-mce-src': u.url}); ed.undoManager.add(); ed.setDirty(true); }
        else { if (replace) ed.selection.select(replace); ed.insertContent(`<img data-drawio="1" src="${u.url}" alt="diagram">`); }
        close();
      } catch (err) { alert('Saving diagram failed: ' + err.message); }
    } else if (m.event === 'exit') {
      close();
    }
  }
  window.addEventListener('message', onMessage);
}

tinymce.init({
  selector: '#editor',
  license_key: 'gpl',
  height: 'calc(100vh - 230px)',
  min_height: 400,
  menubar: false, promotion: false, branding: false,
  plugins: 'lists link image table code autolink',
  toolbar: 'undo redo | blocks | bold italic underline | bullist numlist | link image table | drawio | code',
  relative_urls: false, convert_urls: false,
  images_upload_handler: async blobInfo => (await upload(new File([blobInfo.blob()], blobInfo.filename()))).url,
  content_style: 'body{font:17px/1.6 -apple-system,BlinkMacSystemFont,Inter,"Segoe UI",sans-serif;letter-spacing:-.008em;max-width:70ch;margin:28px auto;padding:0 28px}'
    + 'h1,h2,h3{letter-spacing:-.02em} h2{font-size:24px} img{max-width:100%;height:auto;border-radius:10px} img[data-drawio]{cursor:pointer}'
    + 'table{border-collapse:collapse} td,th{border:1px solid #d2d2d7;padding:8px 12px}',
  skin: dark ? 'oxide-dark' : 'oxide',
  content_css: dark ? 'dark' : 'default',  // no web fonts in the editor, so it never waits on the internet
  link_list: '/links.json',
  link_title: false,
  link_target_list: false,
  setup(ed) {
    ed.ui.registry.addButton('drawio', {text: 'Diagram', tooltip: 'Insert draw.io diagram', onAction: () => openDrawio(ed, null)});
    ed.on('dblclick', async e => {
      if (e.target.nodeName === 'IMG' && e.target.hasAttribute('data-drawio')) return openDrawio(ed, e.target);
      const link = e.target.closest('a[href$=".drawio"]');  // an imported .drawio file
      if (link) { e.preventDefault(); openDrawio(ed, null, await (await fetch(link.getAttribute('href'))).text(), link); }
    });
    // Dropped files of any type: upload to PC1, then insert as an image or a download link.
    ed.on('drop', async e => {
      const files = [...(e.dataTransfer?.files || [])];
      if (!files.length) return;
      e.preventDefault();
      ed.selection.placeCaretAt(e.clientX, e.clientY);
      for (const f of files) {
        try {
          const u = await upload(f);
          ed.insertContent(u.image ? `<img src="${u.url}" alt="${esc(u.name)}">` : `<a href="${u.url}">📎 ${esc(u.name)}</a>&nbsp;`);
        } catch (err) {
          ed.notificationManager.open({text: `Upload of ${f.name} failed: ${err.message}`, type: 'error'});
        }
      }
    });
  },
});
