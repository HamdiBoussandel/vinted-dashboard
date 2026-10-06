// pages/Ventes.jsx
import React, { useState, useEffect, useRef } from 'react';
import {
    TrendingUp, Upload, Loader, RefreshCw, CheckCircle2,
    AlertTriangle, Package, Search, XCircle
} from 'lucide-react';

const API_URL = "http://localhost:8000/api";

function CsvImportBlock({ onImported }) {
    const [isUploading, setIsUploading] = useState(false);
    const [report, setReport] = useState(null);
    const [error, setError] = useState(null);
    const fileInputRef = useRef(null);

    const handleFileChange = async (e) => {
        const file = e.target.files?.[0];
        if (!file) return;

        setIsUploading(true);
        setError(null);
        setReport(null);

        try {
            const formData = new FormData();
            formData.append('file', file);

            const res = await fetch(`${API_URL}/ventes/import-csv`, {
                method: 'POST',
                body: formData,
            });

            if (!res.ok) {
                const data = await res.json().catch(() => ({}));
                throw new Error(data.detail || 'Erreur serveur');
            }

            const data = await res.json();
            setReport(data);
            onImported();
        } catch (err) {
            setError(err.message);
        } finally {
            setIsUploading(false);
            if (fileInputRef.current) fileInputRef.current.value = '';
        }
    };

    return (
        <div className="rounded-2xl border border-slate-200 bg-white p-6 shadow-sm mb-8">
            <div className="flex items-center gap-2 mb-4">
                <Upload size={18} className="text-emerald-500" />
                <h2 className="text-sm font-black uppercase tracking-wide text-slate-700">
                    Importer un export CSV Clemz
                </h2>
            </div>

            <label className="flex flex-col items-center justify-center gap-2 border-2 border-dashed border-slate-200 rounded-xl py-8 cursor-pointer hover:border-emerald-300 hover:bg-emerald-50/30 transition-all">
                <input
                    ref={fileInputRef}
                    type="file"
                    accept=".csv"
                    onChange={handleFileChange}
                    className="hidden"
                    disabled={isUploading}
                />
                {isUploading ? (
                    <>
                        <Loader size={22} className="text-emerald-500 animate-spin" />
                        <span className="text-xs font-bold text-slate-500">Import en cours...</span>
                    </>
                ) : (
                    <>
                        <Upload size={22} className="text-slate-300" />
                        <span className="text-xs font-bold text-slate-500">
                            Clique pour choisir un fichier CSV (export Clemz des ventes)
                        </span>
                    </>
                )}
            </label>

            {error && (
                <p className="text-xs text-red-500 font-medium mt-3">{error}</p>
            )}

            {report && (
                <div className="mt-4 flex flex-col gap-2">
                    <div className="flex items-center gap-2 px-4 py-3 rounded-xl bg-emerald-50 border border-emerald-100 text-xs">
                        <CheckCircle2 size={14} className="text-emerald-500 shrink-0" />
                        <span className="text-emerald-700 font-medium">
                            {report.total_importe} vente(s) importée(s) — prix d'achat retrouvé pour {report.prix_achat_trouve}.
                        </span>
                    </div>
                    {report.prix_achat_non_trouve?.length > 0 && (
                        <div className="px-4 py-3 rounded-xl bg-amber-50 border border-amber-100 text-xs">
                            <div className="flex items-center gap-2 text-amber-700 font-medium mb-1">
                                <AlertTriangle size={14} className="shrink-0" />
                                Prix d'achat introuvable pour {report.prix_achat_non_trouve.length} article(s) :
                            </div>
                            <ul className="text-amber-600 pl-6 list-disc space-y-0.5 max-h-32 overflow-y-auto">
                                {report.prix_achat_non_trouve.map((nom, i) => (
                                    <li key={i}>{nom}</li>
                                ))}
                            </ul>
                        </div>
                    )}
                </div>
            )}
        </div>
    );
}

function VenteRow({ vente }) {
    const hasMarge = vente.prix_achat != null;
    const marge = hasMarge ? vente.prix_vente - vente.prix_achat : null;

    return (
        <div className="grid grid-cols-[1fr_auto_auto_auto_auto] gap-3 items-center px-4 py-3 rounded-xl bg-white border border-slate-100 text-xs hover:border-slate-200 transition-all">
            <div>
                <p className="font-medium text-slate-700 truncate">{vente.nom}</p>
                <p className="text-[10px] text-slate-400">
                    {new Date(vente.date_vente).toLocaleDateString('fr-FR')}
                    {vente.client ? ` · ${vente.client}` : ''}
                    {vente.pays_acheteur ? ` · ${vente.pays_acheteur}` : ''}
                </p>
            </div>
            <div className="text-right">
                <p className="text-[9px] text-slate-400 uppercase font-black">Vendu</p>
                <p className="font-bold text-slate-700">{Number(vente.prix_vente).toFixed(2)} €</p>
            </div>
            <div className="text-right">
                <p className="text-[9px] text-slate-400 uppercase font-black">Acheté</p>
                <p className="font-bold text-slate-700">
                    {hasMarge ? `${Number(vente.prix_achat).toFixed(2)} €` : '—'}
                </p>
            </div>
            <div className="text-right w-20">
                <p className="text-[9px] text-slate-400 uppercase font-black">Marge</p>
                <p className={`font-black ${!hasMarge ? 'text-slate-300' : marge >= 0 ? 'text-emerald-600' : 'text-red-500'}`}>
                    {hasMarge ? `${marge >= 0 ? '+' : ''}${marge.toFixed(2)} €` : '—'}
                </p>
            </div>
            <div className="text-[10px] text-slate-400 px-2 py-1 rounded-md bg-slate-50 border border-slate-100 whitespace-nowrap">
                {vente.dressing?.includes('nikkyo') ? 'D1' : vente.dressing?.includes('griselda') ? 'D2' : '—'}
            </div>
        </div>
    );
}

export default function Ventes() {
    const [ventes, setVentes] = useState([]);
    const [isLoading, setIsLoading] = useState(true);
    const [searchTerm, setSearchTerm] = useState('');

    const fetchVentes = async () => {
        setIsLoading(true);
        try {
            const res = await fetch(`${API_URL}/ventes`);
            const data = await res.json();
            setVentes(data.ventes || []);
        } catch (err) {
            console.error("Erreur chargement des ventes :", err);
        } finally {
            setIsLoading(false);
        }
    };

    useEffect(() => { fetchVentes(); }, []);

    const filteredVentes = ventes.filter(v => {
        if (!searchTerm.trim()) return true;
        const term = searchTerm.trim().toLowerCase();
        return (
            v.nom?.toLowerCase().includes(term) ||
            v.client?.toLowerCase().includes(term) ||
            v.pays_acheteur?.toLowerCase().includes(term)
        );
    });

    // Les stats restent calculées sur l'ensemble des ventes, pas juste le filtre affiché
    const totalVentes = ventes.reduce((s, v) => s + Number(v.prix_vente || 0), 0);
    const ventesAvecMarge = ventes.filter(v => v.prix_achat != null);
    const totalMarge = ventesAvecMarge.reduce((s, v) => s + (Number(v.prix_vente) - Number(v.prix_achat)), 0);

    return (
        <div className="p-10 max-w-4xl mx-auto">
            <div className="mb-8 flex items-center justify-between">
                <div>
                    <h1 className="text-2xl font-black tracking-tight">Ventes</h1>
                    <p className="text-sm text-slate-400 mt-1">
                        Historique des ventes, avec rapprochement automatique du prix d'achat par nom d'article.
                    </p>
                </div>
                <button
                    onClick={fetchVentes}
                    className="flex items-center gap-2 px-4 py-2 rounded-lg text-xs font-black uppercase bg-white text-slate-500 border border-slate-200 hover:border-slate-300 transition-all"
                >
                    <RefreshCw size={12} className={isLoading ? 'animate-spin' : ''} />
                    Actualiser
                </button>
            </div>

            <CsvImportBlock onImported={fetchVentes} />

            {/* Stats rapides */}
            {ventes.length > 0 && (
                <div className="grid grid-cols-3 gap-3 mb-6">
                    <div className="rounded-xl border border-slate-200 bg-white p-4">
                        <p className="text-[10px] font-black uppercase text-slate-400">Ventes totales</p>
                        <p className="text-lg font-black text-slate-700">{ventes.length}</p>
                    </div>
                    <div className="rounded-xl border border-slate-200 bg-white p-4">
                        <p className="text-[10px] font-black uppercase text-slate-400">Chiffre d'affaires</p>
                        <p className="text-lg font-black text-slate-700">{totalVentes.toFixed(2)} €</p>
                    </div>
                    <div className="rounded-xl border border-slate-200 bg-white p-4">
                        <p className="text-[10px] font-black uppercase text-slate-400">
                            Marge connue ({ventesAvecMarge.length}/{ventes.length})
                        </p>
                        <p className={`text-lg font-black ${totalMarge >= 0 ? 'text-emerald-600' : 'text-red-500'}`}>
                            {totalMarge >= 0 ? '+' : ''}{totalMarge.toFixed(2)} €
                        </p>
                    </div>
                </div>
            )}

            <div className="flex items-center justify-between gap-4 mb-4">
                <h2 className="text-sm font-black uppercase tracking-wide text-slate-700 whitespace-nowrap">
                    Historique des ventes
                </h2>
                <div className="relative w-72">
                    <div className="absolute inset-y-0 left-0 pl-3 flex items-center pointer-events-none">
                        <Search size={16} className="text-slate-400" />
                    </div>
                    <input
                        type="text"
                        value={searchTerm}
                        onChange={e => setSearchTerm(e.target.value)}
                        placeholder="Rechercher un article, client, pays..."
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
            ) : filteredVentes.length === 0 ? (
                <div className="flex flex-col items-center justify-center py-20 text-slate-400">
                    <TrendingUp size={40} className="mb-4 opacity-30" />
                    <p className="text-sm font-medium">
                        {searchTerm ? "Aucune vente ne correspond à cette recherche." : "Aucune vente importée pour le moment."}
                    </p>
                </div>
            ) : (
                <div className="flex flex-col gap-2">
                    {filteredVentes.map(v => (
                        <VenteRow key={v.id} vente={v} />
                    ))}
                </div>
            )}
        </div>
    );
}