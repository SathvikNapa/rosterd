/**
 * Screen 1 — "Point us at your repo" (Design.html frame 1).
 *
 * Deviation from the brief, forced by the service: the brief says "repo URL
 * only, no file upload", but POST /ingest requires `constraints_yaml` today
 * (rosterd-param-frontend.md gap 1). So the frame's single URL field stays
 * primary and the YAML sits behind a disclosure with a working default —
 * the screen still opens as "paste a URL, press analyze".
 */
import { AnimatePresence, motion } from 'motion/react';
import { useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { AnimatedNumber } from '../components/AnimatedNumber';
import { GraphPreview } from '../components/GraphPreview';
import { Stagger, StaggerItem } from '../components/motion';
import { Banner, Button, Label } from '../components/ui';
import { ingest } from '../lib/api/ingestion';
import { describeError } from '../lib/api/http';
import { HOVER_LIFT, SPRING } from '../lib/motion';
import { useSession } from '../lib/session';
import type { ManifestResponse } from '../lib/types';

/**
 * The shape constraints_loader.py actually accepts: a `constraints` mapping
 * keyed by GRAPH NODE NAME. `direct_assignable` and `entry_only_via` are
 * INFERRED when omitted here (rosterd-ingestion/domain/manifest.py: defaults
 * to `not node.has_interrupt`, via a real AST scan for a call named
 * `interrupt` in the node's function body -- astscan.calls_interrupt -- and
 * `entry_only_via` infers from conditional graph edges into a gated node).
 * So an empty `constraints: {}` block is a genuinely valid starting point
 * for ANY repo -- nothing here is pre-filled to one specific example
 * anymore. Add a business-number cap (`max_qty`, `max_refund_usd`, ...)
 * after analyzing once, keyed by whatever node names the discovery preview
 * on the right actually shows for the repo you pointed this at.
 *
 * A constraint keyed by a node name the discovered graph doesn't have is a
 * hard 422 (constraints_unknown_nodes), not a warning -- fail closed is the
 * documented, correct behavior (README.md "Constraints file").
 */
const DEFAULT_CONSTRAINTS_YAML = `# constraints.yaml — merged over what discovery infers from the code.
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

export function Ingest() {
  const navigate = useNavigate();
  const session = useSession();

  const [repoUrl, setRepoUrl] = useState(session.repoUrl || '');
  const [constraintsYaml, setConstraintsYaml] = useState(DEFAULT_CONSTRAINTS_YAML);
  // Which repo URL the navigator last hand-edited constraints.yaml FOR --
  // null means "not hand-edited since this repo URL was set". Scoped to the
  // URL, not a plain once-true-forever boolean: a boolean here meant a
  // single edit (even just poking at the box out of curiosity) permanently
  // stopped the box from re-syncing to whatever DIFFERENT repo got typed in
  // next -- confirmed live, pointing this screen at react-agent while a
  // previous repo's constraints (keyed by node names react-agent doesn't
  // have) were still sitting in the box produced a hard 422
  // constraints_unknown_nodes before discovery's result could ever reach
  // the Review screen, which reads as "no agents found" with no indication
  // why. Comparing against the CURRENT repoUrl means switching repos always
  // re-syncs the box, unless you've specifically edited it for that exact URL.
  const [editedForUrl, setEditedForUrl] = useState<string | null>(null);
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

  const stats = result
    ? {
        agents: result.agents.length,
        tools: result.agents.reduce((sum, agent) => sum + agent.tools.length, 0),
        gates: result.agents.filter((agent) => !agent.direct_assignable).length,
      }
    : null;

  return (
    <main className="page" style={{ flexDirection: 'row', gap: 48, alignItems: 'flex-start', padding: 64 }}>
      <Stagger style={{ width: 600, flexShrink: 0, display: 'flex', flexDirection: 'column', gap: 24 }}>
        <StaggerItem className="page__head">
          <h1>Point us at your repo</h1>
          <p style={{ fontSize: 15, maxWidth: 480, lineHeight: 1.6 }}>
            No constraints file to write. We read your agents, their tools, and the rules that already live in
            your code.
          </p>
        </StaggerItem>

        <StaggerItem className="field">
          <label className="label" htmlFor="repo">
            Repository URL
          </label>
          <motion.input
            id="repo"
            className="input"
            type="text"
            value={repoUrl}
            onChange={(event) => {
              const nextUrl = event.target.value;
              setRepoUrl(nextUrl);
              // Keep the constraints default matched to whichever repo is
              // typed in, unless the navigator specifically edited the box
              // for THIS exact URL already -- see editedForUrl's comment
              // above. Checked against nextUrl directly (not against the
              // not-yet-updated `repoUrl` still in this closure).
              if (editedForUrl !== nextUrl) {
                setConstraintsYaml(DEFAULT_CONSTRAINTS_YAML);
              }
            }}
            placeholder="https://github.com/you/your-agent-repo"
            spellCheck={false}
            whileFocus={{ borderColor: 'var(--accent)' }}
            transition={SPRING}
          />
        </StaggerItem>

        <StaggerItem className="field">
          <motion.button
            type="button"
            className="linkish"
            onClick={() => setShowYaml((open) => !open)}
            whileTap={{ scale: 0.98 }}
          >
            <motion.span
              aria-hidden="true"
              style={{ display: 'inline-block', marginRight: 6 }}
              animate={{ rotate: showYaml ? 90 : 0 }}
              transition={SPRING}
            >
              ▸
            </motion.span>
            {showYaml ? 'Hide constraints.yaml' : 'constraints.yaml (required by the service today)'}
          </motion.button>
          <AnimatePresence initial={false}>
            {showYaml && (
              <motion.div
                key="yaml-disclosure"
                initial={{ height: 0, opacity: 0 }}
                animate={{ height: 'auto', opacity: 1 }}
                exit={{ height: 0, opacity: 0 }}
                transition={{ height: SPRING, opacity: { duration: 0.2 } }}
                style={{ overflow: 'hidden' }}
              >
                <textarea
                  className="textarea"
                  value={constraintsYaml}
                  onChange={(event) => {
                    setEditedForUrl(repoUrl);
                    setConstraintsYaml(event.target.value);
                  }}
                  spellCheck={false}
                  aria-label="constraints.yaml"
                  style={{ marginTop: 10 }}
                />
                <span className="muted">
                  POST /ingest requires this field, but an empty <code>constraints: {'{}'}</code> block is valid —
                  <code>direct_assignable</code> and <code>entry_only_via</code> are inferred from the code
                  automatically. Just make sure any node name you <em>do</em> add here (for a business-number cap
                  like <code>max_qty</code>) matches a node this repo actually has, or ingest fails with{' '}
                  <code>constraints_unknown_nodes</code> — analyze once first to see the real names on the right.
                </span>
              </motion.div>
            )}
          </AnimatePresence>
        </StaggerItem>

        <StaggerItem>
          <Button
            className="btn--lg"
            style={{ alignSelf: 'flex-start' }}
            onClick={analyze}
            disabled={busy || !repoUrl.trim()}
          >
            <AnimatePresence mode="wait" initial={false}>
              {busy ? (
                <motion.span
                  key="busy"
                  className="row"
                  style={{ gap: 8 }}
                  initial={{ opacity: 0 }}
                  animate={{ opacity: 1 }}
                  exit={{ opacity: 0 }}
                >
                  <motion.span
                    aria-hidden="true"
                    style={{
                      width: 12,
                      height: 12,
                      borderRadius: '50%',
                      border: '2px solid rgba(255,255,255,0.4)',
                      borderTopColor: '#fff',
                      display: 'inline-block',
                    }}
                    animate={{ rotate: 360 }}
                    transition={{ duration: 0.7, repeat: Infinity, ease: 'linear' }}
                  />
                  Analyzing…
                </motion.span>
              ) : (
                <motion.span key="idle" initial={{ opacity: 0 }} animate={{ opacity: 1 }} exit={{ opacity: 0 }}>
                  Analyze repository →
                </motion.span>
              )}
            </AnimatePresence>
          </Button>
        </StaggerItem>

        {error && <Banner tone="danger">{error}</Banner>}

        <StaggerItem style={{ display: 'flex', flexDirection: 'column', gap: 10, marginTop: 8 }}>
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
            <motion.div
              key={index}
              className="row"
              style={{ gap: 10, fontSize: 13, color: 'var(--text-body)' }}
              whileHover={{ x: 3, color: 'var(--text)' }}
              transition={SPRING}
            >
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
            </motion.div>
          ))}
        </StaggerItem>
      </Stagger>

      <section style={{ flex: 1, display: 'flex', alignItems: 'center', justifyContent: 'center' }}>
        <motion.div
          className="card card--xl"
          style={{ width: '100%', maxWidth: 540, padding: 32 }}
          initial={{ opacity: 0, scale: 0.96, y: 14 }}
          animate={{ opacity: 1, scale: 1, y: 0 }}
          transition={{ ...SPRING, delay: 0.1 }}
          whileHover={result ? HOVER_LIFT : undefined}
        >
          <div className="label" style={{ marginBottom: 24, display: 'block' }}>
            Discovery preview
          </div>
          <GraphPreview graph={result?.graph ?? null} />
          <div style={{ marginTop: 20, fontSize: 13, color: 'var(--text-muted)' }}>
            {stats ? (
              <span className="row" style={{ gap: 6, flexWrap: 'wrap' }}>
                <AnimatedNumber value={stats.agents} format={(n) => `${n} agent${n === 1 ? '' : 's'} found`} />
                <span aria-hidden="true">·</span>
                <AnimatedNumber value={stats.tools} format={(n) => `${n} tool schema${n === 1 ? '' : 's'} parsed`} />
                <span aria-hidden="true">·</span>
                <AnimatedNumber
                  value={stats.gates}
                  format={(n) => `${n} approval gate${n === 1 ? '' : 's'} detected`}
                />
              </span>
            ) : (
              'Nothing analyzed yet.'
            )}
          </div>
        </motion.div>
      </section>
    </main>
  );
}
