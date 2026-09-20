/**
 * Shared motion vocabulary (motion.dev / `motion/react`).
 *
 * Kept in one file so every screen animates on the same curve and timing —
 * a UI where each list picks its own easing reads as noisy, not lively.
 *
 * ## Reduced motion
 *
 * `<MotionConfig reducedMotion="user">` in main.tsx is necessary but NOT
 * sufficient, and it is worth being precise about why. It stops Motion
 * *animating* transforms — it does not stop a variant from *applying* one.
 * A variant of `initial: { y: 8 }` still renders the element 8px down and
 * then snaps it into place. Measured, with prefers-reduced-motion: reduce:
 * the page shell sat at y=6 for nine frames and then jumped to 0 — a visible
 * lurch, which is exactly what the setting exists to avoid.
 *
 * So anything that offsets or scales has a fade-only twin here, and the
 * `use*` hooks below pick between them. MotionConfig still earns its place:
 * it covers the springs, `whileHover`/`whileTap`, and layout animation,
 * where snapping to the target is the right reduced-motion behaviour.
 */
import { stagger, useReducedMotion } from 'motion/react';
import type { Transition, Variants } from 'motion/react';

/** The frames' own easing — matches --ease-out in tokens.css. */
export const EASE_OUT = [0.22, 1, 0.36, 1] as const;

/** Settling, not bouncy: used for anything that moves between two places. */
export const SPRING: Transition = { type: 'spring', stiffness: 420, damping: 36, mass: 0.8 };

/** A pop with a little overshoot, for a value changing in place. */
export const POP: Transition = { type: 'spring', stiffness: 600, damping: 18, mass: 0.7 };

/**
 * Screen-to-screen. Exit is faster than enter: the outgoing screen should
 * get out of the way, not perform.
 */
export const pageVariants: Variants = {
  initial: { opacity: 0, y: 8 },
  animate: { opacity: 1, y: 0, transition: { duration: 0.28, ease: EASE_OUT } },
  exit: { opacity: 0, y: -6, transition: { duration: 0.16, ease: 'easeIn' } },
};

const pageVariantsFade: Variants = {
  initial: { opacity: 0 },
  animate: { opacity: 1, transition: { duration: 0.2, ease: EASE_OUT } },
  exit: { opacity: 0, transition: { duration: 0.12, ease: 'easeIn' } },
};

/**
 * Lists and card decks. The container itself is invisible — it only owns the
 * timing, so children can be laid out by whatever the parent already uses
 * (grid, flex, <tbody>).
 */
export const staggerContainer: Variants = {
  initial: {},
  // `delayChildren: stagger(...)` is the v12+ API. The older `staggerChildren`
  // key still type-checks against motion 13 but is silently ignored at
  // runtime — measured: every row started in the same frame. With this, the
  // rows start ~50ms apart and the list settles in ~620ms.
  animate: { transition: { delayChildren: stagger(0.045, { startDelay: 0.04 }) } },
};

export const staggerItem: Variants = {
  initial: { opacity: 0, y: 10 },
  animate: { opacity: 1, y: 0, transition: { duration: 0.32, ease: EASE_OUT } },
};

const staggerItemFade: Variants = {
  initial: { opacity: 0 },
  animate: { opacity: 1, transition: { duration: 0.24, ease: EASE_OUT } },
};

/** Rows in a table: no y-shift (it fights the row borders), just a fade-in. */
export const staggerRow: Variants = {
  initial: { opacity: 0 },
  animate: { opacity: 1, transition: { duration: 0.26, ease: EASE_OUT } },
};

/** The routed-screen transition, fade-only when the OS asks for that. */
export function usePageVariants(): Variants {
  return useReducedMotion() ? pageVariantsFade : pageVariants;
}

/** One member of a stagger, fade-only when the OS asks for that. */
export function useStaggerItem(): Variants {
  return useReducedMotion() ? staggerItemFade : staggerItem;
}

/**
 * The agent bubble's pod-count badge. Scaling from 0 is a pop-in when it
 * cannot be animated, so reduced motion gets a plain fade instead.
 */
export function useBadgePresence() {
  const reduce = useReducedMotion();
  return reduce
    ? { initial: { opacity: 0 }, animate: { opacity: 1 }, exit: { opacity: 0 } }
    : { initial: { scale: 0, opacity: 0 }, animate: { scale: 1, opacity: 1 }, exit: { scale: 0, opacity: 0 } };
}
