/**
 * The Ingest frame's "Discovery preview": the discovered graph drawn as
 * circles on a line. Laid out from the real GraphSpec rather than the three
 * hard-coded nodes in the frame — the same zig-zag, any node count.
 */
import type { GraphSpec } from '../lib/types';

const WIDTH = 480;
const HEIGHT = 220;
const RADIUS = 34;

export function GraphPreview({ graph }: { graph: GraphSpec | null }) {
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

  return (
    <svg
      width="100%"
      height={HEIGHT}
      viewBox={`0 0 ${WIDTH} ${HEIGHT}`}
      role="img"
      aria-label={`Discovered agent graph: ${nodes.join(', ')}`}
    >
      {edges.map((edge, index) => {
        const from = positions.get(edge.source)!;
        const to = positions.get(edge.target)!;
        return (
          <line
            key={`${edge.source}-${edge.target}-${index}`}
            x1={from.x}
            y1={from.y}
            x2={to.x}
            y2={to.y}
            stroke="var(--border)"
            strokeWidth={2}
            strokeDasharray={edge.condition ? '5 4' : undefined}
          />
        );
      })}
      {nodes.map((node) => {
        const point = positions.get(node)!;
        return (
          <g key={node}>
            <circle cx={point.x} cy={point.y} r={RADIUS} fill="var(--surface)" stroke="var(--accent-mid)" strokeWidth={2} />
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
          </g>
        );
      })}
    </svg>
  );
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
