/**
 * Thin wrappers over motion/react so screens read as markup, not as
 * animation config. Every one of these is presentational — none of them
 * changes layout, so they can be dropped around existing elements.
 */
import { motion } from 'motion/react';
import type { ReactNode } from 'react';
import { staggerContainer, staggerRow, useStaggerItem } from '../lib/motion';

interface StaggerProps {
  children: ReactNode;
  className?: string;
  style?: React.CSSProperties;
}

/** Owns the timing for a group of <Item>s. Renders a plain div. */
export function Stagger({ children, className, style }: StaggerProps) {
  return (
    <motion.div
      className={className}
      style={style}
      variants={staggerContainer}
      initial="initial"
      animate="animate"
    >
      {children}
    </motion.div>
  );
}

/** One member of a <Stagger>. Fades and rises (fade only if the OS asks). */
export function StaggerItem({ children, className, style }: StaggerProps) {
  return (
    <motion.div className={className} style={style} variants={useStaggerItem()}>
      {children}
    </motion.div>
  );
}

/**
 * Table equivalents. `<tbody>`/`<tr>` cannot be wrapped in a div without
 * breaking table layout, so these render the real elements.
 */
export function StaggerBody({ children }: { children: ReactNode }) {
  return (
    <motion.tbody variants={staggerContainer} initial="initial" animate="animate">
      {children}
    </motion.tbody>
  );
}

export function StaggerTr({ children }: { children: ReactNode }) {
  return <motion.tr variants={staggerRow}>{children}</motion.tr>;
}
