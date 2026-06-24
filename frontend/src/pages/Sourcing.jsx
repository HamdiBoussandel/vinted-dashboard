import React, { useState, useEffect, useRef } from 'react';
import axios from 'axios';
import ProductCardSniper  from '../components/ProductCardSniper';
import { Settings2, Activity, ShieldAlert, Power } from 'lucide-react';


const MOCK_PRODUCTS = [
  {
    id: "39847291",
    title: "Veste en laine A.P.C. Marine",
    brand_title: "A.P.C.",
    price: { amount: "85.0" },
    size_title: "L",
    url: "https://www.vinted.fr",
    photos: [{ url: "https://images1.vinted.net/t/05_022af_aZr5nA3dQ8rzjXG7PFx2hQyL/f800/1774607964.webp?s=de8ab160d9c17e4d73facd881f997324dd4ef973" }], // Remplace par une vraie URL d'image pour tester
    filter_name: "A.P.C",
    shipping_est: 3.75,
    service_fee: 4.95,
    total_invested: 93.70,
    relative_time: "À l'instant",
    is_new: true
  },
  {
    id: "40129384",
    title: "Chino Beige Octobre Éditions",
    brand_title: "Octobre Éditions",
    price: { amount: "45.0" },
    size_title: "42",
    url: "https://www.vinted.fr",
    photos: [{ url: "https://images1.vinted.net/t/06_01cb9_mZ4ND1sZ8XVhZMaL51CTGrRQ/f800/1774471460.webp?s=c32e1f21a77419fd1d8876074042e415416885d7" }],
    filter_name: "Octobre",
    shipping_est: 3.10,
    service_fee: 2.95,
    total_invested: 51.05,
    relative_time: "Il y a 4 min",
    is_new: true
  }
];


const Sourcing = () => {
  const [filters, setFilters] = useState([]);
  const [activeFilter, setActiveFilter] = useState(null);
  const [pendingProducts, setPendingProducts] = useState([]);
  //const [products, setProducts] = useState([]);
  const [products, setProducts] = useState(MOCK_PRODUCTS);
  const [workerStatus, setWorkerStatus] = useState(false);
  const [isToggling, setIsToggling] = useState(false);

  const [errors, setErrors] = useState([]);      // Pour corriger l'erreur de l'image
  const [isScrolling, setIsScrolling] = useState(false); // Pour le détecteur de scroll

  const API_URL = 'http://localhost:8000/api';

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
              setProducts(prev => [...freshArticles, ...prev].slice(0, 100));
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
    setProducts(prev => [...pendingProducts, ...prev].slice(0, 100));
    setPendingProducts([]);
    window.scrollTo({ top: 0, behavior: 'smooth' });
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

  const filteredProducts = activeFilter 
  ? products.filter(p => {
      // 1. On vérifie si le backend a déjà tagué le produit avec le nom du filtre
      if (p.filter_name === activeFilter.nom) return true;

      // 2. Sinon, on vérifie si la marque du produit est dans la liste des marques du filtre
      // On utilise .some() pour une comparaison insensible à la casse
      return activeFilter.marques?.some(marque => 
        p.brand_title?.toLowerCase().includes(marque.toLowerCase())
      );
    })
  : products;
  
  return (
    <div className="p-12 bg-slate-50 min-h-screen">
      {/* 4. BOUTON DE NOTIFICATION (STREAK) */}
      <div className="fixed top-24 left-1/2 -translate-x-1/2 z-50">
        {pendingProducts.length > 0 && (
          <button 
            onClick={releasePending}
            className="flex items-center gap-2 bg-slate-900 text-white px-6 py-2.5 rounded-full shadow-2xl animate-bounce border border-slate-700 transition-all hover:scale-105"
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
        {filters.map((f) => (
          <button
            key={f.id || f.nom}
            onClick={() => {
              setActiveFilter(f);
              setPendingProducts([]);
            }}
            className={`px-4 py-2 rounded text-xs font-semibold transition-all whitespace-nowrap 
              ${activeFilter?.nom === f.nom 
                  ? 'bg-slate-900 text-white shadow-md' 
                  : 'bg-white text-slate-500 hover:bg-slate-50 border border-slate-200'
              }`}
          >
            {f.nom}
          </button>
        ))}
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
    </div>
  );
};

export default Sourcing;