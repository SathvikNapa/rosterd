/**
 * The circular agent bubble the frames use everywhere, with the stacked
 * pod-count badge when a pool has scaled past one instance ("Fulfillment ×3").
 *
 * The count animates on change rather than swapping the number — the product
 * spec calls this out as a live demo moment, not a detail. It is a Motion
 * spring rather than a CSS keyframe so the pop is interruptible: during a
 * flash sale the count can tick 1→2→4 faster than a 600ms animation runs,
 * and a keyframe would restart from scale(1) each time and visibly stutter.
 *
 * The badge also enters and leaves with AnimatePresence, so a pool crossing
 * the ×1 boundary in either direction is a transition, not a pop-in.
 */
import { AnimatePresence, motion } from 'motion/react';
import { initial, palette } from '../lib/format';
import type { Tone } from '../lib/format';
import { useReducedMotion } from 'motion/react';
import { POP, SPRING, useBadgePresence } from '../lib/motion';
import './AgentBubble.css';

interface Props {
  name: string;
  tone: Tone;
  size?: number;
  /** Pool size. The badge only renders above 1, as in the frames. */
  replicas?: number;
}

export function AgentBubble({ name, tone, size = 52, replicas = 1 }: Props) {
  const colors = palette(tone);
  const presence = useBadgePresence();
  const reduce = useReducedMotion();

  return (
    <div className="bubble" style={{ width: size, height: size }}>
      <span
        className="bubble__disc"
        style={{
          width: size,
          height: size,
          background: colors.bg,
          border: `2px solid ${colors.border}`,
          color: colors.fg,
          fontSize: Math.round(size / 3),
        }}
        aria-hidden="true"
      >
        {initial(name)}
      </span>
      <AnimatePresence>
        {replicas > 1 && (
          <motion.span
            className="bubble__count"
            style={{ background: colors.border, color: '#fff' }}
            aria-label={`${replicas} instances running`}
            {...presence}
            transition={SPRING}
          >
            {/* Keyed on the number so each change remounts and re-pops.
                A scale that cannot animate is just a wrongly-sized glyph for
                a frame, so reduced motion skips the overshoot entirely. */}
            <motion.span
              key={replicas}
              initial={reduce ? false : { scale: 1.6 }}
              animate={{ scale: 1 }}
              transition={POP}
              style={{ display: 'inline-block' }}
            >
              ×{replicas}
            </motion.span>
          </motion.span>
        )}
      </AnimatePresence>
    </div>
  );
}
