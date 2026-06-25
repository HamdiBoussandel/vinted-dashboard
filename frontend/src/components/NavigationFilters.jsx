// components/NavigationFilters.jsx
import React, { useState } from 'react'; // 🚨 Ajout de useState
import { CheckCheck, Loader2 } from 'lucide-react'; // 🚨 Ajout de Loader2

export default function NavigationFilters({
    segments,
    activeSegment,
    setActiveSegment,
    searchTerm,
    setSearchTerm,
    inventory, // Requis pour le bulk treat
    filteredArticles,
    onBulkTreat,
    isTreating,        // Remonté dans Dashboard pour cohérence
    setIsTreating
}) {

    return (
        <div className="flex gap-8 mt-10 bg-[#E7F4FB] border-3 border-white rounded-md p-2 items-center">
            {/* SEGMENTS DE NAVIGATION */}
            {segments.map((seg) => (
                <button
                    key={seg.id}
                    onClick={() => { setActiveSegment(seg.id); setSearchTerm(''); }}
                    className={`relative px-3 py-3 rounded-md text-sm font-semibold transition-all ${
                        activeSegment === seg.id && !searchTerm
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

            {/* BOUTON "TOUT MARQUER TRAITÉ" — visible sur tous les segments sauf Tous et Republications */}
            {activeSegment !== 'Tous' &&
             activeSegment !== 'Republications' &&
             activeSegment !== 'Republish' &&
             filteredArticles.length > 0 && (
                <div className="ml-auto flex items-center gap-3">
                </div>
            )}
        </div>
    );
}