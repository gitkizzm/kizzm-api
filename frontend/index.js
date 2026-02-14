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
  const votingHintEl = document.getElementById("votingHint");
  const votingResultsEl = document.getElementById("votingResults");
  const submitBestDeckVoteBtn = document.getElementById("submitBestDeckVote");
  const resetBestDeckVoteBtn = document.getElementById("resetBestDeckVote");

  let reportState = null;
  let bestDeckVotingState = null;
  let chipPreviewUi = {
    modalStyle: false,
    revealAnimation: false,
    swipeEnabled: true,
  };

  const chipPreviewOverlayEl = document.getElementById('chipPreviewOverlay');
  const chipPreviewOverlayCloseEl = document.getElementById('chipPreviewOverlayClose');
  const chipPreviewModalEl = document.getElementById('chipPreviewModal');
  const chipPreviewModalCloseEl = document.getElementById('chipPreviewModalClose');
  const chipPreviewModalCardHostEl = document.getElementById('chipPreviewModalCardHost');
  const chipPreviewNamesEl = document.getElementById('chipPreviewNames');
  let chipPreviewFrontSlot = 1;
  let chipPreviewTouchStartX = null;

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
        chip.dataset.commander = String(playerMeta?.[player]?.commander || '').trim();
        chip.dataset.commander2 = String(playerMeta?.[player]?.commander2 || '').trim();
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

  async function loadChipPreviewSettings(){
    try{
      const r = await fetch('/api/settings/effective', { cache: 'no-store' });
      const data = await r.json();
      if(!r.ok) return;
      chipPreviewUi.modalStyle = !!data?.settings?.ui?.chip_preview_modal_style;
      chipPreviewUi.revealAnimation = !!data?.settings?.ui?.chip_preview_reveal_animation;
      chipPreviewUi.swipeEnabled = data?.settings?.ui?.chip_preview_swipe_enabled === true;
    }catch(_){
      // defaults bleiben aktiv
    }
  }

  function setChipPreviewNames(commander1, commander2){
    if(!chipPreviewNamesEl) return;
    const c1 = String(commander1 || '').trim();
    const c2 = String(commander2 || '').trim();
    const names = chipPreviewFrontSlot === 2 ? [c2, c1].filter(Boolean) : [c1, c2].filter(Boolean);
    chipPreviewNamesEl.textContent = names.join(' / ');
  }

  function setChipPreviewFrontSlot(slot){
    const previewEl = document.getElementById('cardPreview');
    chipPreviewFrontSlot = slot === 2 ? 2 : 1;
    previewEl?.classList.toggle('is-swipe-swapped', chipPreviewFrontSlot === 2);
  }

  async function animateChipPreviewFromAvatar(previewEl, avatarEl){
    if(!previewEl || !avatarEl) return;
    const reduce = window.matchMedia && window.matchMedia('(prefers-reduced-motion: reduce)').matches;
    if(reduce) return;

    const startRect = avatarEl.getBoundingClientRect();
    await new Promise((resolve) => requestAnimationFrame(resolve));
    const endRect = previewEl.getBoundingClientRect();
    if(endRect.width <= 0 || endRect.height <= 0) return;

    const startCx = startRect.left + (startRect.width / 2);
    const startCy = startRect.top + (startRect.height / 2);
    const endCx = endRect.left + (endRect.width / 2);
    const endCy = endRect.top + (endRect.height / 2);

    const dx = startCx - endCx;
    const dy = startCy - endCy;
    const startScale = Math.max(0.06, Math.min(0.18, startRect.width / Math.max(endRect.width, 1)));

    try{
      previewEl.getAnimations?.().forEach((anim) => anim.cancel());
    }catch(_){ }

    previewEl.animate(
      [
        { translate: `${dx}px ${dy}px`, scale: `${startScale}`, opacity: 0.25 },
        { translate: '0px 0px', scale: '1', opacity: 1 },
      ],
      {
        duration: 600,
        easing: 'cubic-bezier(0.2, 0.9, 0.2, 1)',
        fill: 'none',
      }
    );
  }

  function bindChipPreviewSwipe(commander1, commander2){
    const previewEl = document.getElementById('cardPreview');
    if(!previewEl) return;

    const hasPartner = !!String(commander2 || '').trim();
    previewEl.classList.toggle('is-chip-preview-partner', hasPartner);
    previewEl.classList.toggle('is-chip-preview-swipe-enabled', hasPartner && chipPreviewUi.swipeEnabled);

    if(!hasPartner){
      return;
    }

    const applyFromDelta = (deltaX) => {
      if(deltaX > 0) setChipPreviewFrontSlot(1);
      else if(deltaX < 0) setChipPreviewFrontSlot(2);
      setChipPreviewNames(commander1, commander2);
    };

    previewEl.onpointerdown = (ev) => {
      chipPreviewTouchStartX = ev.clientX;
      if(typeof previewEl.setPointerCapture === 'function'){
        try{ previewEl.setPointerCapture(ev.pointerId); }catch(_){ }
      }
    };

    previewEl.onpointerup = (ev) => {
      if(chipPreviewTouchStartX === null) return;
      const deltaX = ev.clientX - chipPreviewTouchStartX;
      chipPreviewTouchStartX = null;
      if(Math.abs(deltaX) < 40) return;
      if(chipPreviewUi.swipeEnabled){
        applyFromDelta(deltaX);
      }
    };

    previewEl.ontouchstart = (ev) => {
      chipPreviewTouchStartX = ev.touches?.[0]?.clientX ?? null;
    };

    previewEl.ontouchend = (ev) => {
      if(chipPreviewTouchStartX === null) return;
      const endX = ev.changedTouches?.[0]?.clientX;
      if(typeof endX !== 'number') return;
      const deltaX = endX - chipPreviewTouchStartX;
      chipPreviewTouchStartX = null;
      if(Math.abs(deltaX) < 40) return;
      if(chipPreviewUi.swipeEnabled){
        applyFromDelta(deltaX);
      }
    };

    previewEl.onclick = (ev) => {
      if(chipPreviewUi.swipeEnabled) return;
      const cardEl = ev.target instanceof Element ? ev.target.closest('.card3d[data-slot]') : null;
      const slot = Number(cardEl?.dataset?.slot || '0');
      if(slot <= 0 || slot === chipPreviewFrontSlot) return;
      setChipPreviewFrontSlot(slot);
      setChipPreviewNames(commander1, commander2);
    };
  }

  function closeChipPreview(){
    const previewEl = document.getElementById('cardPreview');
    previewEl?.classList.remove('is-chip-preview-expanded');
    previewEl?.classList.remove('is-chip-preview-partner');
    previewEl?.classList.remove('is-swipe-swapped');
    previewEl?.classList.remove('is-chip-preview-swipe-enabled');
    if(previewEl){
      previewEl.onpointerdown = null;
      previewEl.onpointerup = null;
      previewEl.onpointercancel = null;
      previewEl.ontouchstart = null;
      previewEl.ontouchend = null;
      previewEl.onclick = null;
      try{ previewEl.getAnimations?.().forEach((anim) => anim.cancel()); }catch(_){ }
    }
    chipPreviewTouchStartX = null;
    chipPreviewFrontSlot = 1;
    chipPreviewOverlayEl && (chipPreviewOverlayEl.style.display = 'none');
    if(chipPreviewModalEl){
      chipPreviewModalEl.classList.remove('show');
      chipPreviewModalEl.setAttribute('aria-hidden', 'true');
    }
  }

  async function showChipPreview(commander1, commander2, avatarEl){
    const c1 = String(commander1 || '').trim();
    const c2 = String(commander2 || '').trim();
    if(!c1) return;

    const previewEl = document.getElementById('cardPreview');
    if(!previewEl) return;
    setChipPreviewFrontSlot(1);
    previewEl.classList.add('is-chip-preview-expanded');

    if(chipPreviewUi.modalStyle){
      if(chipPreviewModalCardHostEl && previewEl.parentElement !== chipPreviewModalCardHostEl){
        chipPreviewModalCardHostEl.appendChild(previewEl);
      }
      if(chipPreviewOverlayEl) chipPreviewOverlayEl.style.display = 'none';
      if(chipPreviewModalEl){
        chipPreviewModalEl.classList.add('show');
        chipPreviewModalEl.setAttribute('aria-hidden', 'false');
      }
    }else{
      if(chipPreviewOverlayEl && previewEl.parentElement !== chipPreviewOverlayEl){
        chipPreviewOverlayEl.appendChild(previewEl);
      }
      if(chipPreviewModalEl){
        chipPreviewModalEl.classList.remove('show');
        chipPreviewModalEl.setAttribute('aria-hidden', 'true');
      }
      if(chipPreviewOverlayEl) chipPreviewOverlayEl.style.display = 'block';
    }

    bindChipPreviewSwipe(c1, c2);
    setChipPreviewNames(c1, c2);
    await animateChipPreviewFromAvatar(previewEl, avatarEl);

    await ensureCardPreviewLoaded();
    cardPreview.initCardPreview();
    cardPreview.setPartnerSlotEnabled(!!c2);

    if(chipPreviewUi.revealAnimation){
      await cardPreview.revealCommanders(c1, c2);
    }else{
      await cardPreview.setCommander1(c1);
      if(c2) await cardPreview.setCommander2(c2);
    }
  }

  function bindChipPreviewEvents(){
    const roots = [document.querySelector('.pairing-matchup-grid'), bestDeckVotingRootEl].filter(Boolean);
    if(roots.length === 0) return;

    roots.forEach((root) => {
      root.addEventListener('click', (ev) => {
        const avatarEl = ev.target instanceof Element ? ev.target.closest('.report-player-avatar') : null;
        if(!avatarEl) return;
        const chip = avatarEl.closest('.report-player-chip');
        if(!chip) return;
        const commander1 = String(chip.dataset?.commander || '').trim();
        const commander2 = String(chip.dataset?.commander2 || '').trim();
        if(!commander1) return;
        showChipPreview(commander1, commander2, avatarEl).catch(() => {});
      });
    });

    chipPreviewOverlayCloseEl?.addEventListener('click', closeChipPreview);
    chipPreviewModalCloseEl?.addEventListener('click', closeChipPreview);
    chipPreviewModalEl?.addEventListener('click', (ev) => {
      if(ev.target === chipPreviewModalEl) closeChipPreview();
    });
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

  function setVotingHint(msg){
    if(!votingHintEl) return;
    const message = String(msg || '').trim();
    votingHintEl.textContent = message;
    votingHintEl.style.display = message ? 'block' : 'none';
  }

  function renderVotingResults(results){
    if(!votingResultsEl) return;
    const rows = Array.isArray(results?.rows) ? results.rows : [];
    if(rows.length === 0){
      votingResultsEl.style.display = 'none';
      votingResultsEl.innerHTML = '';
      return;
    }
    const body = rows.map((row, idx) => `
      <tr>
        <td>${idx + 1}</td>
        <td>${escapeHtml(String(row.player || ''))}</td>
        <td>${escapeHtml(String(row.deck_name || ''))}</td>
        <td>${Number(row.game_points || 0)}</td>
        <td>${Number(row.deck_voting_points || 0)}</td>
        <td>${Number(row.guess_points || 0)}</td>
        <td><strong>${Number(row.total_points || 0)}</strong></td>
      </tr>
    `).join('');
    votingResultsEl.innerHTML = `
      <div class="status" style="margin-top:10px;">
        <strong>Overall-Auswertung</strong>
        <div style="overflow:auto; margin-top:8px;">
          <table style="width:100%; border-collapse: collapse; font-size: 0.95em;">
            <thead>
              <tr>
                <th style="text-align:left; padding:4px;">#</th>
                <th style="text-align:left; padding:4px;">Spieler</th>
                <th style="text-align:left; padding:4px;">Deck</th>
                <th style="text-align:right; padding:4px;">Spielpunkte</th>
                <th style="text-align:right; padding:4px;">Deck-Voting</th>
                <th style="text-align:right; padding:4px;">Ratepunkte</th>
                <th style="text-align:right; padding:4px;">Gesamt</th>
              </tr>
            </thead>
            <tbody>${body}</tbody>
          </table>
        </div>
      </div>`;
    votingResultsEl.style.display = 'block';
  }

  function currentVotingPlaces(){
    return Array.isArray(bestDeckVotingState?.places) ? bestDeckVotingState.places : [];
  }

  function shuffleList(items){
    const arr = Array.isArray(items) ? [...items] : [];
    for(let idx = arr.length - 1; idx > 0; idx--){
      const randIdx = Math.floor(Math.random() * (idx + 1));
      [arr[idx], arr[randIdx]] = [arr[randIdx], arr[idx]];
    }
    return arr;
  }

  function renderVotingDeck(deck){
    if(!deck) return '';
    const id = Number(deck.deck_id || 0) || 0;
    const commander = String(deck.commander || '').trim();
    const commander1 = String(deck.commander1 || '').trim();
    const commander2 = String(deck.commander2 || '').trim();
    const title = commander || `Deck #${id}`;
    const avatarUrl = String(deck.avatar_url || '').trim();
    const avatar = avatarUrl
      ? `<img src="${escapeHtml(avatarUrl)}" alt="" class="report-player-avatar-img">`
      : `<span class="report-player-avatar-fallback">${escapeHtml(String(title).slice(0, 1).toUpperCase())}</span>`;

    return `<div class="report-player-chip report-player-chip--matchup report-player-chip--content-left report-player-chip--voting" draggable="true" data-deck-id="${id}" data-commander="${escapeHtml(commander1 || commander)}" data-commander2="${escapeHtml(commander2)}" title="${escapeHtml(title)}" aria-label="${escapeHtml(title)}">
      <div class="report-player-avatar">${avatar}</div>
      <div class="report-player-name">${escapeHtml(title)}</div>
    </div>`;
  }

  function votingCollectPlacements(){
    const result = {};
    for(const place of currentVotingPlaces()){
      const placeId = String(place?.id || '').trim();
      if(!placeId) continue;
      const zone = votingPlacesEl?.querySelector(`.report-dropzone[data-place="${CSS.escape(placeId)}"]`);
      const chip = zone?.querySelector('.report-player-chip[data-deck-id]');
      const value = Number(chip?.dataset?.deckId || '0') || 0;
      result[placeId] = value > 0 ? value : null;
    }
    return result;
  }

  function renderVotingPlaces(){
    if(!votingPlacesEl) return;
    const places = currentVotingPlaces();
    votingPlacesEl.innerHTML = places.map((place) => {
      const placeId = String(place?.id || '').trim();
      const label = String(place?.label || placeId || '').trim();
      if(!placeId) return '';
      return `<div class="report-place-slot" data-place="${escapeHtml(placeId)}"><div class="report-dropzone" data-place="${escapeHtml(placeId)}" data-place-label="${escapeHtml(label)}"></div></div>`;
    }).join('');
  }

  function renderBestDeckVoting(){
    if(!bestDeckVotingState || !votingDecksPoolEl || !votingPlacesEl) return;

    const isPublished = bestDeckVotingState.votingKind === 'results_published';
    const isWaitingResults = bestDeckVotingState.votingKind === 'waiting_results';
    const votingLayoutEl = bestDeckVotingRootEl?.querySelector('.voting-layout');

    renderVotingResults(isPublished ? bestDeckVotingState.results : null);
    if(isPublished || isWaitingResults){
      if(isPublished) setVotingHint('');
      votingDecksPoolEl.innerHTML = '';
      votingPlacesEl.innerHTML = '';
      if(votingLayoutEl) votingLayoutEl.style.display = 'none';
      if(votingErrorEl) votingErrorEl.style.display = 'none';
      if(submitBestDeckVoteBtn?.parentElement) submitBestDeckVoteBtn.parentElement.style.display = 'none';
      submitBestDeckVoteBtn && (submitBestDeckVoteBtn.disabled = true);
      resetBestDeckVoteBtn && (resetBestDeckVoteBtn.disabled = true);
      return;
    }

    if(votingLayoutEl) votingLayoutEl.style.display = '';
    if(submitBestDeckVoteBtn?.parentElement) submitBestDeckVoteBtn.parentElement.style.display = '';

    renderVotingPlaces();
    const places = currentVotingPlaces();
    const placedIds = new Set(
      places
        .map((place) => Number(bestDeckVotingState.placements?.[String(place.id || '').trim()] || 0))
        .filter((it) => it > 0)
    );

    const poolDecks = (bestDeckVotingState.candidates || []).filter((deck) => !placedIds.has(Number(deck.deck_id || 0)));
    votingDecksPoolEl.innerHTML = poolDecks.map(renderVotingDeck).join('');

    for(const place of places){
      const placeId = String(place?.id || '').trim();
      if(!placeId) continue;
      const zone = votingPlacesEl.querySelector(`.report-dropzone[data-place="${CSS.escape(placeId)}"]`);
      if(!zone) continue;
      const deckId = Number(bestDeckVotingState.placements?.[placeId] || 0) || 0;
      const deck = (bestDeckVotingState.candidates || []).find((item) => Number(item.deck_id || 0) === deckId);
      zone.innerHTML = deck ? renderVotingDeck(deck) : '';
    }

    const complete = places.length > 0 && places.every((place) => Number(bestDeckVotingState.placements?.[String(place.id || '').trim()] || 0) > 0);
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
    let touchDraggedFromPlace = null;
    let dragFromPlace = null;
    const places = currentVotingPlaces().map((place) => String(place?.id || '').trim()).filter(Boolean);
    const zones = [votingDecksPoolEl, ...Array.from(bestDeckVotingRootEl.querySelectorAll('.report-dropzone'))].filter(Boolean);

    function clearZoneHighlights(){
      zones.forEach((z) => z.classList.remove('is-over'));
    }

    function zoneAtPoint(clientX, clientY){
      const el = document.elementFromPoint(clientX, clientY);
      if(!el) return null;
      return el.closest('#votingDecksPool, #bestDeckVoting .report-dropzone');
    }

    function isPlace(place){
      return places.includes(String(place || '').trim());
    }

    function placeDeckInZone(deckId, zone, sourcePlace = null){
      const numericDeckId = Number(deckId || 0) || 0;
      if(!numericDeckId || !zone || !bestDeckVotingState) return;

      const targetPlace = String(zone.dataset.place || '').trim();

      let inferredSourcePlace = '';
      for(const place of places){
        if(Number(bestDeckVotingState.placements?.[place] || 0) === numericDeckId){
          inferredSourcePlace = place;
          break;
        }
      }

      const normalizedSourcePlace = String(sourcePlace || inferredSourcePlace || '').trim();
      const sourceIsPlace = isPlace(normalizedSourcePlace);
      const targetIsPlace = isPlace(targetPlace);
      const targetCurrentDeckId = targetIsPlace ? (Number(bestDeckVotingState.placements?.[targetPlace] || 0) || null) : null;

      for(const place of places){
        if(Number(bestDeckVotingState.placements?.[place] || 0) === numericDeckId){
          bestDeckVotingState.placements[place] = null;
        }
      }

      if(targetIsPlace){
        bestDeckVotingState.placements[targetPlace] = numericDeckId;

        if(
          sourceIsPlace
          && normalizedSourcePlace !== targetPlace
          && targetCurrentDeckId
          && targetCurrentDeckId !== numericDeckId
        ){
          bestDeckVotingState.placements[normalizedSourcePlace] = targetCurrentDeckId;
        }
      }

      renderBestDeckVoting();
      setVotingError('');
    }

    const chips = Array.from(bestDeckVotingRootEl.querySelectorAll('.report-player-chip[draggable="true"]'));
    chips.forEach((chip) => {
      chip.addEventListener('dragstart', (ev) => {
        const deckId = chip.dataset.deckId;
        if(!deckId) return;
        dragFromPlace = chip.closest('.report-dropzone')?.dataset?.place || null;
        ev.dataTransfer?.setData('text/plain', deckId);
      });

      chip.addEventListener('dragend', () => {
        dragFromPlace = null;
      });

      chip.addEventListener('touchstart', () => {
        const deckId = chip.dataset.deckId;
        if(!deckId) return;
        touchDraggedDeckId = deckId;
        touchDraggedFromPlace = chip.closest('.report-dropzone')?.dataset?.place || null;
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
        if(targetZone) placeDeckInZone(touchDraggedDeckId, targetZone, touchDraggedFromPlace);
        touchDraggedDeckId = null;
        touchDraggedFromPlace = null;
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
        placeDeckInZone(deckId, zone, dragFromPlace);
        dragFromPlace = null;
      });
    });
  }

  async function loadBestDeckVotingData(){
    const r = await fetch(`/api/voting/best-deck/current?deck_id=${encodeURIComponent(currentDeckId)}`, { cache: 'no-store' });
    const data = await r.json();
    if(!r.ok) throw new Error(data?.detail || 'Voting konnte nicht geladen werden.');

    const placesRaw = Array.isArray(data.places) ? data.places : [
      { id: '1', label: 'Rang 1' },
      { id: '2', label: 'Rang 2' },
      { id: '3', label: 'Rang 3' },
    ];
    const votingKind = String(data.voting_kind || 'top3_fixed').trim();
    const places = votingKind === 'deck_creator_guess' ? shuffleList(placesRaw) : placesRaw;
    const placements = {};
    for(const place of places){
      const placeId = String(place?.id || '').trim();
      if(!placeId) continue;
      placements[placeId] = Number(data.placements?.[placeId] || 0) || null;
    }

    bestDeckVotingState = {
      votingKind,
      candidates: shuffleList(data.candidates || []),
      places,
      placements,
      hasVote: !!data.has_vote,
      results: data.results || null,
    };

    if(bestDeckVotingTitleEl){
      bestDeckVotingTitleEl.textContent = data.phase_title || 'Best-Deck-Voting';
    }

    const isPublished = String(data.voting_kind || '').trim() === 'results_published';
    setVotingHint(isPublished ? '' : (data.status_message || ''));

    renderBestDeckVoting();
    if(bestDeckVotingState.votingKind === 'results_published' || bestDeckVotingState.votingKind === 'waiting_results') setVotingError('');
    else setVotingError(bestDeckVotingState.hasVote ? 'Voting wurde bereits bestätigt.' : '');
  }

  function initBestDeckVoting(){
    if(!bestDeckVotingRootEl) return;

    loadBestDeckVotingData().catch((err) => {
      setVotingError(err?.message || 'Voting konnte nicht geladen werden.');
    });

    resetBestDeckVoteBtn?.addEventListener('click', () => {
      if(!bestDeckVotingState || bestDeckVotingState.hasVote) return;
      for(const place of currentVotingPlaces()){
        const placeId = String(place?.id || '').trim();
        if(!placeId) continue;
        bestDeckVotingState.placements[placeId] = null;
      }
      setVotingError('');
      renderBestDeckVoting();
    });

    submitBestDeckVoteBtn?.addEventListener('click', async () => {
      if(!bestDeckVotingState || bestDeckVotingState.hasVote) return;

      const placements = votingCollectPlacements();
      const places = currentVotingPlaces();
      const complete = places.length > 0 && places.every((place) => Number(placements[String(place.id || '').trim()] || 0) > 0);
      if(!complete){
        setVotingError('Bitte alle Decks vollständig zuordnen.');
        return;
      }

      try{
        const r = await fetch('/api/voting/best-deck/submit', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ deck_id: currentDeckId, placements }),
        });
        const data = await r.json();
        if(!r.ok) throw new Error(data?.detail || 'Voting konnte nicht gespeichert werden.');
        await loadBestDeckVotingData();
        setVotingError('Voting erfolgreich bestätigt.');
      }catch(err){
        setVotingError(err?.message || 'Voting konnte nicht gespeichert werden.');
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
    await hydratePairingMatchupChips();
    await loadChipPreviewSettings();
    bindChipPreviewEvents();

    document.addEventListener('keydown', (ev) => {
      if(ev.key === 'Escape') closeChipPreview();
    });

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
