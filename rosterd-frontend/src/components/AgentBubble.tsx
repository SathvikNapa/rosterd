/**
 * The circular agent bubble the frames use everywhere, with the stacked
 * pod-count badge when a pool has scaled past one instance ("Fulfillment ×3").
 *
 * The count animates on change rather than swapping the number — the product
 * spec calls this out as a live demo moment, not a detail.
 */
import { useEffect, useRef, useState } from 'react';
import { initial, palette } from '../lib/format';
import type { Tone } from '../lib/format';
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
  const bumped = useBump(replicas);

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
      {replicas > 1 && (
        <span
          className={bumped ? 'bubble__count bubble__count--bump' : 'bubble__count'}
          style={{ background: colors.border, color: '#fff' }}
          aria-label={`${replicas} instances running`}
        >
          ×{replicas}
        </span>
      )}
    </div>
  );
}

/** True for one animation frame window after `value` changes. */
function useBump(value: number): boolean {
  const previous = useRef(value);
  const [bumped, setBumped] = useState(false);

  useEffect(() => {
    if (previous.current === value) return;
    previous.current = value;
    setBumped(true);
    const timer = window.setTimeout(() => setBumped(false), 600);
    return () => window.clearTimeout(timer);
  }, [value]);

  return bumped;
}
