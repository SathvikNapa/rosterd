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
 * keyed by GRAPH NODE NAME. `direct_assignable` and `entry_only_via` are now
 * INFERRED when omitted here (rosterd-ingestion/manifest.py: defaults to
 * `not node.has_interrupt`, via a real AST scan for a call named `interrupt`
 * in the node's function body -- astscan.calls_interrupt -- and
 * `entry_only_via` infers from conditional graph edges into a gated node).
 * That's new: it used to be a hard `direct_assignable: false` for any node
 * with no explicit YAML entry, which meant an empty `constraints: {}`
 * submitted through this screen made every agent unreachable and Ask had
 * nothing to propose against (confirmed live: reproduced the exact bug
 * report, "No agent in this manifest is directly assignable", this way).
 *
 * What's still NOT inferred: numeric caps (`max_qty`, `max_refund_usd`)
 * mirror real `Field(le=...)` limits on the tool schemas, but nothing reads
 * those schemas yet -- they still have to be typed here. So the minimal
 * correct YAML for a repo is just the business numbers, nothing else; this
 * default is rosterd-example's real ones, verified live against the running
 * service to produce the exact same contract as the old fully-spelled-out
 * version (order_intake/fulfillment assignable, refund_exception gated with
 * entry_only_via: [order_intake]) with none of direct_assignable,
 * entry_only_via, or purpose typed by hand.
 */
const ROSTERD_EXAMPLE_URL = 'https://github.com/SathvikNapa/rosterd-example';

const ROSTERD_EXAMPLE_CONSTRAINTS_YAML = `# constraints.yaml — merged over what discovery infers from the code.
# direct_assignable and entry_only_via are now inferred (interrupt() +
# graph edges) when omitted -- only the business numbers below still need a
# human. This is rosterd-example's real repo
# (${ROSTERD_EXAMPLE_URL}).
version: 1
constraints:
  fulfillment:
    max_qty: 50
  refund_exception:
    max_refund_usd: 100
`;

// Any OTHER repo — the node names above are rosterd-example's own, and a
// constraint keyed by a node name that doesn't exist in the discovered
// graph is a hard 422 (constraints_unknown_nodes), not a warning: fail
// closed is the documented, correct behavior (README.md "Constraints
// file"). Confirmed live: pointing this screen at a real public repo
// (langchain-ai/react-agent, nodes call_model/tools) while still holding
// the rosterd-example default produced exactly that error. direct_assignable
// and entry_only_via are inferred automatically regardless of repo, so an
// empty constraints block is a genuinely valid starting point — add
// business-number caps here, keyed by whatever node names the discovery
// preview on the right actually shows, once you see them.
const GENERIC_CONSTRAINTS_YAML = `# constraints.yaml — merged over what discovery infers from the code.
# direct_assignable and entry_only_via are inferred automatically (interrupt()
# calls + graph edges) -- nothing required here for that. Add a business-rule
# cap after analyzing once you can see the discovered node names on the right,
# e.g.:
# constraints:
#   my_node:
#     max_qty: 50
version: 1
constraints: {}
`;

function defaultConstraintsFor(repoUrl: string): string {
  return repoUrl.trim() === ROSTERD_EXAMPLE_URL ? ROSTERD_EXAMPLE_CONSTRAINTS_YAML : GENERIC_CONSTRAINTS_YAML;
}

export function Ingest() {
  const navigate = useNavigate();
  const session = useSession();

  // https://github.com/SathvikNapa/rosterd-example is a real, public mirror
  // of rosterd-demo-agent -- a clean `git clone` away, unlike the acme
  // placeholder this used to default to (which doesn't exist and made
  // ingest fail with repo_fetch_failed on first load). See
  // TeamPieces/rosterd-param-frontend.md's Ingest gap.
  const [repoUrl, setRepoUrl] = useState(session.repoUrl || ROSTERD_EXAMPLE_URL);
  const [constraintsYaml, setConstraintsYaml] = useState(defaultConstraintsFor(repoUrl));
  // Only true once the navigator has typed into the constraints box
  // directly -- until then, switching the repo URL keeps the constraints
  // default in sync with it (rosterd-example's real numbers for that one
  // repo, an empty-but-valid block for anything else) instead of silently
  // carrying rosterd-example's node names over to a repo that doesn't have
  // them.
  const [constraintsEdited, setConstraintsEdited] = useState(false);
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
            onChange={(event) => {
              const nextUrl = event.target.value;
              setRepoUrl(nextUrl);
              // Keep the constraints default matched to whichever repo is
              // typed in, right up until the navigator edits the box
              // themselves -- see constraintsEdited's comment above.
              if (!constraintsEdited) {
                setConstraintsYaml(defaultConstraintsFor(nextUrl));
              }
            }}
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
                onChange={(event) => {
                  setConstraintsEdited(true);
                  setConstraintsYaml(event.target.value);
                }}
                spellCheck={false}
                aria-label="constraints.yaml"
              />
              <span className="muted">
                POST /ingest requires this field, but an empty <code>constraints: {'{}'}</code> block is valid —
                <code>direct_assignable</code> and <code>entry_only_via</code> are inferred from the code
                automatically. Just make sure any node name you <em>do</em> add here (for a business-number cap
                like <code>max_qty</code>) matches a node this repo actually has, or ingest fails with{' '}
                <code>constraints_unknown_nodes</code> — analyze once first to see the real names on the right.
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
