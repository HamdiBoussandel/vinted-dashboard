import React, { useState } from 'react';
import { Tag, Loader2 } from 'lucide-react';
import toast from 'react-hot-toast';
import { priceEstimationService } from '../services/api';

export default function PriceEstimationPanel() {
    const [query, setQuery] = useState('');
    const [loading, setLoading] = useState(false);
    const [result, setResult] = useState(null);

    const handleSearch = async () => {
        if (!query.trim()) return;
        setLoading(true);
        setResult(null);
        try {
            const data = await priceEstimationService.search(query.trim());
            setResult(data);
        } catch (err) {
            toast.error(err.message || "Échec de l'estimation de prix");
        } finally {
            setLoading(false);
        }
    };

    const stats = result?.stats;
    const gemini = result?.gemini_estimate;

    return (
        <div className="bg-white rounded-3xl border border-slate-200 shadow-sm p-6 flex flex-col gap-5 h-fit">
            <div className="flex items-center gap-2">
                <Tag className="text-cyan-500" size={20} />
                <h2 className="text-sm font-black uppercase tracking-tight text-slate-800">
                    Estimation de prix
                </h2>
            </div>

            <div className="flex flex-col gap-2">
                <input
                    type="text"
                    value={query}
                    onChange={(e) => setQuery(e.target.value)}
                    onKeyDown={(e) => e.key === 'Enter' && handleSearch()}
                    placeholder="Ex: Jean Levi's 501 taille 38"
                    className="w-full px-3 py-2 text-sm border border-slate-200 rounded-xl focus:outline-none focus:ring-2 focus:ring-cyan-400"
                />
                <button
                    onClick={handleSearch}
                    disabled={loading || !query.trim()}
                    className="flex items-center justify-center gap-2 py-2 bg-slate-900 text-white rounded-lg font-black text-xs uppercase tracking-wide hover:bg-slate-800 disabled:opacity-50 transition-all"
                >
                    {loading ? <Loader2 size={14} className="animate-spin" /> : <Tag size={14} />}
                    {loading ? "Recherche en cours..." : "Estimer le prix"}
                </button>
            </div>

            {stats && (
                <div className="grid grid-cols-2 gap-2">
                    <div className="bg-slate-50 p-3 rounded-xl">
                        <p className="text-[9px] text-slate-400 uppercase font-bold">Médiane</p>
                        <p className="text-lg font-black text-slate-800">{stats.median} €</p>
                    </div>
                    <div className="bg-slate-50 p-3 rounded-xl">
                        <p className="text-[9px] text-slate-400 uppercase font-bold">Moyenne</p>
                        <p className="text-lg font-black text-slate-800">{stats.mean} €</p>
                    </div>
                    <div className="bg-slate-50 p-3 rounded-xl">
                        <p className="text-[9px] text-slate-400 uppercase font-bold">Min / Max</p>
                        <p className="text-sm font-bold text-slate-600">{stats.min}€ – {stats.max}€</p>
                    </div>
                    <div className="bg-slate-50 p-3 rounded-xl">
                        <p className="text-[9px] text-slate-400 uppercase font-bold">Annonces</p>
                        <p className="text-sm font-bold text-slate-600">
                            {stats.count_after_filter}/{stats.count_total}
                        </p>
                    </div>
                </div>
            )}

            {gemini && (
                <div className="bg-emerald-50 border border-emerald-100 rounded-xl p-4">
                    <p className="text-[9px] text-emerald-600 uppercase font-bold mb-1">
                        Prix conseillé (Gemini)
                    </p>
                    <p className="text-2xl font-black text-emerald-900 mb-2">
                        {gemini.prix_conseille ?? '—'} €
                    </p>
                    <p className="text-[11px] text-emerald-800 italic leading-relaxed">
                        {gemini.justification}
                    </p>
                </div>
            )}
        </div>
    );
}