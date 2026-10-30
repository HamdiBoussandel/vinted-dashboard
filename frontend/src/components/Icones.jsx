// components/Icones.jsx
import React from 'react';
import {
    AlertTriangle, Camera, CheckCircle2, ChevronsDown, DoorOpen, Flame, Gem, Hourglass, Leaf, Package, Pencil,
    Recycle, Siren, Skull, TrendingDown, XCircle,
} from 'lucide-react';
import { retirerEmojis } from '../utils/emojis';

// Icône en ligne avec le texte : suit sa taille et sa couleur, et ne casse pas
// le retour à la ligne des libellés longs.
export function AvecIcone(props) {
    const { Icone, children } = props;
    return (
        <span>
            <Icone size="1.15em" className="inline-block align-[-0.18em] mr-1.5" aria-hidden="true" />
            {children}
        </span>
    );
}

// Textes reçus du backend, qui commencent par un emoji : le backend s'en sert
// dans sa logique (ex. action_label.startswith("⏳"), supabase_service.py), donc
// ils ne sont convertis qu'ici, à l'affichage.
const ICONES_BACKEND = {
    '✅': CheckCircle2, '❌': XCircle, '⚠': AlertTriangle, '⏳': Hourglass, '♻': Recycle, '📉': TrendingDown,
    '🔻': ChevronsDown, '🚨': Siren, '🚪': DoorOpen, '💎': Gem, '💀': Skull, '✏': Pencil, '📸': Camera,
    '📦': Package, '🍂': Leaf, '🔥': Flame,
};

export function TexteIcone({ texte }) {
    if (texte == null) return null;
    const brut = String(texte).trimStart();
    const Icone = ICONES_BACKEND[Array.from(brut)[0]];
    const propre = retirerEmojis(brut);
    return Icone ? <AvecIcone Icone={Icone}>{propre}</AvecIcone> : propre;
}
