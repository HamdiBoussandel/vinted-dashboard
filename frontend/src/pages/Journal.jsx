// pages/Journal.jsx
import React, { useState, useEffect, useCallback } from 'react';
import {
    CheckCircle, XCircle, AlertCircle, Clock, Loader,
    Power, ScanLine, TrendingDown, RefreshCw, Heart, ChevronLeft, ChevronRight,
} from 'lucide-react';
import { maintenanceService } from '../services/api';

// Couvre à la fois les statuts VM ("succes"/"echec") et les statuts de tâche
// (global_status : "success"/"failed"/"partial_success"/"running") -- même
// langage visuel que TaskHistory.jsx pour rester cohérent entre les 2 pages.
const STATUS_CONFIG = {
    success: { label: 'Succès', icon: <CheckCircle size={14} className="text-emerald-500" />, bg: 'bg-emerald-50 border-emerald-100', text: 'text-emerald-700' },
    succes: { label: 'Succès', icon: <CheckCircle size={14} className="text-emerald-500" />, bg: 'bg-emerald-50 border-emerald-100', text: 'text-emerald-700' },
    partial_success: { label: 'Succès partiel', icon: <AlertCircle size={14} className="text-amber-500" />, bg: 'bg-amber-50 border-amber-100', text: 'text-amber-700' },
    failed: { label: 'Échec', icon: <XCircle size={14} className="text-red-500" />, bg: 'bg-red-50 border-red-100', text: 'text-red-700' },
    echec: { label: 'Échec', icon: <XCircle size={14} className="text-red-500" />, bg: 'bg-red-50 border-red-100', text: 'text-red-700' },
    running: { label: 'En cours', icon: <Loader size={14} className="animate-spin text-blue-500" />, bg: 'bg-blue-50 border-blue-100', text: 'text-blue-700' },
    inconnu: { label: 'Pas encore passé', icon: <Clock size={14} className="text-slate-300" />, bg: 'bg-slate-50 border-slate-100', text: 'text-slate-400' },
};

const ICONS_ETAPE = [Power, ScanLine, ScanLine, TrendingDown, TrendingDown, RefreshCw, RefreshCw, Heart, Power];

function formatHeure(iso) {
    if (!iso) return null;
    return new Date(iso).toLocaleTimeString('fr-FR', { hour: '2-digit', minute: '2-digit' });
}

function EtapeCard({ etape, index }) {
    const cfg = STATUS_CONFIG[etape.statut] || STATUS_CONFIG.inconnu;
    const Icon = ICONS_ETAPE[index];
    const heure = formatHeure(etape.heure);

    return (
        <div className={`rounded-xl border p-4 flex items-center justify-between gap-4 ${cfg.bg}`}>
            <div className="flex items-center gap-3 min-w-0">
                <Icon size={16} className="text-slate-400 shrink-0" />
                <div className="min-w-0">
                    <p className="text-sm font-bold text-slate-700 flex items-center gap-2">
                        {etape.label}
                        {heure && <span className="text-[10px] font-medium text-slate-400">· {heure}</span>}
                    </p>
                    {etape.compteurs && (
                        <p className="text-[11px] text-slate-500 mt-0.5">
                            {etape.compteurs.succes}/{etape.compteurs.total} réussi(s)
                            {etape.compteurs.bloques_quota > 0 && (
                                <span className="text-amber-600 font-semibold"> · {etape.compteurs.bloques_quota} bloqué(s) quota</span>
                            )}
                            {etape.compteurs.erreurs_clemz > 0 && (
                                <span className="text-red-700 font-semibold"> · {etape.compteurs.erreurs_clemz} erreur(s) Clemz</span>
                            )}
                        </p>
                    )}
                    {etape.prevu && (
                        <p className="text-[10px] text-slate-400 mt-0.5">
                            Prévu : {etape.prevu.niveau === 'calendrier' ? 'calendrier' : etape.prevu.niveau}
                            {etape.prevu.actif && etape.prevu.horaire && ` vers ${etape.prevu.horaire.slice(0, 5)}`}
                            {etape.prevu.actif && ` · cible ${etape.prevu.volume_cible}`}
                            {!etape.prevu.actif && ' · repos'}
                        </p>
                    )}
                    {etape.detail && (
                        <p className="text-[10px] text-slate-400 mt-0.5">{etape.detail}</p>
                    )}
                </div>
            </div>
            <div className={`flex items-center gap-1.5 text-xs font-bold shrink-0 ${cfg.text}`}>
                {cfg.icon}
                {cfg.label}
            </div>
        </div>
    );
}

export default function Journal() {
    const [date, setDate] = useState(() => new Date().toISOString().slice(0, 10));
    const [journal, setJournal] = useState(null);
    const [isLoading, setIsLoading] = useState(true);

    const fetchJournal = useCallback(async () => {
        setIsLoading(true);
        try {
            const data = await maintenanceService.getJournal(date);
            setJournal(data);
        } catch {
            setJournal(null);
        } finally {
            setIsLoading(false);
        }
    }, [date]);

    useEffect(() => { fetchJournal(); }, [fetchJournal]);

    const decalerDate = (jours) => {
        const d = new Date(date);
        d.setDate(d.getDate() + jours);
        setDate(d.toISOString().slice(0, 10));
    };

    const estAujourdhui = date === new Date().toISOString().slice(0, 10);

    return (
        <div className="p-8">
            <div className="flex items-center justify-between mb-8">
                <div>
                    <h1 className="text-2xl font-black text-slate-800">Journal de routine</h1>
                    <p className="text-sm text-slate-400 mt-1">
                        Démarrage VM → scraping → baisses de prix → republications → partage vues/favoris → extinction, dans l'ordre du scénario quotidien.
                    </p>
                </div>

                <div className="flex items-center gap-2 bg-white rounded-xl border border-slate-100 shadow-sm px-3 py-2">
                    <button onClick={() => decalerDate(-1)} className="p-1.5 rounded-lg hover:bg-slate-50 text-slate-500">
                        <ChevronLeft size={16} />
                    </button>
                    <input
                        type="date"
                        value={date}
                        max={new Date().toISOString().slice(0, 10)}
                        onChange={(e) => setDate(e.target.value)}
                        className="text-sm font-semibold text-slate-700 border-none focus:outline-none"
                    />
                    <button onClick={() => decalerDate(1)} disabled={estAujourdhui} className="p-1.5 rounded-lg hover:bg-slate-50 text-slate-500 disabled:opacity-30 disabled:cursor-not-allowed">
                        <ChevronRight size={16} />
                    </button>
                </div>
            </div>

            {isLoading ? (
                <p className="text-sm text-slate-400">Chargement...</p>
            ) : !journal ? (
                <p className="text-sm text-red-500">Impossible de charger le journal de cette journée.</p>
            ) : (
                <div className="space-y-2 max-w-3xl">
                    {journal.etapes.map((etape, i) => (
                        <EtapeCard key={i} etape={etape} index={i} />
                    ))}
                </div>
            )}
        </div>
    );
}
