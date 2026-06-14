"use client";

import { useState } from "react";
import { ArrowRightIcon, ChevronLeftIcon, ChevronRightIcon } from "@/components/icons";

export interface PromoSlide {
  title: string;
  desc: string;
}

/* Promotional card with carousel controls (reference #3). */
export function PromoCarousel({ slides }: { slides: PromoSlide[] }) {
  const [i, setI] = useState(0);
  const slide = slides[i];
  return (
    <div className="promo">
      <div className="glow" />
      <div className="carousel-head" style={{ position: "relative", zIndex: 1 }}>
        <span className="ix">
          {i + 1} of {slides.length}
        </span>
        <div className="carousel-nav">
          <button
            type="button"
            aria-label="Previous"
            onClick={() => setI((p) => (p - 1 + slides.length) % slides.length)}
          >
            <ChevronLeftIcon />
          </button>
          <button
            type="button"
            aria-label="Next"
            onClick={() => setI((p) => (p + 1) % slides.length)}
          >
            <ChevronRightIcon />
          </button>
        </div>
      </div>
      <div className="pt">{slide.title}</div>
      <div className="pd">{slide.desc}</div>
      <div className="go">
        <ArrowRightIcon />
      </div>
    </div>
  );
}
