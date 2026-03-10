"use client";

import { useRef } from "react";
import Link from "next/link";
import { motion, useInView } from "framer-motion";
import {
  Mic,
  PenTool,
  AlertTriangle,
  Play,
  ArrowRight,
  Users,
} from "lucide-react";
import { MurmurLogoMark, WaveformToSketch, BackgroundDoodles } from "@/components/murmur-doodles";

/* ── Animation variants ── */
const fadeUp = {
  hidden: { opacity: 0, y: 30 },
  visible: (i: number) => ({
    opacity: 1,
    y: 0,
    transition: { delay: i * 0.12, duration: 0.6, ease: [0.16, 1, 0.3, 1] },
  }),
};

const drawLine = {
  hidden: { pathLength: 0, opacity: 0 },
  visible: (i: number) => ({
    pathLength: 1,
    opacity: 1,
    transition: {
      pathLength: { delay: i * 0.2, duration: 1.4, ease: "easeInOut" },
      opacity: { delay: i * 0.2, duration: 0.3 },
    },
  }),
};

/* ── Scroll-animated section wrapper ── */
function RevealSection({
  children,
  className,
}: {
  children: React.ReactNode;
  className?: string;
}) {
  const ref = useRef(null);
  const inView = useInView(ref, { once: true, margin: "-80px" });

  return (
    <div ref={ref} className={className}>
      <motion.div
        initial="hidden"
        animate={inView ? "visible" : "hidden"}
        variants={{
          hidden: {},
          visible: { transition: { staggerChildren: 0.1 } },
        }}
      >
        {children}
      </motion.div>
    </div>
  );
}

/* ── Hero background sketch ── */
function HeroSketch() {
  return (
    <motion.svg
      viewBox="0 0 800 600"
      fill="none"
      className="absolute inset-0 w-full h-full pointer-events-none"
      strokeLinecap="round"
      strokeLinejoin="round"
      initial="hidden"
      animate="visible"
    >
      {/* Large waveform arc — left side */}
      <motion.path
        d="M80 300 Q120 180 160 300 Q200 420 240 300 Q280 180 320 300"
        stroke="hsl(var(--amber))"
        strokeWidth="1.5"
        opacity="0.12"
        custom={0}
        variants={drawLine}
      />

      {/* Geometric shapes — right side */}
      <motion.circle
        cx="620"
        cy="200"
        r="60"
        stroke="hsl(var(--lavender))"
        strokeWidth="1"
        opacity="0.08"
        custom={2}
        variants={drawLine}
      />
      <motion.path
        d="M550 400 L600 320 L650 400 Z"
        stroke="hsl(var(--sage))"
        strokeWidth="1"
        opacity="0.08"
        custom={3}
        variants={drawLine}
      />

      {/* Scattered dots */}
      {[
        [400, 100],
        [700, 350],
        [150, 480],
        [500, 500],
      ].map(([cx, cy], i) => (
        <motion.circle
          key={i}
          cx={cx}
          cy={cy}
          r="2"
          fill="hsl(var(--chalk-faint))"
          custom={4 + i}
          variants={{
            hidden: { opacity: 0 },
            visible: (j: number) => ({
              opacity: 0.3,
              transition: { delay: j * 0.2, duration: 0.5 },
            }),
          }}
        />
      ))}

      {/* Math notation — very faint */}
      <motion.text
        x="680"
        y="140"
        fill="hsl(var(--chalk-faint))"
        fontSize="14"
        fontFamily="var(--font-handwriting)"
        custom={8}
        variants={{
          hidden: { opacity: 0 },
          visible: (i: number) => ({
            opacity: 0.15,
            transition: { delay: i * 0.2, duration: 0.8 },
          }),
        }}
      >
        F = ma
      </motion.text>
      <motion.text
        x="100"
        y="520"
        fill="hsl(var(--chalk-faint))"
        fontSize="14"
        fontFamily="var(--font-handwriting)"
        custom={9}
        variants={{
          hidden: { opacity: 0 },
          visible: (i: number) => ({
            opacity: 0.12,
            transition: { delay: i * 0.2, duration: 0.8 },
          }),
        }}
      >
        &int; f(x) dx
      </motion.text>
    </motion.svg>
  );
}

/* ── Step illustration: sketchy hand-drawn icons ── */
function StepIcon({ step }: { step: 1 | 2 | 3 }) {
  const colors = {
    1: "hsl(var(--amber))",
    2: "hsl(var(--lavender))",
    3: "hsl(var(--sage))",
  };
  return (
    <motion.svg
      viewBox="0 0 80 80"
      fill="none"
      className="w-16 h-16 mx-auto mb-4"
      strokeLinecap="round"
      strokeLinejoin="round"
      initial="hidden"
      whileInView="visible"
      viewport={{ once: true }}
    >
      {step === 1 && (
        <>
          {/* Speech bubble */}
          <motion.path
            d="M20 20 C20 15 60 15 60 20 C60 45 60 45 60 45 C60 50 20 50 20 45 L20 20"
            stroke={colors[1]}
            strokeWidth="2"
            custom={0}
            variants={drawLine}
          />
          <motion.path
            d="M35 50 L30 62 L42 50"
            stroke={colors[1]}
            strokeWidth="2"
            custom={1}
            variants={drawLine}
          />
          {/* Text lines */}
          <motion.path d="M28 28 L52 28" stroke={colors[1]} strokeWidth="1.5" custom={2} variants={drawLine} />
          <motion.path d="M28 35 L48 35" stroke={colors[1]} strokeWidth="1.5" custom={3} variants={drawLine} />
        </>
      )}
      {step === 2 && (
        <>
          {/* Gear / agent being built */}
          <motion.circle cx="40" cy="36" r="16" stroke={colors[2]} strokeWidth="2" custom={0} variants={drawLine} />
          <motion.circle cx="40" cy="36" r="6" stroke={colors[2]} strokeWidth="1.5" custom={1} variants={drawLine} />
          {/* Sparkle lines */}
          <motion.path d="M40 14 L40 8" stroke={colors[2]} strokeWidth="1.5" custom={2} variants={drawLine} />
          <motion.path d="M58 18 L63 13" stroke={colors[2]} strokeWidth="1.5" custom={3} variants={drawLine} />
          <motion.path d="M62 36 L68 36" stroke={colors[2]} strokeWidth="1.5" custom={4} variants={drawLine} />
          {/* Progress dots */}
          <motion.circle cx="30" cy="64" r="2.5" fill={colors[2]} custom={5} variants={{ hidden: { opacity: 0 }, visible: (i: number) => ({ opacity: 1, transition: { delay: i * 0.2 } }) }} />
          <motion.circle cx="40" cy="64" r="2.5" fill={colors[2]} custom={6} variants={{ hidden: { opacity: 0 }, visible: (i: number) => ({ opacity: 0.5, transition: { delay: i * 0.2 } }) }} />
          <motion.circle cx="50" cy="64" r="2.5" fill={colors[2]} custom={7} variants={{ hidden: { opacity: 0 }, visible: (i: number) => ({ opacity: 0.25, transition: { delay: i * 0.2 } }) }} />
        </>
      )}
      {step === 3 && (
        <>
          {/* Canvas with sketch */}
          <motion.rect x="15" y="12" width="50" height="40" rx="3" stroke={colors[3]} strokeWidth="2" custom={0} variants={drawLine} />
          {/* Triangle sketch inside canvas */}
          <motion.path d="M28 44 L40 22 L52 44 Z" stroke={colors[3]} strokeWidth="1.5" custom={1} variants={drawLine} />
          {/* Sound waves below */}
          <motion.path d="M25 62 Q32 56 40 62 Q48 68 55 62" stroke={colors[3]} strokeWidth="1.5" custom={2} variants={drawLine} />
        </>
      )}
    </motion.svg>
  );
}

/* ════════════════════════════════════════════════════════
   LANDING PAGE
   ════════════════════════════════════════════════════════ */
export default function LandingPage() {
  return (
    <>
      {/* ── HERO ── */}
      <section className="relative min-h-screen flex items-center justify-center overflow-hidden pt-20">
        <HeroSketch />

        <div className="relative z-10 max-w-4xl mx-auto px-6 text-center">
          <motion.div
            initial="hidden"
            animate="visible"
            variants={{ visible: { transition: { staggerChildren: 0.12 } } }}
          >
            {/* Badge */}
            <motion.div custom={0} variants={fadeUp} className="inline-flex items-center gap-2 glass-card px-4 py-2 rounded-full mb-8">
              <span className="w-2 h-2 rounded-full bg-sage animate-pulse" />
              <span className="text-xs font-mono text-chalk-soft tracking-wide uppercase">
                Voice-first AI learning
              </span>
            </motion.div>

            {/* Headline */}
            <motion.h1
              custom={1}
              variants={fadeUp}
              className="text-hero md:text-[4.5rem] font-bold tracking-tight leading-[1.05] mb-6"
            >
              Your AI tutor that{" "}
              <span className="text-amber">draws</span> while it{" "}
              <span className="text-lavender">talks</span>
            </motion.h1>

            {/* Sub-headline */}
            <motion.p
              custom={2}
              variants={fadeUp}
              className="text-lg md:text-xl text-chalk-soft max-w-2xl mx-auto mb-10 leading-relaxed"
            >
              Speak naturally. Murmur listens, understands, and sketches diagrams,
              equations, and graphs on a live canvas — just like the best teacher
              you ever had.
            </motion.p>

            {/* CTAs */}
            <motion.div custom={3} variants={fadeUp} className="flex items-center justify-center gap-4 flex-wrap">
              <Link
                href="/register"
                className="inline-flex items-center gap-2 bg-amber text-void font-semibold px-8 py-4 rounded-xl text-lg hover:brightness-110 transition-all active:scale-[0.97] shadow-orb-amber"
              >
                Get Started
                <ArrowRight className="h-5 w-5" />
              </Link>
              <a
                href="#demo"
                className="inline-flex items-center gap-2 glass-card px-8 py-4 rounded-xl text-lg font-medium text-foreground hover:bg-graphite transition-all"
              >
                <Play className="h-5 w-5" />
                Watch Demo
              </a>
            </motion.div>

            {/* Waveform-to-sketch illustration */}
            <motion.div custom={4} variants={fadeUp} className="mt-16 flex justify-center">
              <WaveformToSketch className="max-w-[320px] opacity-70" />
            </motion.div>
          </motion.div>
        </div>

        {/* Scroll indicator */}
        <motion.div
          className="absolute bottom-8 left-1/2 -translate-x-1/2"
          animate={{ y: [0, 8, 0] }}
          transition={{ duration: 2, repeat: Infinity, ease: "easeInOut" }}
        >
          <div className="w-6 h-10 rounded-full border-2 border-chalk-faint/40 flex justify-center pt-2">
            <div className="w-1.5 h-1.5 rounded-full bg-chalk-soft" />
          </div>
        </motion.div>
      </section>

      {/* ── HOW IT WORKS ── */}
      <RevealSection className="py-32 px-6">
        <div className="max-w-5xl mx-auto">
          <motion.div custom={0} variants={fadeUp} className="text-center mb-20">
            <p className="text-sm font-mono text-amber tracking-widest uppercase mb-4">
              How it works
            </p>
            <h2 className="text-display font-bold tracking-tight">
              Three steps to a smarter study session
            </h2>
          </motion.div>

          <div className="grid md:grid-cols-3 gap-12 md:gap-8">
            {/* Step 1 */}
            <motion.div custom={1} variants={fadeUp} className="text-center">
              <StepIcon step={1} />
              <div className="inline-flex items-center justify-center w-8 h-8 rounded-full border border-amber/30 text-amber text-sm font-mono mb-4">
                1
              </div>
              <h3 className="text-lg font-semibold mb-2">Describe yourself</h3>
              <p className="text-chalk-soft text-sm leading-relaxed">
                &quot;I&apos;m preparing for JEE Physics and I struggle with rotational mechanics.&quot;
                Tell Murmur who you are and what you need help with.
              </p>
            </motion.div>

            {/* Step 2 */}
            <motion.div custom={2} variants={fadeUp} className="text-center">
              <StepIcon step={2} />
              <div className="inline-flex items-center justify-center w-8 h-8 rounded-full border border-lavender/30 text-lavender text-sm font-mono mb-4">
                2
              </div>
              <h3 className="text-lg font-semibold mb-2">AI builds your agent</h3>
              <p className="text-chalk-soft text-sm leading-relaxed">
                Murmur creates a personalized AI tutor tailored to your syllabus,
                pace, and weak spots. No two agents are the same.
              </p>
            </motion.div>

            {/* Step 3 */}
            <motion.div custom={3} variants={fadeUp} className="text-center">
              <StepIcon step={3} />
              <div className="inline-flex items-center justify-center w-8 h-8 rounded-full border border-sage/30 text-sage text-sm font-mono mb-4">
                3
              </div>
              <h3 className="text-lg font-semibold mb-2">Learn with voice + canvas</h3>
              <p className="text-chalk-soft text-sm leading-relaxed">
                Ask a question out loud. Your agent speaks the answer while
                drawing diagrams and equations in real time on the canvas.
              </p>
            </motion.div>
          </div>
        </div>
      </RevealSection>

      {/* ── Hand-drawn divider ── */}
      <div className="flex justify-center py-4">
        <motion.svg
          viewBox="0 0 200 8"
          className="w-48 h-2"
          initial="hidden"
          whileInView="visible"
          viewport={{ once: true }}
        >
          <motion.path
            d="M0 4 Q50 0 100 4 Q150 8 200 4"
            stroke="hsl(var(--chalk-faint))"
            strokeWidth="1"
            fill="none"
            custom={0}
            variants={drawLine}
          />
        </motion.svg>
      </div>

      {/* ── FEATURE HIGHLIGHTS ── */}
      <RevealSection className="py-32 px-6">
        <div className="max-w-5xl mx-auto">
          <motion.div custom={0} variants={fadeUp} className="text-center mb-20">
            <p className="text-sm font-mono text-lavender tracking-widest uppercase mb-4">
              Features
            </p>
            <h2 className="text-display font-bold tracking-tight">
              Built for how you actually learn
            </h2>
          </motion.div>

          <div className="grid md:grid-cols-2 gap-6">
            {[
              {
                icon: <Mic className="h-6 w-6" />,
                title: "Voice-first interaction",
                description:
                  "No typing, no clicking through menus. Just speak naturally and your AI tutor responds instantly with voice and visuals.",
                accent: "amber",
              },
              {
                icon: <PenTool className="h-6 w-6" />,
                title: "Live visual canvas",
                description:
                  "Diagrams, free-body diagrams, graphs, and equations appear on a shared canvas in real time as the AI explains concepts.",
                accent: "lavender",
              },
              {
                icon: <Users className="h-6 w-6" />,
                title: "Personalized agents",
                description:
                  "Your agent remembers your syllabus, learning pace, and past sessions. It adapts to you, not the other way around.",
                accent: "sage",
              },
              {
                icon: <AlertTriangle className="h-6 w-6" />,
                title: "Struggle detection",
                description:
                  "Murmur notices when you're confused — hesitation in your voice, repeated questions — and adjusts its explanation strategy.",
                accent: "ember",
              },
            ].map((feature, i) => (
              <motion.div
                key={feature.title}
                custom={i + 1}
                variants={fadeUp}
                className="glass-card glass-card-hover p-8 rounded-2xl group"
              >
                <div
                  className={`inline-flex items-center justify-center w-12 h-12 rounded-xl mb-5 bg-${feature.accent}/10 text-${feature.accent}`}
                  style={{
                    backgroundColor: `hsl(var(--${feature.accent}) / 0.1)`,
                    color: `hsl(var(--${feature.accent}))`,
                  }}
                >
                  {feature.icon}
                </div>
                <h3 className="text-lg font-semibold mb-2">{feature.title}</h3>
                <p className="text-chalk-soft text-sm leading-relaxed">
                  {feature.description}
                </p>
              </motion.div>
            ))}
          </div>
        </div>
      </RevealSection>

      {/* ── DEMO SECTION ── */}
      <RevealSection className="py-32 px-6" >
        <div id="demo" className="max-w-4xl mx-auto scroll-mt-24">
          <motion.div custom={0} variants={fadeUp} className="text-center mb-12">
            <p className="text-sm font-mono text-sage tracking-widest uppercase mb-4">
              See it in action
            </p>
            <h2 className="text-display font-bold tracking-tight">
              A real tutoring session
            </h2>
          </motion.div>

          <motion.div
            custom={1}
            variants={fadeUp}
            className="glass-card rounded-2xl overflow-hidden aspect-video flex items-center justify-center relative group"
          >
            {/* Placeholder frame with decorative elements */}
            <div className="absolute inset-0 flex items-center justify-center">
              <BackgroundDoodles className="opacity-20" />
            </div>

            <div className="relative z-10 text-center">
              <div className="w-20 h-20 rounded-full glass-card flex items-center justify-center mb-6 mx-auto group-hover:scale-105 transition-transform cursor-pointer">
                <Play className="h-8 w-8 text-amber ml-1" />
              </div>
              <p className="text-chalk-soft text-sm">
                Demo video coming soon
              </p>
              <p className="text-chalk-faint text-xs mt-1">
                Watch how Murmur explains projectile motion with real-time diagrams
              </p>
            </div>

            {/* Decorative corners */}
            <svg className="absolute top-4 left-4 w-8 h-8" viewBox="0 0 32 32" fill="none">
              <path d="M0 12 L0 0 L12 0" stroke="hsl(var(--chalk-faint))" strokeWidth="1.5" opacity="0.3" />
            </svg>
            <svg className="absolute top-4 right-4 w-8 h-8" viewBox="0 0 32 32" fill="none">
              <path d="M20 0 L32 0 L32 12" stroke="hsl(var(--chalk-faint))" strokeWidth="1.5" opacity="0.3" />
            </svg>
            <svg className="absolute bottom-4 left-4 w-8 h-8" viewBox="0 0 32 32" fill="none">
              <path d="M0 20 L0 32 L12 32" stroke="hsl(var(--chalk-faint))" strokeWidth="1.5" opacity="0.3" />
            </svg>
            <svg className="absolute bottom-4 right-4 w-8 h-8" viewBox="0 0 32 32" fill="none">
              <path d="M20 32 L32 32 L32 20" stroke="hsl(var(--chalk-faint))" strokeWidth="1.5" opacity="0.3" />
            </svg>
          </motion.div>
        </div>
      </RevealSection>

      {/* ── CTA FOOTER ── */}
      <section className="py-32 px-6 relative overflow-hidden">
        <div className="absolute inset-0 pointer-events-none">
          <div
            className="absolute top-1/2 left-1/2 -translate-x-1/2 -translate-y-1/2 w-[600px] h-[600px] rounded-full opacity-[0.04]"
            style={{
              background:
                "radial-gradient(circle, hsl(var(--amber)) 0%, transparent 70%)",
            }}
          />
        </div>

        <RevealSection className="relative z-10">
          <div className="max-w-2xl mx-auto text-center">
            <motion.h2
              custom={0}
              variants={fadeUp}
              className="text-display font-bold tracking-tight mb-6"
            >
              Ready to learn differently?
            </motion.h2>
            <motion.p
              custom={1}
              variants={fadeUp}
              className="text-chalk-soft text-lg mb-10 leading-relaxed"
            >
              Stop staring at textbooks. Start having conversations with an AI
              that draws, explains, and adapts to you.
            </motion.p>
            <motion.div custom={2} variants={fadeUp}>
              <Link
                href="/register"
                className="inline-flex items-center gap-2 bg-amber text-void font-semibold px-10 py-4 rounded-xl text-lg hover:brightness-110 transition-all active:scale-[0.97] shadow-orb-amber"
              >
                Create your agent
                <ArrowRight className="h-5 w-5" />
              </Link>
            </motion.div>
            <motion.p custom={3} variants={fadeUp} className="text-chalk-faint text-xs mt-6">
              Free to start. No credit card required.
            </motion.p>
          </div>
        </RevealSection>
      </section>

      {/* ── FOOTER ── */}
      <footer className="border-t border-chalk-faint/20 py-8 px-6">
        <div className="max-w-5xl mx-auto flex items-center justify-between">
          <div className="flex items-center gap-2">
            <MurmurLogoMark className="h-5 w-5" />
            <span className="text-xs text-chalk-soft font-mono">Murmur</span>
          </div>
          <p className="text-xs text-chalk-faint">
            Voice AI that thinks visually.
          </p>
        </div>
      </footer>
    </>
  );
}
