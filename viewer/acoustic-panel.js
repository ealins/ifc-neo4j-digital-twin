(() => {
  const state = {
    registry: null,
    registryName: 'bundled acoustic_registry_v1.json',
    selectedNode: null,
  };

  const $ac = (id) => document.getElementById(id);

  function esc(v) {
    return String(v ?? '')
      .replaceAll('&', '&amp;').replaceAll('<', '&lt;').replaceAll('>', '&gt;')
      .replaceAll('"', '&quot;').replaceAll("'", '&#039;');
  }

  function row(label, value, unit='') {
    if (value === null || value === undefined || value === '') return '';
    return `<div><dt>${esc(label)}</dt><dd>${esc(value)}${unit ? ` ${esc(unit)}` : ''}</dd></div>`;
  }

  function getLink(node) {
    const pset = node?.properties?.Pset_AcousticLink;
    if (!pset || typeof pset !== 'object') return null;
    const uri = pset.AcousticRecordURI;
    if (!uri) return null;
    return {
      uri: String(uri),
      status: pset.MappingStatus || 'LINKED',
      basis: pset.MappingBasis || '',
    };
  }

  function setStatus(text, kind='neutral') {
    const el = $ac('acousticRegistryStatus');
    if (!el) return;
    el.textContent = text;
    el.dataset.kind = kind;
  }

  function showEmpty(message='Select an IFC entity containing Pset_AcousticLink.') {
    const empty = $ac('acousticEmpty');
    const details = $ac('acousticDetails');
    if (!empty || !details) return;
    empty.textContent = message;
    empty.classList.remove('hidden');
    details.classList.add('hidden');
    details.innerHTML = '';
  }

  function findRecord(uri) {
    return state.registry?.records?.[uri] || null;
  }

  function renderSelected() {
    const node = state.selectedNode;
    if (!node) return showEmpty();

    const link = getLink(node);
    if (!link) {
      return showEmpty('This IFC entity has no Pset_AcousticLink.AcousticRecordURI.');
    }

    const record = findRecord(link.uri);
    if (!record) {
      return showEmpty(`Broken/unresolved acoustic link: ${link.uri}`);
    }

    const a = record.assembly || {};
    const s = record.source || {};
    const provenance = (record.provenance || []).map(p =>
      `<li><code>${esc(p.predicate)}</code><span>${esc(p.object)}</span></li>`
    ).join('');
    const layers = (a.layers || []).map(layer =>
      `<li><strong>${esc(layer.position)}.</strong> ${esc(layer.name)} · ${esc(layer.thickness_m)} m</li>`
    ).join('');

    const empty = $ac('acousticEmpty');
    const details = $ac('acousticDetails');
    empty.classList.add('hidden');
    details.classList.remove('hidden');
    details.innerHTML = `
      <div class="acoustic-hero">
        <div><span class="acoustic-metric">${esc(record.acoustic_metric || 'Rw')}</span>
        <strong>${esc(record.value)} ${esc(record.unit || '')}</strong></div>
        <span class="mapping-badge">${esc(link.status)}</span>
      </div>
      <div class="acoustic-claim">${esc(record.claim_scope || 'External reference acoustic record.')}</div>
      <dl class="acoustic-dl">
        ${row('Assembly', a.name)}
        ${row('Construction', a.construction_type)}
        ${row('Thickness', a.total_thickness_m, 'm')}
        ${row('Test area', a.test_area_m2, 'm²')}
        ${row('Specimen', a.specimen_length_x_m && a.specimen_length_y_m ? `${a.specimen_length_x_m} × ${a.specimen_length_y_m} m` : null)}
        ${row('Surface mass', a.surface_mass_kg_m2, 'kg/m²')}
        ${row('Source', s.organisation)}
        ${row('Reference', s.reference)}
        ${row('Year', s.year)}
        ${row('Method', s.method)}
        ${row('Version', s.version)}
      </dl>
      ${layers ? `<div class="acoustic-subsection"><strong>Reference assembly layers</strong><ul>${layers}</ul></div>` : ''}
      <div class="acoustic-subsection"><strong>Mapping basis</strong><p>${esc(link.basis || 'No mapping basis supplied in IFC.')}</p></div>
      <div class="acoustic-subsection"><strong>PROV-O provenance</strong><ul>${provenance || '<li>No explicit provenance relations.</li>'}</ul></div>
      <div class="acoustic-links">
        ${s.source_uri ? `<a href="${esc(s.source_uri)}" target="_blank" rel="noopener">VaBDat acoustic record ↗</a>` : ''}
        ${s.component_uri ? `<a href="${esc(s.component_uri)}" target="_blank" rel="noopener">VaBDat component ↗</a>` : ''}
      </div>`;
  }

  function validateRegistry(obj) {
    if (!obj || typeof obj !== 'object' || !obj.records || typeof obj.records !== 'object') {
      throw new Error('Registry JSON must contain a top-level "records" object keyed by AcousticRecordURI.');
    }
    return obj;
  }

  async function loadBundledRegistry() {
    const response = await fetch('/viewer-assets/acoustic-data/acoustic_registry_v1.json', {cache: 'no-store'});
    if (!response.ok) throw new Error(`${response.status} ${response.statusText}`);
    state.registry = validateRegistry(await response.json());
    state.registryName = 'bundled acoustic_registry_v1.json';
    setStatus(`${Object.keys(state.registry.records).length} acoustic record loaded`, 'ok');
    renderSelected();
  }

  async function uploadRegistry(file) {
    const text = await file.text();
    const parsed = validateRegistry(JSON.parse(text));
    state.registry = parsed;
    state.registryName = file.name;
    setStatus(`${Object.keys(parsed.records).length} record(s) · ${file.name}`, 'ok');
    renderSelected();
  }

  window.addEventListener('ifc-node-selected', (ev) => {
    state.selectedNode = ev.detail?.node || null;
    renderSelected();
  });
  window.addEventListener('ifc-node-cleared', () => {
    state.selectedNode = null;
    showEmpty();
  });

  document.addEventListener('DOMContentLoaded', () => {
    const input = $ac('acousticRegistryInput');
    const reset = $ac('resetAcousticRegistryBtn');
    input?.addEventListener('change', async () => {
      const file = input.files?.[0];
      if (!file) return;
      try {
        await uploadRegistry(file);
      } catch (err) {
        console.error(err);
        setStatus(`Registry error: ${err.message}`, 'error');
      }
    });
    reset?.addEventListener('click', () => loadBundledRegistry().catch(err => setStatus(`Registry error: ${err.message}`, 'error')));
    loadBundledRegistry().catch(err => {
      console.error(err);
      setStatus(`Bundled registry unavailable: ${err.message}`, 'error');
    });
  });
})();
