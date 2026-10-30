// VintedPro Sync -- service worker.
// Seul point de contact avec le backend local : une page vinted.fr ne peut
// pas appeler localhost directement (Private Network Access de Chrome).

const BACKEND = 'http://localhost:8000/api/extension';

async function appelBackend(chemin, methode, corps) {
  try {
    const r = await fetch(`${BACKEND}${chemin}`, {
      method: methode,
      headers: { 'Content-Type': 'application/json' },
      body: corps ? JSON.stringify(corps) : undefined,
    });
    const donnees = await r.json().catch(() => ({}));
    if (!r.ok) return { ok: false, erreur: donnees.detail || `HTTP ${r.status}` };
    return { ok: true, ...donnees };
  } catch (e) {
    return { ok: false, erreur: 'Backend injoignable (localhost:8000 lancé ?)' };
  }
}

// État mémorisé pour le popup : dernier compte détecté + réponse du backend
async function majEtat(compte) {
  let backend = null;
  if (compte && compte.connecte) {
    backend = await appelBackend('/ping', 'POST', { member_id: compte.member_id, login: compte.login });
  }
  const etat = { compte, backend, maj: new Date().toISOString() };
  await chrome.storage.local.set({ etat });
  return etat;
}

chrome.runtime.onMessage.addListener((msg, _sender, repondre) => {
  if (msg.type === 'compte_detecte') {
    majEtat(msg.compte).then(repondre);
    return true;
  }
  if (msg.type === 'lier' || msg.type === 'delier') {
    chrome.storage.local.get('etat').then(async ({ etat }) => {
      const compte = etat && etat.compte;
      if (!compte || !compte.connecte) {
        repondre({ ok: false, erreur: 'Aucun compte Vinted détecté — ouvre un onglet vinted.fr connecté.' });
        return;
      }
      const res = msg.type === 'lier'
        ? await appelBackend('/lier', 'POST', { member_id: compte.member_id, login: compte.login })
        : await appelBackend(`/lier/${compte.member_id}`, 'DELETE');
      const nouvel = await majEtat(compte);
      repondre({ ...res, etat: nouvel });
    });
    return true;
  }
});
