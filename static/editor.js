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
function openDrawio(ed, img) {
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
      send({action: 'load', autosave: 0, xml: img ? await toDataUrl(img.src) : ''});
    } else if (m.event === 'save') {
      send({action: 'export', format: 'xmlpng', spinKey: 'saving'});
    } else if (m.event === 'export') {
      try {
        const u = await upload(dataUrlToFile(m.data, 'diagram.png'));
        if (img) { ed.dom.setAttribs(img, {src: u.url, 'data-mce-src': u.url}); ed.undoManager.add(); ed.setDirty(true); }
        else ed.insertContent(`<img data-drawio="1" src="${u.url}" alt="diagram">`);
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
  height: '70vh',
  menubar: false, promotion: false, branding: false,
  plugins: 'lists link image table code autolink',
  toolbar: 'undo redo | blocks | bold italic underline | bullist numlist | link image table | drawio | code',
  relative_urls: false, convert_urls: false,
  images_upload_handler: async blobInfo => (await upload(new File([blobInfo.blob()], blobInfo.filename()))).url,
  content_style: 'img{max-width:100%;height:auto} img[data-drawio]{cursor:pointer}',
  skin: window.matchMedia('(prefers-color-scheme: dark)').matches ? 'oxide-dark' : 'oxide',
  content_css: window.matchMedia('(prefers-color-scheme: dark)').matches ? 'dark' : 'default',
  setup(ed) {
    ed.ui.registry.addButton('drawio', {text: 'Diagram', tooltip: 'Insert draw.io diagram', onAction: () => openDrawio(ed, null)});
    ed.on('dblclick', e => { if (e.target.nodeName === 'IMG' && e.target.hasAttribute('data-drawio')) openDrawio(ed, e.target); });
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
