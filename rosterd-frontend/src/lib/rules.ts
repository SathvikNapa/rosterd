/**
 * Flattens a manifest into the per-rule rows the Review and Contracts screens
 * render.
 *
 * Provenance is the awkward part. Ingestion's `AgentManifestEntry.constraints`
 * is a free-form object with no `source` or `confidence` per constraint
 * (rosterd-param-frontend.md gap 4), but the kernel's `GET /manifest` exposes
 * the same manifest as `list[ConstraintRule]` — field/op/value/source/
 * confidence — after adapting it through legacy_constraints.py. So: render
 * from ingestion (which always has the manifest), and overlay the kernel's
 * source/confidence when the kernel is governing that same manifest.
 *
 * Nothing is invented. A constraint with no kernel counterpart renders with
 * no confidence rather than a guessed one.
 */
import type {
  AgentManifestEntry,
  Confidence,
  ConstraintRule,
  ConstraintSource,
  KernelManifestEntry,
} from './types';

export interface InferredRule {
  key: string;
  agentId: string;
  /** The constraint's key in ingestion's object, or a synthetic marker. */
  constraintKey: string;
  /** Rendered rule text, e.g. "max_refund_usd ≤ 100". */
  label: string;
  value: unknown;
  /** Structural rules (assignability, routing) are not free-text editable. */
  editable: boolean;
  source: ConstraintSource | 'graph' | null;
  confidence: Confidence | null;
}

const ENTRY_GATE = '__entry_only_via';
const PRIOR_NODE = 'requires_prior_node';

export function rulesForAgent(
  agent: AgentManifestEntry,
  kernelEntry?: KernelManifestEntry,
): InferredRule[] {
  const rules: InferredRule[] = [];
  const kernelRules = kernelEntry?.constraints ?? [];

  for (const [constraintKey, value] of Object.entries(agent.constraints ?? {})) {
    if (value === null || value === undefined) continue;

    if (constraintKey === PRIOR_NODE) {
      rules.push({
        key: `${agent.id}:${constraintKey}`,
        agentId: agent.id,
        constraintKey,
        label: `requires prior node: ${String(value)}`,
        value,
        // kernel deliberately does NOT map this to a ConstraintRule: it is a
        // graph-ordering fact enforced at /dispatch via entry_only_via.
        editable: false,
        source: 'graph',
        confidence: null,
      });
      continue;
    }

    const match = matchKernelRule(kernelRules, value);
    rules.push({
      key: `${agent.id}:${constraintKey}`,
      agentId: agent.id,
      constraintKey,
      label: `${constraintKey} ${operatorFor(constraintKey, match?.op)} ${formatValue(value)}`,
      value,
      editable: true,
      source: match?.source ?? null,
      confidence: match?.confidence ?? null,
    });
  }

  if (!agent.direct_assignable) {
    rules.push({
      key: `${agent.id}:${ENTRY_GATE}`,
      agentId: agent.id,
      constraintKey: ENTRY_GATE,
      label: agent.entry_only_via.length
        ? `not directly assignable — entry only via ${agent.entry_only_via.join(', ')}`
        : 'not directly assignable',
      value: agent.entry_only_via,
      editable: false,
      // An agent gated behind another is what an interrupt() call in the
      // graph produces; the kernel enforces it at the /dispatch boundary.
      source: 'interrupt',
      confidence: 'high',
    });
  }

  return rules;
}

export function allRules(
  agents: AgentManifestEntry[],
  kernelEntries: KernelManifestEntry[] = [],
): InferredRule[] {
  const byId = new Map(kernelEntries.map((entry) => [entry.id, entry]));
  return agents.flatMap((agent) => rulesForAgent(agent, byId.get(agent.id)));
}

/**
 * Kernel rules carry a tool-argument field path ("tool_calls[*].args.amount_usd"),
 * not ingestion's constraint key, so they are matched on the bound value —
 * the one thing both sides agree on verbatim. Ambiguous when an agent has two
 * constraints with the same value, which is why an unmatched constraint
 * simply renders without provenance instead of being paired by position.
 */
function matchKernelRule(rules: ConstraintRule[], value: unknown): ConstraintRule | undefined {
  return rules.find((rule) => sameValue(rule.value, value));
}

function sameValue(a: unknown, b: unknown): boolean {
  if (a === b) return true;
  if (typeof a === 'number' && typeof b === 'number') return a === b;
  return String(a) === String(b);
}

function operatorFor(constraintKey: string, op?: ConstraintRule['op']): string {
  if (op === 'lte') return '≤';
  if (op === 'gte') return '≥';
  if (op === 'eq') return '=';
  if (op === 'in') return 'in';
  if (op === 'not_in') return 'not in';
  if (/^max_|_max$|_cap$/.test(constraintKey)) return '≤';
  if (/^min_|_min$/.test(constraintKey)) return '≥';
  return '=';
}

export function formatValue(value: unknown): string {
  if (Array.isArray(value)) return value.map((item) => String(item)).join(', ');
  if (typeof value === 'object' && value !== null) return JSON.stringify(value);
  return String(value);
}

/**
 * Applies a Review edit back onto the manifest entries that get POSTed to
 * /manifest/{id}/confirm. Numbers stay numbers so the kernel's adapter still
 * recognizes them as bounds.
 */
export function applyRuleEdit(
  agents: AgentManifestEntry[],
  agentId: string,
  constraintKey: string,
  raw: string,
): AgentManifestEntry[] {
  return agents.map((agent) => {
    if (agent.id !== agentId) return agent;
    return {
      ...agent,
      constraints: { ...agent.constraints, [constraintKey]: coerce(raw) },
    };
  });
}

function coerce(raw: string): unknown {
  const trimmed = raw.trim();
  if (trimmed === '') return '';
  if (trimmed === 'true') return true;
  if (trimmed === 'false') return false;
  const asNumber = Number(trimmed);
  return Number.isFinite(asNumber) && /^-?\d*\.?\d+$/.test(trimmed) ? asNumber : trimmed;
}

/** Contracts' "Spend limit" column: the first money-shaped constraint. */
export function spendLimit(agent: AgentManifestEntry): string | null {
  for (const [key, value] of Object.entries(agent.constraints ?? {})) {
    if (value === null || value === undefined) continue;
    if (/usd|amount|spend|refund|price|cost/i.test(key) && typeof value === 'number') {
      return `$${value} / action`;
    }
    if (/qty|quantity|stock|inventory/i.test(key)) {
      return `${key.replace(/^max_/, '')} ≤ ${formatValue(value)}`;
    }
  }
  return null;
}
