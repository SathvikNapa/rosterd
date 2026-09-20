/**
 * The right-hand "Team" card from the Roster and Task Run frames: bubble,
 * name, status pill, and one line of context. The card border picks up the
 * status color once an agent is doing something (the frames draw idle agents
 * with the plain 1px border and active ones with a 1.5px colored one).
 */
import { motion } from 'motion/react';
import type { ReactNode } from 'react';
import { palette } from '../lib/format';
import { SPRING, useStaggerItem } from '../lib/motion';
import type { Tone } from '../lib/format';
import { AgentBubble } from './AgentBubble';
import { SmallBadge } from './ui';

interface Props {
  name: string;
  tone: Tone;
  statusLabel: string;
  detail: ReactNode;
  replicas?: number;
  onClick?: () => void;
  selected?: boolean;
}

export function AgentCard({ name, tone, statusLabel, detail, replicas = 1, onClick, selected }: Props) {
  const colors = palette(tone);
  const itemVariants = useStaggerItem();
  const active = tone !== 'neutral' || selected;

  const card = (
    <>
      <AgentBubble name={name} tone={tone} replicas={replicas} />
      <div style={{ minWidth: 0 }}>
        <div className="row" style={{ gap: 8 }}>
          <span style={{ fontSize: 14, fontWeight: 700, color: 'var(--text-strong)' }}>{name}</span>
          <SmallBadge tone={tone}>{statusLabel}</SmallBadge>
        </div>
        <div style={{ fontSize: 12, color: 'var(--text-muted)', marginTop: 3 }}>{detail}</div>
      </div>
    </>
  );

  const style = {
    display: 'flex',
    alignItems: 'center',
    gap: 16,
    border: active ? `1.5px solid ${colors.border}` : '1px solid var(--border)',
    borderRadius: 'var(--r-lg)',
    background: 'var(--surface)',
    padding: '16px 18px',
    width: '100%',
    textAlign: 'left' as const,
    font: 'inherit',
    color: 'inherit',
  };

  // `variants` only resolve when a <Stagger> is above this in the tree; on
  // its own the card just renders, which is why no `initial` is set here.
  if (!onClick) return <motion.div variants={itemVariants} style={style}>{card}</motion.div>;

  return (
    <motion.button
      type="button"
      variants={itemVariants}
      style={{ ...style, cursor: 'pointer' }}
      onClick={onClick}
      whileHover={{ y: -2, borderColor: colors.border }}
      whileTap={{ scale: 0.985, y: 0 }}
      transition={SPRING}
    >
      {card}
    </motion.button>
  );
}
