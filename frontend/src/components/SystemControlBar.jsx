// components/SystemControlBar.jsx
import React from 'react';
import { AlertCircle, CheckCircle2, PlayCircle, Search, XCircle,Trash2 } from 'lucide-react';

export default function SystemControlBar({ 
    sysState, 
    isScraping, 
    handleRunScraper, 
    searchTerm, 
    setSearchTerm,
    onClearDatabase
}) {
    return (
        <div className="flex flex-wrap items-center gap-6 bg-white p-4 rounded-2xl border-3 border-[#E7F4FB] mb-6">
            
            {/* BLOC GAUCHE : STATUT DE SYNCHRONISATION */}
            <div className="flex items-center gap-4 bg-slate-50/50 p-1.5 rounded-xl border border-slate-100">
                <div className="flex flex-col px-3 border-r border-slate-200">
                    <span className="text-[10px] font-black text-slate-400 uppercase tracking-wider">
                        Synchronisation
                    </span>

                    {sysState.is_clemz_running ? (
                        /* --- ÉTAT : SYNCHRO EN COURS --- */
                        <div className="flex items-center gap-2">
                            <span className="relative flex h-2 w-2">
                                <span className="animate-ping absolute inline-flex h-full w-full rounded-full bg-indigo-400 opacity-75"></span>
                                <span className="relative inline-flex rounded-full h-2 w-2 bg-indigo-500"></span>
                            </span>
                            <span className="text-sm font-bold text-indigo-600 animate-pulse">
                                Sync Clemz en cours...
                            </span>
                        </div>
                    ) : (
                        /* --- ÉTAT : EN ATTENTE (Par défaut) --- */
                        <span className="text-sm font-bold text-slate-600">
                            {sysState.status_TEST || "En attente"}
                        </span>
                    )}
                </div>

                {/* Indicateur global d'erreur */}
                <div className="flex flex-col justify-center">
                    {sysState.has_error ? (
                        <span className="flex items-center gap-1 text-[10px] font-black text-red-500 bg-red-50 px-2 py-1 rounded">
                            <AlertCircle size={12} /> ERREUR
                        </span>
                    ) : (
                        <span className="flex items-center gap-1 text-[10px] font-black text-emerald-500 bg-emerald-50 px-2 py-1 rounded">
                            <CheckCircle2 size={12} /> SYSTÈME OK
                        </span>
                    )}
                </div>
            </div>

            {/* 2. BARRE DE RECHERCHE INTÉGRÉE */}
            <div className="relative flex-1 max-w-md">
                <div className="absolute inset-y-0 left-0 pl-3 flex items-center pointer-events-none">
                    <Search size={16} className="text-slate-400" />
                </div>
                <input
                    type="text"
                    placeholder="Rechercher un produit dans l'inventaire..."
                    value={searchTerm}
                    onChange={(e) => setSearchTerm(e.target.value)}
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

            {/* BLOC CENTRE : HISTORIQUE DES CRONS */}
            <div className="flex items-center gap-8 ml-auto mr-8">
                {/* CRON 14H */}
                <div className="flex flex-col">
                    <span className="text-[9px] font-black text-slate-400 uppercase tracking-tighter">
                        Dernier Scan 14h
                    </span>
                    <span className="text-[10px] font-black text-slate-600 uppercase">
                        {sysState.last_cron_14h || '---'}
                    </span>
                </div>

                {/* CRON 22H */}
                <div className="flex flex-col">
                    <span className="text-[9px] font-black text-slate-400 uppercase tracking-tighter">
                        Dernier Scan 22h
                    </span>
                    <span className="text-[10px] font-black text-indigo-600 uppercase">
                        {sysState.last_cron_22h || '---'} {/* <-- Corrigé : last_cron_22h */}
                    </span>
                </div>
            </div>

            <div className="flex items-center gap-3 ml-auto">
                {/* BOUTON VIDER LA BASE (Discret mais accessible) */}
                <button
                    onClick={onClearDatabase}
                    title="Vider la base de données"
                    className="p-2.5 text-slate-400 hover:text-red-500 hover:bg-red-50 rounded-xl transition-all"
                >
                    <Trash2 size={20} />
                </button>

                {/* BOUTON DE LANCEMENT */}
                <button
                    onClick={handleRunScraper}
                    disabled={isScraping}
                    className={`flex items-center gap-2 px-6 py-2.5 rounded-xl font-bold transition-all ${
                        isScraping 
                            ? 'bg-slate-100 text-slate-400 cursor-not-allowed' 
                            : 'bg-emerald-500 text-white hover:scale-105 shadow-lg shadow-emerald-100'
                    }`}
                >
                    {isScraping ? (
                        <>
                            <div className="animate-spin h-4 w-4 border-2 border-slate-400 border-t-transparent rounded-full" />
                            <span className="text-[10px] uppercase font-black">Action en cours...</span>
                        </>
                    ) : (
                        <>
                            <PlayCircle size={16} />
                            <span className="text-[10px] uppercase font-black">Lancer Scraping</span>
                        </>
                    )}
                </button>
            </div>
        </div>
    );
}