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