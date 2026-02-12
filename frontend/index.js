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

  const bestDeckVotingRootEl = document.getElementById("bestDeckVoting");
  const bestDeckVotingTitleEl = document.getElementById("bestDeckVotingTitle");
  const votingDecksPoolEl = document.getElementById("votingDecksPool");
  const votingPlacesEl = document.getElementById("votingPlaces");
  const votingErrorEl = document.getElementById("votingError");
  const submitBestDeckVoteBtn = document.getElementById("submitBestDeckVote");
  const resetBestDeckVoteBtn = document.getElementById("resetBestDeckVote");

  let reportState = null;
  let bestDeckVotingState = null;

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
    const trimmed = String(name || "").trim();
    return trimmed ? trimmed[0].toUpperCase() : "?";
  }

  function reportDisplayName(name){
    const parts = String(name || "").trim().split(/\s+/).filter(Boolean);
    if(parts.length <= 1) return String(name || "").trim();
    return `${parts[0]} ${parts[1][0].toUpperCase()}.`;
  }

  function renderReportPlayer(name){
    const meta = reportState?.playerMeta?.[name] || {};
    const avatar = meta.avatar_url
      ? `<img src="${escapeHtml(meta.avatar_url)}" alt="" class="report-player-avatar-img">`
      : `<span class="report-player-avatar-fallback">${escapeHtml(reportAvatarLabel(name))}</span>`;

    return `<div class="report-player-chip" draggable="true" data-player="${escapeHtml(name)}" title="${escapeHtml(name)}">
      <div class="report-player-avatar">${avatar}</div>
      <div class="report-player-name">${escapeHtml(reportDisplayName(name))}</div>
    </div>`;
  }

  async function hydratePairingMatchupChips(){
    const chips = Array.from(document.querySelectorAll('.pairing-matchup-grid .report-player-chip--matchup[data-player]'));
    if(chips.length === 0 || currentDeckId <= 0) return;

    try{
      const r = await fetch(`/api/round-report/current?deck_id=${encodeURIComponent(currentDeckId)}`, { cache:'no-store' });
      const data = await r.json();
      if(!r.ok) return;

      const playerMeta = data?.player_meta || {};
      chips.forEach((chip) => {
        const player = String(chip.dataset.player || '').trim();
        if(!player) return;

        const avatarUrl = playerMeta?.[player]?.avatar_url;
        const avatarEl = chip.querySelector('.report-player-avatar');
        const nameEl = chip.querySelector('.report-player-name');
        if(nameEl) nameEl.textContent = reportDisplayName(player);
        if(!avatarEl) return;

        if(avatarUrl){
          avatarEl.innerHTML = `<img src="${escapeHtml(avatarUrl)}" alt="" class="report-player-avatar-img">`;
        }else{
          avatarEl.innerHTML = `<span class="report-player-avatar-fallback">${escapeHtml(reportAvatarLabel(player))}</span>`;
        }
      });
    }catch(_){
      // Fallback bleibt bei servergerenderten Initialen.
    }
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

  function normalizePlacementsByRules(placements, triggerPlayer = null){
    const buckets = {
      "1": [...(placements["1"] || [])],
      "2": [...(placements["2"] || [])],
      "3": [...(placements["3"] || [])],
      "4": [...(placements["4"] || [])],
    };

    function cloneBuckets(src){
      return {
        "1": [...(src["1"] || [])],
        "2": [...(src["2"] || [])],
        "3": [...(src["3"] || [])],
        "4": [...(src["4"] || [])],
      };
    }

    function pushDownWithCascade(state, place, incoming){
      if(place > 4) return false;
      const key = String(place);
      const prev = state[key] || [];
      state[key] = [...incoming];
      if(prev.length === 0) return true;
      return pushDownWithCascade(state, place + 1, prev);
    }

    function shiftTieGroupDown(state, place){
      const key = String(place);
      const tieGroup = [...(state[key] || [])];
      state[key] = [];
      return pushDownWithCascade(state, place + 1, tieGroup);
    }

    function pushUpWithCascade(state, place, incoming){
      if(place < 1) return false;
      const key = String(place);
      const prev = state[key] || [];
      state[key] = [...incoming];
      if(prev.length === 0) return true;
      return pushUpWithCascade(state, place - 1, prev);
    }

    function tryFreeHigherSlotsByLifting(state, place, neededFreeHigher){
      // Freie Slots direkt über dem Gleichstand schaffen, indem blockierende Gruppen
      // nach oben geschoben werden (z.B. 3 -> 2), falls unten kein Platz mehr ist.
      for(let step = 1; step <= neededFreeHigher; step++){
        const target = place - step;
        const targetKey = String(target);
        const blocked = state[targetKey] || [];
        if(blocked.length === 0) continue;

        state[targetKey] = [];
        const ok = pushUpWithCascade(state, target - 1, blocked);
        if(!ok) return false;
      }
      return true;
    }

    let changed = true;
    while(changed){
      changed = false;

      // Platz 1 darf nie geteilt sein.
      if((buckets["1"] || []).length > 1){
        const snapshot = cloneBuckets(buckets);
        const okTop = shiftTieGroupDown(buckets, 1);
        if(!okTop) return snapshot;
        changed = true;
      }

      // Für Gleichstand auf Platz p mit n Spielern müssen n-1 höhere Plätze frei sein.
      // Sind sie nicht frei (oder existieren nicht genug höhere Plätze), rutscht die Gruppe nach unten.
      for(let p = 2; p <= 4; p++){
        const place = String(p);
        const tieGroup = buckets[place] || [];
        if(tieGroup.length <= 1) continue;

        const neededFreeHigher = tieGroup.length - 1;
        let mustMoveDown = neededFreeHigher > (p - 1);

        if(!mustMoveDown){
          for(let step = 1; step <= neededFreeHigher; step++){
            const higher = String(p - step);
            if((buckets[higher] || []).length > 0){
              mustMoveDown = true;
              break;
            }
          }
        }

        if(!mustMoveDown) continue;

        const snapshot = cloneBuckets(buckets);
        const ok = shiftTieGroupDown(buckets, p);

        if(!ok){
          // Sonderfall-Bugfix:
          // Wenn ein Gleichstand auf Platz 4 nicht weiter nach unten verschoben werden kann,
          // versuchen wir zuerst die blockierenden höheren Plätze nach oben zu schieben.
          // Beispiel: [4: A], [3: B], A -> 4 (tie) => B wird nach 2 geschoben.
          if(p === 4){
            const lifted = cloneBuckets(snapshot);
            const liftedOk = tryFreeHigherSlotsByLifting(lifted, p, neededFreeHigher);
            if(liftedOk){
              buckets["1"] = lifted["1"];
              buckets["2"] = lifted["2"];
              buckets["3"] = lifted["3"];
              buckets["4"] = lifted["4"];
              changed = true;
              continue;
            }
          }

          // Sonderfall laut Anforderung:
          // Wenn die Umverteilung durch den neu gesetzten Spieler ausgelöst wurde
          // und nach unten kein Platz mehr frei ist, rutscht dieser auf Platz 1.
          const reset = cloneBuckets(snapshot);
          if(triggerPlayer){
            for(const k of ["1", "2", "3", "4"]){
              reset[k] = (reset[k] || []).filter((name) => name !== triggerPlayer);
            }
            reset["1"] = [...(reset["1"] || []), triggerPlayer];
            return normalizePlacementsByRules(reset, null);
          }
          return reset;
        }

        changed = true;
      }
    }

    return buckets;
  }

  function applyNormalizedPlacementsToState(triggerPlayer = null){
    if(!reportState) return;
    const placements = reportCollectPlacements();

    const normalized = normalizePlacementsByRules(placements, triggerPlayer);
    reportState.placements = [];
    for(const place of ["1","2","3","4"]){
      for(const player of normalized[place] || []){
        reportState.placements.push({ player, place });
      }
    }
    reportRender();
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
    updateReportPlaceSlotLayout();
    reportBindDraggable();
  }

  function updateReportPlaceSlotLayout(){
    if(!reportPlacesEl) return;
    const slots = Array.from(reportPlacesEl.querySelectorAll('.report-place-slot'));
    if(slots.length === 0) return;

    const style = getComputedStyle(reportPlacesEl);
    const gap = Number.parseFloat(style.rowGap || style.gap || '0') || 0;
    const containerHeight = reportPlacesEl.clientHeight || 240;

    let occupiedTotal = 0;
    const emptySlots = [];

    for(const slot of slots){
      const zone = slot.querySelector('.report-dropzone');
      const chipCount = zone ? zone.querySelectorAll('.report-player-chip').length : 0;
      if(chipCount > 0){
        const needed = 18 + (chipCount * 39);
        occupiedTotal += needed;
        slot.style.flex = `0 0 ${needed}px`;
      }else{
        emptySlots.push(slot);
      }
    }

    const freeSpace = containerHeight - occupiedTotal - (gap * Math.max(0, slots.length - 1));
    const perEmpty = emptySlots.length > 0 ? Math.max(0, Math.floor(freeSpace / emptySlots.length)) : 0;
    for(const slot of emptySlots){
      slot.style.flex = `1 1 ${perEmpty}px`;
    }
  }

  function reportBindDraggable(){
    let touchDraggedPlayer = null;

    const zones = [reportPlayersPoolEl, ...Array.from(document.querySelectorAll('.report-dropzone'))].filter(Boolean);

    function clearZoneHighlights(){
      zones.forEach((z) => z.classList.remove('is-over'));
    }

    function zoneAtPoint(clientX, clientY){
      const el = document.elementFromPoint(clientX, clientY);
      if(!el) return null;
      return el.closest('#reportPlayersPool, .report-dropzone');
    }

    function placePlayerInZone(player, zone){
      if(!player || !zone || !reportState) return;
      reportState.placements = reportState.placements.filter((pl) => pl.player !== player);
      const place = zone.dataset.place;
      if(place){
        reportState.placements.push({ player, place });
      }
      reportRender();
      applyNormalizedPlacementsToState(player);
    }

    const chips = Array.from(document.querySelectorAll('.report-player-chip[draggable="true"]'));
    chips.forEach((chip) => {
      chip.addEventListener('dragstart', (ev) => {
        const player = chip.dataset.player;
        if(!player) return;
        ev.dataTransfer?.setData('text/plain', player);
      });

      chip.addEventListener('touchstart', (ev) => {
        const player = chip.dataset.player;
        if(!player) return;
        touchDraggedPlayer = player;
        chip.classList.add('is-touch-picked');
      }, { passive: true });

      chip.addEventListener('touchmove', (ev) => {
        if(!touchDraggedPlayer) return;
        const t = ev.touches?.[0];
        if(!t) return;
        const targetZone = zoneAtPoint(t.clientX, t.clientY);
        clearZoneHighlights();
        targetZone?.classList.add('is-over');
      }, { passive: true });

      chip.addEventListener('touchend', (ev) => {
        chip.classList.remove('is-touch-picked');
        if(!touchDraggedPlayer) return;
        const t = ev.changedTouches?.[0];
        const targetZone = t ? zoneAtPoint(t.clientX, t.clientY) : null;
        clearZoneHighlights();
        if(targetZone){
          placePlayerInZone(touchDraggedPlayer, targetZone);
        }
        touchDraggedPlayer = null;
      }, { passive: true });
    });

    zones.forEach((zone) => {
      if(zone.dataset.dragBound === '1') return;
      zone.dataset.dragBound = '1';

      zone.addEventListener('dragover', (ev) => {
        ev.preventDefault();
        zone.classList.add('is-over');
      });
      zone.addEventListener('dragleave', () => zone.classList.remove('is-over'));
      zone.addEventListener('drop', (ev) => {
        ev.preventDefault();
        zone.classList.remove('is-over');
        const player = ev.dataTransfer?.getData('text/plain');
        placePlayerInZone(player, zone);
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
      playerMeta: data.player_meta || {},
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
      applyNormalizedPlacementsToState();
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


  function setVotingError(msg){
    if(!votingErrorEl) return;
    const message = String(msg || '').trim();
    votingErrorEl.textContent = message;
    votingErrorEl.style.display = message ? 'block' : 'none';
  }

  function renderVotingDeck(deck){
    if(!deck) return '';
    const id = Number(deck.deck_id || 0) || 0;
    const builder = String(deck.deckersteller || '').trim();
    const commander = String(deck.commander || '').trim();
    const title = commander || `Deck #${id}`;
    const subline = builder ? `Erbauer: ${builder}` : `Deck #${id}`;
    return `<div class="report-player-chip" draggable="true" data-deck-id="${id}" title="${escapeHtml(title)}">
      <div class="report-player-avatar"><span class="report-player-avatar-fallback">${escapeHtml(String(id))}</span></div>
      <div class="report-player-name">${escapeHtml(title)}<span class="report-player-subline">${escapeHtml(subline)}</span></div>
    </div>`;
  }

  function votingCollectPlacements(){
    const result = { '1': null, '2': null, '3': null };
    for(const place of ['1','2','3']){
      const zone = votingPlacesEl?.querySelector(`.report-dropzone[data-place="${place}"]`);
      const chip = zone?.querySelector('.report-player-chip[data-deck-id]');
      const value = Number(chip?.dataset?.deckId || '0') || 0;
      result[place] = value > 0 ? value : null;
    }
    return result;
  }

  function renderBestDeckVoting(){
    if(!bestDeckVotingState || !votingDecksPoolEl || !votingPlacesEl) return;

    const placedIds = new Set(
      Object.values(bestDeckVotingState.placements || {})
        .map((it) => Number(it || 0))
        .filter((it) => it > 0)
    );

    const poolDecks = (bestDeckVotingState.candidates || []).filter((deck) => !placedIds.has(Number(deck.deck_id || 0)));
    votingDecksPoolEl.innerHTML = poolDecks.map(renderVotingDeck).join('');

    for(const place of ['1','2','3']){
      const zone = votingPlacesEl.querySelector(`.report-dropzone[data-place="${place}"]`);
      if(!zone) continue;
      const deckId = Number(bestDeckVotingState.placements?.[place] || 0) || 0;
      const deck = (bestDeckVotingState.candidates || []).find((item) => Number(item.deck_id || 0) === deckId);
      zone.innerHTML = deck ? renderVotingDeck(deck) : '';
    }

    const complete = ['1','2','3'].every((place) => Number(bestDeckVotingState.placements?.[place] || 0) > 0);
    if(submitBestDeckVoteBtn) submitBestDeckVoteBtn.disabled = !complete || !!bestDeckVotingState.hasVote;
    if(resetBestDeckVoteBtn) resetBestDeckVoteBtn.disabled = !!bestDeckVotingState.hasVote;

    if(bestDeckVotingState.hasVote){
      const allChips = Array.from(bestDeckVotingRootEl?.querySelectorAll('.report-player-chip') || []);
      allChips.forEach((chip) => {
        chip.setAttribute('draggable', 'false');
        chip.classList.add('is-static');
      });
      return;
    }

    bindBestDeckVotingDraggable();
  }

  function bindBestDeckVotingDraggable(){
    if(!bestDeckVotingState || !bestDeckVotingRootEl) return;

    let touchDraggedDeckId = null;
    const zones = [votingDecksPoolEl, ...Array.from(bestDeckVotingRootEl.querySelectorAll('.report-dropzone'))].filter(Boolean);

    function clearZoneHighlights(){
      zones.forEach((z) => z.classList.remove('is-over'));
    }

    function zoneAtPoint(clientX, clientY){
      const el = document.elementFromPoint(clientX, clientY);
      if(!el) return null;
      return el.closest('#votingDecksPool, #bestDeckVoting .report-dropzone');
    }

    function placeDeckInZone(deckId, zone){
      const numericDeckId = Number(deckId || 0) || 0;
      if(!numericDeckId || !zone || !bestDeckVotingState) return;

      for(const place of ['1','2','3']){
        if(Number(bestDeckVotingState.placements?.[place] || 0) === numericDeckId){
          bestDeckVotingState.placements[place] = null;
        }
      }

      const targetPlace = String(zone.dataset.place || '').trim();
      if(targetPlace && ['1','2','3'].includes(targetPlace)){
        bestDeckVotingState.placements[targetPlace] = numericDeckId;
      }

      renderBestDeckVoting();
      setVotingError('');
    }

    const chips = Array.from(bestDeckVotingRootEl.querySelectorAll('.report-player-chip[draggable="true"]'));
    chips.forEach((chip) => {
      chip.addEventListener('dragstart', (ev) => {
        const deckId = chip.dataset.deckId;
        if(!deckId) return;
        ev.dataTransfer?.setData('text/plain', deckId);
      });

      chip.addEventListener('touchstart', () => {
        const deckId = chip.dataset.deckId;
        if(!deckId) return;
        touchDraggedDeckId = deckId;
        chip.classList.add('is-touch-picked');
      }, { passive: true });

      chip.addEventListener('touchmove', (ev) => {
        if(!touchDraggedDeckId) return;
        const t = ev.touches?.[0];
        if(!t) return;
        const targetZone = zoneAtPoint(t.clientX, t.clientY);
        clearZoneHighlights();
        targetZone?.classList.add('is-over');
      }, { passive: true });

      chip.addEventListener('touchend', (ev) => {
        chip.classList.remove('is-touch-picked');
        if(!touchDraggedDeckId) return;
        const t = ev.changedTouches?.[0];
        const targetZone = t ? zoneAtPoint(t.clientX, t.clientY) : null;
        clearZoneHighlights();
        if(targetZone) placeDeckInZone(touchDraggedDeckId, targetZone);
        touchDraggedDeckId = null;
      }, { passive: true });
    });

    zones.forEach((zone) => {
      if(zone.dataset.dragBound === '1') return;
      zone.dataset.dragBound = '1';

      zone.addEventListener('dragover', (ev) => {
        ev.preventDefault();
        zone.classList.add('is-over');
      });
      zone.addEventListener('dragleave', () => zone.classList.remove('is-over'));
      zone.addEventListener('drop', (ev) => {
        ev.preventDefault();
        zone.classList.remove('is-over');
        const deckId = ev.dataTransfer?.getData('text/plain');
        placeDeckInZone(deckId, zone);
      });
    });
  }

  async function loadBestDeckVotingData(){
    const r = await fetch(`/api/voting/best-deck/current?deck_id=${encodeURIComponent(currentDeckId)}`, { cache: 'no-store' });
    const data = await r.json();
    if(!r.ok) throw new Error(data?.detail || 'Best-Deck-Voting konnte nicht geladen werden.');

    bestDeckVotingState = {
      candidates: data.candidates || [],
      placements: {
        '1': Number(data.placements?.['1'] || 0) || null,
        '2': Number(data.placements?.['2'] || 0) || null,
        '3': Number(data.placements?.['3'] || 0) || null,
      },
      hasVote: !!data.has_vote,
    };

    if(bestDeckVotingTitleEl && data.phase_title){
      bestDeckVotingTitleEl.textContent = data.phase_title;
    }

    renderBestDeckVoting();
    setVotingError(bestDeckVotingState.hasVote ? 'Voting wurde bereits bestätigt.' : '');
  }

  function initBestDeckVoting(){
    if(!bestDeckVotingRootEl) return;

    loadBestDeckVotingData().catch((err) => {
      setVotingError(err?.message || 'Best-Deck-Voting konnte nicht geladen werden.');
    });

    resetBestDeckVoteBtn?.addEventListener('click', () => {
      if(!bestDeckVotingState || bestDeckVotingState.hasVote) return;
      bestDeckVotingState.placements = { '1': null, '2': null, '3': null };
      setVotingError('');
      renderBestDeckVoting();
    });

    submitBestDeckVoteBtn?.addEventListener('click', async () => {
      if(!bestDeckVotingState || bestDeckVotingState.hasVote) return;

      const placements = votingCollectPlacements();
      const complete = ['1','2','3'].every((place) => Number(placements[place] || 0) > 0);
      if(!complete){
        setVotingError('Bitte Rang 1 bis 3 vollständig belegen.');
        return;
      }

      try{
        const r = await fetch('/api/voting/best-deck/submit', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ deck_id: currentDeckId, placements }),
        });
        const data = await r.json();
        if(!r.ok) throw new Error(data?.detail || 'Best-Deck-Voting konnte nicht gespeichert werden.');
        bestDeckVotingState.hasVote = true;
        setVotingError('Voting erfolgreich bestätigt.');
        renderBestDeckVoting();
      }catch(err){
        setVotingError(err?.message || 'Best-Deck-Voting konnte nicht gespeichert werden.');
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
    initBestDeckVoting();
    hydratePairingMatchupChips();

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
