// Emojis, sélecteurs de variante et drapeaux (paires d'indicateurs régionaux).
const EMOJIS = /\p{Extended_Pictographic}|\u{FE0F}|\u{200D}|\u{20E3}|[\u{1F1E6}-\u{1F1FF}]/gu;

export function retirerEmojis(texte) {
    return texte == null ? texte : String(texte).replace(EMOJIS, '').replace(/ {2,}/g, ' ').trim();
}
