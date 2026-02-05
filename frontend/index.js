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
      hideBox(commander2Box);
    }
  }

  function escapeHtml(s){
    return String(s)
      .replaceAll("&","&amp;")
      .replaceAll("<","&lt;")
      .replaceAll(">","&gt;")
      .replaceAll('"',"&quot;")
      .replaceAll("'","&#039;");
  }

  function renderBox(boxEl, names){
    if(!boxEl) return;
    if(!names || names.length === 0){
      hideBox(boxEl);
      return;
    }
    boxEl.innerHTML = names
      .map(n => `<div class="suggest-item" data-name="${escapeHtml(n)}">${escapeHtml(n)}</div>`)
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

      // Commander1: sobald User editiert/cleart -> confirmation ungültig -> commander2 reset
      if (inputEl === commander1Input) {
        if (!q) {
          commander1ConfirmedName = null;
          setCommander2Enabled(false);
          loadDefaultBackground();
        } else if (commander1ConfirmedName && q !== commander1ConfirmedName) {
          commander1ConfirmedName = null;
          setCommander2Enabled(false);
        }
      }

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
      const name = item.getAttribute("data-name");
      inputEl.value = name;
      hideBox(boxEl);
      if(onPicked) await onPicked(name);
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

    // commander2 always starts disabled until commander1 is confirmed + partner-capable
    setCommander2Enabled(false);

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
