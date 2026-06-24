// components/DotProgressBar.jsx
import React from 'react';
import { motion } from 'framer-motion';

export default function DotProgressBar({ progressValue = 0 }) {
    const dots = Array.from({ length: 10 });
    const activeDots = Math.round(progressValue / 10);

    // Variantes pour le conteneur (gère l'ordre d'allumage)
    const containerVariants = {
        hidden: { opacity: 1 },
        visible: {
            opacity: 1,
            transition: {
                staggerChildren: 0.08, // Vitesse de la progression (80ms entre chaque point)
            },
        },
    };

    // Variantes pour chaque point
    const dotVariants = {
        hidden: { scale: 0.4, opacity: 0 },
        visible: {
            scale: 1,
            opacity: 1,
            transition: { type: "spring", stiffness: 500, damping: 30 }
        }
    };

    return (
        <div className="w-full mt-2">
            <div className="flex justify-between items-center mb-3">
                <span className="text-slate-400 text-xs font-medium">Progression</span>
                <span className="text-slate-400 text-xs font-medium">{progressValue}%</span>
            </div>

            <motion.div
                className="flex justify-between items-center px-1"
                variants={containerVariants}
                initial="hidden"
                animate="visible"
            >
                {dots.map((_, index) => {
                    const isActive = index < activeDots;
                    return (
                        <motion.div
                            key={index}
                            variants={dotVariants}
                            className={`w-2.5 h-2.5 rounded-full ${isActive
                                    ? "bg-gradient-to-tr from-[#70E1F5] to-[#54B7ED] shadow-[0_0_12px_rgba(112,225,245,0.8)]"
                                    : "bg-[#d1e4f5]"
                                }`}
                        />
                    );
                })}
            </motion.div>
        </div>
    );
};