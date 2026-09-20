/**
 * The Ingest frame's "Discovery preview": the discovered graph drawn as
 * circles on a line. Laid out from the real GraphSpec rather than the three
 * hard-coded nodes in the frame — the same zig-zag, any node count.
 *
 * Animated on purpose, not decoratively: this is the first thing anyone
 * sees after clicking Analyze, the moment the pitch ("we read your agents
 * straight out of the code") has to land. An edge draws itself in (a real
 * path-length reveal, not a fade) before its two nodes pop in, so the
 * sequence reads as "the graph is being traced", not "a picture appeared".
 */
import { motion, useReducedMotion } from 'motion/react';
import { useState } from 'react';
import type { GraphSpec } from '../lib/types';
import { EASE_OUT, POP } from '../lib/motion';

const WIDTH = 480;
const HEIGHT = 220;
const RADIUS = 34;

export function GraphPreview({ graph }: { graph: GraphSpec | null }) {
  const reduce = useReducedMotion();
  const [hovered, setHovered] = useState<string | null>(null);
  const nodes = graph?.nodes ?? [];
  if (nodes.length === 0) {
    return (
      <div style={{ height: HEIGHT, display: 'grid', placeItems: 'center', color: 'var(--text-faint)', fontSize: 13 }}>
        No graph discovered yet.
      </div>
    );
  }

  const positions = layout(nodes);
  const edges = (graph?.edges ?? []).filter(
    (edge) => positions.has(edge.source) && positions.has(edge.target),
  );
  const neighbors = hovered ? neighborSet(hovered, edges) : null;

  return (
    <svg
      width="100%"
      height={HEIGHT}
      viewBox={`0 0 ${WIDTH} ${HEIGHT}`}
      role="img"
      aria-label={`Discovered agent graph: ${nodes.join(', ')}`}
      // A re-ingest reuses the same node names, so a `key` on the graph's
      // own shape forces the whole tree to remount and replay the reveal --
      // otherwise Analyze-ing a second repo would show the old graph
      // instantly swapped for the new one, no trace-in at all.
      key={nodes.join('>') + edges.map((e) => `${e.source}-${e.target}`).join(',')}
    >
      {edges.map((edge, index) => {
        const from = positions.get(edge.source)!;
        const to = positions.get(edge.target)!;
        const dimmed = neighbors && !(neighbors.has(edge.source) && neighbors.has(edge.target));
        return (
          <motion.line
            key={`${edge.source}-${edge.target}-${index}`}
            x1={from.x}
            y1={from.y}
            x2={to.x}
            y2={to.y}
            stroke="var(--border)"
            strokeWidth={dimmed ? 2 : 2.5}
            strokeDasharray={edge.condition ? '5 4' : undefined}
            initial={reduce ? { opacity: 0 } : { pathLength: 0, opacity: 0 }}
            animate={{
              pathLength: 1,
              opacity: dimmed ? 0.35 : 1,
              stroke: dimmed ? 'var(--border)' : hovered ? 'var(--accent-mid)' : 'var(--border)',
            }}
            transition={
              reduce
                ? { duration: 0.2 }
                : {
                    pathLength: { duration: 0.5, delay: 0.05 * index, ease: EASE_OUT },
                    opacity: { duration: 0.3, delay: 0.05 * index },
                    stroke: { duration: 0.2 },
                  }
            }
          />
        );
      })}
      {nodes.map((node, index) => {
        const point = positions.get(node)!;
        const isHovered = hovered === node;
        const dimmed = neighbors && !neighbors.has(node);
        return (
          <motion.g
            key={node}
            onHoverStart={() => setHovered(node)}
            onHoverEnd={() => setHovered((current) => (current === node ? null : current))}
            style={{ cursor: 'default' }}
            initial={reduce ? { opacity: 0 } : { opacity: 0, scale: 0.4 }}
            animate={{ opacity: dimmed ? 0.4 : 1, scale: isHovered ? 1.08 : 1 }}
            transition={
              reduce
                ? { duration: 0.2, delay: 0.03 * index }
                : { ...POP, delay: 0.5 + 0.07 * index, opacity: { duration: 0.25 } }
            }
          >
            <circle
              cx={point.x}
              cy={point.y}
              r={RADIUS}
              fill="var(--surface)"
              stroke={isHovered ? 'var(--accent)' : 'var(--accent-mid)'}
              strokeWidth={isHovered ? 3 : 2}
            />
            <text
              x={point.x}
              y={point.y + 4}
              textAnchor="middle"
              fill="var(--text)"
              fontSize={fontFor(nodeLabel(node))}
              fontFamily="IBM Plex Mono, monospace"
            >
              <title>{node}</title>
              {nodeLabel(node)}
            </text>
          </motion.g>
        );
      })}
    </svg>
  );
}

/** Everything one hop from `node`, itself included -- used to dim the rest
 * of the graph on hover so "who talks to whom" reads at a glance. */
function neighborSet(node: string, edges: GraphSpec['edges']): Set<string> {
  const set = new Set([node]);
  for (const edge of edges) {
    if (edge.source === node) set.add(edge.target);
    if (edge.target === node) set.add(edge.source);
  }
  return set;
}

/** Alternating high/low across the width, as the frame draws it. */
function layout(nodes: string[]): Map<string, { x: number; y: number }> {
  const positions = new Map<string, { x: number; y: number }>();
  const step = nodes.length === 1 ? 0 : (WIDTH - 180) / (nodes.length - 1);
  nodes.forEach((node, index) => {
    positions.set(node, {
      x: nodes.length === 1 ? WIDTH / 2 : 90 + step * index,
      y: index % 2 === 0 ? 60 : 120,
    });
  });
  return positions;
}

/**
 * Keep the whole node name when it fits the 68px circle at a readable size;
 * otherwise truncate. The full name is always in the <title>, so nothing is
 * lost to the shortening.
 */
function nodeLabel(node: string): string {
  const short = node.replace(/_(node|agent)$/, '');
  return short.length > 13 ? `${short.slice(0, 12)}…` : short;
}

function fontFor(label: string): number {
  if (label.length <= 7) return 11;
  if (label.length <= 10) return 9;
  return 8;
}
