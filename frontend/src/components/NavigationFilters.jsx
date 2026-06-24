// components/NavigationFilters.jsx
import React, { useState } from 'react'; // 🚨 Ajout de useState
import { Plus, CheckCheck, XCircle, Search, Loader2 } from 'lucide-react'; // 🚨 Ajout de Loader2

export default function NavigationFilters({
    segments,
    activeSegment,
    setActiveSegment,
    searchTerm,
    setSearchTerm,
    inventory, // Requis pour le bulk treat
    filteredArticles,
    onStageSection,
    onBulkTreat,
    onBulkRepublish
}) {

    const [isRepublishing, setIsRepublishing] = useState(false);
    const [isTreating, setIsTreating] = useState(false); // 👈 NOUVEL ÉTAT
    return (
        <div className="flex gap-8 mt-10 bg-[#E7F4FB] border-3 border-white rounded-md p-2 items-center">
            {/* BOUTONS DE SEGMENTS (Structure d'origine conservée) */}
            {segments.map((seg) => (
                <button
                    key={seg.id}
                    onClick={() => { setActiveSegment(seg.id); setSearchTerm(''); }}
                    className={`relative px-3 py-3 rounded-md text-sm font-semibold transition-all ${activeSegment === seg.id && !searchTerm
                            ? 'bg-[#6ED8EF] shadow-[0_0_12px_rgba(112,225,245,0.8)] text-slate-800'
                            : 'text-slate-500 hover:text-slate-700'
                        }`}
                >
                    {seg.label}

                    {/* PASTILLE DE NOTIFICATION */}
                    {seg.count > 0 && seg.id !== 'Tous' && (
                        <span className="absolute -top-1 -left-1 flex h-2 w-2">
                            <span className="animate-ping absolute inline-flex h-full w-full rounded-full bg-red-400 opacity-75"></span>
                            <span className="relative inline-flex rounded-full h-2 w-2 bg-red-500 shadow-sm"></span>
                        </span>
                    )}
                </button>
            ))}

            {/* ZONE D'ACTIONS GLOBALES (Alignée à droite via ml-auto) */}
            {activeSegment !== 'Tous' && filteredArticles.length > 0 && (
                <div className="ml-auto flex items-center gap-3">

                    {/* 1. BOUTON PRÉPARER (Restaure le bouton pour le panier flottant) */}
                    <button
                        onClick={() => onStageSection(filteredArticles)}
                        className="flex items-center gap-1.5 text-[10px] font-black uppercase tracking-tighter bg-indigo-600 text-white px-3 py-2.5 rounded-lg hover:bg-indigo-700 transition-all shadow-md shadow-indigo-100"
                    >
                        <Plus size={14} />
                        Préparer ({filteredArticles.length})
                    </button>

                    {/* NOUVEAU BOUTON : TOUT MARQUER TRAITÉ (Style discret) */}
                    {/* 1. BOUTON CLASSIQUE : TOUT MARQUER TRAITÉ (Invisible sur l'onglet Republications) */}
                    {(activeSegment !== 'Republications' && activeSegment !== 'Republish') && (
                        <button
                            disabled={isTreating} // On désactive pendant le chargement
                            onClick={async () => {
                                const toTreat = filteredArticles.filter(a => !a.is_done);
                                if (toTreat.length > 0) {
                                    setIsTreating(true); // Démarre le spinner
                                    await onBulkTreat(toTreat); // Attend que l'API termine
                                    setIsTreating(false); // Arrête le spinner
                                }
                            }}
                            className={`flex items-center gap-1.5 text-[10px] font-black uppercase tracking-tighter bg-white px-3 py-2.5 rounded-lg transition-all ${
                                isTreating 
                                ? 'text-slate-400 cursor-not-allowed' 
                                : 'text-indigo-500 hover:text-indigo-700 hover:bg-indigo-50'
                            }`}
                        >
                            {isTreating ? (
                                <Loader2 size={14} className="animate-spin" />
                            ) : (
                                <CheckCheck size={14} />
                            )}
                            {isTreating ? "Traitement..." : `Tout marquer traité (${filteredArticles.length})`}
                        </button>
                    )}

                    {/* 2. NOUVEAU BOUTON : TOUT REPUBLIER (Visible UNIQUEMENT sur l'onglet Republications) */}
                    {(activeSegment === 'Republications' || activeSegment === 'Republish') && (
                        <button
                            disabled={isRepublishing} // Désactivé pendant le chargement
                            onClick={async () => {
                                setIsRepublishing(true);
                                // On attend la fin du processus avant d'enlever le spinner
                                await onBulkRepublish(filteredArticles);
                                setIsRepublishing(false);
                            }}
                            className={`flex items-center gap-1.5 text-[10px] font-black uppercase tracking-tighter bg-white border px-3 py-2.5 rounded-lg transition-all ${
                                isRepublishing 
                                ? 'text-slate-400 border-slate-200 cursor-not-allowed' 
                                : 'text-emerald-500 border-emerald-100 hover:text-emerald-700 hover:bg-emerald-50'
                            }`}
                        >
                            {isRepublishing ? (
                                <Loader2 size={14} className="animate-spin" />
                            ) : (
                                <CheckCheck size={14} />
                            )}
                            {isRepublishing ? "Mise à jour..." : `Tout marquer republié (${filteredArticles.length})`}
                        </button>
                    )}
                </div>
            )}
        </div>
    );
}