(() => {
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

  // --- WebSocket live reload (CCP) ---
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
      }catch(_){}
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

  // ----- Clear confirm modal -----
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

  document.addEventListener("DOMContentLoaded", () => {
    loadDefaultBackground();
    connectWS();
    initClearModal();
  });
})();
