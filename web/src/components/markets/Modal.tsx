"use client";

import { memo, useEffect } from "react";

/**
 * Shared modal shell for the GAUNTLET-P13 function layer (ECO / news
 * search / alerts / monitors / command palette). Solid overlay color
 * (NO backdrop-filter/blur — perf budget), panel rides the existing
 * gdc-panel rise animation (transform+opacity only), Esc +
 * click-outside close. Body scroll locks while open.
 */
function TerminalModalImpl({
  open,
  onClose,
  title,
  subtitle,
  badge,
  children,
  width = "max-w-[720px]",
  label,
}: {
  open: boolean;
  onClose: () => void;
  title: string;
  subtitle?: string;
  badge?: React.ReactNode;
  children: React.ReactNode;
  width?: string;
  label: string;
}) {
  useEffect(() => {
    if (!open) return;
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") onClose();
    };
    window.addEventListener("keydown", onKey);
    const prev = document.body.style.overflow;
    document.body.style.overflow = "hidden";
    return () => {
      window.removeEventListener("keydown", onKey);
      document.body.style.overflow = prev;
    };
  }, [open, onClose]);

  if (!open) return null;
  return (
    <div
      className="fixed inset-0 z-[90] flex items-start justify-center overflow-y-auto bg-[#08090d]/88 px-4 py-[7vh]"
      onMouseDown={(e) => {
        if (e.target === e.currentTarget) onClose();
      }}
      role="dialog"
      aria-modal="true"
      aria-label={label}
    >
      <div className={`gdc-panel w-full ${width} pb-4 pt-3.5`}>
        <div className="mb-2 flex flex-wrap items-center gap-x-3 gap-y-1 px-4 sm:px-5">
          <h2
            className="gdc-spec"
            style={{ fontSize: "10.5px", color: "#c8a04b" }}
          >
            {title}
          </h2>
          {badge}
          <span className="h-px flex-1 bg-[#1a1f2c]" />
          {subtitle && (
            <span className="text-[9px] uppercase tracking-[0.16em] text-[#8a93a6]">
              {subtitle}
            </span>
          )}
          <button
            onClick={onClose}
            className="gdc-chip cursor-pointer px-2 py-0.5 text-[10px] text-[#8a93a6] transition-colors hover:text-[#e8ecf4]"
            aria-label="Close dialog"
          >
            ESC ✕
          </button>
        </div>
        <div className="px-4 sm:px-5">{children}</div>
      </div>
    </div>
  );
}

export const TerminalModal = memo(TerminalModalImpl);
