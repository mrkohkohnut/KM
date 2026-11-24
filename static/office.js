// Opens the document in OnlyOffice (loaded from the document server); the server saves it back to KM.
const box = document.getElementById('office');
const config = JSON.parse(document.getElementById('office-config').textContent);
if (window.DocsAPI) {
  box.replaceChildren(Object.assign(document.createElement('div'), {id: 'office-editor'}));
  new DocsAPI.DocEditor('office-editor', config);
} else {
  box.innerHTML = `<p class="note">The document editor at ${box.dataset.server} isn't reachable. Check that the
    onlyoffice container is running (docker compose ps). You can still download the file.</p>`;
}
