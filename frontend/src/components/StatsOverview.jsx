// components/StatsOverview.jsx
import React from 'react';
import AnimatedPrice from './AnimatedPrice';
import DotProgressBar from './DotProgressBar';

export default function StatsOverview({ stats, sales, isLoading }) {
    if (isLoading || !stats?.ca_global_format || !sales?.current) {
        return (
            <div className="flex gap-6 mt-8 w-full">
                <div className="text-slate-400 animate-pulse font-medium">
                    Chargement des statistiques de performance...
                </div>
            </div>
        );
    }

    return (
        <div className="flex flex-wrap justify-start gap-6 mt-8">
            {/* CARTE 1 : MOIS EN COURS */}
            <div className="flex-1 min-w-[270px] p-6 rounded-xl bg-[#E7F4FB] border-3 border-white">
                <span className="block text-sm text-slate-500 font-medium mb-4">Ce mois-ci</span>
                <div className="text-3xl font-black tabular-nums mb-6">
                    <AnimatedPrice
                        value={sales.current.total_vendu_format}
                        targetObjective={sales.current.objectif_du_mois}
                    />
                </div>
                <DotProgressBar progressValue={sales.current.progression_value} />
            </div>

            {/* CARTE 2 : MOIS DERNIER */}
            <div className="flex-1 min-w-[270px] p-6 rounded-xl bg-[#E7F4FB] border-3 border-white">
                <span className="block text-sm text-slate-500 font-medium mb-4">Mois dernier</span>
                <div className="text-3xl font-black tabular-nums mb-6">
                    <AnimatedPrice
                        value={sales.last?.total_vendu_format}
                        targetObjective={sales.last?.objectif_du_mois}
                    />
                </div>
                <DotProgressBar progressValue={sales.last?.progression_value || 0} />
            </div>

            {/* CARTE 3 : BÉNÉFICE TOTAL */}
            <div className="flex-1 min-w-[270px] p-6 rounded-xl bg-[#E7F4FB] border-3 border-white">
                <span className="block text-sm text-slate-500 font-medium mb-4">Bénéfice total</span>
                <div className="text-3xl font-black tabular-nums mb-6">
                    <AnimatedPrice value={stats.benefice_format} />
                </div>
            </div>

            {/* CARTE 4 : TOTAL VENTES (GLOBAL) */}
            <div className="flex-1 min-w-[270px] p-6 rounded-xl bg-[#E7F4FB] border-3 border-white">
                <span className="block text-sm text-slate-500 font-medium mb-4">Total ventes (Global)</span>
                <div className="text-3xl font-black tabular-nums mb-6">
                    <AnimatedPrice value={stats.ca_global_format} />
                </div>
            </div>
        </div>
    );
}