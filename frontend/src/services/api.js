const API_URL = "http://127.0.0.1:8000/api";

export const inventoryService = {
  async getArticles() {
    const response = await fetch(`${API_URL}/inventory`);
    if (!response.ok) throw new Error("Erreur réseau");
    return response.json();
  },

  // Centralisation du statut système (app_state)
  async getSystemState() {
    const response = await fetch(`${API_URL}/state`);
    if (!response.ok) throw new Error("Erreur réseau");
    return response.json();
  },

  // Déclenche la reconnexion manuelle d'un dressing ("d1" ou "d2")
  async reconnectSession(dressing) {
    const response = await fetch(`${API_URL}/reconnect-session/${dressing}`, { method: "POST" });
    if (!response.ok) throw new Error("Erreur réseau");
    return response.json();
  },

  async markMultipleAsTreated(ids) {
    // On remplace le Promise.all (qui spammait le serveur) par une seule requête globale
    const response = await fetch(`${API_URL}/treat-multiple`, {
      method: "PATCH",
      headers: { 
        "Content-Type": "application/json" 
      },
      body: JSON.stringify({ ids: ids }) // On envoie le tableau d'IDs
    });
    
    if (!response.ok) throw new Error("Erreur lors du traitement groupé");
    return response.json();
  },

  // À ajouter juste en dessous de markMultipleAsTreated :
  async markMultipleAsRepublished(ids) {
    const response = await fetch(`${API_URL}/republish-multiple`, {
      method: "PATCH",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ ids: ids })
    });
    
    if (!response.ok) throw new Error("Erreur lors de la republication groupée");
    return response.json();
  },

  async getScoreTrends(days = 14) {
    const response = await fetch(`${API_URL}/score-trends?days=${days}`);
    if (!response.ok) throw new Error("Erreur réseau");
    return response.json();
  },
}

export const priceEstimationService = {
  async search(query) {
    const response = await fetch(`${API_URL}/price-estimation/search`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ query }),
    });

    if (!response.ok) {
      const errData = await response.json().catch(() => ({}));
      throw new Error(errData.detail || "Erreur lors de l'estimation de prix");
    }
    return response.json();
  },
};

export const maintenanceService = {
  async getProfiles() {
    const response = await fetch(`${API_URL}/maintenance/profiles`);
    if (!response.ok) throw new Error("Erreur réseau");
    return response.json();
  },

  // Journal de routine (démarrage VM -> scraping -> baisse de prix ->
  // republication -> extinction VM), une journée à la fois.
  // apiUrl optionnel : permet de lire le journal d'un AUTRE backend (la VM,
  // sur le réseau local) au lieu du backend de ce PC.
  async getJournal(date, apiUrl = API_URL) {
    const params = date ? `?date=${date}` : '';
    const response = await fetch(`${apiUrl}/maintenance/journal${params}`, { signal: AbortSignal.timeout(10000) });
    if (!response.ok) throw new Error("Erreur réseau");
    return response.json();
  },

  async openBrowser(profileKey) {
    const response = await fetch(`${API_URL}/maintenance/open-browser`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ profile: profileKey }),
    });
    if (!response.ok) {
      const errData = await response.json().catch(() => ({}));
      throw new Error(errData.detail || "Erreur lors de l'ouverture du navigateur");
    }
    return response.json();
  },

  // Compare la version locale (Playwright) à celle de Chrome, sans rien copier
  async getExtensionInfo() {
    const response = await fetch(`${API_URL}/maintenance/extension`);
    if (!response.ok) throw new Error("Erreur réseau");
    return response.json();
  },

  async updateExtension() {
    const response = await fetch(`${API_URL}/maintenance/update-extension`, {
      method: "POST",
    });
    if (!response.ok) {
      const errData = await response.json().catch(() => ({}));
      throw new Error(errData.detail || "Erreur lors de la mise à jour de l'extension");
    }
    return response.json();
  },

  async getWatchdogStatus() {
    const response = await fetch(`${API_URL}/maintenance/watchdog`);
    if (!response.ok) throw new Error("Erreur réseau");
    return response.json();
  },

  async toggleWatchdog(active) {
    const response = await fetch(`${API_URL}/maintenance/watchdog/toggle`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ active }),
    });
    if (!response.ok) {
      const errData = await response.json().catch(() => ({}));
      throw new Error(errData.detail || "Erreur lors du changement d'état du watchdog");
    }
    return response.json();
  },

  async getClemzVisible() {
    const response = await fetch(`${API_URL}/maintenance/clemz-visible`);
    if (!response.ok) throw new Error("Erreur réseau");
    return response.json();
  },

  async toggleClemzVisible(visible) {
    const response = await fetch(`${API_URL}/maintenance/clemz-visible/toggle`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ visible }),
    });
    if (!response.ok) {
      const errData = await response.json().catch(() => ({}));
      throw new Error(errData.detail || "Erreur lors du changement d'état du navigateur visible");
    }
    return response.json();
  },

  async getBaissePrixAutoStatus() {
    const response = await fetch(`${API_URL}/maintenance/baisse-prix-auto`);
    if (!response.ok) throw new Error("Erreur réseau");
    return response.json();
  },

  async toggleBaissePrixAuto(active) {
    const response = await fetch(`${API_URL}/maintenance/baisse-prix-auto/toggle`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ active }),
    });
    if (!response.ok) {
      const errData = await response.json().catch(() => ({}));
      throw new Error(errData.detail || "Erreur lors du changement d'état de la baisse de prix automatique");
    }
    return response.json();
  },

  async getScrapingAutoStatus() {
    const response = await fetch(`${API_URL}/maintenance/scraping-auto`);
    if (!response.ok) throw new Error("Erreur réseau");
    return response.json();
  },

  async toggleScrapingAuto(active) {
    const response = await fetch(`${API_URL}/maintenance/scraping-auto/toggle`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ active }),
    });
    if (!response.ok) {
      const errData = await response.json().catch(() => ({}));
      throw new Error(errData.detail || "Erreur lors du changement d'état du scraping automatique");
    }
    return response.json();
  },

  async getRepublicationAutoStatus() {
    const response = await fetch(`${API_URL}/maintenance/republication-auto`);
    if (!response.ok) throw new Error("Erreur réseau");
    return response.json();
  },

  async toggleRepublicationAuto(active) {
    const response = await fetch(`${API_URL}/maintenance/republication-auto/toggle`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ active }),
    });
    if (!response.ok) {
      const errData = await response.json().catch(() => ({}));
      throw new Error(errData.detail || "Erreur lors du changement d'état de la republication automatique");
    }
    return response.json();
  },

  async getPartageVuesFavorisAutoStatus() {
    const response = await fetch(`${API_URL}/maintenance/partage-vues-favoris-auto`);
    if (!response.ok) throw new Error("Erreur réseau");
    return response.json();
  },

  async togglePartageVuesFavorisAuto(active) {
    const response = await fetch(`${API_URL}/maintenance/partage-vues-favoris-auto/toggle`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ active }),
    });
    if (!response.ok) {
      const errData = await response.json().catch(() => ({}));
      throw new Error(errData.detail || "Erreur lors du changement d'état du partage vues/favoris automatique");
    }
    return response.json();
  },

  // Vide le stockage local de l'extension Clemz (chrome.storage.local, IndexedDB)
  // pour le profil donné -- à utiliser après un crash qui laisse Clemz figé sur
  // un écran obsolète. Le profil doit être fermé.
  async resetExtensionState(profileKey) {
    const response = await fetch(`${API_URL}/maintenance/reset-extension-state`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ profile: profileKey }),
    });
    if (!response.ok) {
      const errData = await response.json().catch(() => ({}));
      throw new Error(errData.detail || "Erreur lors de la réinitialisation de l'extension Clemz");
    }
    return response.json();
  },
};

export const preparerAnnoncesService = {
  async getWatcherStatus() {
    const response = await fetch(`${API_URL}/preparer-annonces/watcher/status`);
    if (!response.ok) throw new Error("Erreur réseau");
    return response.json();
  },

  async startWatcher() {
    const response = await fetch(`${API_URL}/preparer-annonces/watcher/start`, { method: "POST" });
    if (!response.ok) throw new Error("Erreur lors du démarrage de l'écouteur");
    return response.json();
  },

  async stopWatcher() {
    const response = await fetch(`${API_URL}/preparer-annonces/watcher/stop`, { method: "POST" });
    if (!response.ok) throw new Error("Erreur lors de l'arrêt de l'écouteur");
    return response.json();
  },

  async getLots(limit = 50) {
    const response = await fetch(`${API_URL}/preparer-annonces/lots?limit=${limit}`);
    if (!response.ok) throw new Error("Erreur réseau");
    return response.json();
  },

  getPhotoUrl(path) {
    return `${API_URL}/preparer-annonces/photo?path=${encodeURIComponent(path)}`;
  },

  // Crée un lot directement depuis des fichiers glissés-déposés depuis
  // l'explorateur de fichiers (mode "Regrouper manuellement").
  async creerLotDepuisFichiers(fichiers, dressing = "Dressing 1") {
    const formData = new FormData();
    for (const fichier of fichiers) {
      formData.append("fichiers", fichier);
    }
    formData.append("dressing", dressing);
    const response = await fetch(`${API_URL}/preparer-annonces/lots/creer-depuis-fichiers`, {
      method: "POST",
      body: formData,
    });
    if (!response.ok) {
      const errData = await response.json().catch(() => ({}));
      throw new Error(errData.detail || "Erreur lors de la création du lot.");
    }
    return response.json();
  },

  // Change le dressing (Dressing 1/2) choisi pour un lot -- déterminera quel
  // profil Chrome sera utilisé à la création du brouillon Vinted.
  async changerDressingLot(lotId, dressing) {
    const response = await fetch(`${API_URL}/preparer-annonces/lots/${lotId}/dressing`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ dressing }),
    });
    if (!response.ok) {
      const errData = await response.json().catch(() => ({}));
      throw new Error(errData.detail || "Erreur lors du changement de dressing");
    }
    return response.json();
  },

  async splitLot(lotId, photosAExtraire) {
    const response = await fetch(`${API_URL}/preparer-annonces/lots/${lotId}/split`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ photos_a_extraire: photosAExtraire }),
    });
    if (!response.ok) {
      const errData = await response.json().catch(() => ({}));
      throw new Error(errData.detail || "Erreur lors de la division du lot");
    }
    return response.json();
  },

  async clearHistory() {
    const response = await fetch(`${API_URL}/preparer-annonces/lots`, { method: "DELETE" });
    if (!response.ok) {
      const errData = await response.json().catch(() => ({}));
      throw new Error(errData.detail || "Erreur lors du vidage de l'historique");
    }
    return response.json();
  },

  async supprimerLot(lotId) {
    const response = await fetch(`${API_URL}/preparer-annonces/lots/${lotId}`, { method: "DELETE" });
    if (!response.ok) {
      const errData = await response.json().catch(() => ({}));
      throw new Error(errData.detail || "Erreur lors de la suppression du lot");
    }
    return response.json();
  },
  
  async lancerCreationBrouillons() {
    const response = await fetch(`${API_URL}/preparer-annonces/lancer-brouillons`, { method: "POST" });
    if (!response.ok) {
      const errData = await response.json().catch(() => ({}));
      throw new Error(errData.detail || "Erreur lors du lancement");
    }
    return response.json();
  },

  // Crée le brouillon Vinted pour un seul lot 'generation_ok', sans attendre
  // les autres lots prêts.
  async creerBrouillonUnique(lotId) {
    const response = await fetch(`${API_URL}/preparer-annonces/lots/${lotId}/creer-brouillon`, { method: "POST" });
    if (!response.ok) {
      const errData = await response.json().catch(() => ({}));
      throw new Error(errData.detail || "Erreur lors du lancement de la création du brouillon");
    }
    return response.json();
  },

  async getStatutBrouillons() {
    const response = await fetch(`${API_URL}/preparer-annonces/statut-brouillons`);
    if (!response.ok) throw new Error("Erreur réseau");
    return response.json();
  },

  async validerLot(lotId) {
    const response = await fetch(`${API_URL}/preparer-annonces/lots/${lotId}/valider`, { method: "POST" });
    if (!response.ok) {
      const errData = await response.json().catch(() => ({}));
      throw new Error(errData.detail || "Erreur lors de la validation du lot");
    }
    return response.json();
  },

  // Remet un lot 'draft_error' en file (sans repasser par Gemini) et relance
  // le worker de création de brouillon Vinted.
  async reessayerCreationBrouillon(lotId) {
    const response = await fetch(`${API_URL}/preparer-annonces/lots/${lotId}/reessayer-creation`, { method: "POST" });
    if (!response.ok) {
      const errData = await response.json().catch(() => ({}));
      throw new Error(errData.detail || "Erreur lors de la relance de la création du brouillon");
    }
    return response.json();
  },

  // Valide en bloc tous les lots en 'pending_validation' -- mode "valider tout".
  async validerTousLesLots() {
    const response = await fetch(`${API_URL}/preparer-annonces/lots/valider-tout`, { method: "POST" });
    if (!response.ok) {
      const errData = await response.json().catch(() => ({}));
      throw new Error(errData.detail || "Erreur lors de la validation groupée");
    }
    return response.json();
  },

  // Déplace une ou plusieurs photos d'un lot vers un autre lot EXISTANT
  // (lotIdCible fourni) ou vers un nouveau lot (lotIdCible omis/null).
  async deplacerPhotos(lotId, photosADeplacer, lotIdCible) {
    const response = await fetch(`${API_URL}/preparer-annonces/lots/${lotId}/deplacer-photos`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ photos_a_deplacer: photosADeplacer, lot_id_cible: lotIdCible || null }),
    });
    if (!response.ok) {
      const errData = await response.json().catch(() => ({}));
      throw new Error(errData.detail || "Erreur lors du déplacement des photos");
    }
    return response.json();
  },

  // Relance la génération IA (titre/description/prix) d'un lot en tenant compte
  // des ajustements manuels (état/défaut, fabrication) cochés par l'utilisateur.
  async regenererLot(lotId, ajustements) {
    const response = await fetch(`${API_URL}/preparer-annonces/lots/${lotId}/regenerate`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ ajustements }),
    });
    if (!response.ok) {
      const errData = await response.json().catch(() => ({}));
      throw new Error(errData.detail || "Erreur lors de la régénération du lot");
    }
    return response.json();
  },
  async toggleExclusionSaisonniere(articleId, exclu) {
    const response = await fetch(`${API_URL}/exclusion-saisonniere/${articleId}`, {
        method: "PATCH",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ exclu }),
    });
    if (!response.ok) {
        const errData = await response.json().catch(() => ({}));
        throw new Error(errData.detail || "Erreur lors du basculement de l'exclusion saisonnière");
    }
    return response.json();
},

};

export const riskGuardService = {
  // Statut anti-détection par dressing (convalescence en cours ou non) --
  // purement informatif, cf. services/risk_guard.py.
  async getStatut() {
    const response = await fetch(`${API_URL}/risk-guard/statut`);
    if (!response.ok) throw new Error("Erreur réseau");
    return response.json();
  },
};