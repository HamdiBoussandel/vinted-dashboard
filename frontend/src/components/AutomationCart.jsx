// components/AutomationCart.jsx
import React from 'react';
import { Zap } from 'lucide-react';

export default function AutomationCart({ 
    selectedItems, 
    isAutomating, 
    onLaunch, 
    onClear 
}) {
    // Si aucun produit n'est sélectionné, on ne l'affiche pas
    if (selectedItems.length === 0) return null;

    return (
        <div className="fixed bottom-8 right-8 z-50 flex flex-col items-end gap-3">
            {/* Badge de rappel du nombre de produits */}
            <div className="bg-white px-4 py-2 rounded-lg shadow-xl border border-slate-100 text-[10px] font-black uppercase text-slate-500">
                {selectedItems.length} {selectedItems.length > 1 ? 'produits' : 'produit'} en attente
            </div>

            {/* Bouton de lancement principal */}
            <button
                onClick={onLaunch}
                disabled={isAutomating}
                className="flex items-center gap-3 px-8 py-4 bg-indigo-600 text-white rounded-2xl font-black uppercase text-xs hover:scale-105 transition-all shadow-2xl disabled:opacity-50 active:scale-95"
            >
                {isAutomating ? (
                    <div className="animate-spin h-4 w-4 border-2 border-white border-t-transparent rounded-full" />
                ) : (
                    <Zap size={18} fill="currentColor" />
                )}
                Lancer Pilotage Automatique ({selectedItems.length})
            </button>

            {/* Option pour annuler la sélection */}
            <button
                onClick={onClear}
                className="bg-white px-4 py-2 rounded-lg shadow-xl border border-slate-100 text-[10px] hover:text-slate-500 font-black uppercase text-red-500"
            >
                Vider la sélection
            </button>
        </div>
    );
}