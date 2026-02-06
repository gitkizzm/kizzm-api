// card_preview.js
// Kapselt die 3D-Kartenanzeige über der glass-card.

const API_BG_COMMANDER = "/api/background/commander";
const SCRYFALL_CARD_BACK = "https://cards.scryfall.io/back.png";

let root = null;
let slot1 = null;
let slot2 = null;

function qs(el, sel){ return el ? el.querySelector(sel) : null; }

function setFaceImage(cardEl, which, url){
  const face = qs(cardEl, `.card3d-face.${which}`);
  if(!face) return;

  if(which === "front"){
    if(url){
      face.style.backgroundImage = `url("${url}")`;
      cardEl.classList.remove("is-placeholder");
    }else{
      face.style.backgroundImage = "";
      cardEl.classList.add("is-placeholder");
    }
    return;
  }

  // back: darf placeholder-status nicht beeinflussen
  if(url){
    face.style.backgroundImage = `url("${url}")`;
  }else{
    face.style.backgroundImage = "";
  }
}

async function fetchBorderCrop(name){
  // nutzt deinen bestehenden Endpoint; liefert bei dir border_crop (fallback large)
  const r = await fetch(`${API_BG_COMMANDER}?name=${encodeURIComponent(name)}`, { cache: "no-store" });
  if(!r.ok) return null;
  const data = await r.json();
  return data?.url || null;
}

function applyMode(isPair){
  if(!root) return;
  root.classList.toggle("is-pair", !!isPair);
  root.classList.toggle("is-single", !isPair);
}

export function initCardPreview(){
  root = document.getElementById("cardPreview");
  if(!root) return;

  slot1 = qs(root, `.card3d.slot1`);
  slot2 = qs(root, `.card3d.slot2`);

  // Backfaces setzen (Scryfall Card Back)
  if(slot1) setFaceImage(slot1, "back", SCRYFALL_CARD_BACK);
  if(slot2) setFaceImage(slot2, "back", SCRYFALL_CARD_BACK);

  // Startzustand: single + Slot2 versteckt
  applyMode(false);
  // Startzustand: Slot1 Placeholder, single + Slot2 versteckt (Placeholder)
  applyMode(false);

  if(slot1){
    setFaceImage(slot1, "front", null); // => "?"
  }

  if(slot2){
    setFaceImage(slot2, "front", null); // => "?"
    slot2.classList.add("is-hidden");
    slot2.classList.add("is-placeholder");
  }
}

export async function setCommander1(name){
  if(!slot1) return;

  const n = (name || "").trim();
  if(!n){
    setFaceImage(slot1, "front", null);
    return;
  }

  const url = await fetchBorderCrop(n);
  setFaceImage(slot1, "front", url);
}

export function setPartnerSlotEnabled(enabled){
  if(!root || !slot2) return;

  if(enabled){
    // Slot2 sichtbar machen, aber als Placeholder
    slot2.classList.remove("is-hidden");
    slot2.classList.add("is-placeholder");
    setFaceImage(slot2, "front", null);
    applyMode(true);
  }else{
    // Slot2 zurücksetzen und verstecken
    setFaceImage(slot2, "front", null);
    slot2.classList.add("is-hidden");
    slot2.classList.add("is-placeholder");
    applyMode(false);
  }
}

export async function setCommander2(name){
  if(!slot2) return;

  const n = (name || "").trim();
  if(!n){
    setFaceImage(slot2, "front", null);
    return;
  }

  const url = await fetchBorderCrop(n);
  setFaceImage(slot2, "front", url);
}

export function resetCommander1(){
  if(!slot1) return;
  setFaceImage(slot1, "front", null);
}

export function resetCommander2(){
  if(!slot2) return;
  setFaceImage(slot2, "front", null);
}

export async function revealCommander1(name){
  if(!slot1) return;

  const n = (name || "").trim();
  if(!n) return;

  // Prefers-reduced-motion respektieren
  const reduce = window.matchMedia && window.matchMedia("(prefers-reduced-motion: reduce)").matches;
  if(reduce){
    await setCommander1(n);
    return;
  }

  // Während Reveal: kein Float-„Wackeln“
  const prevAnim = slot1.style.animation;
  slot1.style.animation = "none";

  // Start: Front erstmal leer/Placeholder, damit Reveal spürbar ist
  setFaceImage(slot1, "front", null);

  // Basispose (muss zu deinem CSS passen: rotateX(10deg) rotateY(-10deg))
  // Wir animieren mehrere schnelle Spins und bremsen dann aus.
  const baseX = 10;
  const baseY = -10;

  // Mehrfach drehen (z.B. 3.5 Umdrehungen) -> wirkt „fancy“, endet auf Front
  const spins = 4.0; // 3.5 = endet auf Vorderseite (0deg) bei richtiger Basis
  const endY = baseY; // wieder in Ausgangslage

  const duration = 2400;

  // Start: Rotation sehr schnell, dann immer langsamer (über Offsets gesteuert)
  const anim = slot1.animate(
    [
      { transform: `rotateX(${baseX}deg) rotateY(${baseY}deg)` },

      // sehr viel Rotation früh
      { transform: `rotateX(${baseX}deg) rotateY(${baseY + 360 * 2.2}deg)`, offset: 0.22 },

      // weniger Rotation pro Zeit
      { transform: `rotateX(${baseX}deg) rotateY(${baseY + 360 * 3.1}deg)`, offset: 0.48 },

      // stark auslaufend
      { transform: `rotateX(${baseX}deg) rotateY(${baseY + 360 * 3.6}deg)`, offset: 0.74 },

      // Ende exakt auf Front
      { transform: `rotateX(${baseX}deg) rotateY(${baseY + 360 * spins}deg)` },
    ],
    {
      duration,
      easing: "linear",  // Offsets machen das Abbremsen sichtbar
      fill: "forwards",
    }
  );

  // Kurz vor Ende das Frontbild laden und setzen (damit es „revealed“ wirkt)
  // Wir warten nicht zu lang, damit der Fetch noch während des Spins fertig wird.
  try{
    // kleine Verzögerung, damit erst „Rücken“ wahrgenommen wird
    // Bild parallel laden (ohne zu blocken)
    const urlPromise = fetchBorderCrop(n);

    // „Reveal“-Zeitpunkt: noch während die Karte rotiert
    setTimeout(async () => {
      try{
        const url = await urlPromise;
        setFaceImage(slot1, "front", url);  // direkt setzen, ohne zusätzliche Logik
      }catch(_){}
    }, 900);
  }catch(_){}

  // Warten bis Animation fertig ist
  try{ await anim.finished; }catch(_){}

  try{
    anim.commitStyles();   // finalen Zustand als inline style übernehmen
  }catch(_){}

  anim.cancel();

  // Jetzt zurück zur CSS-Logik, ohne sichtbaren Sprung:
  // (Transform ist identisch zur CSS-Basis, weil wir auf 4.0 Spins enden.)
  slot1.style.transform = "";
  slot1.style.animation = prevAnim || "";
}

export async function revealCommanders(commander1Name, commander2Name){
  const c1 = (commander1Name || "").trim();
  const c2 = (commander2Name || "").trim();

  if(!c1) return;

  // Slot2 Zustand wie in der Registrierung setzen
  setPartnerSlotEnabled(!!c2);

  // Slot1 sofort starten (nicht awaiten -> läuft parallel)
  const p1 = revealCommander1(c1);

  // Slot2: während Slot1 ausläuft starten
  if(c2){
    // Timing: so wählen, dass Slot1 schon in der „slowdown“-Phase ist
    // Bei deiner duration ~2400ms passt ~1450-1650ms meist gut.
    const OVERLAP_START_MS = 700;

    await new Promise(r => setTimeout(r, OVERLAP_START_MS));
    const p2 = revealCommander2(c2);

    // Beide fertig werden lassen
    await Promise.allSettled([p1, p2]);
    return;
  }

  // Kein Partner: nur Slot1 fertig abwarten
  await p1;
}

export async function revealCommander2(name){
  if(!slot2) return;

  const n = (name || "").trim();
  if(!n) return;

  const reduce = window.matchMedia && window.matchMedia("(prefers-reduced-motion: reduce)").matches;
  if(reduce){
    await setCommander2(n);
    return;
  }

  const prevAnim = slot2.style.animation;
  slot2.style.animation = "none";

  // Front erstmal leer/Placeholder für sichtbaren Reveal
  setFaceImage(slot2, "front", null);

  const baseX = 10;
  // Slot2 Basis-Y in Pair-Mode ist bei dir positiv (siehe CSS), wir nehmen +16 als „gefühlte“ Pose
  const baseY = 16;

  const spins = 4;

  const duration = 2400;

  const anim = slot2.animate(
    [
      { transform: `rotateX(${baseX}deg) rotateY(${baseY}deg)` },
      { transform: `rotateX(${baseX}deg) rotateY(${baseY + 360 * 2.2}deg)`, offset: 0.22 },
      { transform: `rotateX(${baseX}deg) rotateY(${baseY + 360 * 3.1}deg)`, offset: 0.48 },
      { transform: `rotateX(${baseX}deg) rotateY(${baseY + 360 * 3.6}deg)`, offset: 0.74 },
      { transform: `rotateX(${baseX}deg) rotateY(${baseY + 360 * spins}deg)` },
    ],
    { duration, easing: "linear", fill: "forwards" }
  );

  const urlPromise = fetchBorderCrop(n);

  setTimeout(async () => {
    try{
      const url = await urlPromise;
      setFaceImage(slot2, "front", url);
    }catch(_){}
  }, 900);

  try{ await anim.finished; }catch(_){}
  try{ anim.commitStyles(); }catch(_){}
  anim.cancel();

  slot2.style.transform = "";
  slot2.style.animation = prevAnim || "";
}