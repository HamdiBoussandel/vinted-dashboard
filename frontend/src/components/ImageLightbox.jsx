import React, { useEffect, useCallback, useRef } from 'react';
import { X, ChevronLeft, ChevronRight } from 'lucide-react';

const ImageLightbox = ({ photos, currentIndex, onClose, onNavigate }) => {
  const total = photos.length;
  const thumbsRef = useRef(null);
  const mainRef = useRef(null);

  const goPrev = useCallback(() => {
    onNavigate((currentIndex - 1 + total) % total);
  }, [currentIndex, total, onNavigate]);

  const goNext = useCallback(() => {
    onNavigate((currentIndex + 1) % total);
  }, [currentIndex, total, onNavigate]);

  // Molette sur les miniatures : défilement horizontal de la bande
  useEffect(() => {
    const el = thumbsRef.current;
    if (!el) return;

    const handleWheel = (e) => {
      e.preventDefault();
      e.stopPropagation();
      el.scrollLeft += e.deltaY;
    };

    el.addEventListener('wheel', handleWheel, { passive: false });
    return () => el.removeEventListener('wheel', handleWheel);
  }, [photos]);

  // Molette sur la zone principale : navigation photo suivante/précédente
  useEffect(() => {
    const el = mainRef.current;
    if (!el) return;

    const handleMainWheel = (e) => {
      e.preventDefault();
      if (e.deltaY > 0) {
        goNext();
      } else if (e.deltaY < 0) {
        goPrev();
      }
    };

    el.addEventListener('wheel', handleMainWheel, { passive: false });
    return () => el.removeEventListener('wheel', handleMainWheel);
  }, [goNext, goPrev]);

  // Navigation clavier (flèches + Échap)
  useEffect(() => {
    const handleKeyDown = (e) => {
      if (e.key === 'Escape') onClose();
      if (e.key === 'ArrowLeft') goPrev();
      if (e.key === 'ArrowRight') goNext();
    };
    window.addEventListener('keydown', handleKeyDown);
    return () => window.removeEventListener('keydown', handleKeyDown);
  }, [goPrev, goNext, onClose]);

  if (!photos || total === 0) return null;

  return (
    <div
      ref={mainRef}
      className="fixed inset-0 z-[100] bg-black/90 flex items-center justify-center"
      onClick={onClose}
    >
      {/* Bouton fermer */}
      <button
        onClick={(e) => { e.stopPropagation(); onClose(); }}
        className="absolute top-6 right-6 text-white/80 hover:text-white p-2 rounded-full hover:bg-white/10 transition-colors"
      >
        <X size={28} />
      </button>

      {/* Compteur */}
      {total > 1 && (
        <div className="absolute top-6 left-1/2 -translate-x-1/2 text-white/70 text-sm font-bold">
          {currentIndex + 1} / {total}
        </div>
      )}

      {/* Flèche précédente */}
      {total > 1 && (
        <button
          onClick={(e) => { e.stopPropagation(); goPrev(); }}
          className="absolute left-4 md:left-8 text-white/80 hover:text-white p-3 rounded-full hover:bg-white/10 transition-colors"
        >
          <ChevronLeft size={36} />
        </button>
      )}

      {/* Image principale */}
      <img
        src={photos[currentIndex]?.full_size_url || photos[currentIndex]?.url}
        alt=""
        className="max-h-[90vh] max-w-[90vw] object-contain rounded-lg select-none"
        onClick={(e) => e.stopPropagation()}
      />

      {/* Flèche suivante */}
      {total > 1 && (
        <button
          onClick={(e) => { e.stopPropagation(); goNext(); }}
          className="absolute right-4 md:right-8 text-white/80 hover:text-white p-3 rounded-full hover:bg-white/10 transition-colors"
        >
          <ChevronRight size={36} />
        </button>
      )}

      {/* Miniatures en bas */}
      {total > 1 && (
        <div
          ref={thumbsRef}
          className="absolute bottom-6 left-1/2 -translate-x-1/2 flex gap-2 max-w-[90vw] overflow-x-auto px-4"
          onClick={(e) => e.stopPropagation()}
        >
          {photos.map((photo, idx) => (
            <button
              key={idx}
              onClick={() => onNavigate(idx)}
              className={`shrink-0 w-14 h-14 rounded-lg overflow-hidden border-2 transition-all ${
                idx === currentIndex ? 'border-cyan-400 opacity-100' : 'border-transparent opacity-50 hover:opacity-80'
              }`}
            >
              <img src={photo.url} alt="" className="w-full h-full object-cover" />
            </button>
          ))}
        </div>
      )}
    </div>
  );
};

export default ImageLightbox;