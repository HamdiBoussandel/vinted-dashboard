// components/AnimatedPrice.jsx
import React, { useEffect } from 'react';
import { animate, useMotionValue, useTransform, motion } from 'framer-motion';


export default function AnimatedPrice({ value, targetObjective }) {
    const count = useMotionValue(0);

    // Formatage en direct avec séparateur de milliers et 2 décimales
    // Nettoyage de la valeur pour l'animation (ex: "4,00 €" -> 4.0)
    const numericValue = typeof value === 'string'
        ? parseFloat(value.replace(/[^\d,.-]/g, '').replace(',', '.'))
        : (value || 0);

    // Formatage avec le point (.) via 'en-US'
    const formattedValue = useTransform(count, (latest) => {
        return latest.toLocaleString('en-US', {
            minimumFractionDigits: 2,
            maximumFractionDigits: 2
        });
    });

    useEffect(() => {
        const controls = animate(count, isNaN(numericValue) ? 0 : numericValue, {
            duration: 2,
            ease: [0.16, 1, 0.3, 1],
        });
        return () => controls.stop();
    }, [numericValue, count]);

    return (
        <div className="flex items-center">
            <motion.span className="text-3xl font-normal tabular-nums">
                {useTransform(formattedValue, v => v.split('.')[0])}
            </motion.span>
            {/* Symbole € masqué si un objectif est présent (Cartes 1 & 2) */}
            <span className="text-[0.7em] mt-2 text-slate-400">.</span>

            {/* C'est ici que vous changez la couleur des centimes (ex: text-slate-400 ou text-blue-400) */}
            <motion.span className="text-3xl font-normal text-slate-400">
                {useTransform(formattedValue, v => v.split('.')[1])}
            </motion.span>

            {/* Symbole € masqué si un objectif est présent (Cartes 1 & 2) */}
            {!targetObjective && (
                <span className="text-3xl font-normal tabular-nums text-slate-400 ml-0.5">€</span>
            )}

            {/* Affichage de l'objectif */}
            {targetObjective && (
                <span className="text-xs font-medium text-slate-400 mt-3 ml-1">
                    / {targetObjective} €
                </span>
            )}
        </div>
    );
}