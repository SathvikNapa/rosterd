/**
 * A number that physically travels to its new value instead of snapping --
 * replica counts, queue depth, scores. This is one of the highest-leverage
 * places for motion in a system whose whole pitch is "this is live, watch
 * it react": a value that visibly counts is legible as *change*, the same
 * value re-rendered identically reads as static even when it isn't.
 *
 * Built on useSpring/useTransform rather than a naive setInterval tween --
 * the spring inherits the same settle character as everything else in
 * lib/motion.ts, and a value that changes again mid-animation retargets
 * smoothly instead of restarting from 0.
 */
import { motion, useMotionValue, useReducedMotion, useSpring, useTransform } from 'motion/react';
import { useEffect } from 'react';

interface AnimatedNumberProps {
  value: number;
  /** e.g. (n) => `${n}%`. Defaults to a plain rounded integer. */
  format?: (rounded: number) => string;
  className?: string;
  style?: React.CSSProperties;
}

export function AnimatedNumber({ value, format, className, style }: AnimatedNumberProps) {
  const reduce = useReducedMotion();
  const motionValue = useMotionValue(value);
  const spring = useSpring(motionValue, { stiffness: 260, damping: 32, mass: 0.9 });
  const display = useTransform(spring, (v) => formatValue(v, format));

  useEffect(() => {
    motionValue.set(value);
  }, [value, motionValue]);

  if (reduce) {
    return (
      <span className={className} style={style}>
        {formatValue(value, format)}
      </span>
    );
  }

  return (
    <motion.span className={className} style={style}>
      {display}
    </motion.span>
  );
}

function formatValue(v: number, format?: (rounded: number) => string): string {
  const rounded = Math.round(v);
  return format ? format(rounded) : rounded.toLocaleString();
}
