// VintedPro Sync -- fenêtre de l'extension : état du compte détecté et liaison.

const $ = (id) => document.getElementById(id);

function afficher(texte, classe, id) {
  $(id).textContent = texte;
  $(id).className = classe;
}

function rendre(etat) {
  const compte = etat && etat.compte;
  const backend = etat && etat.backend;
  $('lier').disabled = true;
  $('delier').hidden = true;

  if (!compte) {
    afficher('Ouvre un onglet vinted.fr', 'neutre', 'compte');
    afficher('—', 'neutre', 'dressing');
    afficher('—', 'neutre', 'liaison');
    return;
  }
  if (compte.connecte === false) {
    afficher('Non connecté', 'ko', 'compte');
    afficher('—', 'neutre', 'dressing');
    afficher('—', 'neutre', 'liaison');
    return;
  }
  if (compte.connecte !== true) {
    afficher(`Indéterminé (${compte.erreur || '?'})`, 'ko', 'compte');
    return;
  }
  afficher(`@${compte.login} (${compte.member_id})`, 'ok', 'compte');

  if (!backend || !backend.ok) {
    afficher('—', 'neutre', 'dressing');
    afficher(backend ? backend.erreur : 'Backend non contacté', 'ko', 'liaison');
    return;
  }
  if (!backend.connu) {
    afficher('Inconnu (ni D1 ni D2)', 'ko', 'dressing');
    afficher('Impossible', 'ko', 'liaison');
    return;
  }
  afficher(backend.dressing, 'ok', 'dressing');
  if (backend.lie) {
    const depuis = backend.liaison && backend.liaison.lie_le ? backend.liaison.lie_le.replace('T', ' ') : '';
    afficher(`✅ Lié${depuis ? ` depuis le ${depuis}` : ''}`, 'ok', 'liaison');
    $('delier').hidden = false;
  } else {
    afficher('Non lié', 'neutre', 'liaison');
    $('lier').disabled = false;
  }
}

async function ongletVinted() {
  const [onglet] = await chrome.tabs.query({ active: true, currentWindow: true });
  return onglet && onglet.url && onglet.url.startsWith('https://www.vinted.fr') ? onglet : null;
}

async function redetecter() {
  $('message').textContent = '';
  const onglet = await ongletVinted();
  if (!onglet) {
    $('message').textContent = "Place-toi sur un onglet vinted.fr pour détecter le compte.";
    return;
  }
  try {
    await chrome.tabs.sendMessage(onglet.id, { type: 'redetecter' });
  } catch (e) {
    $('message').textContent = "Recharge l'onglet vinted.fr (extension installée après son ouverture).";
  }
  // le service worker a mis l'état à jour entre-temps
  setTimeout(async () => rendre((await chrome.storage.local.get('etat')).etat), 600);
}

async function action(type) {
  $('message').textContent = '…';
  const res = await chrome.runtime.sendMessage({ type });
  $('message').textContent = res.ok ? '' : `❌ ${res.erreur}`;
  rendre(res.etat || (await chrome.storage.local.get('etat')).etat);
}

// --- Découverte API (lecture seule) ---------------------------------------
// Affiche la STRUCTURE de la réponse : clés de premier niveau, puis le premier
// élément de chaque liste en entier (valeurs tronquées), données personnelles
// masquées -- assez pour concevoir l'import sans exposer de données inutiles.
const CLES_SENSIBLES = /(address|adresse|email|phone|telephone|iban|postal|zip|street|city|first_name|last_name|full_name|token|secret|password)/i;

function resumer(valeur, profondeur = 0) {
  if (valeur === null || typeof valeur !== 'object') {
    return typeof valeur === 'string' && valeur.length > 60 ? `${valeur.slice(0, 60)}…` : valeur;
  }
  if (Array.isArray(valeur)) {
    if (!valeur.length) return [];
    return [`(${valeur.length} élément(s), le 1er :)`, resumer(valeur[0], profondeur + 1)];
  }
  if (profondeur > 5) return '{…}';
  const sortie = {};
  for (const [cle, v] of Object.entries(valeur)) {
    sortie[cle] = CLES_SENSIBLES.test(cle) ? '«masqué»' : resumer(v, profondeur + 1);
  }
  return sortie;
}

async function explorer(chemin) {
  $('resultat-statut').textContent = '…';
  $('resultat').value = '';
  $('copier').hidden = true;
  const onglet = await ongletVinted();
  if (!onglet) {
    $('resultat-statut').textContent = 'Place-toi sur un onglet vinted.fr connecté.';
    return;
  }
  let res;
  try {
    res = await chrome.tabs.sendMessage(onglet.id, { type: 'explorer', chemin });
  } catch (e) {
    $('resultat-statut').textContent = "Recharge l'onglet vinted.fr puis réessaie.";
    return;
  }
  $('resultat-statut').textContent = res.erreur ? `❌ ${res.erreur}` : `HTTP ${res.status} — ${chemin}`;
  $('resultat-statut').className = res.status === 200 ? 'ok' : 'ko';
  if (res.json) {
    $('resultat').value = `GET ${chemin} → HTTP ${res.status}\n${JSON.stringify(resumer(res.json), null, 1)}`;
    $('copier').hidden = false;
  }
}

document.querySelectorAll('[data-chemin]').forEach((b) =>
  b.addEventListener('click', () => { $('chemin').value = b.dataset.chemin; explorer(b.dataset.chemin); }));
$('explorer').addEventListener('click', () => explorer($('chemin').value.trim()));
$('copier').addEventListener('click', async () => {
  await navigator.clipboard.writeText($('resultat').value);
  $('copier').textContent = 'Copié ✔';
  setTimeout(() => { $('copier').textContent = 'Copier le résultat'; }, 1500);
});

$('lier').addEventListener('click', () => action('lier'));
$('delier').addEventListener('click', () => action('delier'));
$('rafraichir').addEventListener('click', redetecter);

chrome.storage.local.get('etat').then(({ etat }) => rendre(etat));
redetecter();
