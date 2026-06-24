// components/ProductCard.jsx
import React from 'react';
import CustomCheckbox from './CustomCheckbox';

export default function ProductCard({
    art,
    activeSegment,
    processingIds,
    onTreat,
    onRepublish,
    isSelected, onToggleSelection
}) {
    // Logique de style de température (extraite pour rester locale au composant)
    const getTemperatureStyle = (score) => {
        if (score >= 12) return { bar: "bg-red-600 animate-pulse", text: "text-red-600", label: "🔥 BRÛLANT" };
        if (score >= 8) return { bar: "bg-orange-500", text: "text-orange-600", label: "☀️ CHAUD" };
        if (score >= 5) return { bar: "bg-amber-400", text: "text-amber-600", label: "⛅ TIÈDE" };
        return { bar: "bg-slate-300", text: "text-slate-400", label: "❄️ FROID" };
    };

    // 2. LOGIQUE NOUVELLE : Style pour le Dressing
    const getDressingStyles = (dressingName) => {
        if (dressingName === "Dressing 1") {
            return "bg-blue-50 text-blue-600 border-blue-100";
        }
        if (dressingName === "Dressing 2") {
            return "bg-purple-50 text-purple-600 border-purple-100";
        }
        return "bg-slate-50 text-slate-400 border-slate-100";
    };

    const temp = getTemperatureStyle(art.score);

    return (
        <div className={`group relative grid grid-cols-[40px_100px_1fr_1fr_420px] items-center p-4 rounded-2xl transition-all duration-500 bg-white border border-slate-100 shadow-sm hover:shadow-md mb-2 ${isSelected ? 'border-emerald-500 ring-2 ring-emerald-500' : 'border-slate-200'}`}>
            <div className="flex items-center gap-2">
                <CustomCheckbox
                    id={`checkbox-${art.id}`}      // ID unique obligatoire pour l'accessibilité
                    name={`status-${art.id}`}
                    checked={isSelected}   // On initialise avec la valeur de ton article
                    onChange={onToggleSelection}
                />
            </div>

            {/* IMAGE */}
            <div className="relative w-22 h-30 shrink-0 rounded-xl overflow-hidden">
                <img src={art.photo_url || '...'} className="w-full h-full object-cover" alt={art.nom} />
            </div>

            {/* INFOS & STATS DÉTAILLÉES */}
            <div className="flex flex-col gap-1">
                <h3 className="font-bold text-sm line-clamp-2">{art.nom}</h3>
                <div className="flex gap-3 text-[10px] font-mono font-bold text-slate-500 bg-slate-50 p-2 rounded-lg border border-slate-100">
                    <span title="En vente depuis">⌛️ {art.jours_en_vente}j</span>
                    <span title="Depuis republication">📅 {art.jours_en_ligne}j</span>
                    <span title="Vues réelles">👁️ {art.v_reel}</span>
                    <span title="Favoris réels">❤️ {art.f_reel}</span>
                    <span title="Rang">🚩 {art.rang}</span>
                    {art.prix_vente <= art.prix_plancher && (
                        <span className="bg-orange-100 text-orange-700 border border-orange-200 px-2 py-1 rounded-md text-[9px] font-bold uppercase tracking-tighter">
                            ⚠️ PLANCHER ATTEINT ({art.prix_plancher}€)
                        </span>
                    )}
                    {/* --- NOUVEAU : Affichage du Label d'Action --- */}
                    {/* On n'affiche le label que s'il y a une action requise (on masque le "✅ OK" pour garder l'écran propre) */}
                    {art.action_label && art.action_label !== "✅ OK" && (
                        <div className="text-[10px] font-black tracking-wide text-slate-700 bg-slate-100 px-2 py-1 rounded-md w-fit border border-slate-200 shadow-sm my-0.5">
                            {art.action_label}
                        </div>
                    )}
                </div>

                {/* JAUGE D'ATTIRANCE */}
                <div className="w-full max-w-[200px] mt-1">
                    <div className="flex justify-between text-[9px] font-black text-slate-400 mb-1">
                        <span>ATTIRANCE</span>
                        <span className={temp.text}>{art.score}%</span>
                    </div>
                    <div className="h-1.5 w-full bg-slate-100 rounded-full overflow-hidden">
                        <div className={`h-full transition-all duration-1000 ${temp.bar}`}
                            style={{ width: `${Math.min(art.score * 5, 100)}%` }}></div>
                    </div>
                </div>
            </div>

            {/* BADGES DE STATUT */}
            <div className="flex items-center justify-center border border-slate-50 h-full px-4 gap-x-1">
                {art.is_new && (
                    <span className="flex items-center gap-x-1.5 rounded-md px-2 py-1 text-xs font-medium bg-blue-50 text-blue-600">
                        Nouveau
                    </span>
                )}

                {art.action_label?.includes("LIQUIDATION") && (
                    <span className={`px-2 py-1 rounded text-[10px] font-black uppercase ${art.action_label.includes("Poste OK")
                            ? 'bg-slate-200 text-slate-600' // Gris si déjà fait
                            : 'bg-red-600 text-white animate-pulse' // Rouge flash si à faire
                        }`}>
                        💀 Liquidation
                    </span>
                )}

                {/* 🟢 NOUVEAU TAG : URGENCE SORTIE (90j+) */}
                {art.is_very_old && (
                    <span className="bg-amber-950 text-amber-400 px-2 py-1 rounded-md text-[9px] font-black tracking-widest uppercase flex items-center gap-1 shadow-sm border border-amber-800">
                        🚨 URGENCE SORTIE
                    </span>
                )}

                {/* ✅ NOUVEAU : Tag Dressing */}
                {art.dressing && (
                    <span className={`text-[9px] font-black px-2 py-0.5 rounded-md border uppercase tracking-tighter ${getDressingStyles(art.dressing)}`}>
                        {art.dressing}
                    </span>
                )}

                <span className={`flex items-center gap-x-1.5 rounded-md px-2 py-1 text-xs font-medium 
                    ${art.is_critical ? 'bg-red-100/50 text-red-700' : 'bg-green-50 text-green-700'}`}>
                    {art.is_critical ? 'Critique' : 'Stable'}
                </span>

                {/* 2. NOUVEAU TAG RAISON : S'affiche uniquement si c'est un Shadow Ban */}
                {art.is_invisible && (
                    <span className="bg-slate-800 text-white px-2 py-1 rounded-md text-[9px] font-bold tracking-wider uppercase flex items-center gap-1 shadow-sm">
                        👻 Invisible
                    </span>
                )}
            </div>

            {/* PRIX ET ACTIONS */}
            <div className="flex items-center justify-end gap-6">
                <div className="text-right">
                    <p className="text-[9px] text-slate-400 uppercase tracking-wide">Achat</p>
                    <p className="text-sm font-black">{art.prix_achat > 0 ? `${art.prix_achat.toFixed(2)}€` : "—"}</p>
                </div>
                {(art.prix_achat > 0) && (
                    <>
                        <div className="text-right px-3 border-l border-slate-100">
                            <p className="text-[9px] text-orange-400 uppercase tracking-wide font-bold">Plancher</p>
                            <p className="text-sm font-black text-orange-600">
                                {(art.prix_plancher || 0).toFixed(2)}€
                            </p>
                        </div>
                        <div className="text-right px-3 border-l border-slate-100">
                            <p className="text-[9px] text-emerald-400 uppercase tracking-wide font-bold">Optimiste (x2.6)</p>
                            <p className="text-sm font-black text-emerald-600">
                                {(art.prix_optimiste || 0).toFixed(2)}€
                            </p>
                        </div>
                    </>
                )}

                {/* 3. PRIX DE VENTE ACTUEL (Vinted) */}
                <div className="text-right pl-3 border-l border-slate-100">
                    <p className="text-[9px] text-slate-400 uppercase tracking-wide">Vente</p>
                    <p className={`text-sm font-black ${art.action_label?.includes("LIQUIDATION") ? 'text-red-600 animate-bounce' : 'text-blue-600'}`}>
                        {(art.prix_vente || 0).toFixed(2)}€
                    </p>
                </div>

                <div className="flex items-center justify-end gap-3 pr-4">
                    {(art.is_stuck || art.is_statut_quo || art.is_low_perf) && (
                        <button
                            onClick={() => onTreat(art.id)}
                            disabled={art.is_done}
                            className="group flex items-center gap-2 px-4 py-3 rounded-xl font-bold text-xs bg-red-50 text-red-600 border border-red-100 hover:bg-red-600 hover:text-white transition-all"
                        >
                            <span>{art.is_stuck ? 'OFFRE -5%' : 'BAISSE -10%'}</span>
                        </button>
                    )}

                    {(art.is_invisible || art.needs_time_republish || art.is_critical) && (
                        <button
                            onClick={() => onRepublish(art.nom, art.id)}
                            disabled={processingIds.has(art.id)}
                            className="group flex items-center gap-2 px-4 py-3 rounded-xl font-bold text-xs bg-blue-50 text-blue-600 border border-blue-100 hover:bg-blue-600 hover:text-white transition-all"
                        >
                            {processingIds.has(art.id) ? 'Mise à jour...' : 'Republication OK'}
                        </button>
                    )}
                </div>
            </div>
        </div>
    );
}