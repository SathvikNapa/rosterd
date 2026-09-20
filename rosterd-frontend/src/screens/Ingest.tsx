/**
 * Screen 1 — "Point us at your repo" (Design.html frame 1).
 *
 * Deviation from the brief, forced by the service: the brief says "repo URL
 * only, no file upload", but POST /ingest requires `constraints_yaml` today
 * (rosterd-param-frontend.md gap 1). So the frame's single URL field stays
 * primary and the YAML sits behind a disclosure with a working default —
 * the screen still opens as "paste a URL, press analyze".
 */
import { useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { GraphPreview } from '../components/GraphPreview';
import { Banner, Label } from '../components/ui';
import { ingest } from '../lib/api/ingestion';
import { describeError } from '../lib/api/http';
import { isDemo } from '../lib/config';
import { useSession } from '../lib/session';
import type { ManifestResponse } from '../lib/types';

/**
 * The shape constraints_loader.py actually accepts: a `constraints` mapping
 * keyed by GRAPH NODE NAME. An empty mapping is legal syntactically, but NOT
 * a safe default: `direct_assignable` is never inferred from the code, only
 * ever read from this YAML (rosterd-kernel/manifest.py:
 * `direct_assignable = bool(block.direct_assignable) if ... else False` --
 * there is no AST-based fallback, despite `refund_node.py`'s own docstring
 * describing interrupt() as "the signal ingestion reads"). An empty mapping
 * submitted through this screen means every agent comes back
 * direct_assignable: false, entry_only_via: [] -- unreachable, and Ask has
 * nothing to propose against. Confirmed live: reproduced the exact bug
 * report ("No agent in this manifest is directly assignable") by ingesting
 * with `constraints: {}` against the real repo below.
 *
 * So the default here is the real constraints.yaml for
 * https://github.com/SathvikNapa/rosterd-example (this screen's own default
 * repo URL) -- verified against the live service to produce
 * order_intake/fulfillment: direct_assignable true,
 * refund_exception: direct_assignable false + entry_only_via [order_intake].
 * Point this screen at a different repo and this YAML needs updating too;
 * it isn't inferred from the URL.
 */
const DEFAULT_CONSTRAINTS_YAML = `# constraints.yaml — merged over what static analysis infers.
# Keys under \`constraints\` must be node names from the graph.
# direct_assignable is NOT inferred from the code -- set it explicitly per
# node or nothing here will be dispatchable. This is rosterd-example's real
# constraints.yaml (https://github.com/SathvikNapa/rosterd-example).
version: 1
constraints:
  order_intake:
    purpose: Classifies an incoming order as standard, high-value, or fraud-flagged
    direct_assignable: true
  fulfillment:
    purpose: Reserves inventory for a standard/high-value order
    direct_assignable: true
    max_qty: 50
  refund_exception:
    purpose: Issues refunds; calls interrupt() for fraud-flagged/high-value orders
    direct_assignable: false
    entry_only_via: [order_intake]
    max_refund_usd: 100
`;

export function Ingest() {
  const navigate = useNavigate();
  const session = useSession();

  // https://github.com/SathvikNapa/rosterd-example is a real, public mirror
  // of rosterd-demo-agent -- a clean `git clone` away, unlike the acme
  // placeholder this used to default to (which doesn't exist and made
  // ingest fail with repo_fetch_failed on first load). See
  // TeamPieces/rosterd-param-frontend.md's Ingest gap.
  const [repoUrl, setRepoUrl] = useState(session.repoUrl || 'https://github.com/SathvikNapa/rosterd-example');
  const [constraintsYaml, setConstraintsYaml] = useState(DEFAULT_CONSTRAINTS_YAML);
  const [showYaml, setShowYaml] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [result, setResult] = useState<ManifestResponse | null>(session.manifest);

  const analyze = async () => {
    setBusy(true);
    setError(null);
    try {
      const manifest = await ingest(repoUrl.trim(), constraintsYaml);
      setResult(manifest);
      session.setRepoUrl(repoUrl.trim());
      session.setDraftManifestId(manifest.manifest_id);
      session.setConfirmedManifestId(manifest.status === 'confirmed' ? manifest.manifest_id : null);
      session.setManifest(manifest);
      navigate('/review');
    } catch (cause) {
      setError(describeError(cause));
    } finally {
      setBusy(false);
    }
  };

  const summary = result
    ? `${result.agents.length} agent${result.agents.length === 1 ? '' : 's'} found · ` +
      `${result.agents.reduce((sum, agent) => sum + agent.tools.length, 0)} tool schemas parsed · ` +
      `${result.agents.filter((agent) => !agent.direct_assignable).length} approval gate` +
      `${result.agents.filter((agent) => !agent.direct_assignable).length === 1 ? '' : 's'} detected`
    : 'Nothing analyzed yet.';

  return (
    <main className="page" style={{ flexDirection: 'row', gap: 48, alignItems: 'flex-start', padding: 64 }}>
      <section style={{ width: 600, flexShrink: 0, display: 'flex', flexDirection: 'column', gap: 24 }}>
        <div className="page__head">
          <h1>Point us at your repo</h1>
          <p style={{ fontSize: 15, maxWidth: 480, lineHeight: 1.6 }}>
            No constraints file to write. We read your agents, their tools, and the rules that already live in
            your code.
          </p>
        </div>

        <div className="field">
          <label className="label" htmlFor="repo">
            Repository URL
          </label>
          <input
            id="repo"
            className="input"
            type="text"
            value={repoUrl}
            onChange={(event) => setRepoUrl(event.target.value)}
            placeholder="https://github.com/SathvikNapa/rosterd-example"
            spellCheck={false}
          />
        </div>

        <div className="field">
          <button type="button" className="linkish" onClick={() => setShowYaml((open) => !open)}>
            {showYaml ? 'Hide constraints.yaml' : 'constraints.yaml (required by the service today)'}
          </button>
          {showYaml && (
            <>
              <textarea
                className="textarea"
                value={constraintsYaml}
                onChange={(event) => setConstraintsYaml(event.target.value)}
                spellCheck={false}
                aria-label="constraints.yaml"
              />
              <span className="muted">
                POST /ingest requires this field. Discovery still infers tools and edges on its own, but{' '}
                <code>direct_assignable</code> comes from here — with an empty <code>constraints</code> block
                no agent is directly assignable, and the Ask screen will have nothing to propose.
              </span>
            </>
          )}
        </div>

        <button
          type="button"
          className="btn btn--lg"
          style={{ alignSelf: 'flex-start' }}
          onClick={analyze}
          disabled={busy || !repoUrl.trim() || isDemo}
          title={isDemo ? 'Demo mode: no ingestion service is being called.' : undefined}
        >
          {busy ? 'Analyzing…' : 'Analyze repository →'}
        </button>

        {isDemo && <Banner tone="info">Demo mode — the fixtures from the Figma frames are already loaded.</Banner>}
        {error && <Banner tone="danger">{error}</Banner>}

        <div style={{ display: 'flex', flexDirection: 'column', gap: 10, marginTop: 8 }}>
          <Label>What we read directly from code</Label>
          {[
            <>Tool argument schemas — typed limits become constraints</>,
            <>Conditional edges — who can route to whom</>,
            <>
              <code style={{ background: 'var(--surface-muted)', padding: '1px 5px', borderRadius: 4 }}>
                interrupt()
              </code>{' '}
              calls — which agents need approval
            </>,
          ].map((line, index) => (
            <div key={index} className="row" style={{ gap: 10, fontSize: 13, color: 'var(--text-body)' }}>
              <span
                aria-hidden="true"
                style={{
                  width: 6,
                  height: 6,
                  borderRadius: '50%',
                  background: 'var(--accent)',
                  flexShrink: 0,
                }}
              />
              {line}
            </div>
          ))}
        </div>
      </section>

      <section style={{ flex: 1, display: 'flex', alignItems: 'center', justifyContent: 'center' }}>
        <div className="card card--xl" style={{ width: '100%', maxWidth: 540, padding: 32 }}>
          <div className="label" style={{ marginBottom: 24, display: 'block' }}>
            Discovery preview
          </div>
          <GraphPreview graph={result?.graph ?? null} />
          <div style={{ marginTop: 20, fontSize: 13, color: 'var(--text-muted)' }}>{summary}</div>
        </div>
      </section>
    </main>
  );
}
