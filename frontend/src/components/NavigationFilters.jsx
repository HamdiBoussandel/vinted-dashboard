// components/NavigationFilters.jsx
import React from 'react';

export default function NavigationFilters({
    segments,
    activeSegment,
    setActiveSegment,
    searchTerm,
    setSearchTerm,
}) {

    return (
        <div className="flex gap-2 mt-10 bg-white rounded-xl p-1.5 items-center shadow-soft w-fit">
            {/* SEGMENTS DE NAVIGATION */}
            {segments.map((seg) => {
                const estActif = activeSegment === seg.id && !searchTerm;
                return (
                    <button
                        key={seg.id}
                        onClick={() => { setActiveSegment(seg.id); setSearchTerm(''); }}
                        className={`flex items-center gap-2 px-4 py-2 rounded-lg text-sm font-bold border-2 transition-all ${
                            estActif
                                ? 'bg-brand-500 border-brand-400/40 text-white'
                                : 'bg-white border-transparent text-slate-500 hover:border-slate-200 hover:text-slate-700'
                        }`}
                    >
                        {seg.label}
                    </button>
                );
            })}
        </div>
    );
}