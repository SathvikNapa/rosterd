/**
 * The marketing landing page — the one screen that renders outside
 * <AppShell> (no nav bar, no live-data chrome), reached at "/". Its job is
 * one pitch and one CTA into the real app ("/ingest"), not a live view of
 * anything.
 *
 * Every animation here is real motion/react (the same library — and the
 * same lib/motion.ts vocabulary — every other screen uses), not a CSS
 * approximation: the hero copy staggers in with EASE_OUT, the hero graph
 * reuses the actual <GraphPreview> path-draw component from Ingest, the
 * stat counters ride AnimatedNumber's spring, and the feature/proof cards
 * and the scale bar reveal via useInView the first time they're scrolled
 * into frame.
 *
 * The two "proof" cards are not illustrative copy — the replica curve and
 * the killed-run numbers are from a real run of this stack (a Black Friday
 * load test and a real over-cap payment dispatch), not invented stats.
 */
import { motion, useInView } from 'motion/react';
import type { ReactNode } from 'react';
import { useRef } from 'react';
import { Link } from 'react-router-dom';
import { AnimatedNumber } from '../components/AnimatedNumber';
import { GraphPreview } from '../components/GraphPreview';
import { Badge } from '../components/ui';
import { EASE_OUT, HOVER_LIFT, LIVE_PULSE, TAP_PRESS } from '../lib/motion';
import type { GraphSpec } from '../lib/types';
import './Landing.css';

const MotionLink = motion(Link);
const MotionAnchor = motion.a;

/** The real confirmed-manifest topology (order_intake's two routes), not a
 * placeholder graph — matches rosterd-kernel/tests/test_app.py's
 * test_graph_endpoint exactly. */
const HERO_GRAPH: GraphSpec = {
  nodes: ['order_intake', 'fulfillment', 'refund_exception'],
  edges: [
    { source: 'order_intake', target: 'fulfillment', condition: 'not_flagged' },
    { source: 'order_intake', target: 'refund_exception', condition: 'fraud_flagged' },
  ],
};

export function Landing() {
  return (
    <div style={{ width: '100%', minHeight: '100vh', background: 'var(--bg)', color: 'var(--text)', fontFamily: 'var(--font-sans)' }}>
      {/* NAV */}
      <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', maxWidth: 1120, margin: '0 auto', padding: '30px 24px 0' }}>
        <span style={{ fontFamily: 'var(--font-mono)', fontWeight: 600, fontSize: 15, letterSpacing: '-0.01em', color: 'var(--text-strong)' }}>
          rosterd
        </span>
        <div style={{ display: 'flex', alignItems: 'center', gap: 16 }}>
          <div style={{ display: 'flex', alignItems: 'center', gap: 8, fontFamily: 'var(--font-mono)', fontSize: 11, textTransform: 'uppercase', letterSpacing: '0.08em', color: 'var(--ok)' }}>
            <motion.span
              aria-hidden="true"
              animate={LIVE_PULSE}
              style={{ width: 7, height: 7, borderRadius: '50%', background: 'var(--ok)', display: 'inline-block' }}
            />
            enforced, not simulated
          </div>
          <Link to="/ingest" className="linkish" style={{ fontFamily: 'var(--font-mono)', fontSize: 12.5, fontWeight: 600, color: 'var(--accent-dark)' }}>
            Launch the app →
          </Link>
        </div>
      </div>

      {/* HERO */}
      <div className="landing__hero-grid" style={{ maxWidth: 1120, margin: '0 auto', padding: '64px 24px 32px' }}>
        <div>
          <motion.div
            initial={{ opacity: 0, y: 10 }}
            animate={{ opacity: 1, y: 0 }}
            transition={{ duration: 0.5, ease: EASE_OUT }}
            style={{ fontFamily: 'var(--font-mono)', fontSize: 12, letterSpacing: '0.14em', textTransform: 'uppercase', color: 'var(--accent-dark)', fontWeight: 600, marginBottom: 16 }}
          >
            contract-enforced ai agents
          </motion.div>
          <motion.h1
            className="landing__hero-title"
            initial={{ opacity: 0, y: 16 }}
            animate={{ opacity: 1, y: 0 }}
            transition={{ duration: 0.6, delay: 0.08, ease: EASE_OUT }}
            style={{ fontSize: 50, lineHeight: 1.1, fontWeight: 700, letterSpacing: '-0.02em', margin: '0 0 18px', color: 'var(--text-strong)' }}
          >
            If your agent tried something it shouldn't,{' '}
            <span style={{ color: 'var(--accent-dark)' }}>what actually stops it?</span>
          </motion.h1>
          <motion.p
            initial={{ opacity: 0, y: 12 }}
            animate={{ opacity: 1, y: 0 }}
            transition={{ duration: 0.6, delay: 0.16, ease: EASE_OUT }}
            style={{ fontSize: 17, lineHeight: 1.6, color: 'var(--text-body)', maxWidth: '46ch', margin: '0 0 30px' }}
          >
            rosterd reads your agent's own code, turns it into an enforced contract, and kills anything that
            breaks it. Live, not after the fact.
          </motion.p>
          <motion.div
            initial={{ opacity: 0, y: 10 }}
            animate={{ opacity: 1, y: 0 }}
            transition={{ duration: 0.5, delay: 0.26, ease: EASE_OUT }}
            style={{ display: 'flex', gap: 14, flexWrap: 'wrap' }}
          >
            <MotionLink
              to="/ingest"
              className="btn btn--lg"
              whileHover={HOVER_LIFT}
              whileTap={TAP_PRESS}
              style={{ textDecoration: 'none' }}
            >
              See it enforce a violation
            </MotionLink>
            <MotionAnchor
              href="#how"
              className="btn btn--lg btn--ghost"
              whileHover={HOVER_LIFT}
              whileTap={TAP_PRESS}
              style={{ textDecoration: 'none' }}
            >
              How it works
            </MotionAnchor>
          </motion.div>
        </div>

        <div style={{ background: 'var(--surface)', border: '1px solid var(--border)', borderRadius: 16, padding: 22 }}>
          <GraphPreview graph={HERO_GRAPH} />
          <div style={{ fontFamily: 'var(--font-mono)', fontSize: 11, color: 'var(--text-muted)', marginTop: 4, textAlign: 'center' }}>
            discovered straight from the running LangGraph, not typed by hand
          </div>
        </div>
      </div>

      {/* REFRAME */}
      <div style={{ maxWidth: 780, margin: '40px auto 0', padding: '0 24px', textAlign: 'center' }}>
        <p style={{ fontSize: 22, lineHeight: 1.5, fontWeight: 600, color: 'var(--text-strong)', margin: 0 }}>
          You don't review what an agent <em>might</em> do. You review the contract it{' '}
          <span style={{ color: 'var(--accent-dark)', fontStyle: 'normal' }}>can't</span> break.
        </p>
      </div>

      {/* STATS */}
      <div className="landing__stats-grid" style={{ maxWidth: 1120, margin: '56px auto 0', padding: '0 24px' }}>
        <Stat target={5} label="agents under one contract" />
        <Stat target={10} suffix="×" label="replica ceiling, real load" />
        <Stat target={100} prefix="$" label="refund cap, at the boundary" />
        <Stat target={2000} prefix="$" label="payment cap, breach it and watch" />
      </div>

      {/* HOW IT WORKS */}
      <div id="how" style={{ maxWidth: 1120, margin: '80px auto 0', padding: '0 24px' }}>
        <div style={{ fontFamily: 'var(--font-mono)', fontSize: 12, letterSpacing: '0.12em', textTransform: 'uppercase', color: 'var(--accent-dark)', fontWeight: 600, marginBottom: 10 }}>
          how it works
        </div>
        <h2 style={{ fontSize: 30, fontWeight: 700, color: 'var(--text-strong)', margin: '0 0 36px', letterSpacing: '-0.01em' }}>
          Discover the contract. Confirm it. Enforce it, live.
        </h2>
        <div className="landing__feature-grid">
          <Feature
            step="01 · discover"
            title="Read the real code"
            body="Point rosterd at a live agent repo. It reads the graph structure and tool schemas straight out of the code, no annotations, no SDK."
          />
          <Feature
            step="02 · confirm"
            title="A human signs off"
            body="Nothing here was typed by hand, and nothing governs a real dispatch until a person reviews and confirms it."
            delay={0.06}
          />
          <Feature
            step="03 · enforce"
            title="Every call, checked"
            body="Each tool call is checked against the confirmed contract the moment it happens. Break it, and the run dies, with a trace to prove it."
            delay={0.12}
          />
        </div>
      </div>

      {/* PROOF */}
      <div style={{ maxWidth: 1120, margin: '80px auto 0', padding: '0 24px' }}>
        <div style={{ fontFamily: 'var(--font-mono)', fontSize: 12, letterSpacing: '0.12em', textTransform: 'uppercase', color: 'var(--accent-dark)', fontWeight: 600, marginBottom: 10 }}>
          verified live
        </div>
        <h2 style={{ fontSize: 30, fontWeight: 700, color: 'var(--text-strong)', margin: '0 0 32px', letterSpacing: '-0.01em' }}>
          Not a mockup. This is what the running stack just did.
        </h2>
        <div className="landing__proof-grid">
          <Reveal>
            <div style={{ background: 'var(--surface)', border: '1px solid var(--border)', borderRadius: 14, padding: 24, height: '100%' }}>
              <div style={{ display: 'flex', justifyContent: 'space-between', fontFamily: 'var(--font-mono)', fontSize: 12, color: 'var(--text-muted)', marginBottom: 10 }}>
                <span>fulfillment · replicas under load</span>
                <span>1 → 10</span>
              </div>
              <ScaleBar />
              <p style={{ fontSize: 13, color: 'var(--text-body)', margin: '14px 0 0' }}>
                Black Friday load test, four pools at once: order_intake, catalog, fulfillment, payment all climbed
                to the same ceiling, simultaneously, on the real kernel.
              </p>
            </div>
          </Reveal>
          <Reveal delay={0.06}>
            <div style={{ background: 'var(--surface)', border: '1px solid var(--danger-border)', borderRadius: 14, padding: 24, height: '100%' }}>
              <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 12 }}>
                <span style={{ fontFamily: 'var(--font-mono)', fontSize: 12, color: 'var(--text-muted)' }}>payment · ORD-5002</span>
                <Badge tone="danger">killed</Badge>
              </div>
              <div style={{ fontFamily: 'var(--font-mono)', fontSize: 12.5, color: 'var(--text-body)', lineHeight: 1.6 }}>
                violation: tool_calls[*].args.amount lte 2000
                <br />
                actual: 5000.0
              </div>
              <p style={{ fontSize: 13, color: 'var(--text-body)', margin: '14px 0 0' }}>
                A real dispatch, a real over-cap request, caught at the tool-call boundary. The same enforcement
                path as every other agent, extended to one that didn't exist an hour earlier.
              </p>
            </div>
          </Reveal>
        </div>
      </div>

      {/* CLOSING CTA */}
      <div style={{ maxWidth: 780, margin: '88px auto 0', padding: '48px 24px', textAlign: 'center' }}>
        <h2 style={{ fontSize: 28, fontWeight: 700, color: 'var(--text-strong)', margin: '0 0 14px', letterSpacing: '-0.01em' }}>
          Give your agents rules they can't talk their way out of.
        </h2>
        <p style={{ fontSize: 15, color: 'var(--text-body)', margin: '0 0 26px' }}>
          Same code, same contract, running live, every time.
        </p>
        <div style={{ display: 'flex', gap: 14, justifyContent: 'center', flexWrap: 'wrap' }}>
          <MotionLink to="/ingest" className="btn btn--lg" whileHover={HOVER_LIFT} whileTap={TAP_PRESS} style={{ textDecoration: 'none' }}>
            Watch it enforce a violation
          </MotionLink>
          <MotionAnchor
            href="https://github.com/SathvikNapa/rosterd-example"
            target="_blank"
            rel="noreferrer"
            className="btn btn--lg btn--ghost"
            whileHover={HOVER_LIFT}
            whileTap={TAP_PRESS}
            style={{ textDecoration: 'none' }}
          >
            View the example repo
          </MotionAnchor>
        </div>
      </div>

      {/* FOOTER */}
      <div style={{ borderTop: '1px solid var(--border)', marginTop: 40, padding: 24, textAlign: 'center', fontFamily: 'var(--font-mono)', fontSize: 12, color: 'var(--text-faint)' }}>
        rosterd: contract-enforced multi-agent orchestration. Built by Sathvik, Param, Joy &amp; Shruti.
      </div>
    </div>
  );
}

/** Fades/rises the first time it's scrolled into view, then stays put. */
function Reveal({ children, delay = 0 }: { children: ReactNode; delay?: number }) {
  const ref = useRef<HTMLDivElement | null>(null);
  const inView = useInView(ref, { once: true, margin: '-10% 0px -10% 0px' });
  return (
    <motion.div
      ref={ref}
      initial={{ opacity: 0, y: 26 }}
      animate={inView ? { opacity: 1, y: 0 } : {}}
      transition={{ duration: 0.55, delay, ease: EASE_OUT }}
      style={{ height: '100%' }}
    >
      {children}
    </motion.div>
  );
}

function Feature({ step, title, body, delay = 0 }: { step: string; title: string; body: string; delay?: number }) {
  return (
    <Reveal delay={delay}>
      <div className="landing__feature-card" style={{ background: 'var(--surface)', border: '1px solid var(--border)', borderRadius: 14, padding: '26px 24px', height: '100%' }}>
        <div style={{ fontFamily: 'var(--font-mono)', fontSize: 12, color: 'var(--accent-dark)', fontWeight: 700, marginBottom: 12 }}>{step}</div>
        <h3 style={{ fontSize: 17, fontWeight: 700, margin: '0 0 8px', color: 'var(--text-strong)' }}>{title}</h3>
        <p style={{ fontSize: 14, lineHeight: 1.6, color: 'var(--text-body)', margin: 0 }}>{body}</p>
      </div>
    </Reveal>
  );
}

/** A stat cell that counts up (via AnimatedNumber's spring) the first time
 * it scrolls into view, rather than rendering the final number outright. */
function Stat({ target, prefix = '', suffix = '', label }: { target: number; prefix?: string; suffix?: string; label: string }) {
  const ref = useRef<HTMLDivElement | null>(null);
  const inView = useInView(ref, { once: true, margin: '-10% 0px -10% 0px' });
  return (
    <div ref={ref} style={{ background: 'var(--surface)', padding: '22px 20px' }}>
      <div style={{ fontFamily: 'var(--font-mono)', fontSize: 30, fontWeight: 700, color: 'var(--text-strong)' }}>
        {prefix}
        <AnimatedNumber value={inView ? target : 0} />
        {suffix}
      </div>
      <div style={{ fontSize: 12.5, color: 'var(--text-muted)', marginTop: 4 }}>{label}</div>
    </div>
  );
}

/** Fills 8% → 100% the first time it's scrolled into view — the "1 → 10
 * replicas" bar in the proof section. */
function ScaleBar() {
  const ref = useRef<HTMLDivElement | null>(null);
  const inView = useInView(ref, { once: true, margin: '-10% 0px -10% 0px' });
  return (
    <div ref={ref} style={{ height: 10, borderRadius: 999, background: 'var(--surface-muted)', overflow: 'hidden' }}>
      <motion.div
        style={{ height: '100%', borderRadius: 999, background: 'linear-gradient(90deg, var(--accent-mid), var(--accent-dark))' }}
        initial={{ width: '8%' }}
        animate={inView ? { width: '100%' } : { width: '8%' }}
        transition={{ duration: 1.3, ease: EASE_OUT }}
      />
    </div>
  );
}
