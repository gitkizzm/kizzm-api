(() => {
  let settingsState = null;

  function setBackground(url, zoom){
    if (url !== undefined && url !== null) {
      document.documentElement.style.setProperty('--bg-image', `url("${url}")`);
    }
    if (zoom !== undefined && zoom !== null) {
      document.documentElement.style.setProperty('--bg-zoom', String(zoom));
    }
  }

  async function loadDefaultBackground(){
    try{
      const r = await fetch("/api/background/default", { cache:"no-store" });
      if(!r.ok) return;
      const data = await r.json();
      if(data && data.url){
        setBackground(data.url, data.zoom || 1.12);
      }
    }catch(_){ }
  }

  function wsUrl(params){
    const proto = (location.protocol === "https:") ? "wss" : "ws";
    return `${proto}://${location.host}/ws?${params}`;
  }

  function connectWS(){
    const ws = new WebSocket(wsUrl("channel=ccp"));
    let pingTimer = null;

    ws.onmessage = (ev) => {
      if (ev.data === "pong") return;

      try{
        const msg = JSON.parse(ev.data);
        if(msg.type === "state_changed" && msg.scope === "global"){
          location.reload();
        }
      }catch(_){ }
    };

    ws.onopen = () => {
      pingTimer = setInterval(() => {
        if(ws.readyState === 1) ws.send("ping");
      }, 25000);
    };

    ws.onclose = () => {
      if (pingTimer) clearInterval(pingTimer);
      setTimeout(connectWS, 1000);
    };

    ws.onerror = () => {
      try { ws.close(); } catch(_) { }
    };
  }

  function initClearModal(){
    const modal = document.getElementById("clearModal");
    const openBtn = document.getElementById("openClearModal");
    const cancelBtn = document.getElementById("cancelClear");
    const confirmBtn = document.getElementById("confirmClear");
    const clearForm = document.getElementById("clearForm");

    function openModal(){
      if(!modal) return;
      modal.classList.add("show");
      modal.setAttribute("aria-hidden", "false");
      cancelBtn?.focus();
    }

    function closeModal(){
      if(!modal) return;
      modal.classList.remove("show");
      modal.setAttribute("aria-hidden", "true");
      openBtn?.focus();
    }

    openBtn?.addEventListener("click", openModal);
    cancelBtn?.addEventListener("click", closeModal);

    modal?.addEventListener("click", (e) => {
      if(e.target === modal) closeModal();
    });

    document.addEventListener("keydown", (e) => {
      if(e.key === "Escape" && modal?.classList.contains("show")){
        closeModal();
      }
    });

    confirmBtn?.addEventListener("click", () => {
      clearForm?.submit();
    });
  }

  function initTabs(){
    const tabs = Array.from(document.querySelectorAll('.ccp-tab'));
    const panels = Array.from(document.querySelectorAll('.ccp-panel'));

    function activate(name){
      tabs.forEach((tab) => {
        const active = tab.dataset.tab === name;
        tab.classList.toggle('is-active', active);
        tab.setAttribute('aria-selected', active ? 'true' : 'false');
      });
      panels.forEach((panel) => {
        const active = panel.dataset.panel === name;
        panel.classList.toggle('is-active', active);
        panel.hidden = !active;
      });
    }

    tabs.forEach((tab) => {
      tab.addEventListener('click', () => activate(tab.dataset.tab));
    });
  }

  function setAlert(message, tone=''){
    const el = document.getElementById('settingsAlert');
    if(!el) return;
    el.style.display = message ? 'block' : 'none';
    el.classList.remove('ok', 'danger');
    if(tone) el.classList.add(tone);
    el.textContent = message || '';
  }

  function editable(key){
    const map = settingsState?.editability || {};
    return !!(map[key] && map[key].editable);
  }

  function metaText(){
    if(!settingsState) return 'Keine Settings geladen.';
    const meta = settingsState.meta || {};
    return `State: ${settingsState.event_state} · Source: ${meta.source || '-'}${meta.error ? ` · Error: ${meta.error}` : ''}`;
  }

  function getByPath(obj, path){
    return path.split('.').reduce((acc, key) => (acc ? acc[key] : undefined), obj);
  }

  function setByPath(obj, path, value){
    const parts = path.split('.');
    let cursor = obj;
    for(let i=0; i<parts.length-1; i++){
      const p = parts[i];
      if(!cursor[p] || typeof cursor[p] !== 'object') cursor[p] = {};
      cursor = cursor[p];
    }
    cursor[parts[parts.length-1]] = value;
  }

  function populateSettingsForm(){
    const metaEl = document.getElementById('settingsMeta');
    if(metaEl) metaEl.textContent = metaText();
    const s = settingsState?.settings;
    if(!s) return;

    const fields = Array.from(document.querySelectorAll('[data-key]'));
    fields.forEach((field) => {
      const key = field.dataset.key;
      let value = getByPath(s, key);

      if(key === 'participants') value = (value || []).join('\n');
      if(key === 'voting.points_scheme') value = JSON.stringify(value || {}, null, 2);

      if(field.type === 'checkbox') field.checked = !!value;
      else field.value = value ?? '';

      const canEdit = editable(key);
      field.disabled = !canEdit;
      const label = field.closest('label');
      label?.classList.toggle('is-locked', !canEdit);
      if(!canEdit){
        const lock = settingsState.editability[key]?.lock_level || 'locked';
        field.title = `Gesperrt (${lock})`;
      } else {
        field.title = '';
      }
    });

    const saveBtn = document.getElementById('saveSettingsBtn');
    saveBtn.disabled = fields.every((f) => f.disabled);
  }

  async function loadSettings(){
    try{
      const r = await fetch('/api/settings/effective', { cache:'no-store' });
      if(!r.ok) throw new Error('settings load failed');
      settingsState = await r.json();
      populateSettingsForm();
    }catch(_){
      setAlert('Settings konnten nicht geladen werden.', 'danger');
    }
  }

  function collectPatch(){
    const base = settingsState?.settings || {};
    const patch = {};
    const fields = Array.from(document.querySelectorAll('[data-key]'));

    for(const field of fields){
      if(field.disabled) continue;
      const key = field.dataset.key;
      let value;

      if(field.type === 'checkbox') value = !!field.checked;
      else value = field.value;

      if(key === 'participants'){
        value = String(value || '').split('\n').map((it) => it.trim()).filter(Boolean);
      } else if(key === 'voting.points_scheme'){
        try{ value = JSON.parse(value || '{}'); }
        catch(_){ throw new Error('Voting Points JSON ist ungültig.'); }
      } else if(field.type === 'number'){
        value = value === '' ? null : Number(value);
      }

      const prev = getByPath(base, key);
      const changed = JSON.stringify(prev) !== JSON.stringify(value);
      if(changed) setByPath(patch, key, value);
    }

    return patch;
  }

  async function saveSettings(){
    try{
      const patch = collectPatch();
      if(Object.keys(patch).length === 0){
        setAlert('Keine Änderungen zu speichern.', 'ok');
        return;
      }

      const r = await fetch('/api/settings', {
        method: 'PATCH',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(patch),
      });

      const payload = await r.json();
      if(!r.ok){
        setAlert(payload?.detail || 'Speichern fehlgeschlagen.', 'danger');
        return;
      }

      settingsState = payload;
      settingsState.meta = (await (await fetch('/api/settings/effective', {cache:'no-store'})).json()).meta;
      populateSettingsForm();
      setAlert(`Gespeichert: ${(payload.changed_keys || []).join(', ')}`, 'ok');
    }catch(err){
      setAlert(err?.message || 'Speichern fehlgeschlagen.', 'danger');
    }
  }

  async function resetSettings(){
    try{
      const r = await fetch('/api/settings/reset', { method: 'POST' });
      const payload = await r.json();
      if(!r.ok){
        setAlert(payload?.detail || 'Reset fehlgeschlagen.', 'danger');
        return;
      }
      settingsState = payload;
      populateSettingsForm();
      setAlert('Settings wurden auf Defaults zurückgesetzt (im Rahmen der Locks).', 'ok');
    }catch(_){
      setAlert('Reset fehlgeschlagen.', 'danger');
    }
  }

  function initSettingsActions(){
    document.getElementById('saveSettingsBtn')?.addEventListener('click', saveSettings);
    document.getElementById('resetSettingsBtn')?.addEventListener('click', resetSettings);
  }

  function initHostsMultiselect(){
    const wrapper = document.getElementById('hostsMultiselect');
    const toggle = document.getElementById('hostsToggle');
    const dropdown = document.getElementById('hostsDropdown');
    const nativeSelect = document.getElementById('hosts');
    if(!wrapper || !toggle || !dropdown || !nativeSelect) return;

    const checkboxes = Array.from(dropdown.querySelectorAll('input[type="checkbox"]'));
    const optionsByValue = new Map(Array.from(nativeSelect.options).map((opt) => [opt.value, opt]));
    const placeholder = wrapper.dataset.placeholder || 'Hosts auswählen';

    function selectedLabels(){
      return checkboxes.filter((cb) => cb.checked).map((cb) => cb.value);
    }

    function syncLabel(){
      const names = selectedLabels();
      if(names.length === 0){
        toggle.textContent = placeholder;
      }else if(names.length <= 2){
        toggle.textContent = names.join(', ');
      }else{
        toggle.textContent = `${names.length} Hosts ausgewählt`;
      }
    }

    function syncNativeSelect(){
      checkboxes.forEach((cb) => {
        const option = optionsByValue.get(cb.value);
        if(option) option.selected = cb.checked;
        const parentLabel = cb.closest('.multiselect-option');
        parentLabel?.setAttribute('aria-selected', cb.checked ? 'true' : 'false');
      });
    }

    function setOpen(open){
      wrapper.classList.toggle('is-open', open);
      dropdown.hidden = !open;
      toggle.setAttribute('aria-expanded', open ? 'true' : 'false');
    }

    toggle.addEventListener('click', () => {
      setOpen(dropdown.hidden);
    });

    checkboxes.forEach((cb) => {
      cb.addEventListener('change', () => {
        syncNativeSelect();
        syncLabel();
      });
    });

    document.addEventListener('click', (ev) => {
      if(!wrapper.contains(ev.target)) setOpen(false);
    });

    document.addEventListener('keydown', (ev) => {
      if(ev.key === 'Escape') setOpen(false);
    });

    syncNativeSelect();
    syncLabel();
  }

  document.addEventListener("DOMContentLoaded", () => {
    loadDefaultBackground();
    connectWS();
    initClearModal();
    initTabs();
    initHostsMultiselect();
    initSettingsActions();
    loadSettings();
  });
})();
