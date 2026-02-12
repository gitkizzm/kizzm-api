// --- Card Preview module (dynamic import, keeps index.js as classic script) ---
let cardPreview = {
  initCardPreview: null,
  setCommander1: null,
  setPartnerSlotEnabled: null,
  setCommander2: null,
  resetCommander1: null,
  resetCommander2: null,
};

async function ensureCardPreviewLoaded(){
  if(cardPreview.initCardPreview) return;

  const mod = await import("/static/card_preview.js");
  cardPreview.initCardPreview = mod.initCardPreview;
  cardPreview.setCommander1 = mod.setCommander1;
  cardPreview.setPartnerSlotEnabled = mod.setPartnerSlotEnabled;
  cardPreview.setCommander2 = mod.setCommander2;

  cardPreview.resetCommander1 = mod.resetCommander1;
  cardPreview.resetCommander2 = mod.resetCommander2;

  cardPreview.revealCommander1 = mod.revealCommander1;
  cardPreview.revealCommander2 = mod.revealCommander2;
  cardPreview.revealCommanders = mod.revealCommanders;
}

(() => {
  const MIN_CHARS = 3;
  const DEBOUNCE_MS = 350;

  // deck_id kommt aus data-Attribut (weil Jinja in externem JS nicht funktioniert)
  const rootCard = document.querySelector(".glass-card");
  const currentDeckId = Number(rootCard?.dataset?.deckId || "0") || 0;

  const reportModal = document.getElementById("reportModal");
  const openReportModalBtn = document.getElementById("openReportModal");
  const cancelReportBtn = document.getElementById("cancelReport");
  const submitReportBtn = document.getElementById("submitReport");
  const reportTitleEl = document.getElementById("reportModalTitle");
  const reportPlayersPoolEl = document.getElementById("reportPlayersPool");
  const reportPlacesEl = document.getElementById("reportPlaces");
  const reportModalErrorEl = document.getElementById("reportModalError");

  let reportState = null;

  const commander1Input = document.getElementById("commander");
  const commander1Box = document.getElementById("commanderSuggestBox");
  const commander1Spinner = document.getElementById("commanderSpinner");

  const commander2Input = document.getElementById("commander2");
  const commander2Box = document.getElementById("commander2SuggestBox");
  const commander2Spinner = document.getElementById("commander2Spinner");

  const commander1Id = document.getElementById("commander_id");
  const commander2Id = document.getElementById("commander2_id");
  const submitBtn = document.getElementById("submitBtn");
  const commander2LiveErrorEl = document.getElementById("commander2LiveError");

  let commander1Confirmed = false;
  let commander2Confirmed = false;
  let commander2LiveError = "";

  function setCommander2LiveError(message){
    commander2LiveError = (message || "").trim();
    if(!commander2LiveErrorEl) return;
    commander2LiveErrorEl.textContent = commander2LiveError;
    commander2LiveErrorEl.style.display = commander2LiveError ? "block" : "none";
  }

  async function validateCommanderComboNow(){
    const c1id = (commander1Id?.value || "").trim();
    const c2id = (commander2Id?.value || "").trim();
    if(!c1id || !c2id){
      setCommander2LiveError("");
      return true;
    }

    try{
      const params = new URLSearchParams({ commander_id: c1id, commander2_id: c2id });
      const r = await fetch(`/api/validate_commander_combo?${params.toString()}`, { cache:"no-store" });
      const data = await r.json();
      if(r.ok && data?.legal){
        setCommander2LiveError("");
        return true;
      }
      setCommander2LiveError(data?.error || "Diese Commander-Kombination ist nicht legal.");
      return false;
    }catch(_){
      setCommander2LiveError("Die Legalitätsprüfung ist aktuell nicht erreichbar. Bitte erneut versuchen.");
      return false;
    }
  }

  function updateSubmitEnabled(){
    if(!submitBtn) return;

    const c1HasText = !!(commander1Input?.value || "").trim();
    const c2HasText = !!(commander2Input?.value || "").trim();

    // optional, aber wenn Text da ist => confirmed + id muss da sein
    const c1Ok = !c1HasText || (commander1Confirmed && !!(commander1Id?.value || "").trim());
    const c2Ok = !c2HasText || (!commander2Input.disabled && commander2Confirmed && !!(commander2Id?.value || "").trim());

    // wenn commander2 gesetzt ist, muss commander1 gesetzt & confirmed sein
    const comboOk = !c2HasText || (c1HasText && commander1Confirmed);

    const legalityOk = !commander2LiveError;
    submitBtn.disabled = !(c1Ok && c2Ok && comboOk && legalityOk);
  }

  let commander1ConfirmedName = null;

  function setSpinner(spinnerEl, isLoading){
    if (!spinnerEl) return;
    spinnerEl.classList.toggle("show", !!isLoading);
  }

  function setBackground(url, zoom){
    if (url !== undefined && url !== null) {
      document.documentElement.style.setProperty('--bg-image', `url("${url}")`);
      try{ localStorage.setItem("bg_url", String(url)); }catch(_){}
    }
    if (zoom !== undefined && zoom !== null) {
      document.documentElement.style.setProperty('--bg-zoom', String(zoom));
      try{ localStorage.setItem("bg_zoom", String(zoom)); }catch(_){}
    }
  }

  function applyCachedBackground(){
    try{
      const url = localStorage.getItem("bg_url");
      const zoom = localStorage.getItem("bg_zoom");
      if(url){
        // sofort setzen -> kein „grauer“ Zustand beim ersten Render
        document.documentElement.style.setProperty('--bg-image', `url("${url}")`);
      }
      if(zoom){
        document.documentElement.style.setProperty('--bg-zoom', String(zoom));
      }
    }catch(_){}
  }

  async function loadDefaultBackground(){
    try{
      const r = await fetch("/api/background/default", { cache:"no-store" });
      if(!r.ok) return;
      const data = await r.json();
      if(data && data.url){
        setBackground(data.url, data.zoom || 1.12);
      }
    }catch(_){}
  }

  async function checkPartnerCapable(name){
    try{
      const r = await fetch(`/api/commander_partner_capable?name=${encodeURIComponent(name)}`, { cache:"no-store" });
      if(!r.ok) return false;
      const data = await r.json();
      return !!(data && data.partner_capable);
    }catch(_){
      return false;
    }
  }

  function hideBox(boxEl){
    if(!boxEl) return;
    boxEl.style.display = "none";
    boxEl.innerHTML = "";
  }

  function setCommander2Enabled(enabled){
    if (!commander2Input) return;
    commander2Input.disabled = !enabled;
    if (!enabled) {
        commander2Input.value = "";
        if (commander2Id) commander2Id.value = "";
        commander2Confirmed = false;
        setCommander2LiveError("");
        hideBox(commander2Box);
    }
    updateSubmitEnabled();
  }

  function escapeHtml(s){
    return String(s)
      .replaceAll("&","&amp;")
      .replaceAll("<","&lt;")
      .replaceAll(">","&gt;")
      .replaceAll('"',"&quot;")
      .replaceAll("'","&#039;");
  }

  function renderBox(boxEl, items){
    if(!boxEl) return;
    if(!items || items.length === 0){
      hideBox(boxEl);
      return;
    }
    boxEl.innerHTML = items
      .map(it => {
        const name = it?.name ?? "";
        const id = it?.id ?? "";
        return `<div class="suggest-item" data-name="${escapeHtml(name)}" data-id="${escapeHtml(id)}">${escapeHtml(name)}</div>`;
      })
      .join("");
    boxEl.style.display = "block";
  }

  function attachSuggest({ inputEl, boxEl, spinnerEl, endpointUrlBuilder, onPicked }){
    if(!inputEl || !boxEl) return;

    let timer = null;
    let lastQuery = "";
    let inFlight = false;

    async function fetchSuggest(q){
      if(inFlight) return;
      inFlight = true;
      setSpinner(spinnerEl, true);
      try{
        const url = endpointUrlBuilder(q);
        const resp = await fetch(url, { cache:"no-store" });
        if(!resp.ok){ hideBox(boxEl); return; }
        const data = await resp.json();
        if(q !== lastQuery) return;
        renderBox(boxEl, data);
      }catch(_){
        hideBox(boxEl);
      }finally{
        setSpinner(spinnerEl, false);
        inFlight = false;
      }
    }

    inputEl.addEventListener("input", () => {
      const q = inputEl.value.trim();
      lastQuery = q;

      // Any typing invalidates the confirmed selection + stored ids
    if (inputEl === commander1Input) {
    commander1Confirmed = false;
    commander1ConfirmedName = null;
    if (commander1Id) commander1Id.value = "";
    // editing commander1 invalidates commander2
    setCommander2Enabled(false);
    setCommander2LiveError("");
    } else if (inputEl === commander2Input) {
    commander2Confirmed = false;
    setCommander2LiveError("");
    if (commander2Id) commander2Id.value = "";
    }
    updateSubmitEnabled();

      if(q.length === 0){
        setSpinner(spinnerEl, false);
        hideBox(boxEl);
        return;
      }
      if(q.length < MIN_CHARS){
        setSpinner(spinnerEl, false);
        hideBox(boxEl);
        return;
      }

      clearTimeout(timer);
      timer = setTimeout(() => fetchSuggest(q), DEBOUNCE_MS);
    });

    boxEl.addEventListener("click", async (ev) => {
      const item = ev.target.closest(".suggest-item");
      if(!item) return;
      const name = item.getAttribute("data-name") || "";
        const id = item.getAttribute("data-id") || "";

        inputEl.value = name;

        // Store ids + mark confirmed
        if (inputEl === commander1Input) {
        if (commander1Id) commander1Id.value = id;
        commander1Confirmed = true;
        commander1ConfirmedName = name;
        } else if (inputEl === commander2Input) {
        if (commander2Id) commander2Id.value = id;
        commander2Confirmed = true;
        }

        hideBox(boxEl);

        if(onPicked) await onPicked(name);

        if (inputEl === commander2Input) {
          const legal = await validateCommanderComboNow();
          if (!legal) commander2Confirmed = false;
        }

        updateSubmitEnabled();
    });

    document.addEventListener("click", (ev) => {
      if(ev.target === inputEl || boxEl.contains(ev.target)) return;
      hideBox(boxEl);
    });

    inputEl.addEventListener("keydown", (ev) => {
      if(ev.key === "Escape") hideBox(boxEl);
    });
  }

  // --- WebSocket live reload ("/" + "/?deck_id=...") ---

  function setReportError(msg){
    if(!reportModalErrorEl) return;
    const message = (msg || "").trim();
    reportModalErrorEl.textContent = message;
    reportModalErrorEl.style.display = message ? "block" : "none";
  }

  function reportAvatarLabel(name){
    const parts = String(name || "").trim().split(/\s+/).filter(Boolean);
    if(parts.length === 0) return "?";
    if(parts.length === 1) return parts[0][0].toUpperCase();
    return `${parts[0][0]}${parts[1][0]}`.toUpperCase();
  }

  function renderReportPlayer(name){
    return `<div class="report-player-chip" draggable="true" data-player="${escapeHtml(name)}">
      <div class="report-player-avatar">${escapeHtml(reportAvatarLabel(name))}</div>
      <div>${escapeHtml(name)}</div>
    </div>`;
  }

  function reportCollectPlacements(){
    const result = { "1": [], "2": [], "3": [], "4": [] };
    for(const place of ["1","2","3","4"]){
      const zone = reportPlacesEl?.querySelector(`.report-dropzone[data-place="${place}"]`);
      if(!zone) continue;
      const chips = Array.from(zone.querySelectorAll('.report-player-chip'));
      result[place] = chips.map((el) => el.dataset.player).filter(Boolean);
    }
    return result;
  }

  function reportRender(){
    if(!reportState || !reportPlayersPoolEl || !reportPlacesEl) return;
    reportPlayersPoolEl.innerHTML = "";
    const poolPlayers = reportState.players.filter((p) => !reportState.placements.some((pl) => pl.player === p));
    reportPlayersPoolEl.innerHTML = poolPlayers.map(renderReportPlayer).join("");

    for(const place of ["1","2","3","4"]){
      const zone = reportPlacesEl.querySelector(`.report-dropzone[data-place="${place}"]`);
      if(!zone) continue;
      const placed = reportState.placements.filter((pl) => pl.place === place).map((pl) => pl.player);
      zone.innerHTML = placed.map(renderReportPlayer).join("");
    }
    reportBindDraggable();
  }

  function reportBindDraggable(){
    const chips = Array.from(document.querySelectorAll('.report-player-chip[draggable="true"]'));
    chips.forEach((chip) => {
      chip.addEventListener('dragstart', (ev) => {
        const player = chip.dataset.player;
        if(!player) return;
        ev.dataTransfer?.setData('text/plain', player);
      });
    });

    const zones = [reportPlayersPoolEl, ...Array.from(document.querySelectorAll('.report-dropzone'))].filter(Boolean);
    zones.forEach((zone) => {
      zone.addEventListener('dragover', (ev) => {
        ev.preventDefault();
        zone.classList.add('is-over');
      });
      zone.addEventListener('dragleave', () => zone.classList.remove('is-over'));
      zone.addEventListener('drop', (ev) => {
        ev.preventDefault();
        zone.classList.remove('is-over');
        const player = ev.dataTransfer?.getData('text/plain');
        if(!player || !reportState) return;
        reportState.placements = reportState.placements.filter((pl) => pl.player !== player);
        const place = zone.dataset.place;
        if(place){
          reportState.placements.push({ player, place });
        }
        reportRender();
      });
    });
  }

  function closeReportModal(){
    if(!reportModal) return;
    reportModal.classList.remove('show');
    reportModal.setAttribute('aria-hidden', 'true');
    openReportModalBtn?.focus();
  }

  async function loadReportData(){
    const r = await fetch(`/api/round-report/current?deck_id=${encodeURIComponent(currentDeckId)}`, { cache:'no-store' });
    const data = await r.json();
    if(!r.ok) throw new Error(data?.detail || 'Rundenreport konnte nicht geladen werden.');

    reportState = {
      round: data.round,
      table: data.table,
      players: data.players || [],
      hasReport: !!data.has_report,
      placements: [],
    };

    if(data.report?.raw_placements){
      for(const place of ["1","2","3","4"]){
        for(const player of (data.report.raw_placements[place] || [])){
          reportState.placements.push({ player, place });
        }
      }
    }

    if(reportTitleEl){
      reportTitleEl.textContent = `Ergebnis melden, Runde ${reportState.round}, Tisch ${reportState.table}`;
    }

    reportRender();
    setReportError(reportState.hasReport ? 'Für diesen Tisch wurde bereits ein Ergebnis gemeldet.' : '');
    if(submitReportBtn) submitReportBtn.disabled = reportState.hasReport;
  }

  function initReportModal(){
    if(!reportModal || !openReportModalBtn) return;

    openReportModalBtn.addEventListener('click', async () => {
      try{
        await loadReportData();
        reportModal.classList.add('show');
        reportModal.setAttribute('aria-hidden', 'false');
      }catch(err){
        alert(err?.message || 'Rundenreport konnte nicht geladen werden.');
      }
    });

    cancelReportBtn?.addEventListener('click', closeReportModal);

    reportModal.addEventListener('click', (ev) => {
      if(ev.target === reportModal) closeReportModal();
    });

    submitReportBtn?.addEventListener('click', async () => {
      if(!reportState) return;
      const placements = reportCollectPlacements();
      const assignedCount = Object.values(placements).flat().length;
      if(assignedCount !== reportState.players.length){
        setReportError('Bitte alle Spieler auf eine Platzierung ziehen.');
        return;
      }

      try{
        const r = await fetch('/api/round-report/submit', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ deck_id: currentDeckId, placements }),
        });
        const data = await r.json();
        if(!r.ok) throw new Error(data?.detail || 'Speichern fehlgeschlagen.');
        closeReportModal();
        location.reload();
      }catch(err){
        setReportError(err?.message || 'Speichern fehlgeschlagen.');
      }
    });
  }

  function wsUrl(params){
    const proto = (location.protocol === "https:") ? "wss" : "ws";
    return `${proto}://${location.host}/ws?${params}`;
  }

  function connectWS(){
    const params = (currentDeckId !== 0)
      ? `deck_id=${encodeURIComponent(currentDeckId)}`
      : "channel=home";

    const ws = new WebSocket(wsUrl(params));
    let pingTimer = null;

    ws.onmessage = (ev) => {
      if (ev.data === "pong") return;

      let msg;
      try{ msg = JSON.parse(ev.data); }catch(_){ return; }

      if(currentDeckId === 0 && msg.type === "state_changed" && msg.scope === "global"){
        location.reload();
        return;
      }

      if(currentDeckId !== 0 && msg.type === "state_changed" && msg.scope === "deck" && msg.deck_id === currentDeckId){
        location.reload();
      }
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
      try { ws.close(); } catch(_) {}
    };
  }

  // --- init ---
  document.addEventListener("DOMContentLoaded", async () => {
    applyCachedBackground();

    const cp = document.getElementById("cardPreview");
    if(cp){
      await ensureCardPreviewLoaded();
      cardPreview.initCardPreview();

      // Reveal-Animation nach Erhalt-Bestätigung (Deck-Verteilung)
      try{
        const doReveal = (cp.dataset?.reveal === "1");
        const c1 = (cp.dataset?.commander || "").trim();
        const c2 = (cp.dataset?.commander2 || "").trim();

        if(doReveal && c1){
          await cardPreview.revealCommanders(c1, c2);
        }
      }catch(_){}
    }

    // Mobile/Keyboard: focused field into view
    for (const el of [commander1Input, commander2Input]) {
      if(!el) continue;
      el.addEventListener("focus", () => {
        setTimeout(() => el.scrollIntoView({ behavior: "smooth", block: "center" }), 250);
      });
    }

    // Reset preview if inputs are cleared
    commander1Input?.addEventListener("input", async () => {
      const v = (commander1Input.value || "").trim();
      if(v.length === 0){
        commander1Confirmed = false;
        if (commander1Id) commander1Id.value = "";

        await ensureCardPreviewLoaded();
        cardPreview.resetCommander1();

        // partner slot + input zurück
        setCommander2Enabled(false);
        cardPreview.setPartnerSlotEnabled(false);
        cardPreview.resetCommander2();

        updateSubmitEnabled();
      }
    });

    commander2Input?.addEventListener("input", async () => {
      const v = (commander2Input.value || "").trim();
      if(v.length === 0){
        commander2Confirmed = false;
        if (commander2Id) commander2Id.value = "";
        setCommander2LiveError("");

        await ensureCardPreviewLoaded();
        cardPreview.resetCommander2();

        updateSubmitEnabled();
      }
    });

    loadDefaultBackground();

    // Restore state on server-rendered errors (values may be prefilled)
    commander1Confirmed = !!((commander1Input?.value || "").trim() && (commander1Id?.value || "").trim());
    commander2Confirmed = !!((commander2Input?.value || "").trim() && (commander2Id?.value || "").trim());

    // commander2 enabled only if it has confirmed value OR will be enabled by picking commander1 again
    if (commander2Confirmed) {
    setCommander2Enabled(true);
    } else {
    setCommander2Enabled(false);
    }

    setCommander2LiveError("");
    updateSubmitEnabled();

    initReportModal();

    // start WS
    connectWS();
  });

  // Commander 1 suggest
  attachSuggest({
    inputEl: commander1Input,
    boxEl: commander1Box,
    spinnerEl: commander1Spinner,
    endpointUrlBuilder: (q) => `/api/commander_suggest?q=${encodeURIComponent(q)}`,
    onPicked: async (name) => {
      commander1ConfirmedName = name;

      // Card Preview (3D Karte) aktualisieren
      await ensureCardPreviewLoaded();
      await cardPreview.setCommander1(name);

      // Partnerfähigkeit prüfen -> Slot2 Placeholder einblenden/ausblenden
      const partnerCapable = await checkPartnerCapable(name);
      setCommander2Enabled(partnerCapable);
      cardPreview.setPartnerSlotEnabled(partnerCapable);
    }
  });

  // Commander 2 suggest (only is:partner)
  attachSuggest({
    inputEl: commander2Input,
    boxEl: commander2Box,
    spinnerEl: commander2Spinner,
    endpointUrlBuilder: (q) => `/api/partner_suggest?q=${encodeURIComponent(q)}`,
    onPicked: async (name) => {
      await ensureCardPreviewLoaded();
      await cardPreview.setCommander2(name);
    }
  });

})();
