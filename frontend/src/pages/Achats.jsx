// pages/Achats.jsx
import React, { useState, useEffect } from 'react';
import {
    ShoppingBag, Plus, Trash2, Package, Wallet, Loader,
    RefreshCw, ChevronDown, ChevronUp, Calculator, ImageOff, Search, XCircle,
    Layers, List
} from 'lucide-react';

const API_URL = "http://localhost:8000/api";

function todayISO() {
    return new Date().toISOString().slice(0, 10);
}

function emptyArticle() {
    return { nom: '', prix_brut: '' };
}

// Autocomplétion du nom d'article à partir de l'inventaire réel (dressing) --
// évite le copier-coller manuel entre l'onglet dressing et le formulaire
// d'achat (cf. échange du 14/09/2026). `suggestions` : liste plate
// {id, nom, dressing, photo_url} chargée une fois par LotForm.
function ArticleNomAutocomplete({ value, onChange, suggestions, placeholder }) {
    const [open, setOpen] = useState(false);

    const term = value.trim().toLowerCase();
    const matches = term.length >= 2
        ? suggestions.filter(s => s.nom.toLowerCase().includes(term)).slice(0, 8)
        : [];

    return (
        <div className="relative flex-1">
            <input
                type="text"
                value={value}
                onChange={e => { onChange(e.target.value); setOpen(true); }}
                onFocus={() => setOpen(true)}
                onBlur={() => setTimeout(() => setOpen(false), 150)} // laisse le clic sur une suggestion se déclencher avant la fermeture
                placeholder={placeholder}
                className="w-full px-3 py-2 rounded-lg border border-slate-200 text-sm"
                autoComplete="off"
            />
            {open && matches.length > 0 && (
                <div className="absolute z-20 mt-1 w-full max-h-56 overflow-y-auto bg-white border border-slate-200 rounded-lg shadow-lg">
                    {matches.map(s => (
                        <button
                            key={s.id}
                            type="button"
                            onMouseDown={e => e.preventDefault()} // évite que le blur ferme le menu avant le clic
                            onClick={() => { onChange(s.nom); setOpen(false); }}
                            className="flex items-center gap-2 w-full text-left px-3 py-2 hover:bg-slate-50 text-sm"
                        >
                            {s.photo_url ? (
                                <img src={s.photo_url} alt="" className="w-6 h-6 rounded object-cover shrink-0 border border-slate-100" />
                            ) : (
                                <div className="w-6 h-6 rounded bg-slate-100 shrink-0" />
                            )}
                            <span className="truncate text-slate-700">{s.nom}</span>
                            <span className="ml-auto text-[9px] font-black uppercase text-slate-400 shrink-0">{s.dressing}</span>
                        </button>
                    ))}
                </div>
            )}
        </div>
    );
}

// --- Calcul de répartition en temps réel côté client, pour l'aperçu avant envoi ---
function calculerRepartition(articles, fraisProtection, fraisPort) {
    const totalBrut = articles.reduce((sum, a) => sum + (parseFloat(a.prix_brut) || 0), 0);
    const fraisCommuns = (parseFloat(fraisProtection) || 0) + (parseFloat(fraisPort) || 0);

    return articles.map(a => {
        const prixBrut = parseFloat(a.prix_brut) || 0;
        const part = totalBrut > 0 ? prixBrut / totalBrut : 0;
        const partFrais = Math.round(part * fraisCommuns * 100) / 100;
        const prixAchat = Math.round((prixBrut + partFrais) * 100) / 100;
        return { ...a, partFrais, prixAchat };
    });
}

function LotForm({ onCreated }) {
    const [mode, setMode] = useState('lot'); // 'lot' | 'solo'
    const [dateAchat, setDateAchat] = useState(todayISO());
    const [vendeur, setVendeur] = useState('');
    const [fraisProtection, setFraisProtection] = useState('');
    const [fraisPort, setFraisPort] = useState('');
    const [montantPorteMonnaie, setMontantPorteMonnaie] = useState('');
    const [notes, setNotes] = useState('');
    const [articles, setArticles] = useState([emptyArticle()]);
    const [isSaving, setIsSaving] = useState(false);
    const [error, setError] = useState(null);
    const [inventoryNoms, setInventoryNoms] = useState([]);

    // Chargé une fois pour alimenter l'autocomplétion du nom d'article --
    // les noms actifs du dressing, pas besoin de re-fetch à chaque frappe.
    useEffect(() => {
        fetch(`${API_URL}/inventory`)
            .then(r => r.json())
            .then(data => setInventoryNoms((data || []).map(a => ({
                id: a.id, nom: a.nom, dressing: a.dressing, photo_url: a.photo_url,
            }))))
            .catch(() => {});
    }, []);

    const switchMode = (newMode) => {
        setMode(newMode);
        // En mode solo, un seul article, pas de frais séparés (tout est dans le prix payé)
        if (newMode === 'solo') {
            setArticles([emptyArticle()]);
            setFraisProtection('');
            setFraisPort('');
            setVendeur('');
        }
    };

    const updateArticle = (index, field, value) => {
        setArticles(prev => prev.map((a, i) => i === index ? { ...a, [field]: value } : a));
    };

    const addArticle = () => setArticles(prev => [...prev, emptyArticle()]);
    const removeArticle = (index) => setArticles(prev => prev.filter((_, i) => i !== index));

    const repartition = calculerRepartition(articles, fraisProtection, fraisPort);
    const totalBrut = articles.reduce((sum, a) => sum + (parseFloat(a.prix_brut) || 0), 0);
    const totalFrais = (parseFloat(fraisProtection) || 0) + (parseFloat(fraisPort) || 0);
    const totalPaye = totalBrut + totalFrais;

    const isValid = articles.every(a => a.nom.trim() && parseFloat(a.prix_brut) > 0) && articles.length > 0;

    const handleSubmit = async () => {
        if (!isValid) return;
        setIsSaving(true);
        setError(null);
        try {
            const res = await fetch(`${API_URL}/achats/lot`, {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({
                    date_achat: dateAchat,
                    vendeur_vinted: vendeur || null,
                    frais_protection_acheteur: parseFloat(fraisProtection) || 0,
                    frais_port: parseFloat(fraisPort) || 0,
                    montant_porte_monnaie: parseFloat(montantPorteMonnaie) || 0,
                    notes: notes || null,
                    articles: articles.map(a => ({
                        nom: a.nom.trim(),
                        prix_brut: parseFloat(a.prix_brut),
                    })),
                }),
            });
            if (!res.ok) {
                const data = await res.json().catch(() => ({}));
                throw new Error(data.detail || 'Erreur serveur');
            }
            // Reset du formulaire
            setDateAchat(todayISO());
            setVendeur('');
            setFraisProtection('');
            setFraisPort('');
            setMontantPorteMonnaie('');
            setNotes('');
            setArticles([emptyArticle()]);
            onCreated();
        } catch (err) {
            setError(err.message);
        } finally {
            setIsSaving(false);
        }
    };

    return (
        <div className="rounded-2xl border border-slate-200 bg-white p-6 shadow-sm mb-8">
            <div className="flex items-center justify-between mb-5">
                <div className="flex items-center gap-2">
                    <ShoppingBag size={18} className="text-emerald-500" />
                    <h2 className="text-sm font-black uppercase tracking-wide text-slate-700">
                        {mode === 'solo' ? 'Nouvel achat seul' : 'Nouveau lot d\'achat'}
                    </h2>
                </div>

                {/* Toggle Solo / Lot */}
                <div className="flex items-center gap-1 bg-slate-100 rounded-lg p-1">
                    <button
                        onClick={() => switchMode('solo')}
                        className={`px-3 py-1.5 rounded-lg text-[10px] font-black uppercase transition-all ${mode === 'solo' ? 'bg-white text-slate-700 shadow-sm' : 'text-slate-400'
                            }`}
                    >
                        Achat seul
                    </button>
                    <button
                        onClick={() => switchMode('lot')}
                        className={`px-3 py-1.5 rounded-lg text-[10px] font-black uppercase transition-all ${mode === 'lot' ? 'bg-white text-slate-700 shadow-sm' : 'text-slate-400'
                            }`}
                    >
                        Lot négocié
                    </button>
                </div>
            </div>

            {mode === 'solo' ? (
                /* --- MODE SOLO : un seul article, prix déjà tout compris --- */
                <div className="grid grid-cols-2 gap-3 mb-5">
                    <div>
                        <label className="text-[10px] font-black uppercase text-slate-400">Date</label>
                        <input
                            type="date"
                            value={dateAchat}
                            onChange={e => setDateAchat(e.target.value)}
                            className="w-full mt-1 px-3 py-2 rounded-lg border border-slate-200 text-sm"
                        />
                    </div>
                    <div>
                        <label className="text-[10px] font-black uppercase text-slate-400">Notes (optionnel)</label>
                        <input
                            type="text"
                            value={notes}
                            onChange={e => setNotes(e.target.value)}
                            placeholder="Vendeur, contexte..."
                            className="w-full mt-1 px-3 py-2 rounded-lg border border-slate-200 text-sm"
                        />
                    </div>
                </div>
            ) : (
                /* --- MODE LOT : infos générales du lot --- */
                <>
                    <div className="grid grid-cols-4 gap-3 mb-5">
                        <div>
                            <label className="text-[10px] font-black uppercase text-slate-400">Date</label>
                            <input
                                type="date"
                                value={dateAchat}
                                onChange={e => setDateAchat(e.target.value)}
                                className="w-full mt-1 px-3 py-2 rounded-lg border border-slate-200 text-sm"
                            />
                        </div>
                        <div>
                            <label className="text-[10px] font-black uppercase text-slate-400">Vendeur (optionnel)</label>
                            <input
                                type="text"
                                value={vendeur}
                                onChange={e => setVendeur(e.target.value)}
                                placeholder="nikkyo_75"
                                className="w-full mt-1 px-3 py-2 rounded-lg border border-slate-200 text-sm"
                            />
                        </div>
                        <div>
                            <label className="text-[10px] font-black uppercase text-slate-400">Frais protection acheteur</label>
                            <input
                                type="number" step="0.01" min="0"
                                value={fraisProtection}
                                onChange={e => setFraisProtection(e.target.value)}
                                placeholder="3.65"
                                className="w-full mt-1 px-3 py-2 rounded-lg border border-slate-200 text-sm"
                            />
                        </div>
                        <div>
                            <label className="text-[10px] font-black uppercase text-slate-400">Frais de port</label>
                            <input
                                type="number" step="0.01" min="0"
                                value={fraisPort}
                                onChange={e => setFraisPort(e.target.value)}
                                placeholder="4.65"
                                className="w-full mt-1 px-3 py-2 rounded-lg border border-slate-200 text-sm"
                            />
                        </div>
                    </div>

                    <div className="grid grid-cols-2 gap-3 mb-5">
                        <div>
                            <label className="text-[10px] font-black uppercase text-slate-400">
                                Montant déduit du porte-monnaie Vinted (optionnel)
                            </label>
                            <input
                                type="number" step="0.01" min="0"
                                value={montantPorteMonnaie}
                                onChange={e => setMontantPorteMonnaie(e.target.value)}
                                placeholder="53.82"
                                className="w-full mt-1 px-3 py-2 rounded-lg border border-slate-200 text-sm"
                            />
                        </div>
                        <div>
                            <label className="text-[10px] font-black uppercase text-slate-400">Notes (optionnel)</label>
                            <input
                                type="text"
                                value={notes}
                                onChange={e => setNotes(e.target.value)}
                                placeholder="Négociation, contexte..."
                                className="w-full mt-1 px-3 py-2 rounded-lg border border-slate-200 text-sm"
                            />
                        </div>
                    </div>
                </>
            )}

            {/* Liste des articles */}
            <div className="mb-3">
                {mode === 'lot' && (
                    <div className="flex items-center justify-between mb-2">
                        <label className="text-[10px] font-black uppercase text-slate-400">
                            Articles du lot ({articles.length})
                        </label>
                        <button
                            onClick={addArticle}
                            className="flex items-center gap-1 text-[10px] font-black uppercase text-emerald-600 hover:text-emerald-700"
                        >
                            <Plus size={12} /> Ajouter un article
                        </button>
                    </div>
                )}

                <div className="flex flex-col gap-2">
                    {repartition.map((art, i) => (
                        <div key={i} className="flex items-center gap-2">
                            <ArticleNomAutocomplete
                                value={art.nom}
                                onChange={val => updateArticle(i, 'nom', val)}
                                suggestions={inventoryNoms}
                                placeholder={mode === 'solo' ? "Nom de l'article" : `Article ${i + 1} (ex: Lacoste S)`}
                            />
                            <input
                                type="number" step="0.01" min="0"
                                value={art.prix_brut}
                                onChange={e => updateArticle(i, 'prix_brut', e.target.value)}
                                placeholder={mode === 'solo' ? 'Prix payé (tout compris)' : 'Prix négocié'}
                                className="w-40 px-3 py-2 rounded-lg border border-slate-200 text-sm"
                            />
                            {mode === 'lot' && (
                                <div className="w-28 text-xs text-slate-500 font-medium text-right shrink-0">
                                    {art.prix_brut ? `= ${art.prixAchat.toFixed(2)} €` : '—'}
                                </div>
                            )}
                            {mode === 'lot' && articles.length > 1 && (
                                <button
                                    onClick={() => removeArticle(i)}
                                    className="p-1.5 text-slate-300 hover:text-red-500 transition-all"
                                >
                                    <Trash2 size={14} />
                                </button>
                            )}
                        </div>
                    ))}
                </div>
            </div>

            {/* Récapitulatif */}
            <div className="flex items-center gap-4 mt-4 mb-4 px-4 py-3 rounded-xl bg-slate-50 border border-slate-100 text-xs">
                <div className="flex items-center gap-1.5 text-slate-500">
                    <Calculator size={13} />
                    <span>Sous-total articles : <strong className="text-slate-700">{totalBrut.toFixed(2)} €</strong></span>
                </div>
                <div className="text-slate-500">
                    + Frais communs : <strong className="text-slate-700">{totalFrais.toFixed(2)} €</strong>
                </div>
                <div className="ml-auto text-slate-700 font-black">
                    Total du lot : {totalPaye.toFixed(2)} €
                </div>
            </div>

            {error && (
                <p className="text-xs text-red-500 font-medium mb-3">{error}</p>
            )}

            <button
                onClick={handleSubmit}
                disabled={!isValid || isSaving}
                className={`w-full flex items-center justify-center gap-2 px-6 py-2 rounded-lg text-sm font-black transition-all ${!isValid || isSaving
                        ? 'bg-slate-100 text-slate-400 cursor-not-allowed'
                        : 'bg-emerald-500 text-white hover:scale-[1.01] shadow-lg shadow-emerald-100'
                    }`}
            >
                {isSaving ? (
                    <><Loader size={14} className="animate-spin" /> Enregistrement...</>
                ) : (
                    <><ShoppingBag size={14} /> Créer le lot</>
                )}
            </button>
        </div>
    );
}

function LotCard({ lot }) {
    const [expanded, setExpanded] = useState(false);
    const totalArticles = lot.articles?.length ?? 0;
    const totalBrut = lot.articles?.reduce((sum, a) => sum + (a.prix_brut || 0), 0) ?? 0;

    return (
        <div className="rounded-2xl border border-slate-200 bg-white p-5 shadow-sm">
            <div className="flex items-center justify-between gap-4">
                <div className="flex items-center gap-3">
                    <Package size={16} className="text-slate-400" />
                    <div>
                        <p className="text-sm font-bold text-slate-700">
                            {lot.vendeur_vinted || `Lot #${lot.id}`}
                        </p>
                        <p className="text-[11px] text-slate-400">
                            {new Date(lot.date_achat).toLocaleDateString('fr-FR')} · {totalArticles} article{totalArticles > 1 ? 's' : ''}
                        </p>
                    </div>
                </div>

                <div className="flex items-center gap-4">
                    <div className="text-right">
                        <p className="text-[10px] text-slate-400 uppercase font-black">Total payé</p>
                        <p className="text-sm font-black text-slate-700">{Number(lot.total_paye).toFixed(2)} €</p>
                    </div>
                    {lot.montant_porte_monnaie > 0 && (
                        <div className="flex items-center gap-1 text-[10px] font-black uppercase px-2 py-1 rounded-md border bg-indigo-50 text-indigo-600 border-indigo-100">
                            <Wallet size={11} /> −{Number(lot.montant_porte_monnaie).toFixed(2)} €
                        </div>
                    )}
                    <button
                        onClick={() => setExpanded(e => !e)}
                        className="text-[10px] font-black uppercase text-slate-400 hover:text-slate-600 transition-all px-3 py-1.5 rounded-lg hover:bg-slate-50 border border-transparent hover:border-slate-200 flex items-center gap-1"
                    >
                        {expanded ? <>Masquer <ChevronUp size={12} /></> : <>Détail <ChevronDown size={12} /></>}
                    </button>
                </div>
            </div>

            {expanded && (
                <div className="mt-4 flex flex-col gap-1.5">
                    {lot.notes && (
                        <p className="text-xs text-slate-400 italic mb-2">"{lot.notes}"</p>
                    )}
                    <div className="grid grid-cols-[auto_1fr_auto_auto_auto] gap-2 px-3 py-1.5 text-[10px] font-black uppercase text-slate-400">
                        <span></span>
                        <span>Article</span>
                        <span className="text-right">Prix négocié</span>
                        <span className="text-right">Frais alloués</span>
                        <span className="text-right">Prix d'achat final</span>
                    </div>
                    {lot.articles?.map((art, i) => (
                        <div key={i} className="grid grid-cols-[auto_1fr_auto_auto_auto] gap-2 items-center px-3 py-2 rounded-lg bg-slate-50 border border-slate-100 text-xs">
                            {art.photo_url ? (
                                <img
                                    src={art.photo_url}
                                    alt={art.nom}
                                    className="w-9 h-9 rounded-md object-cover border border-slate-200"
                                />
                            ) : (
                                <div
                                    className="w-9 h-9 rounded-md bg-slate-100 border border-slate-200 flex items-center justify-center"
                                    title="Aucune correspondance trouvée dans l'inventaire (vérifie l'orthographe du nom)"
                                >
                                    <ImageOff size={14} className="text-slate-300" />
                                </div>
                            )}
                            <span className="font-medium text-slate-700">{art.nom}</span>
                            <span className="text-right text-slate-500">{Number(art.prix_brut).toFixed(2)} €</span>
                            <span className="text-right text-slate-500">+{Number(art.part_frais).toFixed(2)} €</span>
                            <span className="text-right font-bold text-slate-700">{Number(art.prix_achat).toFixed(2)} €</span>
                        </div>
                    ))}
                    <div className="grid grid-cols-[auto_1fr_auto_auto_auto] gap-2 px-3 py-1.5 text-xs mt-1">
                        <span />
                        <span className="font-black text-slate-700">Total</span>
                        <span className="text-right font-black text-slate-700">{totalBrut.toFixed(2)} €</span>
                        <span />
                        <span className="text-right font-black text-slate-700">{Number(lot.total_paye).toFixed(2)} €</span>
                    </div>
                </div>
            )}
        </div>
    );
}

function ArticleRow({ art }) {
    return (
        <div className="grid grid-cols-[auto_1fr_auto_auto_auto] gap-3 items-center px-4 py-3 rounded-2xl border border-slate-200 bg-white shadow-sm text-sm">
            {art.photo_url ? (
                <img
                    src={art.photo_url}
                    alt={art.nom}
                    className="w-11 h-11 rounded-lg object-cover border border-slate-200"
                />
            ) : (
                <div
                    className="w-11 h-11 rounded-lg bg-slate-100 border border-slate-200 flex items-center justify-center"
                    title="Aucune correspondance trouvée dans l'inventaire (vérifie l'orthographe du nom)"
                >
                    <ImageOff size={16} className="text-slate-300" />
                </div>
            )}
            <div className="min-w-0">
                <p className="font-medium text-slate-700 truncate">{art.nom}</p>
                <p className="text-[10px] text-slate-400 uppercase tracking-wide">
                    {new Date(art.date_achat).toLocaleDateString('fr-FR')}
                    {art.vendeur_vinted ? ` · ${art.vendeur_vinted}` : ''}
                </p>
            </div>
            <span className="text-right text-slate-500 text-xs">{Number(art.prix_brut).toFixed(2)} €</span>
            <span className="text-right text-slate-500 text-xs">+{Number(art.part_frais).toFixed(2)} €</span>
            <span className="text-right font-black text-slate-700">{Number(art.prix_achat).toFixed(2)} €</span>
        </div>
    );
}

export default function Achats() {
    const [lots, setLots] = useState([]);
    const [isLoading, setIsLoading] = useState(true);
    const [searchTerm, setSearchTerm] = useState('');
    const [viewMode, setViewMode] = useState('liste'); // 'lot' | 'liste'

    const fetchLots = async () => {
        setIsLoading(true);
        try {
            const res = await fetch(`${API_URL}/achats/lots`);
            const data = await res.json();
            setLots(data.lots || []);
        } catch (err) {
            console.error("Erreur chargement des lots d'achat :", err);
        } finally {
            setIsLoading(false);
        }
    };

    useEffect(() => { fetchLots(); }, []);

    // Un lot correspond à la recherche si un de ses articles OU le vendeur matche
    const filteredLots = lots.filter(lot => {
        if (!searchTerm.trim()) return true;
        const term = searchTerm.trim().toLowerCase();
        const vendeurMatch = lot.vendeur_vinted?.toLowerCase().includes(term);
        const articleMatch = lot.articles?.some(a => a.nom?.toLowerCase().includes(term));
        return vendeurMatch || articleMatch;
    });

    // Vue liste à plat : chaque article de chaque lot, avec les infos du lot
    // parent rattachées (date, vendeur) -- le calcul du prix d'achat reste celui
    // fait par lot côté backend, on ne fait qu'aplatir l'affichage ici.
    const flatArticles = lots
        .flatMap(lot => (lot.articles || []).map(art => ({
            ...art,
            date_achat: lot.date_achat,
            vendeur_vinted: lot.vendeur_vinted,
            lot_id: lot.id,
        })))
        .filter(art => {
            if (!searchTerm.trim()) return true;
            const term = searchTerm.trim().toLowerCase();
            return art.nom?.toLowerCase().includes(term) || art.vendeur_vinted?.toLowerCase().includes(term);
        })
        .sort((a, b) => new Date(b.date_achat) - new Date(a.date_achat));

    return (
        <div className="p-10 max-w-4xl mx-auto">
            {/* En-tête */}
            <div className="mb-8 flex items-center justify-between">
                <div>
                    <h1 className="text-2xl font-black tracking-tight">Achats</h1>
                    <p className="text-sm text-slate-400 mt-1">
                        Saisie des lots négociés avec répartition automatique des frais — alimente le prix d'achat
                        utilisé par le scoring et les automatisations de baisse de prix.
                    </p>
                </div>
                <button
                    onClick={fetchLots}
                    className="flex items-center gap-2 px-4 py-2 rounded-lg text-xs font-black uppercase bg-white text-slate-500 border border-slate-200 hover:border-slate-300 transition-all"
                >
                    <RefreshCw size={12} className={isLoading ? 'animate-spin' : ''} />
                    Actualiser
                </button>
            </div>

            <LotForm onCreated={fetchLots} />

            {/* Historique des lots */}
            <div className="flex items-center justify-between gap-4 mb-4">
                <div className="flex items-center gap-3">
                    <h2 className="text-sm font-black uppercase tracking-wide text-slate-700 whitespace-nowrap">
                        Historique
                    </h2>
                    <div className="flex items-center gap-1 bg-slate-100 rounded-lg p-1">
                        <button
                            onClick={() => setViewMode('liste')}
                            className={`flex items-center gap-1.5 px-3 py-1.5 rounded-lg text-[10px] font-black uppercase transition-all ${viewMode === 'liste' ? 'bg-white text-slate-700 shadow-sm' : 'text-slate-400'
                                }`}
                        >
                            <List size={12} /> Liste à plat
                        </button>
                        <button
                            onClick={() => setViewMode('lot')}
                            className={`flex items-center gap-1.5 px-3 py-1.5 rounded-lg text-[10px] font-black uppercase transition-all ${viewMode === 'lot' ? 'bg-white text-slate-700 shadow-sm' : 'text-slate-400'
                                }`}
                        >
                            <Layers size={12} /> Par lot
                        </button>
                    </div>
                </div>
                <div className="relative w-72">
                    <div className="absolute inset-y-0 left-0 pl-3 flex items-center pointer-events-none">
                        <Search size={16} className="text-slate-400" />
                    </div>
                    <input
                        type="text"
                        value={searchTerm}
                        onChange={e => setSearchTerm(e.target.value)}
                        placeholder="Rechercher un article ou un vendeur..."
                        className="block w-full pl-10 pr-10 py-2.5 border border-slate-100 bg-slate-50 rounded-xl text-sm font-medium focus:outline-none focus:ring-2 focus:ring-[#6ED8EF] transition-all"
                    />
                    {searchTerm && (
                        <button
                            onClick={() => setSearchTerm('')}
                            className="absolute inset-y-0 right-0 pr-3 flex items-center text-slate-400 hover:text-red-500"
                        >
                            <XCircle size={14} />
                        </button>
                    )}
                </div>
            </div>

            {isLoading ? (
                <div className="flex items-center justify-center py-20 text-slate-400">
                    <Loader size={24} className="animate-spin mr-3" />
                    Chargement...
                </div>
            ) : viewMode === 'lot' ? (
                filteredLots.length === 0 ? (
                    <div className="flex flex-col items-center justify-center py-20 text-slate-400">
                        <ShoppingBag size={40} className="mb-4 opacity-30" />
                        <p className="text-sm font-medium">
                            {searchTerm ? "Aucun lot ne correspond à cette recherche." : "Aucun lot enregistré pour le moment."}
                        </p>
                    </div>
                ) : (
                    <div className="flex flex-col gap-3">
                        {filteredLots.map(lot => (
                            <LotCard key={lot.id} lot={lot} />
                        ))}
                    </div>
                )
            ) : (
                flatArticles.length === 0 ? (
                    <div className="flex flex-col items-center justify-center py-20 text-slate-400">
                        <Package size={40} className="mb-4 opacity-30" />
                        <p className="text-sm font-medium">
                            {searchTerm ? "Aucun article ne correspond à cette recherche." : "Aucun article enregistré pour le moment."}
                        </p>
                    </div>
                ) : (
                    <div className="flex flex-col gap-2">
                        {flatArticles.map((art, i) => (
                            <ArticleRow key={`${art.lot_id}-${i}`} art={art} />
                        ))}
                    </div>
                )
            )}
        </div>
    );
}