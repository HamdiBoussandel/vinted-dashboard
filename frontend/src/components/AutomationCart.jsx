// components/AutomationCart.jsx
import React from 'react';
import { TrendingDown } from 'lucide-react';

export default function AutomationCart({
    selectedItems,
    isAutomating,
    onLaunch
}) {
    if (selectedItems.length === 0) return null;

    return (
        <div className="fixed bottom-8 right-8 z-50 flex flex-col items-end gap-3">
            {/* Badge */}
            <div className="bg-white px-4 py-2 rounded-lg shadow-xl border border-slate-100 text-[10px] font-black uppercase text-slate-500">
                {selectedItems.length} {selectedItems.length > 1 ? 'articles' : 'article'} à baisser
            </div>

            {/* Bouton principal */}
            <button
                onClick={onLaunch}
                disabled={isAutomating}
                className="flex items-center gap-3 px-8 py-3 bg-orange-500 text-white rounded-lg font-black uppercase text-xs hover:scale-105 transition-all shadow-2xl shadow-orange-200 disabled:opacity-50 active:scale-95"
            >
                {isAutomating ? (
                    <div className="animate-spin h-4 w-4 border-2 border-white border-t-transparent rounded-full" />
                ) : (
                    <TrendingDown size={18} />
                )}
                {isAutomating
                    ? `Automation en cours...`
                    : `Lancer baisse de prix (${selectedItems.length})`
                }
            </button>
        </div>
    );
}