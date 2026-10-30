// VintedPro Sync -- script injecté sur vinted.fr.
// Détecte le compte connecté via l'API Vinted, appelée DEPUIS la page (mêmes
// cookies que le navigateur). Aucun jeton n'est lu ni transmis : seuls
// l'identifiant de membre et le pseudo partent vers le service worker.

async function detecterCompte() {
  try {
    const r = await fetch('/api/v2/users/current', {
      credentials: 'include',
      headers: { Accept: 'application/json' },
    });
    if (r.status === 401 || r.status === 403) return { connecte: false };
    if (!r.ok) return { connecte: null, erreur: `HTTP ${r.status}` };
    const j = await r.json();
    const u = j && j.user;
    if (!u || !u.id) return { connecte: false };
    return { connecte: true, member_id: String(u.id), login: u.login || '' };
  } catch (e) {
    return { connecte: null, erreur: String(e) };
  }
}

async function signaler() {
  const compte = await detecterCompte();
  chrome.runtime.sendMessage({ type: 'compte_detecte', compte });
}

signaler();

// Découverte (26/10/2026) : appel GET en LECTURE SEULE d'une adresse de l'API
// Vinted, réponse renvoyée au popup uniquement (jamais au backend). Limité à
// /api/v2/ en GET pour qu'aucune action ne puisse partir de là.
async function explorer(chemin) {
  if (!/^\/api\/v2\/[\w\-/?=&.%,]*$/.test(chemin)) {
    return { status: 0, erreur: 'Adresse refusée : uniquement /api/v2/… en lecture.' };
  }
  try {
    const r = await fetch(chemin, { credentials: 'include', headers: { Accept: 'application/json' } });
    let json = null;
    try { json = await r.json(); } catch (e) { /* réponse non JSON */ }
    return { status: r.status, json };
  } catch (e) {
    return { status: 0, erreur: String(e) };
  }
}

// Le popup peut demander une nouvelle détection (après connexion/déconnexion)
chrome.runtime.onMessage.addListener((msg, _sender, repondre) => {
  if (msg.type === 'explorer') {
    explorer(msg.chemin).then(repondre);
    return true;
  }
  if (msg.type === 'redetecter') {
    detecterCompte().then((compte) => {
      chrome.runtime.sendMessage({ type: 'compte_detecte', compte });
      repondre(compte);
    });
    return true;
  }
});
