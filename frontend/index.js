(() => {
  const MIN_CHARS = 3;
  const DEBOUNCE_MS = 350;

  // deck_id kommt aus data-Attribut (weil Jinja in externem JS nicht funktioniert)
  const rootCard = document.querySelector(".glass-card");
  const currentDeckId = Number(rootCard?.dataset?.deckId || "0") || 0;

  const commander1Input = document.getElementById("commander");
  const commander1Box = document.getElementById("commanderSuggestBox");
  const commander1Spinner = document.getElementById("commanderSpinner");

  const commander2Input = document.getElementById("commander2");
  const commander2Box = document.getElementById("commander2SuggestBox");
  const commander2Spinner = document.getElementById("commander2Spinner");

  const commander1Id = document.getElementById("commander_id");
  const commander2Id = document.getElementById("commander2_id");
  const submitBtn = document.getElementById("submitBtn");

  let commander1Confirmed = false;
  let commander2Confirmed = false;

  function updateSubmitEnabled(){
    if(!submitBtn) return;

    const c1HasText = !!(commander1Input?.value || "").trim();
    const c2HasText = !!(commander2Input?.value || "").trim();

    // optional, aber wenn Text da ist => confirmed + id muss da sein
    const c1Ok = !c1HasText || (commander1Confirmed && !!(commander1Id?.value || "").trim());
    const c2Ok = !c2HasText || (!commander2Input.disabled && commander2Confirmed && !!(commander2Id?.value || "").trim());

    // wenn commander2 gesetzt ist, muss commander1 gesetzt & confirmed sein
    const comboOk = !c2HasText || (c1HasText && commander1Confirmed);

    submitBtn.disabled = !(c1Ok && c2Ok && comboOk);
  }

  let commander1ConfirmedName = null;

  function setSpinner(spinnerEl, isLoading){
    if (!spinnerEl) return;
    spinnerEl.classList.toggle("show", !!isLoading);
  }

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
    }catch(_){}
  }

  async function loadCommanderBackground(name){
    try{
      const r = await fetch(`/api/background/commander?name=${encodeURIComponent(name)}`, { cache:"no-store" });
      if(!r.ok) return;
      const data = await r.json();
      if(data && data.url){
        setBackground(data.url, data.zoom || 1.0);
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
    } else if (inputEl === commander2Input) {
    commander2Confirmed = false;
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
  document.addEventListener("DOMContentLoaded", () => {
    // background init
    const current = (commander1Input?.value || "").trim();
    if (!current) loadDefaultBackground();
    else loadCommanderBackground(current);

    // Restore state on server-rendered errors (values may be prefilled)
    commander1Confirmed = !!((commander1Input?.value || "").trim() && (commander1Id?.value || "").trim());
    commander2Confirmed = !!((commander2Input?.value || "").trim() && (commander2Id?.value || "").trim());

    // commander2 enabled only if it has confirmed value OR will be enabled by picking commander1 again
    if (commander2Confirmed) {
    setCommander2Enabled(true);
    } else {
    setCommander2Enabled(false);
    }

    updateSubmitEnabled();

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
      await loadCommanderBackground(name);

      const partnerCapable = await checkPartnerCapable(name);
      setCommander2Enabled(partnerCapable);
    }
  });

  // Commander 2 suggest (only is:partner)
  attachSuggest({
    inputEl: commander2Input,
    boxEl: commander2Box,
    spinnerEl: commander2Spinner,
    endpointUrlBuilder: (q) => `/api/partner_suggest?q=${encodeURIComponent(q)}`,
    onPicked: async (_name) => {}
  });

})();
