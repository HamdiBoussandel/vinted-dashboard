import React, { useState, useEffect, useRef } from 'react';
import axios from 'axios';
import ProductCardSniper from '../components/ProductCardSniper';
import { ArrowUp, Activity } from 'lucide-react';
import FilterForm from '../components/FilterForm';
import { Plus } from 'lucide-react';

const flattenCategories = (node, acc = {}) => {
  Object.entries(node || {}).forEach(([name, val]) => {
    if (val && typeof val === 'object' && 'id' in val) {
      acc[String(val.id)] = name;
      if (val.children) flattenCategories(val.children, acc);
    }
  });
  return acc;
}

const normalize = (text) =>
  (text || '')
    .normalize('NFD')
    .replace(/[\u0300-\u036f]/g, '')
    .trim()
    .toLowerCase();

const Sourcing = () => {
  const [filters, setFilters] = useState([]);
  const [activeFilter, setActiveFilter] = useState(null);
  const [pendingProducts, setPendingProducts] = useState([]);
  const [products, setProducts] = useState([]);
  const [workerStatus, setWorkerStatus] = useState(false);
  const [isToggling, setIsToggling] = useState(false);

  const [showFilterForm, setShowFilterForm] = useState(false);
  const [selectedBrands, setSelectedBrands] = useState([]);

  const [errors, setErrors] = useState([]);      // Pour corriger l'erreur de l'image
  const [isScrolling, setIsScrolling] = useState(false); // Pour le détecteur de scroll

  const API_URL = 'http://localhost:8000/api';

  const [marqueIdToName, setMarqueIdToName] = useState({});
  const [categorieIdToName, setCategorieIdToName] = useState({});
  const [couleurIdToName, setCouleurIdToName] = useState({});

  const [searchTerm, setSearchTerm] = useState('');
  const [selectedTailles, setSelectedTailles] = useState([]);
  const [selectedCategories, setSelectedCategories] = useState([]);
  const [selectedCouleurs, setSelectedCouleurs] = useState([]);

  useEffect(() => {
    const loadReferentiel = async () => {
      try {
        const res = await axios.get(`${API_URL}/sourcing/referentiel`);

        const invertedMarques = {};
        Object.entries(res.data?.marques || {}).forEach(([nom, id]) => {
          const idVal = typeof id === 'object' ? id.id : id;
          invertedMarques[String(idVal)] = nom;
        });
        setMarqueIdToName(invertedMarques);

        setCategorieIdToName(flattenCategories(res.data?.categories || {}));

        const invertedCouleurs = {};
        Object.entries(res.data?.couleurs || {}).forEach(([nom, val]) => {
          const idVal = typeof val === 'object' ? val.id : val;
          invertedCouleurs[String(idVal)] = nom;
        });
        setCouleurIdToName(invertedCouleurs);
      } catch (err) {
        console.error("Erreur chargement référentiel", err);
      }
    };
    loadReferentiel();
  }, []);

  // 2. DÉTECTION DU SCROLL
  useEffect(() => {
    const handleScroll = () => {
      setIsScrolling(window.scrollY > 150);
    };
    window.addEventListener('scroll', handleScroll);
    return () => window.removeEventListener('scroll', handleScroll);
  }, []);

  // À ajouter dans un useEffect au montage du composant
  /*useEffect(() => {
    const fetchInitialState = async () => {
      try {
        const response = await axios.get(`${API_URL}/sniper-status`);
        setWorkerStatus(response.data.sniper_active);
      } catch (error) {
        console.error("Erreur synchro état:", error);
      }
    };
    fetchInitialState();
  }, []);*/

  useEffect(() => {
    const init = async () => {
      try {
        const res = await axios.get(`${API_URL}/sourcing/filters`);
        setFilters(res.data);
        if (res.data.length > 0) setActiveFilter(res.data[0]);
      } catch (err) {
        console.error("Erreur initialisation", err);
      }
    };
    init();
  }, []);

  useEffect(() => {
    const updateLoop = setInterval(async () => {
      if (isToggling) return;
      try {
        // 1. Récupération de l'état (On/Off et Erreurs)
        const stateRes = await axios.get(`${API_URL}/sniper-status`);
        setWorkerStatus(stateRes.data.sniper_active);         // Synchronise le bouton
        setErrors(stateRes.data.errors || []);

        // 2. Récupération des produits
        const feedRes = await axios.get(`${API_URL}/feed-data`);
        const incomingData = feedRes.data || [];

        if (incomingData.length > 0) {
          // On filtre pour ne pas avoir de doublons
          const existingIds = new Set(products.map(p => p.id));
          const pendingIds = new Set(pendingProducts.map(p => p.id));
          const freshArticles = incomingData.filter(p => !existingIds.has(p.id) && !pendingIds.has(p.id));

          if (freshArticles.length > 0) {
            // SI ON EST EN HAUT : Injection directe (Plus fluide)
            if (!isScrolling) {
              setProducts(prev => [...freshArticles, ...prev].slice(0, 500));
            }
            // SI ON LIT EN BAS : On met en attente
            else {
              setPendingProducts(prev => [...freshArticles, ...prev]);
            }
          }
        }

      } catch (err) {
        console.error("Erreur de synchronisation", err);
      }
    }, 3000);
    return () => clearInterval(updateLoop);
  }, [isToggling, products, pendingProducts]);

  // Fonction pour libérer le buffer
  const releasePending = () => {
    setProducts(prev => [...pendingProducts, ...prev].slice(0, 500));
    setPendingProducts([]);
    window.scrollTo({ top: 0, behavior: 'smooth' });
  };

  const refreshFilters = async () => {
    try {
      const res = await axios.get(`${API_URL}/sourcing/filters`);
      setFilters(res.data);
    } catch (err) {
      console.error("Erreur rafraîchissement filtres", err);
    }
  };

  const toggleAgent = async () => {
    if (isToggling) return;
    setIsToggling(true); // 🔒 On verrouille la synchro auto
    // On change l'état local immédiatement pour un feedback visuel instantané
    const targetState = !workerStatus;
    setWorkerStatus(targetState);

    try {
      await axios.post(`${API_URL}/toggle-sniper`);
      setTimeout(() => setIsToggling(false), 1000);
    } catch (error) {
      // En cas d'erreur, on revient à l'état précédent
      setWorkerStatus(!targetState);
      setIsToggling(false);
      console.error("Erreur toggle", error);
    }
  };


  const handleBlacklist = async (productId) => {
    try {
      await axios.post(`${API_URL}/blacklist`, { id: productId });
      setProducts(prev => prev.filter(p => p.id !== productId));
    } catch (err) {
      console.error("Erreur blacklist", err);
    }
  };

  const StatusPill = ({ time, status }) => (
    <div className="flex flex-col items-center p-3 bg-white border rounded-xl min-w-[100px]">
      <span className="text-[10px] font-black text-slate-400 uppercase mb-1">{time}</span>
      <div className={`h-2 w-2 rounded-full mb-1 ${status === 'Succès' ? 'bg-emerald-500' : status === 'Échec' ? 'bg-red-500' : 'bg-slate-200'}`} />
      <span className={`text-[11px] font-bold ${status === 'Échec' ? 'text-red-600' : 'text-slate-700'}`}>{status}</span>
    </div>
  );

  {/*const filteredProducts = activeFilter 
  ? products.filter(p => p.filter_name === activeFilter.nom)
  : products;*/}

  const toggleBrand = (marque) => {
    setSelectedBrands(prev =>
      prev.includes(marque)
        ? prev.filter(m => m !== marque)
        : [...prev, marque]
    );
  };

  const toggleTaille = (taille) => {
    setSelectedTailles(prev =>
      prev.includes(taille)
        ? prev.filter(t => t !== taille)
        : [...prev, taille]
    );
  };

  const toggleCategorie = (categorie) => {
    setSelectedCategories(prev =>
      prev.includes(categorie)
        ? prev.filter(c => c !== categorie)
        : [...prev, categorie]
    );
  };

  const toggleCouleur = (couleur) => {
    setSelectedCouleurs(prev =>
      prev.includes(couleur)
        ? prev.filter(c => c !== couleur)
        : [...prev, couleur]
    );
  };

  const activeFilterBrandNames = (activeFilter?.marque_ids || [])
    .map(id => marqueIdToName[String(id)])
    .filter(Boolean);

  const activeFilterCategorieNames = (activeFilter?.categorie_ids || [])
    .map(id => categorieIdToName[String(id)])
    .filter(Boolean);

  const activeFilterCouleurNames = (activeFilter?.couleur_ids || [])
    .map(id => couleurIdToName[String(id)])
    .filter(Boolean);

  // Tailles déduites directement des articles déjà chargés pour ce filtre
  // (plus fiable que de résoudre l'arbre imbriqué du référentiel des tailles).
  const availableTailles = [...new Set(
    products
      .filter(p => !activeFilter || p.filter_name === activeFilter.nom)
      .map(p => p.size_title)
      .filter(Boolean)
  )];

  const filteredProducts = (activeFilter
    ? products.filter(p => {
      if (p.filter_name === activeFilter.nom) return true;
      return activeFilter.marques?.some(marque =>
        normalize(p.brand_title).includes(normalize(marque))
      );
    })
    : products
  )
    .filter(p =>
      selectedBrands.length === 0 ||
      selectedBrands.some(marque => normalize(p.brand_title).includes(normalize(marque)))
    )
    .filter(p =>
      selectedTailles.length === 0 ||
      selectedTailles.some(taille => normalize(p.size_title) === normalize(taille))
    )
    .filter(p =>
      // Approximatif : Vinted ne renvoie pas la catégorie par article,
      // on cherche le nom de la catégorie dans le titre de l'annonce.
      selectedCategories.length === 0 ||
      selectedCategories.some(cat => normalize(p.title).includes(normalize(cat)))
    )
    .filter(p =>
      // Approximatif, même limitation que pour les catégories.
      selectedCouleurs.length === 0 ||
      selectedCouleurs.some(couleur => normalize(p.title).includes(normalize(couleur)))
    )
    .filter(p =>
      searchTerm.trim() === '' ||
      normalize(p.title).includes(normalize(searchTerm))
    );

  return (
    <div className="p-12 bg-slate-50 min-h-screen">
      {/* 4. BOUTON DE NOTIFICATION (STREAK) */}
      <div className="fixed top-24 left-1/2 -translate-x-1/2 z-50">
        {pendingProducts.length > 0 && (
          <button
            onClick={releasePending}
            className="flex items-center gap-2 bg-slate-900 text-white px-6 py-2 rounded-lg shadow-2xl animate-bounce border border-slate-700 transition-all hover:scale-105"
          >
            <ArrowUp size={16} className="text-cyan-400" />
            <span className="font-black text-xs uppercase">
              {pendingProducts.length} nouveaux articles
            </span>
          </button>
        )}
      </div>

      <div className="flex flex-wrap items-center gap-6 bg-white p-5 rounded-2xl border border-slate-200 shadow-sm mb-8">
        <div className="flex items-center gap-4">
          <div className="relative flex h-4 w-4">
            {/* Voyant avec pulsation si actif */}
            {workerStatus && (
              <span className="animate-ping absolute inline-flex h-full w-full rounded-full bg-emerald-400 opacity-75"></span>
            )}
            <span className={`relative inline-flex rounded-full h-4 w-4 ${workerStatus ? 'bg-emerald-500' : 'bg-slate-300'}`}></span>
          </div>
          <div className="flex flex-col">
            <h1 className="text-sm font-black text-slate-800 uppercase tracking-tight">Radar Vinted</h1>
            <span className="text-[10px] font-bold text-slate-400 uppercase">
              {workerStatus ? "Scan en cours..." : "En veille"}
            </span>
          </div>
        </div>

        {/* Interrupteur Toggle Switch */}
        <div className="flex items-center gap-3 ml-auto px-4 py-2 bg-slate-50 rounded-xl border border-slate-100">
          <span className="text-[10px] font-black text-slate-500 uppercase">Mode Sniper</span>
          <label className="inline-flex items-center cursor-pointer">
            <input type="checkbox" className="sr-only peer" checked={workerStatus || false} onChange={toggleAgent} />
            <div className="relative w-11 h-6 bg-slate-300 peer-focus:outline-none rounded-full peer peer-checked:after:translate-x-full peer-checked:after:border-white after:content-[''] after:absolute after:top-[2px] after:start-[2px] after:bg-white after:border-gray-300 after:border after:rounded-full after:h-5 after:w-5 after:transition-all peer-checked:bg-cyan-600"></div>
          </label>
        </div>
      </div>
      <div className="flex gap-2 mb-8 overflow-x-auto pb-2">
        <button
          onClick={() => setShowFilterForm(true)}
          className="px-4 py-2 rounded-lg text-xs font-semibold bg-cyan-50 text-cyan-700 border border-cyan-200 hover:bg-cyan-100 transition-all flex items-center gap-1 whitespace-nowrap"
        >
          <Plus size={14} /> Nouveau filtre
        </button>
        {filters.map((f) => (
          <button
            key={f.id || f.nom}
            onClick={() => {
              setActiveFilter(f);
              setPendingProducts([]);
              setSelectedBrands([]);
            }}
            className={`px-4 py-2 rounded-lg text-xs font-semibold transition-all whitespace-nowrap
              ${activeFilter?.nom === f.nom
                ? 'bg-slate-900 text-white shadow-md'
                : 'bg-white text-slate-500 hover:bg-slate-50 border border-slate-200'
              }`}
          >
            {f.nom}
          </button>
        ))}
      </div>

      {/* BARRE DE RECHERCHE — automatique, sans bouton OK */}
      <div className="mb-6">
        <input
          type="text"
          value={searchTerm}
          onChange={(e) => setSearchTerm(e.target.value)}
          placeholder="Rechercher un article (titre)..."
          className="w-full px-4 py-2.5 rounded-xl border border-slate-200 text-sm text-slate-700 placeholder:text-slate-400 focus:outline-none focus:ring-2 focus:ring-cyan-500 focus:border-transparent"
        />
      </div>

      {activeFilterBrandNames.length > 0 && (
        <div className="flex gap-2 mb-4 overflow-x-auto pb-2">
          {activeFilterBrandNames.map((marque) => (
            <button
              key={marque}
              onClick={() => toggleBrand(marque)}
              className={`px-3 py-1.5 rounded-lg text-[11px] font-bold transition-all whitespace-nowrap border
          ${selectedBrands.includes(marque)
                  ? 'bg-cyan-600 text-white border-cyan-600 shadow-sm'
                  : 'bg-white text-slate-500 border-slate-200 hover:bg-slate-50'
                }`}
            >
              {marque}
            </button>
          ))}
        </div>
      )}

      {availableTailles.length > 0 && (
        <div className="flex gap-2 mb-4 overflow-x-auto pb-2">
          {availableTailles.map((taille) => (
            <button
              key={taille}
              onClick={() => toggleTaille(taille)}
              className={`px-3 py-1.5 rounded-lg text-[11px] font-bold transition-all whitespace-nowrap border
          ${selectedTailles.includes(taille)
                  ? 'bg-purple-600 text-white border-purple-600 shadow-sm'
                  : 'bg-white text-slate-500 border-slate-200 hover:bg-slate-50'
                }`}
            >
              {taille}
            </button>
          ))}
        </div>
      )}

      {activeFilterCategorieNames.length > 0 && (
        <div className="flex gap-2 mb-4 overflow-x-auto pb-2">
          {activeFilterCategorieNames.map((cat) => (
            <button
              key={cat}
              onClick={() => toggleCategorie(cat)}
              className={`px-3 py-1.5 rounded-lg text-[11px] font-bold transition-all whitespace-nowrap border
          ${selectedCategories.includes(cat)
                  ? 'bg-amber-600 text-white border-amber-600 shadow-sm'
                  : 'bg-white text-slate-500 border-slate-200 hover:bg-slate-50'
                }`}
            >
              {cat}
            </button>
          ))}
        </div>
      )}

      {activeFilterCouleurNames.length > 0 && (
        <div className="flex gap-2 mb-8 overflow-x-auto pb-2">
          {activeFilterCouleurNames.map((couleur) => (
            <button
              key={couleur}
              onClick={() => toggleCouleur(couleur)}
              className={`px-3 py-1.5 rounded-lg text-[11px] font-bold transition-all whitespace-nowrap border
          ${selectedCouleurs.includes(couleur)
                  ? 'bg-rose-600 text-white border-rose-600 shadow-sm'
                  : 'bg-white text-slate-500 border-slate-200 hover:bg-slate-50'
                }`}
            >
              {couleur}
            </button>
          ))}
        </div>
      )}

      {/* ENCART FRAIS DE PORT */}
      <div className="mb-6 p-4 bg-white rounded-2xl border border-slate-100 shadow-sm">
        <p className="text-[10px] font-black text-slate-400 uppercase tracking-wide mb-3">
          Frais de port estimés (acheteur)
        </p>
        <div className="grid grid-cols-3 gap-2">
          {[
            { label: "VintedGo Relais", prix: "3.49€" },
            { label: "Mondial Relay", prix: "3.99€" },
            { label: "VintedGo Domicile", prix: "4.49€" },
            { label: "So Colissimo", prix: "4.49€" },
            { label: "Colissimo", prix: "4.99€" },
            { label: "Chronopost", prix: "5.99€" },
          ].map((mode) => (
            <div key={mode.label} className="flex justify-between items-center px-3 py-2 bg-slate-50 rounded-xl">
              <span className="text-[10px] text-slate-500 font-medium">{mode.label}</span>
              <span className="text-[11px] font-black text-slate-700">{mode.prix}</span>
            </div>
          ))}
        </div>
        <p className="text-[9px] text-slate-300 mt-2 italic">
          Tarifs indicatifs — peuvent varier selon le poids et les promotions Vinted
        </p>
      </div>
      {/* COLONNE GAUCHE : Affichage des Cartes (2 tiers) */}
      <div className="xl:col-span-2">
        <div className="flex items-center justify-between mb-6">
          <div className="flex items-center gap-2">
            <Activity size={18} className="text-cyan-600" />
            <h2 className="font-bold text-slate-800 uppercase text-sm tracking-wider">
              Résultats : {activeFilter?.nom}
            </h2>
          </div>
          <span className="text-[10px] font-black text-slate-400 bg-slate-100 px-3 py-1 rounded-full uppercase">
            {filteredProducts.length} Articles trouvés
          </span>
        </div>

        {/* GRILLE DE 2 COLONNES */}
        <div className="grid grid-cols-1 lg:grid-cols-2 gap-6">
          {filteredProducts.length > 0 ? (
            filteredProducts.map((product) => (
              <ProductCardSniper
                key={product.id}
                product={product}
                activeFilter={activeFilter}
                onDelete={handleBlacklist}
              />
            ))
          ) : (
            <div className="col-span-full py-20 text-center border-2 border-dashed border-slate-200 rounded-3xl">
              <p className="text-slate-400 italic font-medium">En attente de nouvelles pépites...</p>
            </div>
          )}
        </div>
      </div>
      {showFilterForm && (
        <FilterForm
          onClose={() => setShowFilterForm(false)}
          onSaved={refreshFilters}
        />
      )}
    </div>
  );
};

export default Sourcing;