const API_URL = "http://127.0.0.1:8000/api";

export const inventoryService = {
  async getArticles() {
    const response = await fetch(`${API_URL}/inventory`);
    if (!response.ok) throw new Error("Erreur réseau");
    return response.json();
  },

  // Centralisation de la récupération des stats globales
  async getGlobalStats() {
    const response = await fetch(`${API_URL}/stats`);
    if (!response.ok) throw new Error("Erreur réseau");
    return response.json();
  },

  // Centralisation du statut système (app_state)
  async getSystemState() {
    const response = await fetch(`${API_URL}/state`);
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
  }
}


export const salesService = {
  // Récupérer les chiffres d'affaires (année/mois)
  async getSales() {
    const response = await fetch(`${API_URL}/goals`);
    if (!response.ok) throw new Error("Erreur réseau");
    return response.json();
  }
}