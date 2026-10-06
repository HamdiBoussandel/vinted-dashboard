import React from 'react';
import { TrendingDown, Loader } from 'lucide-react';

const POURCENTAGES = [5, 8, 10, 15, 20, 25];

export default function ManualBaisseCart({
    selectedItems,
    pourcentages,
    onChangeItemPourcentage,
    isLaunching,
    onLaunch
}) {
    if (selectedItems.length === 0) return null;

    return (
        <div className="fixed bottom-8 right-8 z-50 flex flex-col items-end gap-3 max-w-sm">
            <div className="bg-white px-4 py-2 rounded-lg shadow-xl border border-slate-100 text-[10px] font-black uppercase text-slate-500">
                {selectedItems.length} article{selectedItems.length > 1 ? 's' : ''} sélectionné{selectedItems.length > 1 ? 's' : ''} (baisse manuelle)
            </div>

            <div className="flex flex-col gap-3 bg-white p-3 rounded-2xl shadow-2xl border border-slate-100 w-full">
                {/* Un taux par article -- pré-rempli avec la suggestion (tranche
                    pépite ou mauvaise perf) mais toujours modifiable avant de
                    lancer, cf. échange du 10/09/2026. */}
                <div className="flex flex-col gap-1.5 max-h-48 overflow-y-auto pr-1">
                    {selectedItems.map((item) => (
                        <div key={item.id} className="flex items-center gap-2">
                            <span className="flex-1 text-xs font-medium text-slate-600 truncate" title={item.nom}>
                                {item.nom}
                            </span>
                            <select
                                value={pourcentages[item.id] ?? 20}
                                onChange={(e) => onChangeItemPourcentage(item.id, Number(e.target.value))}
                                disabled={isLaunching}
                                className="px-2 py-1 rounded-lg border border-slate-200 text-xs font-bold text-slate-700 bg-slate-50 focus:outline-none focus:ring-2 focus:ring-rose-300 shrink-0"
                            >
                                {POURCENTAGES.map(p => (
                                    <option key={p} value={p}>-{p}%</option>
                                ))}
                            </select>
                        </div>
                    ))}
                </div>

                <button
                    onClick={onLaunch}
                    disabled={isLaunching}
                    className="flex items-center justify-center gap-2 px-6 py-2 bg-rose-500 text-white rounded-lg font-black uppercase text-xs hover:scale-105 transition-all shadow-lg shadow-rose-200 disabled:opacity-50"
                >
                    {isLaunching ? (
                        <Loader size={16} className="animate-spin" />
                    ) : (
                        <TrendingDown size={16} />
                    )}
                    {isLaunching ? "Lancement..." : `Baisser (${selectedItems.length})`}
                </button>
            </div>
        </div>
    );
}
