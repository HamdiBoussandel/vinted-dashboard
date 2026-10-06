import React, { useState } from 'react';
import { ExternalLink, Trash2, Clock, Heart,Expand } from 'lucide-react';
import ImageLightbox from './ImageLightbox';

const ProductCardSniper = ({ product, onDelete, activeFilter }) => {
  const [lightboxOpen, setLightboxOpen] = useState(false);
  const [lightboxIndex, setLightboxIndex] = useState(0);

  const photos = product.photos || [];
  const imageUrl = photos[0]?.url || 'https://via.placeholder.com/300x400';
  const filter = product.filter_name || activeFilter?.nom || "Filtre inconnu"
  
  const prixArticle = parseFloat(product.price?.amount) || 0;
  const service = parseFloat(product.service_fee?.amount) || 0;
  const totalReel = parseFloat(product.total_item_price?.amount) || (prixArticle + service);
  const rentabiliteEst = totalReel;
  const etat = product.status || product.item_box?.second_line?.split('·')[1]?.trim() || "N/A";
  const prixRevente = (totalReel * 2).toFixed(2);
  const benefice = totalReel.toFixed(2);

  return (
    <div className="w-full flex bg-white rounded-3xl border border-slate-200 overflow-hidden shadow-sm hover:shadow-md transition-all h-[420px]">

      {/* SECTION GAUCHE : IMAGE ET ACTIONS */}
      <div className="w-[220px] p-3 flex flex-col gap-3 shrink-0 h-full">
        <div
          className="relative flex-1 bg-slate-100 rounded-2xl overflow-hidden group cursor-pointer"
          onClick={() => {
            if (photos.length > 0) {
              setLightboxIndex(0);
              setLightboxOpen(true);
            }
          }}
        >
          <img
            src={imageUrl}
            alt={product.title}
            className="w-full h-full object-cover"
          />
          {photos.length > 0 && (
            <div className="absolute inset-0 bg-black/0 group-hover:bg-black/30 transition-all flex items-center justify-center">
              <Expand size={24} className="text-white opacity-0 group-hover:opacity-100 transition-opacity" />
            </div>
          )}
          {photos.length > 1 && (
            <span className="absolute bottom-2 right-2 bg-black/60 text-white text-[10px] font-bold px-2 py-0.5 rounded-full">
              {photos.length} photos
            </span>
          )}
        </div>
        <div className="flex gap-2">
          <button
            onClick={() => onDelete(product.id)}
            className="flex-1 py-2 flex justify-center bg-white border border-slate-200 rounded-lg text-red-500 hover:bg-red-50 transition-colors"
          >
            <Trash2 size={18} />
          </button>
          <a
            href={product.url}
            target="_blank"
            rel="noopener noreferrer"
            className="flex-1 py-2 flex justify-center bg-white border border-slate-200 rounded-lg text-slate-600 hover:bg-slate-50 transition-colors"
          >
            <ExternalLink size={18} />
          </a>
        </div>
      </div>

      {/* SECTION CENTRALE : INFOS PRODUIT */}
      <div className="flex-1 p-6 flex flex-col justify-between border-x border-slate-100">
        <div>
          <div className="flex justify-between items-start mb-2">
            <span className="px-3 py-1 bg-cyan-400 text-white text-[10px] font-black rounded-lg uppercase">
              {filter}
            </span>
            <span className="flex items-center gap-1 text-[11px] font-bold text-slate-400">
              <Clock size={12} /> {product.relative_time || "À l'instant"}
            </span>
          </div>

          <h4 className="text-xs font-bold text-slate-400 uppercase tracking-tight mb-1">
            {product.brand_title}
          </h4>
          <h2 className="text-xl font-black text-slate-800 leading-tight mb-4">
            {product.title}
          </h2>

          <div className="flex gap-6 mb-4">
            <div>
              <p className="text-lg font-black text-slate-800">{product.size_title || 'N/A'}</p>
              <p className="text-[10px] font-bold text-slate-400 uppercase">Taille</p>
            </div>
            <div>
              <p className="text-[10px] font-bold text-slate-400 uppercase">ETAT</p>
              <p className="text-lg font-black text-slate-800">{etat}</p>
            </div>
          </div>
        </div>
      </div>

      {/* SECTION DROITE : RENTABILITÉ (Le bloc gris) */}
      <div className="w-1/3 bg-slate-50/80 p-6 flex flex-col justify-between">
        <div className="text-center mb-3">
          <p className="text-[11px] font-bold text-slate-400 uppercase mb-1">Total à payer</p>
          <p className="text-3xl font-black text-slate-900 mb-1">
            {totalReel.toFixed(2)} €
          </p>
          <p className="text-[10px] text-slate-400 font-medium">
            protection incluse
          </p>
        </div>
        <div className="flex flex-col gap-1 mt-1">
          <p className="text-[11px] font-bold text-slate-500 italic">
            Revente estimée :&nbsp;
            <span className="text-emerald-600">{prixRevente} €</span>
          </p>
          <p className="text-[11px] font-bold text-slate-500 italic">
            Bénéfice estimé :&nbsp;
            <span className="text-emerald-600">+{totalReel.toFixed(2)} €</span>
          </p>
        </div>

        <div className="flex flex-col gap-2 mt-4">
          <div className="flex justify-between text-xs font-bold text-slate-600">
            <span className="text-slate-400 uppercase text-[10px]">Prix article :</span>
            <span>{prixArticle.toFixed(2)} €</span>
          </div>
          <div className="flex justify-between text-xs font-bold text-slate-600">
            <span className="text-slate-400 uppercase text-[10px]">Protection :</span>
            <span className="text-cyan-600">{service.toFixed(2)} €</span>
          </div>
        </div>
      </div>

      {lightboxOpen && (
        <ImageLightbox
          photos={photos}
          currentIndex={lightboxIndex}
          onClose={() => setLightboxOpen(false)}
          onNavigate={setLightboxIndex}
        />
      )}
    </div>
  );
};

export default ProductCardSniper;